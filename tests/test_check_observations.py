"""Run: .venv/bin/python -m unittest tests.test_check_observations -v"""
import datetime as dt, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_observations as K

def write(rows):
    f = Path(tempfile.mkdtemp()) / "x.csv"; f.write_text("obs_time_utc\n" + "\n".join(t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in rows) + "\n"); return f

class T(unittest.TestCase):
    def test_no_gaps(self):
        t0 = dt.datetime(2026, 12, 1); r = K.check(write([t0 + dt.timedelta(minutes=30 * i) for i in range(20)]), now=t0 + dt.timedelta(hours=10))
        self.assertEqual(r["gaps"], []); self.assertFalse(r["sparse"])

    def test_gap_found(self):
        t0 = dt.datetime(2026, 12, 1); rows = [t0 + dt.timedelta(minutes=30 * i) for i in range(10)] + [t0 + dt.timedelta(hours=10 + 0.5 * i) for i in range(10)]
        r = K.check(write(rows), now=t0 + dt.timedelta(hours=16)); self.assertEqual(len(r["gaps"]), 1)

    def test_daily_feed_is_sparse_not_gappy(self):
        t0 = dt.datetime(2026, 12, 1, 22); r = K.check(write([t0 + dt.timedelta(days=i) for i in range(5)]), now=t0 + dt.timedelta(days=5))
        self.assertTrue(r["sparse"]); self.assertEqual(r["gaps"], [])

if __name__ == "__main__": unittest.main()
