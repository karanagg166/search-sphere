import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class ExtractedObservationData:
    observation_type: str
    display_name: str
    value_numeric: float | None
    value_secondary_numeric: float | None
    value_text: str | None
    unit: str | None
    observed_at: datetime | None
    reported_at: datetime | None
    is_date_inferred: bool
    page_number: int | None
    chunk_index: int | None
    confidence: float
    extraction_method: str = "REGEX"


MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9, "sept": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def parse_date_string(text: str, default_year: int = 2026) -> datetime | None:
    """Parses various date patterns found in medical text into a UTC datetime."""
    text_clean = text.strip()

    # Pattern: YYYY-MM-DD
    m_iso = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", text_clean)
    if m_iso:
        try:
            year, month, day = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            pass

    # Pattern: Month DD, YYYY (e.g. "Oct 4, 2026", "October 4 2026")
    m_mon_yr = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[.,]?\s+(\d{1,2})(?:st|nd|rd|th)?[,\s]+(\d{4})\b",
        text_clean,
        re.IGNORECASE,
    )
    if m_mon_yr:
        try:
            mon_str = m_mon_yr.group(1).lower()
            month = MONTH_NAMES.get(mon_str[:3], 1)
            day = int(m_mon_yr.group(2))
            year = int(m_mon_yr.group(3))
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            pass

    # Pattern: DD Month YYYY (e.g. "04 Oct 2026", "4 October 2026")
    m_day_mon_yr = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[.,]?\s+(\d{4})\b",
        text_clean,
        re.IGNORECASE,
    )
    if m_day_mon_yr:
        try:
            day = int(m_day_mon_yr.group(1))
            mon_str = m_day_mon_yr.group(2).lower()
            month = MONTH_NAMES.get(mon_str[:3], 1)
            year = int(m_day_mon_yr.group(3))
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            pass

    # Pattern: Month DD (e.g. "Oct 2", "Oct 4") - missing year, use default_year
    m_mon_day = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[.,]?\s+(\d{1,2})(?:st|nd|rd|th)?\b",
        text_clean,
        re.IGNORECASE,
    )
    if m_mon_day:
        try:
            mon_str = m_mon_day.group(1).lower()
            month = MONTH_NAMES.get(mon_str[:3], 1)
            day = int(m_mon_day.group(2))
            return datetime(default_year, month, day, tzinfo=timezone.utc)
        except ValueError:
            pass

    # Pattern: MM/DD/YYYY or DD/MM/YYYY
    m_slash = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", text_clean)
    if m_slash:
        p1, p2, yr = int(m_slash.group(1)), int(m_slash.group(2)), int(m_slash.group(3))
        # Ambiguity resolution: if p1 > 12 -> DD/MM/YYYY
        if p1 > 12 and p2 <= 12:
            day, month = p1, p2
        else:
            month, day = p1, p2
        try:
            return datetime(yr, month, day, tzinfo=timezone.utc)
        except ValueError:
            pass

    return None


