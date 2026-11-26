"""Run: .venv/bin/python -m unittest tests.test_ffwx -v   (pure maths, no network)"""
import sys, unittest
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import ffwx


def raw(rows):      # (local time, avg mph, max, min, dir)
    return pd.DataFrame([dict(Date_Time=t, Windspeedmph=a, WindspeedmphMax=mx, WindspeedmphMin=mn, Winddir=d, realQNH=102400, Tempc=20.0, DewPoint=10.0) for t, a, mx, mn, d in rows])


class T(unittest.TestCase):
    def test_bins_units_and_utc(self):
        r = raw([("2026-11-27 09:19:50", 10, 12, 8, 90), ("2026-11-27 09:10:05", 20, 25, 15, 90)])   # newest first; 09:xx AEDT = 22:xx UTC the day before
        o = ffwx.ten_minute(r); self.assertEqual(len(o), 1); x = o.iloc[0]
        self.assertEqual(x["time_utc"], "2026-11-26T22:10Z"); self.assertEqual(x["time_local"], "2026-11-27 09:10"); self.assertEqual(x["n_readings"], 2)
        self.assertAlmostEqual(x["wind_avg_kmh"], 15 * 1.609344, 1); self.assertAlmostEqual(x["wind_max_kmh"], 25 * 1.609344, 1); self.assertAlmostEqual(x["wind_min_kmh"], 8 * 1.609344, 1)
        self.assertEqual(x["wind_dir_deg"], 90); self.assertEqual(x["pressure_hpa"], 1024.0)

    def test_direction_is_a_vector_mean_across_north(self):
        o = ffwx.ten_minute(raw([("2026-06-01 12:01:00", 10, 10, 10, 350), ("2026-06-01 12:00:00", 10, 10, 10, 10)])).iloc[0]
        self.assertIn(o["wind_dir_deg"], (0.0, 360.0))
        calm = ffwx.ten_minute(raw([("2026-06-01 12:01:00", 0, 0, 0, 90), ("2026-06-01 12:00:00", 0, 0, 0, 90)])).iloc[0]
        self.assertEqual(calm["wind_dir_deg"], 90)

    def test_year_end_and_dst_repeat(self):
        u = ffwx.local_to_utc(["2027-01-01 00:00:10", "2026-12-31 23:59:50"]); self.assertEqual(u[0].year, 2026); self.assertEqual((u[0] - u[1]).seconds, 20)
        # DST ends 2026-04-05 03:00 -> 02:00. Newest first: 03:00:10 (standard) ... 02:30 (second pass, +10) ... 02:30 (first pass, +11) ... 01:59
        seq = ["2026-04-05 03:00:10", "2026-04-05 02:30:00", "2026-04-05 02:00:10", "2026-04-05 02:59:50", "2026-04-05 02:30:00", "2026-04-05 01:59:50"]
        u = ffwx.local_to_utc(seq); self.assertEqual(sorted(u, reverse=True), u)       # strictly newest first after conversion
        self.assertEqual((u[1] - u[4]).seconds, 3600)

    def test_merge_keeps_fuller_bin(self):
        mk = lambda n: pd.DataFrame([dict(time_utc="2026-11-26T22:10Z", time_local="x", n_readings=n, wind_min_kmh=0, wind_avg_kmh=0, wind_max_kmh=0, wind_dir_deg=0, temp_c=0, dew_point_c=0, pressure_hpa=0)])
        self.assertEqual(ffwx.merge(mk(20), mk(50)).iloc[0]["n_readings"], 50)
        self.assertEqual(ffwx.merge(mk(50), mk(20)).iloc[0]["n_readings"], 50); self.assertEqual(len(ffwx.merge(mk(5), mk(5))), 1)


    def test_resolution_full_or_thinned(self):
        full = pd.DataFrame([dict(Date_Time=f"2026-11-27 09:{m:02d}:{sec:02d}", Windspeedmph=1, WindspeedmphMax=2, WindspeedmphMin=0, Winddir=90, realQNH=102400, Tempc=20.0, DewPoint=10.0) for m in (10, 11, 12) for sec in (0, 11, 22, 33, 44, 55)][::-1])
        thin = raw([("2026-11-27 09:17:00", 1, 2, 0, 90), ("2026-11-27 09:13:20", 1, 2, 0, 90), ("2026-11-27 09:10:00", 1, 2, 0, 90)])
        self.assertEqual(ffwx.ten_minute(full).iloc[0]["resolution"], "full"); self.assertEqual(ffwx.ten_minute(thin).iloc[0]["resolution"], "thinned")
        old = ffwx.ten_minute(thin).drop(columns="resolution"); self.assertEqual(ffwx.with_resolution(old).iloc[0]["resolution"], "thinned")

    def test_window_choice(self):
        import datetime as dt
        now = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.timezone.utc); h = lambda d: now - dt.timedelta(hours=d)
        self.assertEqual(ffwx.hours_to_request(None, now), (720, False))
        self.assertEqual(ffwx.hours_to_request(h(3), now), (24, False)); self.assertEqual(ffwx.hours_to_request(h(23.9), now), (24, False))
        self.assertEqual(ffwx.hours_to_request(h(24), now), (26, False)); self.assertEqual(ffwx.hours_to_request(h(100.5), now), (102, False))
        self.assertEqual(ffwx.hours_to_request(h(800), now), (720, True))


if __name__ == "__main__": unittest.main()
