import math, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from pgforecast import igc

def _b(t, lat, lon, alt):
    la, lo = abs(lat), abs(lon)
    return (f"B{t // 3600:02d}{t % 3600 // 60:02d}{t % 60:02d}{int(la):02d}{int((la % 1) * 60000):05d}{'S' if lat < 0 else 'N'}"
            f"{int(lo):03d}{int((lo % 1) * 60000):05d}{'W' if lon < 0 else 'E'}A{alt:05d}{alt:05d}")

M_LAT = 1 / 111195.0
def make(name="Test Pilot", start=(-36.75855, 146.96575), vehicle=False):
    """A synthetic flight: 60 s still, 120 s glide north, 3 right-hand circles climbing 1.5 m/s, 300 s glide down. UTC 01:00 on 2024-01-15 = 12:00 local."""
    lines = ["AXCSAAA", "HFDTE150124", f"HFPLTPILOTINCHARGE:{name}"]
    lat, lon = start; alt = 800; t = 3600; hd = 0.0
    def add():
        lines.append(_b(t, lat, lon, int(alt)))
    for _ in range(60): add(); t += 1
    speed = 20.0 if vehicle else 10.0
    for _ in range(120 if not vehicle else 300):
        lat += speed * M_LAT * math.cos(math.radians(hd)); lon += speed * M_LAT * math.sin(math.radians(hd)) / math.cos(math.radians(lat))
        alt += 0 if vehicle else -1.0; t += 1; add()
    if not vehicle:
        for k in range(3 * 40):                       # 40 s per circle, 9 deg/s
            hd += 9.0
            lat += 8 * M_LAT * math.cos(math.radians(hd)); lon += 8 * M_LAT * math.sin(math.radians(hd)) / math.cos(math.radians(lat)); alt += 1.5; t += 1; add()
        for _ in range(300):
            lat += 10 * M_LAT; alt -= 1.0; t += 1; add()
    return "\n".join(lines) + "\n"

MYSTIC = [{"id": "mystic", "lat": -36.75855, "lon": 146.96575, "radius_m": 600}]

class IgcTests(unittest.TestCase):
    def test_flight_and_thermal(self):
        s, th = igc.analyse(igc.parse_igc(make()))
        self.assertEqual(s["date"], "2024-01-15"); self.assertTrue(s["launch_time"].startswith("12:0"))   # UTC -> Melbourne (AEDT, +11)
        self.assertTrue(8 <= s["duration_min"] <= 9.5 and s["max_alt_m"] >= 800)
        self.assertEqual(len(th), 1); self.assertEqual((th[0]["direction"], th[0]["kind"]), ("right", "climb"))
        self.assertTrue(1.2 <= th[0]["climb_ms"] <= 1.8 and 2.5 <= th[0]["turns"] <= 3.5); self.assertEqual(s["n_thermals"], 1)

    def test_drive_is_not_a_flight(self):
        with self.assertRaises(ValueError): igc.analyse(igc.parse_igc(make(vehicle=True)))

    def test_site_matching(self):
        self.assertEqual(igc.match_site(-36.7590, 146.9660, MYSTIC), "mystic")
        self.assertEqual(igc.match_site(-36.19, 147.98, MYSTIC), "unknown@-36.19,147.98")

    def test_pilot_names_unified(self):
        out = igc.canonical_pilots({"a": "Jane Citizen", "b": "Jane", "c": "", "d": "J", "e": "Someone Else"})
        self.assertTrue(out["a"] == out["b"] == out["c"] == out["d"]); self.assertEqual(out["e"], "someone else")

    def test_bad_files_raise(self):
        for bad in ["", "HFDTE150124\nB1\n"]:
            with self.assertRaises(ValueError): igc.parse_igc(bad)

    def test_import_dir_skips_bad_and_never_stores_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "igc"; d.mkdir(); (d / "ok.igc").write_text(make(name="Jane Citizen")); (d / "bad.igc").write_text("garbage")
            db = igc.connect(Path(tmp) / "t.sqlite")
            n, old, failed = igc.import_dir(d, db, MYSTIC, lambda nm: "H" + str(len(nm)))
            self.assertEqual((n, len(failed), failed[0][0]), (1, 1, "bad.igc"))
            self.assertEqual(db.execute("SELECT launch_site, pilot_hash FROM igc_flight").fetchone(), ("mystic", "H12"))
            self.assertNotIn("Jane", "".join(str(r) for r in db.execute("SELECT * FROM igc_flight")))
            self.assertEqual(igc.import_dir(d, db, [], lambda nm: "x")[0], 0)        # re-run adds nothing

if __name__ == "__main__": unittest.main()
