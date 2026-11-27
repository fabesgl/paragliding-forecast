# TODO

Tick items off (`[x]`) as we finish them. Order = priority.

## Next
1. [~] Forecast error step vs BARRA-C2 DONE 2026-11-26 (`data/forecast_error/report.md`); weights built and logged, but the OLD method stays published (`model_weighting: equal`) until the review; one-month review opens a GitHub issue by itself (~2026-12-26). Correction layer: BARRA-based corrections rejected (made real error worse); the existing station-learned layer (`corrections.py`) works but Mystic has no station. Mystic station readings: see item 6; leave 10 m wind uncorrected until then
2. [x] Import the ~500 IGC files a pilot gave me (done 2026-11-26: 510 unique flights; `scripts/import_igc.py`). Still to do: confirm launch names/coords in `sites/launches.yaml`, add Organyà and Dandenongs
3. [ ] Save another year or two of Mystic flights from XContest (manual; `xcontest_pages/mystic_<year>/`)
4. [x] First Creo test, with the rules' deal-breakers as hard limits Creo can't override (first report 2026-11-27: `scripts/creo_test.py`, `data/creo/report.md`; not in the daily job)
5. [ ] "Log a day" form with IGC upload, then open to other Bright pilots (hosting undecided; access code, nicknames, note on what's collected)

6. [ ] Mystic station readings: Fabian will ask the FreeFlightWx station owner in person. Then: write readings to `observations/<id>.csv` in the BOM columns, set `bom_station: <id>` on `mystic_launch`, and the correction layer learns there (30 days to start)
7. [ ] ~2026-12-26: read the weighting review issue (opens by itself), decide whether to switch to `weighted`
8. [ ] Check `sites/bright/calendar.yaml`: the 2026 Bright Open dates came from Visit Bright only, the public holidays were typed from memory (2026 AFL Friday is a guess), and `comp_distance_weight: 0.5` is a guess
9. [ ] ~2026-12-28: run `scripts/creo_review.py` after saving and importing the flights for the logged days (`predictions/creo_log.csv`, shadow mode started 2026-11-27); decide whether Creo is better than the rules on days it never saw

## Ongoing
- [ ] Keep CLAUDE.md, README and tests current
