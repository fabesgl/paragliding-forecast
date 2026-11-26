import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from pgforecast import tzlookup


class TzLookupTests(unittest.TestCase):
    def z(self, lat, lon): return tzlookup.zone_for(lat, lon)[0]

    def test_australian_states(self):
        for (lat, lon), want in {(-36.7586, 146.9658): "Australia/Melbourne", (-37.30, 145.28): "Australia/Melbourne", (-30.67, 150.62): "Australia/Sydney",
                                 (-27.47, 153.03): "Australia/Brisbane", (-16.9, 145.8): "Australia/Brisbane", (-34.9, 138.6): "Australia/Adelaide", (-31.95, 115.86): "Australia/Perth",
                                 (-42.9, 147.3): "Australia/Hobart", (-23.7, 133.9): "Australia/Darwin", (-31.96, 141.47): "Australia/Broken_Hill"}.items():
            self.assertEqual(self.z(lat, lon), want, (lat, lon))

    def test_queensland_nsw_border_near_the_coast(self):
        self.assertEqual(self.z(-28.0, 153.4), "Australia/Brisbane")      # Gold Coast: Queensland, no daylight saving
        self.assertEqual(self.z(-28.64, 153.61), "Australia/Sydney")     # Byron Bay: NSW, which has it

    def test_overseas_uses_nearest_and_says_so(self):
        self.assertEqual(tzlookup.zone_for(42.21, 1.33)[1], "nearest")
        self.assertIn(self.z(42.21, 1.33), ("Europe/Madrid", "Europe/Andorra", "Europe/Paris"))    # Organya: CET either way
        self.assertEqual(self.z(35.2, -111.6), "America/Phoenix")

    def test_override_box_wins(self):
        self.assertEqual(tzlookup.zone_for(32.07, 76.72), ("Asia/Kolkata", "override"))             # Bir Billing (sites/timezones.yaml)


if __name__ == "__main__": unittest.main()
