# Best-effort free-tier reliability

Verified provider documentation and service inventory: 2026-11-14. No paid plan,
worker, disk or Render cron job was provisioned. Continuous 24/7 uptime is not
accepted or guaranteed. Render Free sleeps after 15 minutes without inbound traffic;
waking takes time, and platform restarts/quota suspensions remain possible.

## Shared budget

[Render Free](https://render.com/docs/free) allocates 750 instance-hours per
workspace each calendar month. At inspection, My Workspace contained Search
Sphere and Quick-Clinic, both free. Quick-Clinic was not changed.

| Assumption | 30 days | 31 days |
|---|---:|---:|
| One continuously running service | 720 h | 744 h |
| Two continuously running services | 1,440 h | 1,488 h |
| Remaining after one service alone | 30 h | 6 h |

Actual workspace usage is not available from the connected inventory/metrics API.
Unknown usage is not zero. The monitor reserves at least 24 safety hours, reserves
other services' projected usage and counts the 15-minute idle tail after a warm
window. It refuses unsafe, unknown, previous-month or older-than-24-hour snapshots
**before sending any HTTP request**. This is admission control using an operator
snapshot, not a real-time billing meter. Genuine traffic can exceed that forecast;
check the Render dashboard and disable warming when remaining allowance is low.
A 24-hour window near the start of a 31-day month is deliberately refused with
this safety reserve. Prefer sleep and wake-on-request over exhausting the pool.

## Minimal monitor and fallback workflow

`scripts/warm_monitor.py` uses Python's standard library, only the owned HTTPS
host, no redirects, no authentication, no document content and no inference.
It checks `/health`, plus `/ready` at most once during each 06:00 and 18:00 UTC
hour when admitted. Readiness reaches PostgreSQL with `SELECT 1`; health alone
is not database activity. Supabase may still pause free inactive projects; see
[Supabase's production checklist](https://supabase.com/docs/guides/deployment/going-into-prod).
A paused database is reported unavailable; no fake rows or automatic paid upgrade
are used. The existing twice-daily keep-alive/readiness workflow is preserved.

The optional `.github/workflows/warm-monitor.yml` has schedule
`7,17,27,37,47,57 * * * *` UTC. It runs **only** when repository variable
`SS_WARM_ENABLED=true`; default is off. It installs no dependencies. Six executions
per hour mean 4,320/4,464 executions in 30/31 days. A private repository would incur
at least that many rounded runner minutes (before checkout overhead), exceeding
a typical 2,000-minute allocation. Do not enable it in a private repository
without separately verified sufficient free allowance. Standard public runners
are free, but usage/network policies still apply. Scheduled runs may be delayed,
dropped, or disabled after repository inactivity; see
[GitHub schedule limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

Three consecutive admitted failures produce one failed workflow run/annotation;
configure GitHub's existing Actions notifications for this repository. A subsequent
success reports recovery. There is no Slack/email transmission or premium monitor.
A gap over 30 minutes is missing monitoring evidence, not proof of availability.
Failures distinguish network/cold start, HTTP failure, database, worker state,
revision mismatch and reported memory pressure. The existing authenticated job
errors expose provider quota failures to the owning user; the public monitor does
not reveal account quotas or promise external quota alerts.

## Exact operator setup

No external scheduler account/token or verified permission to bypass provider
idle behavior was available. Neither external pings nor the fallback workflow
have been enabled. Confirm Render's applicable terms/support position first;
public documentation describing idle behavior is not affirmative permission for
artificial keep-alive traffic. Do not set `terms_verified` without that check.

1. Inspect Render **Workspace → Billing/Usage → Free instance hours**, including
   every free service. Record cumulative workspace usage and the current UTC time.
2. Reserve all other services' remaining hours. Choose a conservative UTC window.
3. Save a private `warm-budget.json` (illustrative values, never an actual meter):

```json
{
  "enabled": true,
  "terms_verified": false,
  "sampled_at": "2026-11-14T00:00:00+00:00",
  "used_workspace_hours": 0,
  "other_remaining_hours": 200,
  "safety_hours": 24,
  "free_service_count": 2,
  "other_services_budget_verified": false,
  "start_hour_utc": 3,
  "end_hour_utc": 15,
  "expected_sha": "FULL_VERIFIED_DEPLOYED_SHA"
}
```

4. Set actual values/attestations, then run without any installation:
   `python3 scripts/warm_monitor.py --config /private/warm-budget.json --state /private/warm-state.json`.
   Refused admission prints `ping_sent:false`. Refresh the snapshot daily; stale
   configuration safely stops artificial waking.
5. For GitHub fallback, put that JSON in repository variable `SS_WARM_CONFIG`,
   set `SS_WARM_ENABLED=true`, and manually dispatch **Optional budgeted warm
   monitor**. Confirm one successful admitted probe, then observe scheduled runs.
   Turn `SS_WARM_ENABLED=false` to disable immediately. Do not put secrets in vars.
6. If an already-owned free scheduler can execute Python with private persistent
   state, install the script there and schedule the command at the UTC expression
   above. Verify its own request/execution quota supports 4,464 runs/month first.
   An HTTP-only scheduler can GET `https://search-sphere-api.onrender.com/health`
   with timeout 30 seconds, redirects off, one request each 10 minutes and alerts
   after three failures, **but lacks the budget admission logic**: keep it disabled
   until its schedule/window and all workspace reserves are verified; refresh
   dashboard usage daily and disable before allowance exhaustion. Do not configure
   an unguarded 24-hour ping to both services. No scheduler reliability was tested.

## Recovery and user experience

Liveness does not open a DB connection. Readiness has a four-second total bound,
two 1.5-second attempts, coalesces simultaneous successful probes for one second,
and disposes idle pool connections on a stale-connection failure. It never
retries application transactions. Database pool checkout is bounded at five
seconds. The durable worker polls with bounded exponential backoff after DB
failure, renews active leases and reclaims expired leases through existing SQL
locks. Publication/reprocessing/deletion keep existing ownership and generation
checks; a suspended process cannot continue jobs.

The web app displays its existing waking/reconnecting state, retains the user
session, and adds at most two GET/HEAD retries (2 and 4 seconds). It rechecks the
session before replay. Uploads, deletes and other writes are never automatically
replayed. Policy errors, quota errors, aborted calls and readiness probes do not
enter this retry loop. Persistent unavailability remains visible after bounded
recovery rather than requiring endless refreshes.

## Live observations

Before deploying these changes, the existing Render revision
`01b004478c6736d0431ef81ac48f1bd21c757ec0` timed out on its first 35-second health
request, then returned HTTP 200 after a five-second backoff and another 13.18
seconds. `/ready` returned HTTP 200 in 0.85 seconds. This demonstrates one actual
cold-start recovery and database reachability for the **old** revision. It does
not prove this candidate's worker recovery, uninterrupted availability, or new
extraction deployment. See the release report for final deployment evidence.

## Verified external scheduler option

The [cron-job.org FAQ](https://cron-job.org/en/faq/), checked 2026-11-14,
describes a free service with fair-use job counts, intervals down to one minute,
a 30-second request ceiling and a 64 KiB response ceiling. Six hourly requests
fit that published interval allowance. It gives no timing guarantee, retains
recent HTTP responses, and can disable repeatedly failing jobs. No account/token
was connected and no job was created here.

After the Render budget/terms checks above, an operator can create a **disabled**
job named `Search Sphere budget-approved health`, set URL to
`https://search-sphere-api.onrender.com/health`, method GET, UTC timezone, minutes
`7,17,27,37,47,57`, and only the approved warm-window hours. Send no credentials,
cookies or private headers; retain only public liveness output. Set timeout to
30 seconds and disable redirects if offered. Keep the existing twice-daily
readiness monitor for actual DB activity. An HTTP-only scheduler cannot execute
the local budget admission or consume its state: enable it only after manually
checking daily usage and the window, and disable it before allowance exhaustion.
Its failure email option does not establish the script's three-failure policy;
use the script/GitHub fallback for that tested policy. A cold start may exceed
30 seconds and produce a monitor failure even when the service later recovers.
For these reasons the guarded script remains the safer configurable fallback.
