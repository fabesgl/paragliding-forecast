import math, shutil, sys, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from pgforecast import igc

M_LAT = 1 / 111195.0
MYSTIC = [{"id": "mystic", "lat": -36.75855, "lon": 146.96575, "radius_m": 600}]
HASH = lambda folder: "H:" + folder


def track(start=(-36.75855, 146.96575), vehicle=False, step=1):
    """Synthetic flight as [(t_utc, lat, lon, alt)]: 60 s still, 120 s glide north, 3 right-hand circles climbing 1.5 m/s, 300 s glide down.
    UTC 01:00 on 2024-01-15 = 12:00 local. `step` = seconds between fixes (1 = IGC-like, 10 = a coarse phone track)."""
    out = []; lat, lon = start; alt = 800.0; t = 3600; hd = 0.0
    def add(): out.append((t, lat, lon, int(alt)))
    for _ in range(60 // step): add(); t += step
    speed = 20.0 if vehicle else 10.0
    for _ in range((120 if not vehicle else 300) // step):
        lat += speed * step * M_LAT * math.cos(math.radians(hd)); lon += speed * step * M_LAT * math.sin(math.radians(hd)) / math.cos(math.radians(lat))
        alt += 0 if vehicle else -1.0 * step; t += step; add()
    if not vehicle:
        for _ in range(3 * 40 // step):                       # 40 s per circle, 9 deg/s
            hd += 9.0 * step
            lat += 8 * step * M_LAT * math.cos(math.radians(hd)); lon += 8 * step * M_LAT * math.sin(math.radians(hd)) / math.cos(math.radians(lat)); alt += 1.5 * step; t += step; add()
        for _ in range(300 // step):
            lat += 10 * step * M_LAT; alt -= 1.0 * step; t += step; add()
    return out


def igc_text(tr, name="Test Pilot"):
    lines = ["AXCSAAA", "HFDTE150124", f"HFPLTPILOTINCHARGE:{name}"]
    for t, lat, lon, alt in tr:
        la, lo = abs(lat), abs(lon)
        lines.append(f"B{t // 3600:02d}{t % 3600 // 60:02d}{t % 60:02d}{int(la):02d}{int((la % 1) * 60000):05d}{'S' if lat < 0 else 'N'}"
                     f"{int(lo):03d}{int((lo % 1) * 60000):05d}{'W' if lon < 0 else 'E'}A{alt:05d}{alt:05d}")
    return "\n".join(lines) + "\n"


def gpx_text(tr):
    pts = "".join(f'<trkpt lat="{lat:.7f}" lon="{lon:.7f}"><ele>{alt}.0</ele><time>{datetime.fromtimestamp(1705276800 + t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}</time></trkpt>\n'
                  for t, lat, lon, alt in tr)       # 1705276800 = 2024-01-15 00:00 UTC
    return f'<?xml version="1.0"?><gpx version="1.1" creator="OsmAnd"><trk><trkseg>\n{pts}</trkseg></trk></gpx>'


class IgcTests(unittest.TestCase):
    def test_flight_and_thermal(self):
        s, th = igc.analyse(igc.parse_igc(igc_text(track())))
        self.assertEqual(s["date"], "2024-01-15"); self.assertTrue(s["launch_time"].startswith("12:0"))   # UTC -> Melbourne (AEDT, +11)
        self.assertTrue(8 <= s["duration_min"] <= 9.5 and s["max_alt_m"] >= 800)
        self.assertEqual(len(th), 1); self.assertEqual((th[0]["direction"], th[0]["kind"]), ("right", "climb"))
        self.assertTrue(1.2 <= th[0]["climb_ms"] <= 1.8 and 2.5 <= th[0]["turns"] <= 3.5); self.assertEqual(s["n_thermals"], 1)
        self.assertEqual((s["median_dt_s"], s["low_rate"]), (1.0, 0))

    def test_gpx_matches_igc(self):
        a, ta = igc.analyse(igc.parse_igc(igc_text(track()))); b, tb = igc.analyse(igc.parse_gpx(gpx_text(track())))
        self.assertEqual((a["date"], a["launch_time"]), (b["date"], b["launch_time"])); self.assertAlmostEqual(a["duration_min"], b["duration_min"], delta=0.2)
        self.assertEqual(len(ta), len(tb)); self.assertAlmostEqual(ta[0]["climb_ms"], tb[0]["climb_ms"], delta=0.2)

    def test_low_sample_rate_is_flagged(self):
        s, _ = igc.analyse(igc.parse_gpx(gpx_text(track(step=10))))
        self.assertEqual(s["median_dt_s"], 10.0); self.assertEqual(s["low_rate"], 1)
        s5, _ = igc.analyse(igc.parse_gpx(gpx_text(track(step=3)))); self.assertEqual(s5["low_rate"], 0)

    def test_gpx_without_track_points_is_rejected(self):
        for bad in ["", "<gpx><rte><rtept lat='1' lon='2'/></rte></gpx>", '<gpx><trk><trkseg><trkpt lat="1" lon="2"><ele>3</ele></trkpt></trkseg></trk></gpx>']:
            with self.assertRaises(ValueError): igc.parse_gpx(bad)

    def test_drive_is_not_a_flight(self):
        with self.assertRaises(ValueError): igc.analyse(igc.parse_igc(igc_text(track(vehicle=True))))

    def test_site_matching(self):
        self.assertEqual(igc.match_site(-36.7590, 146.9660, MYSTIC), "mystic")
        self.assertEqual(igc.match_site(-36.19, 147.98, MYSTIC), "unknown@-36.19,147.98")

    def test_bad_igc_raises(self):
        for bad in ["", "HFDTE150124\nB1\n"]:
            with self.assertRaises(ValueError): igc.parse_igc(bad)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name) / "igc_files"
        for d in ("pilot1", "pilot2"): (self.root / d).mkdir(parents=True)
        self.db = igc.connect(Path(self.tmp.name) / "t.sqlite")
    def tearDown(self): self.tmp.cleanup()
    def run_import(self): return igc.import_root(self.root, self.db, MYSTIC, HASH)

    def test_folder_is_the_pilot_and_header_name_is_ignored(self):
        (self.root / "pilot1" / "a.igc").write_text(igc_text(track(), name="Jane Citizen")); (self.root / "pilot2" / "a.igc").write_text(igc_text(track(), name="Jane Citizen"))
        r = self.run_import(); self.assertEqual(r["imported"], 2)    # same file name in two folders = two flights (different pilots)... 
        rows = self.db.execute("SELECT src_file, pilot_folder, pilot_hash FROM igc_flight ORDER BY 1").fetchall()
        self.assertEqual(rows, [("pilot1/a.igc", "pilot1", "H:pilot1"), ("pilot2/a.igc", "pilot2", "H:pilot2")])
        self.assertNotIn("Jane", "".join(str(x) for x in self.db.execute("SELECT * FROM igc_flight")))

    def test_rerun_moved_and_renamed_files_do_not_duplicate(self):
        (self.root / "pilot1" / "a.igc").write_text(igc_text(track())); (self.root / "pilot2" / "b.gpx").write_text(gpx_text(track(step=3)))
        self.assertEqual(self.run_import()["imported"], 2)
        r = self.run_import(); self.assertEqual((r["imported"], r["already"]), (0, 2))
        shutil.copy(self.root / "pilot1" / "a.igc", self.root / "pilot1" / "copy_of_a.igc")          # renamed copy: same content, same pilot
        r = self.run_import(); self.assertEqual((r["imported"], r["same_content"]), (0, 1))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM igc_flight").fetchone()[0], 2)

    def test_same_flight_as_igc_and_gpx_counts_once_in_unique_view(self):
        (self.root / "pilot1" / "a.igc").write_text(igc_text(track())); (self.root / "pilot1" / "a.gpx").write_text(gpx_text(track()))
        self.run_import(); self.assertEqual(self.db.execute("SELECT COUNT(*) FROM igc_flight").fetchone()[0], 2)
        self.assertEqual(self.db.execute("SELECT src_file FROM igc_flight_unique").fetchall(), [("pilot1/a.igc",)])   # the IGC wins

    def test_timezone_comes_from_the_launch_coordinates(self):
        # same UTC clock time, three places: Victoria (+11 in January), Queensland (+10, no daylight saving), Spain (+1)
        for start, off, hh in (((-36.75855, 146.96575), "+1100", "12:0"), ((-27.47, 153.03), "+1000", "11:0"), ((42.21, 1.33), "+0100", "02:0")):
            s, _ = igc.analyse(igc.parse_igc(igc_text(track(start=start)))); self.assertEqual(s["utc_offset"], off); self.assertTrue(s["launch_time"].startswith(hh), (start, s["launch_time"]))
        s, _ = igc.analyse(igc.parse_igc(igc_text(track(start=(-27.47, 153.03)))), tz="Australia/Melbourne"); self.assertEqual((s["utc_offset"], s["tz_method"]), ("+1100", "forced"))

    def test_retime_changes_only_flights_whose_zone_changes(self):
        (self.root / "pilot1" / "bris.igc").write_text(igc_text(track(start=(-27.47, 153.03)))); (self.root / "pilot1" / "vic.igc").write_text(igc_text(track()))
        self.assertEqual(igc.import_root(self.root, self.db, MYSTIC, HASH, tz="Australia/Melbourne")["imported"], 2)      # old behaviour: one zone for everything
        self.assertEqual(self.db.execute("SELECT launch_time FROM igc_flight WHERE src_file='pilot1/bris.igc'").fetchone()[0][:2], "12")
        r = self.run_import()["retime"]                                                                                    # now by coordinates
        self.assertEqual(len(r["changed"]), 1)
        self.assertEqual(r["changed"][0][0], "pilot1/bris.igc")
        self.assertEqual(self.db.execute("SELECT launch_time, utc_offset, tz FROM igc_flight WHERE src_file='pilot1/bris.igc'").fetchone(), ("11:00", "+1000", "Australia/Brisbane"))
        self.assertEqual(self.db.execute("SELECT tz FROM igc_flight WHERE src_file='pilot1/vic.igc'").fetchone()[0], "Australia/Melbourne")
        self.assertEqual(len(self.run_import()["retime"]["changed"]), 0)                                                    # nothing more to do

    def test_adding_a_launch_re_tags_flights_already_imported(self):
        (self.root / "pilot1" / "a.igc").write_text(igc_text(track()))
        igc.import_root(self.root, self.db, [], HASH)
        self.assertTrue(self.db.execute("SELECT launch_site FROM igc_flight").fetchone()[0].startswith("unknown@"))
        r = igc.import_root(self.root, self.db, MYSTIC, HASH)
        self.assertEqual(self.db.execute("SELECT launch_site FROM igc_flight").fetchone()[0], "mystic"); self.assertEqual(sum(r["relabeled"].values()), 1)
        self.assertEqual(igc.import_root(self.root, self.db, MYSTIC, HASH)["relabeled"], {})

    def test_no_fix_point_at_0_0_is_ignored(self):
        tr = track(); txt = igc_text([(3600, 0.0, 0.0, 0)] + tr)
        s, _ = igc.analyse(igc.parse_igc(txt)); self.assertTrue(abs(s["launch_lat"] + 36.75855) < 0.01)

    def test_bad_misplaced_and_other_files(self):
        (self.root / "pilot1" / "ok.gpx").write_text(gpx_text(track(step=12))); (self.root / "pilot1" / "bad.igc").write_text("garbage"); (self.root / "pilot1" / "x.kml").write_text("<kml/>")
        (self.root / "loose.igc").write_text(igc_text(track()))
        r = self.run_import()
        self.assertEqual(r["imported"], 1); self.assertEqual(r["low_rate"], 1); self.assertEqual(r["ignored"], ["pilot1/x.kml"])
        self.assertEqual(sorted(f for f, _ in r["failed"]), ["loose.igc", "pilot1/bad.igc"])
        self.assertEqual(self.db.execute("SELECT low_rate, source_fmt FROM igc_flight").fetchone(), (1, "gpx"))

    def test_old_rows_are_migrated_not_duplicated(self):
        (self.root / "pilot1" / "old.igc").write_text(igc_text(track()))
        self.db.execute("INSERT INTO igc_flight (src_file, pilot_hash, date, launch_time, duration_min) VALUES ('old.igc', 'headerhash', '2024-01-15', '12:01', 9.0)")
        self.db.execute("INSERT INTO igc_thermal (src_file, n, kind) VALUES ('old.igc', 1, 'climb')"); self.db.commit()
        r = self.run_import(); self.assertEqual((r["migrated"], r["imported"]), (1, 0))
        row = self.db.execute("SELECT src_file, pilot_folder, pilot_hash, file_sha1 IS NOT NULL, median_dt_s FROM igc_flight").fetchall()
        self.assertEqual(row, [("pilot1/old.igc", "pilot1", "H:pilot1", 1, 1.0)])
        self.assertEqual(self.db.execute("SELECT src_file FROM igc_thermal").fetchall(), [("pilot1/old.igc",)])


if __name__ == "__main__": unittest.main()
