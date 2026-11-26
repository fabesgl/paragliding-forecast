# TODO

Tick items off (`[x]`) as we finish them. Order = priority.

## Next
1. [~] Forecast error step vs BARRA-C2 DONE 2026-11-26 (`data/forecast_error/report.md`); weights built and logged, but the OLD method stays published (`model_weighting: equal`) until the review; one-month review opens a GitHub issue by itself (~2026-12-26). Correction layer: BARRA-based corrections rejected (made real error worse); the existing station-learned layer (`corrections.py`) works but Mystic has no station. Mystic station readings: see item 8; leave 10 m wind uncorrected until then
2. [x] Import the ~500 IGC files a pilot gave me (done 2026-11-26: 510 unique flights; `scripts/import_igc.py`). Still to do: confirm launch names/coords in `sites/launches.yaml`, add Organyà and Dandenongs
3. [ ] Save another year or two of Mystic flights from XContest (manual; `xcontest_pages/mystic_<year>/`)
4. [ ] First Creo test, with the rules' deal-breakers as hard limits Creo can't override
5. [ ] "Log a day" form with IGC upload, then open to other Bright pilots (Pi endpoint, access code, nicknames, note on what's collected)
6. [~] Emily vs Marcus thermals from IGC tracks (first pass done 2026-11-26, `scripts/thermal_zones.py`; groups differ in size/gain, not in weather; confirm the map with Fabian)

7. [ ] Trigger the daily workflow from the Pi on a cron (`gh workflow run "Daily update"`, workflow_dispatch) because GitHub's scheduled runs start 6-10 h late and unpredictably; keep the GitHub schedule as a backup (the `concurrency` setting already stops overlaps)
8. [ ] Mystic station readings: Fabian will ask the FreeFlightWx station owner in person. Then: write readings to `observations/<id>.csv` in the BOM columns, set `bom_station: <id>` on `mystic_launch`, and the correction layer learns there (30 days to start)
9. [ ] ~2026-12-26: read the weighting review issue (opens by itself), decide whether to switch to `weighted`

## Ongoing
- [ ] Back up `data/` and `xcontest_pages/` to my Pi
- [ ] Keep CLAUDE.md, README and tests current