class MedicalObservationExtractor:
    """
    Conservative, deterministic rule-based extractor for clinical observations.
    Requires contextual labeling to prevent false positives.
    Validates numeric sanity thresholds before creating observations.
    """

    # --- Blood Pressure ---
    # Matches:
    # "BP: 120/80", "Blood Pressure: 120/80 mmHg", "B.P. 120 / 80", "BP - 135/85 mmHg"
    # Negative lookbehind & specific prefix ensures "Room 120", "Invoice 80" won't match!
    RE_BP = re.compile(
        r"(?:(?:blood\s*pressure|b\.?p\.?)\s*[:=-]?\s*)"
        r"(?P<sys>\d{2,3})\s*/\s*(?P<dia>\d{2,3})"
        r"(?:\s*(?P<unit>mm\s*hg))?",
        re.IGNORECASE,
    )

    # --- Heart Rate / Pulse ---
    # Matches: "Heart Rate: 72 bpm", "Pulse: 72 bpm", "Pulse Rate: 72", "HR: 72", "HR 72 bpm"
    RE_HR = re.compile(
        r"(?:(?:heart\s*rate|pulse\s*rate|pulse|h\.?r\.?)\s*[:=-]?\s*)"
        r"(?P<val>\d{2,3})"
        r"(?:\s*(?P<unit>bpm|beats/min|/min))?",
        re.IGNORECASE,
    )

    # --- Respiratory Rate ---
    # Matches: "Respiratory Rate: 16", "RR: 16 breaths/min", "Resp Rate: 18"
    RE_RR = re.compile(
        r"(?:(?:respiratory\s*rate|resp\s*rate|r\.?r\.?)\s*[:=-]?\s*)"
        r"(?P<val>\d{1,2})"
        r"(?:\s*(?P<unit>breaths/min|bpm|/min))?",
        re.IGNORECASE,
    )

    # --- Oxygen Saturation (SpO2) ---
    # Matches: "SpO2: 98%", "SpO2 98%", "Oxygen Saturation: 97 %", "O2 Sat: 98%"
    RE_SPO2 = re.compile(
        r"(?:(?:sp\s*o2|oxygen\s*saturation|o2\s*sat|sa\s*o2)\s*[:=-]?\s*)"
        r"(?P<val>\d{1,3}(?:\.\d+)?)"
        r"(?:\s*(?P<unit>%|percent))?",
        re.IGNORECASE,
    )

    # --- Body Temperature ---
    # Matches: "Temp: 98.6 F", "Temperature: 37 C", "Body Temperature: 98.6°F"
    RE_TEMP = re.compile(
        r"(?:(?:body\s*temperature|temperature|temp)\s*[:=-]?\s*)"
        r"(?P<val>\d{2,3}(?:\.\d+)?)"
        r"(?:\s*(?:°|deg(?:rees)?)?\s*(?P<unit>[FCfc]))\b",
        re.IGNORECASE,
    )

    # --- Weight ---
    # Matches: "Weight: 72 kg", "Wt: 158 lbs", "Body Weight: 70.5 kg"
    RE_WEIGHT = re.compile(
        r"(?:(?:body\s*weight|weight|wt)\s*[:=-]?\s*)"
        r"(?P<val>\d{1,3}(?:\.\d+)?)"
        r"(?:\s*(?P<unit>kg|kgs|lbs|pounds))\b",
        re.IGNORECASE,
    )

    # --- Height ---
    # Matches: "Height: 175 cm", "Ht: 175 cm", "Height: 1.75 m"
    RE_HEIGHT = re.compile(
        r"(?:(?:height|ht)\s*[:=-]?\s*)"
        r"(?P<val>\d{1,3}(?:\.\d+)?)"
        r"(?:\s*(?P<unit>cm|m|meters|inches|in))\b",
        re.IGNORECASE,
    )

    # --- Fasting Glucose / Blood Sugar ---
    # Matches: "Fasting Blood Sugar: 95 mg/dL", "Fasting Glucose: 95 mg/dL", "FBS: 95 mg/dL"
    RE_FASTING_GLUCOSE = re.compile(
        r"(?:(?:fasting\s*(?:blood\s*)?(?:sugar|glucose)|fbs)\s*[:=-]?\s*)"
        r"(?P<val>\d{2,3}(?:\.\d+)?)"
        r"(?:\s*(?P<unit>mg/dl|mmol/l))?",
        re.IGNORECASE,
    )

    # --- Random Glucose / Blood Sugar ---
    # Matches: "Random Blood Sugar: 130 mg/dL", "Random Glucose: 130 mg/dL", "RBS: 130 mg/dL"
    RE_RANDOM_GLUCOSE = re.compile(
        r"(?:(?:random\s*(?:blood\s*)?(?:sugar|glucose)|rbs)\s*[:=-]?\s*)"
        r"(?P<val>\d{2,3}(?:\.\d+)?)"
        r"(?:\s*(?P<unit>mg/dl|mmol/l))?",
        re.IGNORECASE,
    )

    # --- General Blood Glucose (when not specified fasting or random) ---
    # Matches: "Blood Glucose: 105 mg/dL", "Blood Sugar: 105 mg/dL", "Glucose: 105 mg/dL"
    RE_BLOOD_GLUCOSE = re.compile(
        r"(?:(?:blood\s*(?:sugar|glucose)|glucose)\s*[:=-]?\s*)"
        r"(?P<val>\d{2,3}(?:\.\d+)?)"
        r"(?:\s*(?P<unit>mg/dl|mmol/l))?",
        re.IGNORECASE,
    )

    # --- HbA1c ---
    # Matches: "HbA1c: 5.6%", "HbA1c 5.6%", "Hemoglobin A1c: 5.6 %", "A1c: 5.6%"
    RE_HBA1C = re.compile(
        r"(?:(?:glycated\s*hemoglobin|hemoglobin\s*a1c|hba1c|a1c)\s*[:=-]?\s*)"
        r"(?P<val>\d{1,2}(?:\.\d+)?)"
        r"(?:\s*(?P<unit>%|percent))?",
        re.IGNORECASE,
    )

    # --- Hemoglobin ---
    # Matches: "Hemoglobin: 14.2 g/dL", "Hb: 14.2 g/dL", "Hgb: 14.2 g/dL"
    RE_HEMOGLOBIN = re.compile(
        r"(?:(?:hemoglobin|hgb|hb)\s*[:=-]?\s*)"
        r"(?P<val>\d{1,2}(?:\.\d+)?)"
        r"(?:\s*(?P<unit>g/dl|g/l))?",
        re.IGNORECASE,
    )

    def extract_from_text(
        self,
        text: str,
        document_report_date: datetime | None = None,
        page_number: int = 1,
        chunk_index: int = 0,
    ) -> list[ExtractedObservationData]:
        """
        Extracts validated medical observations from a text snippet or document page.
        Identifies inline or preceding dates for accurate timestamping.
        """
        observations: list[ExtractedObservationData] = []
        if not text:
            return observations

        lines = text.split("\n")
        current_date_context = document_report_date
        default_year = document_report_date.year if document_report_date else 2026

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue

            # Check if this line defines a date header (e.g. "Date: 2026-10-02" or "Oct 2, 2026")
            parsed_line_date = parse_date_string(line_str, default_year=default_year)
            if parsed_line_date and len(line_str) <= 30:
                # Likely a standalone date header line
                current_date_context = parsed_line_date

            # Inline date on the same line if present (e.g. "Oct 2 - BP 118/78")
            inline_date = parsed_line_date if (parsed_line_date and len(line_str) > 30) else None

            # Determine observed date and whether it was inferred
            resolved_date = inline_date or current_date_context or document_report_date
            is_date_inferred = True
            if inline_date is not None or (current_date_context is not None and current_date_context != document_report_date):
                is_date_inferred = False

            # 1. Blood Pressure
            for m in self.RE_BP.finditer(line_str):
                try:
                    sys_val = float(m.group("sys"))
                    dia_val = float(m.group("dia"))
                    # Sanity checks
                    if 40 <= sys_val <= 300 and 30 <= dia_val <= 200 and sys_val > dia_val:
                        observations.append(
                            ExtractedObservationData(
                                observation_type="BLOOD_PRESSURE",
                                display_name="Blood Pressure",
                                value_numeric=sys_val,
                                value_secondary_numeric=dia_val,
                                value_text=f"{int(sys_val)}/{int(dia_val)}",
                                unit="mmHg",
                                observed_at=resolved_date,
                                reported_at=document_report_date,
                                is_date_inferred=is_date_inferred,
                                page_number=page_number,
                                chunk_index=chunk_index,
                                confidence=0.98 if m.group("unit") else 0.95,
                                extraction_method="REGEX",
                            )
                        )
                except (ValueError, TypeError):
                    pass

            # 2. Heart Rate
            for m in self.RE_HR.finditer(line_str):
                try:
                    val = float(m.group("val"))
                    if 20 <= val <= 260:
                        unit = "bpm"
                        observations.append(
                            ExtractedObservationData(
                                observation_type="HEART_RATE",
                                display_name="Heart Rate",
                                value_numeric=val,
                                value_secondary_numeric=None,
                                value_text=str(int(val)),
                                unit=unit,
                                observed_at=resolved_date,
                                reported_at=document_report_date,
                                is_date_inferred=is_date_inferred,
                                page_number=page_number,
                                chunk_index=chunk_index,
                                confidence=0.98 if m.group("unit") else 0.92,
                                extraction_method="REGEX",
                            )
                        )
                except (ValueError, TypeError):
                    pass

            # 3. Respiratory Rate
            for m in self.RE_RR.finditer(line_str):
                try:
                    val = float(m.group("val"))
                    if 4 <= val <= 80:
                        observations.append(
                            ExtractedObservationData(
                                observation_type="RESPIRATORY_RATE",
                                display_name="Respiratory Rate",
                                value_numeric=val,
                                value_secondary_numeric=None,
                                value_text=str(int(val)),
                                unit="breaths/min",
                                observed_at=resolved_date,
                                reported_at=document_report_date,
                                is_date_inferred=is_date_inferred,
                                page_number=page_number,
                                chunk_index=chunk_index,
                                confidence=0.95,
                                extraction_method="REGEX",
                            )
                        )
                except (ValueError, TypeError):
                    pass

            # 4. Oxygen Saturation (SpO2)
            for m in self.RE_SPO2.finditer(line_str):
                try:
                    val = float(m.group("val"))
                    if 0 <= val <= 100:
                        observations.append(
                            ExtractedObservationData(
                                observation_type="OXYGEN_SATURATION",
                                display_name="Oxygen Saturation (SpO2)",
                                value_numeric=val,
                                value_secondary_numeric=None,
                                value_text=f"{val:.0f}%" if val.is_integer() else f"{val}%",
                                unit="%",
                                observed_at=resolved_date,
                                reported_at=document_report_date,
                                is_date_inferred=is_date_inferred,
                                page_number=page_number,
                                chunk_index=chunk_index,
                                confidence=0.98 if m.group("unit") else 0.94,
                                extraction_method="REGEX",
                            )
                        )
                except (ValueError, TypeError):
                    pass

            # 5. Body Temperature
            for m in self.RE_TEMP.finditer(line_str):
                try:
                    val = float(m.group("val"))
                    unit = m.group("unit").upper()
                    # Sanity check based on unit
                    valid = False
                    if unit == "F" and 70.0 <= val <= 115.0:
                        valid = True
                    elif unit == "C" and 25.0 <= val <= 45.0:
                        valid = True
                    if valid:
                        observations.append(
                            ExtractedObservationData(
                                observation_type="BODY_TEMPERATURE",
                                display_name="Body Temperature",
                                value_numeric=val,
                                value_secondary_numeric=None,
                                value_text=f"{val:.1f} {unit}",
                                unit=unit,
                                observed_at=resolved_date,
                                reported_at=document_report_date,
                                is_date_inferred=is_date_inferred,
                                page_number=page_number,
                                chunk_index=chunk_index,
                                confidence=0.98,
                                extraction_method="REGEX",
                            )
                        )
                except (ValueError, TypeError):
                    pass

            # 6. Weight
            for m in self.RE_WEIGHT.finditer(line_str):
                try:
                    val = float(m.group("val"))
                    unit = m.group("unit").lower()
                    if unit in ("kgs", "kg"):
                        unit = "kg"
                    elif unit in ("pounds", "lbs"):
                        unit = "lbs"
                    if 1.0 <= val <= 500.0:
                        observations.append(
                            ExtractedObservationData(
                                observation_type="WEIGHT",
                                display_name="Weight",
                                value_numeric=val,
                                value_secondary_numeric=None,
                                value_text=f"{val} {unit}",
                                unit=unit,
                                observed_at=resolved_date,
                                reported_at=document_report_date,
                                is_date_inferred=is_date_inferred,
                                page_number=page_number,
                                chunk_index=chunk_index,
                                confidence=0.98,
                                extraction_method="REGEX",
                            )
                        )
                except (ValueError, TypeError):
                    pass

            # 7. Height
            for m in self.RE_HEIGHT.finditer(line_str):
                try:
                    val = float(m.group("val"))
                    unit = m.group("unit").lower()
                    if unit in ("cm", "in", "m"):
                        if (unit == "cm" and 20 <= val <= 300) or (unit == "m" and 0.3 <= val <= 3.0) or (unit == "in" and 10 <= val <= 120):
                            observations.append(
                                ExtractedObservationData(
                                    observation_type="HEIGHT",
                                    display_name="Height",
                                    value_numeric=val,
                                    value_secondary_numeric=None,
                                    value_text=f"{val} {unit}",
                                    unit=unit,
                                    observed_at=resolved_date,
                                    reported_at=document_report_date,
                                    is_date_inferred=is_date_inferred,
                                    page_number=page_number,
                                    chunk_index=chunk_index,
                                    confidence=0.98,
                                    extraction_method="REGEX",
                                )
                            )
                except (ValueError, TypeError):
                    pass

            # 8. Glucose: Fasting, Random, or General
            # Prioritize Fasting -> Random -> General to avoid multiple duplicate matches on same line
            glucose_matched = False
            for m in self.RE_FASTING_GLUCOSE.finditer(line_str):
                try:
                    val = float(m.group("val"))
                    unit = m.group("unit") or "mg/dL"
                    if 10.0 <= val <= 1000.0:
                        observations.append(
                            ExtractedObservationData(
                                observation_type="FASTING_GLUCOSE",
                                display_name="Fasting Blood Glucose",
                                value_numeric=val,
                                value_secondary_numeric=None,
                                value_text=f"{val:.0f} {unit}" if val.is_integer() else f"{val} {unit}",
                                unit=unit,
                                observed_at=resolved_date,
                                reported_at=document_report_date,
                                is_date_inferred=is_date_inferred,
                                page_number=page_number,
                                chunk_index=chunk_index,
                                confidence=0.98 if m.group("unit") else 0.92,
                                extraction_method="REGEX",
                            )
                        )
                        glucose_matched = True
                except (ValueError, TypeError):
                    pass

            if not glucose_matched:
                for m in self.RE_RANDOM_GLUCOSE.finditer(line_str):
                    try:
                        val = float(m.group("val"))
                        unit = m.group("unit") or "mg/dL"
                        if 10.0 <= val <= 1000.0:
                            observations.append(
                                ExtractedObservationData(
                                    observation_type="RANDOM_GLUCOSE",
                                    display_name="Random Blood Glucose",
                                    value_numeric=val,
                                    value_secondary_numeric=None,
                                    value_text=f"{val:.0f} {unit}" if val.is_integer() else f"{val} {unit}",
                                    unit=unit,
                                    observed_at=resolved_date,
                                    reported_at=document_report_date,
                                    is_date_inferred=is_date_inferred,
                                    page_number=page_number,
                                    chunk_index=chunk_index,
                                    confidence=0.98 if m.group("unit") else 0.92,
                                    extraction_method="REGEX",
                                )
                            )
                            glucose_matched = True
                    except (ValueError, TypeError):
                        pass

            if not glucose_matched:
                for m in self.RE_BLOOD_GLUCOSE.finditer(line_str):
                    try:
                        val = float(m.group("val"))
                        unit = m.group("unit") or "mg/dL"
                        if 10.0 <= val <= 1000.0:
                            observations.append(
                                ExtractedObservationData(
                                    observation_type="BLOOD_GLUCOSE",
                                    display_name="Blood Glucose",
                                    value_numeric=val,
                                    value_secondary_numeric=None,
                                    value_text=f"{val:.0f} {unit}" if val.is_integer() else f"{val} {unit}",
                                    unit=unit,
                                    observed_at=resolved_date,
                                    reported_at=document_report_date,
                                    is_date_inferred=is_date_inferred,
                                    page_number=page_number,
                                    chunk_index=chunk_index,
                                    confidence=0.95 if m.group("unit") else 0.88,
                                    extraction_method="REGEX",
                                )
                            )
                    except (ValueError, TypeError):
                        pass

            # 9. HbA1c
            for m in self.RE_HBA1C.finditer(line_str):
                try:
                    val = float(m.group("val"))
                    if 2.0 <= val <= 25.0:
                        observations.append(
                            ExtractedObservationData(
                                observation_type="HBA1C",
                                display_name="HbA1c",
                                value_numeric=val,
                                value_secondary_numeric=None,
                                value_text=f"{val}%",
                                unit="%",
                                observed_at=resolved_date,
                                reported_at=document_report_date,
                                is_date_inferred=is_date_inferred,
                                page_number=page_number,
                                chunk_index=chunk_index,
                                confidence=0.98 if m.group("unit") else 0.92,
                                extraction_method="REGEX",
                            )
                        )
                except (ValueError, TypeError):
                    pass

            # 10. Hemoglobin
            # Avoid matching HbA1c as general Hemoglobin
            if not self.RE_HBA1C.search(line_str):
                for m in self.RE_HEMOGLOBIN.finditer(line_str):
                    try:
                        val = float(m.group("val"))
                        unit = m.group("unit") or "g/dL"
                        if 1.0 <= val <= 30.0:
                            observations.append(
                                ExtractedObservationData(
                                    observation_type="HEMOGLOBIN",
                                    display_name="Hemoglobin",
                                    value_numeric=val,
                                    value_secondary_numeric=None,
                                    value_text=f"{val} {unit}",
                                    unit=unit,
                                    observed_at=resolved_date,
                                    reported_at=document_report_date,
                                    is_date_inferred=is_date_inferred,
                                    page_number=page_number,
                                    chunk_index=chunk_index,
                                    confidence=0.98 if m.group("unit") else 0.90,
                                    extraction_method="REGEX",
                                )
                            )
                    except (ValueError, TypeError):
                        pass

        return observations

    def extract_from_extracted_document(
        self,
        extracted_doc: Any,
        report_date: datetime | None = None,
    ) -> list[ExtractedObservationData]:
        """Extracts observations from an ExtractedDocument across all pages."""
        all_obs: list[ExtractedObservationData] = []
        if not extracted_doc or not hasattr(extracted_doc, "pages"):
            return all_obs

        for page in extracted_doc.pages:
            page_num = getattr(page, "page_number", 1)
            page_text = getattr(page, "text", "") or ""
            page_obs = self.extract_from_text(
                text=page_text,
                document_report_date=report_date,
                page_number=page_num,
            )
            all_obs.extend(page_obs)

        return all_obs
