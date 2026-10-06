from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import re
from typing import Any

from src.schemas.search import ConversationMessage


class MedicalQueryRoute(str, Enum):
    STRUCTURED = "STRUCTURED"
    RAG = "RAG"
    HYBRID = "HYBRID"


@dataclass
class TimeRangeFilter:
    from_date: datetime | None = None
    to_date: datetime | None = None
    is_latest: bool = False
    is_trend: bool = False
    limit: int = 100
    description: str = ""


@dataclass
class RoutedMedicalQuery:
    route: MedicalQueryRoute
    raw_query: str
    effective_query: str
    target_observation_types: list[str]
    time_filter: TimeRangeFilter


# Structured concept patterns mapped to observation types
STRUCTURED_CONCEPT_PATTERNS: list[tuple[str, list[str]]] = [
    (r"\b(?:blood\s*pressures?|b\.?p\.?|systolic|diastolic|hypertension)\b", ["BLOOD_PRESSURE"]),
    (r"\b(?:fasting\s*(?:blood\s*)?(?:sugar|glucose)|fbs)\b", ["FASTING_GLUCOSE"]),
    (r"\b(?:random\s*(?:blood\s*)?(?:sugar|glucose)|rbs)\b", ["RANDOM_GLUCOSE"]),
    (r"\b(?:glucose|blood\s*sugar|sugars)\b", ["FASTING_GLUCOSE", "RANDOM_GLUCOSE", "BLOOD_GLUCOSE"]),
    (r"\b(?:heart\s*rates?|pulse|pulse\s*rate|h\.?r\.?)\b", ["HEART_RATE"]),
    (r"\b(?:sp\s*o2|oxygen\s*saturation|o2\s*sat|oxygen\s*levels?)\b", ["OXYGEN_SATURATION"]),
    (r"\b(?:temperatures?|body\s*temp|temp|fever)\b", ["BODY_TEMPERATURE"]),
    (r"\b(?:respiratory\s*rates?|resp\s*rate|breathing\s*rate|r\.?r\.?)\b", ["RESPIRATORY_RATE"]),
    (r"\b(?:body\s*weights?|weights?|wt)\b", ["WEIGHT"]),
    (r"\b(?:heights?|ht)\b", ["HEIGHT"]),
    (r"\b(?:glycated\s*hemoglobin|hemoglobin\s*a1c|hba1c|a1c)\b", ["HBA1C"]),
    (r"\b(?:hemoglobin|hgb|hb)\b", ["HEMOGLOBIN"]),
]

# Explicit temporal or numeric aggregation intent for structured queries
STRUCTURED_TEMPORAL_AGG_PATTERN = re.compile(
    r"\b(?:latest|(?:most\s*)?recent|last\s*(?:\d+\s*)?(?:days?|weeks?|months?)|"
    r"past\s*(?:\d+\s*)?(?:days?|weeks?|months?)|"
    r"in\s*the\s*last\s*(?:\d+\s*)?(?:days?|weeks?|months?)|"
    r"in\s*the\s*past\s*(?:\d+\s*)?(?:days?|weeks?|months?)|"
    r"today|yesterday|this\s*week|last\s*week|past\s*week|"
    r"trend|trends|pattern|progression|"
    r"readings|values|measurements|recorded|history|list\s+.*readings)\b",
    re.IGNORECASE,
)

# Narrative / clinical document indicators
NARRATIVE_INTENT_PATTERN = re.compile(
    r"\b(?:discharge\s*(?:report|summary|note|instructions?)|radiology\s*(?:report|note)|"
    r"prescription|prescribed?|medications?|medicines?|drugs?|antibiotics?|"
    r"doctor\s*(?:note|notes|say|said|noted|opined|stated|plan|orders?)|"
    r"physician\s*(?:note|notes|say|said|noted|plan|orders?)|"
    r"clinical\s*(?:note|notes)|chart|summarize|summary|overview|opinion|recommendations?|recommends?|recommended|"
    r"advised?|instructions?|treatment\s*plan|plan\s*of\s*care|(?:care\s*)?plan|diagnosis|findings?|impression|"
    r"physical\s*exam(?:\s*notes?)?|consultation|"
    r"what\s*did\s*the\s*doctor\s*say|what\s*does\s*the\s*.*say|"
    r"in\s*patient\s*medical\s*reports|in\s*the\s*report|in\s*the\s*records?)\b",
    re.IGNORECASE,
)

