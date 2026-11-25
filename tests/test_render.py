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
    def test_ai_text_is_escaped(self):
        h = R.ai_box({"xc_call": "good", "acro_call": "poor", "reasoning": "<script>alert(1)</script>"}, "ok"); self.assertNotIn("<script>", h)
        self.assertEqual(R.ai_box(None, "off"), "")
if __name__ == "__main__": unittest.main()
