# TODO

Tick items off (`[x]`) as we finish them. Order = priority.

## Do first
- [ ] CLAUDE.md (written, awaiting review and commit)

## Next
1. [~] Forecast error step vs BARRA-C2 DONE 2026-11-26 (`data/forecast_error/report.md`); weights built and logged, but the OLD method stays published (`model_weighting: equal`) until the review; one-month review opens a GitHub issue by itself (~2026-12-26). Correction layer: BARRA-based corrections rejected (made real error worse); the existing station-learned layer (`corrections.py`) works but Mystic has no station. NEXT: get Mystic station readings (item 7 FreeFlightWx or own sensor), write them to `observations/<id>.csv` in the BOM columns and set `bom_station: <id>` on `mystic_launch` in `sites/bright.yaml`; the layer then learns at Mystic by itself (30 days to start). Leave 10 m wind uncorrected until then
2. [x] Import the ~500 IGC files a pilot gave me (done 2026-11-26: 510 unique flights; `scripts/import_igc.py`). Still to do: confirm launch names/coords in `sites/launches.yaml`, add Organyà and Dandenongs
3. [ ] Save another year or two of Mystic flights from XContest (manual; `xcontest_pages/mystic_<year>/`)
4. [ ] Quick rule wins: check the rules score cloud cover; use the soaring-weather and XC principles docs
5. [ ] First Creo test, with the rules' deal-breakers as hard limits Creo can't override
6. [ ] "Log a day" form with IGC upload, then open to other Bright pilots (Pi endpoint, access code, nicknames, note on what's collected)
7. [ ] Live "today" panel from FreeFlightWx stations (if the admin agrees)
8. [~] Emily vs Marcus thermals from IGC tracks (first pass done 2026-11-26, `scripts/thermal_zones.py`; groups differ in size/gain, not in weather; confirm the map with Fabian)
9. [ ] Proper thermal model from the temperature profile
10. [ ] Bright extras: sun on the north face, wet ground after rain, bushfire smoke
11. [ ] Ensemble forecasts for real probabilities
12. [ ] Try ECMWF AIFS as a fourth model
13. [ ] Weekly AI review of wrong days, I approve fixes
14. [ ] Scores by wing class
15. [ ] Paragliding weather skill, before adding a second site

16. [ ] Trigger the daily workflow from the Pi on a cron (`gh workflow run "Daily update"`, workflow_dispatch) because GitHub's scheduled runs start 6-10 h late and unpredictably; keep the GitHub schedule as a backup (the `concurrency` setting already stops overlaps)
17. [ ] Mystic station readings: Fabian will ask the FreeFlightWx station owner in person. Then: write readings to `observations/<id>.csv` in the BOM columns, set `bom_station: <id>` on `mystic_launch`, and the correction layer learns there (30 days to start)
18. [ ] ~2026-12-26: read the weighting review issue (opens by itself), decide whether to switch to `weighted`

## Ongoing
- [ ] Back up `data/` and `xcontest_pages/` to my Pi
- [ ] Keep CLAUDE.md, README and tests current
- [ ] Periodic tidy-up review
