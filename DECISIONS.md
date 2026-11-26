# Decisions

Newest at the bottom. Add a line whenever we decide something. Format: date — decision — why.

- 2026-11-25 — **Day score = weakest rule** (not the product of rules). One deal-breaker should sink the day, and many mild ones shouldn't multiply it to nothing.
- 2026-11-25 — **Rule limits live in `sites/bright/rules.yaml`**, commented and validated, so Fabian can edit numbers without touching code.
- 2026-11-25 — **Acro gust limit = launch limit** (25 km/h, 0% at 40), same as XC. It is a launch limit; air turbulence is covered by the shear and westerly rules.
- 2026-11-25 — **Northerly wind aloft is a mild penalty for acro** (max −30%, never a no-go). It drifts pilots back over the hill, so they can't train as low; the effect shrinks when climbs are high.
- 2026-11-25 — **Westerlies aloft penalised** (W/WNW fully, WSW half) for turbulence off Mt Buffalo.
- 2026-11-25 — **Valley wind vs southerly**: a southerly at launch is OK when the thermic valley wind (north) is stronger. But a strong southerly at 850 hPa (>25 km/h) is a no-go that the valley wind can't override. The 6 km/h per m/s thermal factor is a default; calibration reports only and never changes rules by itself.
- 2026-11-25 — **No rain scores 100%** (rain eases the score down from 0.2 mm, 0% at 2 mm).
- 2026-11-25 — **AI second opinion never changes scores**; it only comments. Its hard limits (rain, launch wind, gusts, 850 hPa wind) force "poor".
- 2026-11-25 — **Chat labels are weak evidence**: stored at low weight, one person's reports in a day are shared out, only labels kept (no message text or names). Processed with a local model by default; Claude API is optional with a cost cap.
- 2026-11-25 — **Daily job runs after the newest ECMWF/GFS/ICON runs** (07:45 and 19:45 Melbourne) and records which runs were used. Each model is fetched separately; if one fails the others carry on and the dashboard says so.
- 2026-11-25 — **Forecast 6 days**: first 3 open, days 4–6 collapsed and marked lower confidence.
- 2026-11-25 — **The daily job owns `docs/`, `forecast_archive/`, `predictions/` etc.**; laptop commits must not include them (avoids conflicts).
- 2026-11-25 — **No XContest scraping.** Only pages Fabian saves by hand (his own login, normal browsing) are read, offline.
- 2026-11-25 — **Pilot names are hashed** with a salted hash (`data/chat_salt`); one cross-source pilot hash for XContest.
- 2026-11-26 — **Months where saved XContest flights < XContest's total are "incomplete"**; days with no flights there are "unknown", not "nobody flew". Counted by UTC month, as XContest filters.
- 2026-11-26 — **Tandem (glider class T) stored but excluded from XC grading** (`xc_flight_graded`).
- 2026-11-26 — **XContest duplicates removed by flight ID** (overlapping saved pages/sorts), with date+time+pilot+launch as a backup key.