# Elliptical follow-up pattern (e.g. "Only the last 3 days.", "What about yesterday?", "And today?")
ELLIPTICAL_FOLLOWUP_PATTERN = re.compile(
    r"^(?:only\s+)?(?:the\s+)?(?:in\s+the\s+)?(?:last|past)\s+\d+\s+days[.?]?$|"
    r"^(?:only\s+)?(?:the\s+)?(?:last|past)\s+week[.?]?$|"
    r"^(?:what|how)\s+about\s+(?:yesterday|today|last\s+week|the\s+last\s+\d+\s+days)[.?]?$",
    re.IGNORECASE,
)


import zoneinfo


def parse_relative_time_range(
    query: str,
    now: datetime | None = None,
    tz_name: str | None = "UTC",
) -> TimeRangeFilter:
    """
    Parses relative temporal expressions from query with exact calendar semantics and timezone support.

    Semantics:
    - today: start of current calendar day (00:00:00) -> now
    - yesterday: previous calendar day start (00:00:00) -> previous calendar day end (23:59:59.999999)
    - this week: start of current calendar week (Monday 00:00:00) -> now
    - last week: previous complete Monday-Sunday interval (Monday 00:00:00 -> Sunday 23:59:59.999999)
    - last N days: rolling time window (now - N days -> now)
    - latest / most recent: is_latest=True, limit=1
    - recent: last 10 readings
    - trend: is_trend=True

    Timezone strategy:
    Calculates calendar day boundaries in the user's local timezone (or tz_name), then normalizes
    from_date and to_date to UTC for querying UTC timestamps in PostgreSQL.
    """
    try:
        local_tz = zoneinfo.ZoneInfo(tz_name) if tz_name else timezone.utc
    except Exception:
        local_tz = timezone.utc

    if now is not None:
        local_now = now.astimezone(local_tz) if now.tzinfo else now.replace(tzinfo=local_tz)
    else:
        local_now = datetime.now(local_tz)

    query_lower = query.lower()

    # 1. Latest / Most Recent
    if re.search(r"\b(?:latest|most\s*recent)\b", query_lower):
        return TimeRangeFilter(
            is_latest=True,
            limit=1,
            description="latest",
        )

    # 2. Today: start of current calendar day -> now
    if re.search(r"\btoday\b", query_lower):
        start_of_today_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return TimeRangeFilter(
            from_date=start_of_today_local.astimezone(timezone.utc),
            to_date=local_now.astimezone(timezone.utc),
            description="today",
        )

    # 3. Yesterday: previous calendar day start -> previous calendar day end
    if re.search(r"\byesterday\b", query_lower):
        start_of_today_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_yesterday_local = start_of_today_local - timedelta(days=1)
        end_of_yesterday_local = start_of_today_local - timedelta(microseconds=1)
        return TimeRangeFilter(
            from_date=start_of_yesterday_local.astimezone(timezone.utc),
            to_date=end_of_yesterday_local.astimezone(timezone.utc),
            description="yesterday",
        )

    # 4. Last / Past N Days: rolling time window
    m_days = re.search(r"\b(?:last|past|in\s*the\s*last|in\s*the\s*past)\s*(\d+)\s*days?\b", query_lower)
    if m_days:
        num_days = int(m_days.group(1))
        from_dt = local_now - timedelta(days=num_days)
        return TimeRangeFilter(
            from_date=from_dt.astimezone(timezone.utc),
            to_date=local_now.astimezone(timezone.utc),
            description=f"last {num_days} days",
        )

    # 5. This week: start of current calendar week (Monday) -> now
    if re.search(r"\bthis\s*week\b", query_lower):
        days_since_monday = local_now.weekday()  # Monday is 0, Sunday is 6
        start_of_week_local = (local_now - timedelta(days=days_since_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return TimeRangeFilter(
            from_date=start_of_week_local.astimezone(timezone.utc),
            to_date=local_now.astimezone(timezone.utc),
            description="this week",
        )

    # 6. Last week / past week: previous complete Monday-Sunday interval
    if re.search(r"\b(?:last|past)\s*week\b", query_lower):
        days_since_monday = local_now.weekday()
        this_monday_local = (local_now - timedelta(days=days_since_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        last_monday_local = this_monday_local - timedelta(days=7)
        last_sunday_end_local = this_monday_local - timedelta(microseconds=1)
        return TimeRangeFilter(
            from_date=last_monday_local.astimezone(timezone.utc),
            to_date=last_sunday_end_local.astimezone(timezone.utc),
            description="last week",
        )

    # 7. Trend
    is_trend = bool(re.search(r"\b(?:trend|trends?|pattern|progression)\b", query_lower))

    # 8. Recent
    if re.search(r"\brecent\b", query_lower):
        return TimeRangeFilter(
            is_trend=is_trend,
            limit=10,
            description="recent",
        )

    return TimeRangeFilter(
        is_trend=is_trend,
        description="all available",
    )


class MedicalQueryRouter:
    """
    Determines whether a clinical query targets structured observations, narrative RAG, or hybrid.
    Runs strictly AFTER authentication and doctor authorization in Quick Clinic.
    Does NOT make diagnostic decisions or control access.
    """

    def route_query(
        self,
        query: str,
        conversation_context: list[ConversationMessage] | None = None,
        now: datetime | None = None,
    ) -> RoutedMedicalQuery:
        effective_query = query.strip()

        detected_types: list[str] = []
        for pattern_str, obs_types in STRUCTURED_CONCEPT_PATTERNS:
            if re.search(pattern_str, effective_query, re.IGNORECASE):
                for t in obs_types:
                    if t not in detected_types:
                        detected_types.append(t)

        # Multi-turn context resolution:
        # If the user gives an elliptical temporal refinement (e.g. "Only the last 3 days."),
        # pull the clinical concept from the preceding user turn (PART 57)
        if not detected_types and conversation_context and ELLIPTICAL_FOLLOWUP_PATTERN.search(effective_query.strip()):
            for prev_msg in reversed(conversation_context):
                if prev_msg.role.lower() == "user":
                    for pattern_str, obs_types in STRUCTURED_CONCEPT_PATTERNS:
                        if re.search(pattern_str, prev_msg.content, re.IGNORECASE):
                            for t in obs_types:
                                if t not in detected_types:
                                    detected_types.append(t)
                    if detected_types:
                        concept_name = detected_types[0].replace("_", " ").lower()
                        effective_query = f"{concept_name} {effective_query}"
                        break

        time_filter = parse_relative_time_range(effective_query, now=now)
        has_structured_concept = len(detected_types) > 0
        has_temporal_or_agg = bool(STRUCTURED_TEMPORAL_AGG_PATTERN.search(effective_query))
        has_narrative_intent = bool(NARRATIVE_INTENT_PATTERN.search(effective_query))

        # Classification rules:
        # 1. HYBRID: Structured concept AND explicit narrative clinical notes/reports question
        #    e.g. "What were the recent BP readings and what did the doctor note about them?"
        #    e.g. "How has the patient's blood pressure changed and what does the discharge note say about it?"
        if has_structured_concept and has_narrative_intent:
            route = MedicalQueryRoute.HYBRID

        # 2. STRUCTURED: Structured concept present WITH temporal or aggregation intent, AND no narrative request
        #    e.g. "What was the BP in the last 3 days?", "Show glucose readings this week.", "Latest blood pressure"
        elif has_structured_concept and has_temporal_or_agg and not has_narrative_intent:
            route = MedicalQueryRoute.STRUCTURED

        # 3. RAG: Pure narrative, unspecified documents, or non-aggregated queries
        #    e.g. "Summarize the discharge report.", "What did the radiology note say?", "What is the glucose reading?"
        else:
            route = MedicalQueryRoute.RAG

        return RoutedMedicalQuery(
            route=route,
            raw_query=query,
            effective_query=effective_query,
            target_observation_types=detected_types,
            time_filter=time_filter,
        )


_query_router = MedicalQueryRouter()


def get_medical_query_router() -> MedicalQueryRouter:
    return _query_router
