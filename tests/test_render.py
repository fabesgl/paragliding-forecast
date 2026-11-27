"""Run: .venv/bin/python -m unittest tests.test_render"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from pgforecast import summary as S, render as R

class T(unittest.TestCase):
    def test_labels_and_window(self):
        self.assertEqual([S.label(x) for x in (None, 0, 34, 35, 64, 65, 100)], ["n/a", "poor", "poor", "marginal", "marginal", "good", "good"])
        mk = lambda xs: [{"h": 9 + i, "xc": x} for i, x in enumerate(xs)]
        self.assertEqual(S.best_window(mk([10, 20, 70, 90, 95, 85, 60, 45, 20])), "12–3pm"); self.assertIsNone(S.best_window(mk([10, 20, 30, 34, 20, 10, 5, 5, 5])))
    def test_dial_and_arrow(self):
        d = R.dial(82, "XC"); self.assertIn("Good", d); self.assertIn("206 251", d)
        self.assertIn("rotate(180deg)", R.arrow(0, 10)); self.assertIn("#e24b4a", R.arrow(300, 40))        # wind from N points down; strong wind is red
    def test_creo_panel(self):
        h = R.creo_box({"rating": "Good", "text": "<script>alert(1)</script> helps."}); self.assertNotIn("<script>", h); self.assertIn("Creo (beta)", h); self.assertIn("Good", h)
        self.assertEqual(R.creo_box(None), ""); self.assertEqual(R.creo_box({"rating": None, "text": "x"}), "")
        self.assertNotIn(" km", R.creo_box({"rating": "Great", "text": "A westerly wind aloft (14 km/h at 1,500 m) helps."}).replace("km/h", ""))      # speeds in km/h are fine; no distances
if __name__ == "__main__": unittest.main()
