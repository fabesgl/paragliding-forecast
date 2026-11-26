"""Run: .venv/bin/python -m unittest tests.test_blend -v"""
import sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast import blend as B

TABLE = {"models": ["a", "b", "c"], "weights": {"temp": {"a": 0.5, "b": 0.3, "c": 0.2}, "gust": {"a": 0.4, "b": 0.0, "c": 0.6}, "blh": {"a": 0.0, "b": 1.0, "c": 0.0}}}

class T(unittest.TestCase):
    def test_equal_ignores_the_table(self):
        self.assertEqual(B.blend_series({"a": [10.0], "b": [20.0], "x": [30.0]}, "temp", TABLE, "equal"), [20.0])

    def test_weighted_uses_weights_and_drops_models_not_in_the_table(self):
        out = B.blend_series({"a": [10.0], "b": [20.0], "c": [30.0], "x": [1000.0]}, "temp", TABLE, "weighted")
        self.assertAlmostEqual(out[0], 0.5 * 10 + 0.3 * 20 + 0.2 * 30)

    def test_zero_weight_model_is_never_used(self):
        self.assertAlmostEqual(B.blend_series({"a": [10.0], "b": [999.0], "c": [20.0]}, "gust", TABLE, "weighted")[0], 0.4 * 10 + 0.6 * 20)

    def test_missing_value_reweights_the_rest(self):
        out = B.blend_series({"a": [10.0], "b": [None], "c": [30.0]}, "temp", TABLE, "weighted")
        self.assertAlmostEqual(out[0], (0.5 * 10 + 0.2 * 30) / 0.7)

    def test_only_zero_weight_models_have_data_gives_none(self):
        self.assertEqual(B.blend_series({"a": [None], "b": [None], "c": [5.0]}, "blh", TABLE, "weighted"), [None])
        self.assertEqual(B.blend_series({"a": [1.0], "b": [7.0], "c": [5.0]}, "blh", TABLE, "weighted"), [7.0])

    def test_variable_without_entry_is_equal_over_table_models(self):
        self.assertEqual(B.blend_series({"a": [10.0], "b": [20.0], "x": [1000.0]}, "other", TABLE, "weighted"), [15.0])

    def test_directions_blend_as_angles(self):
        out = B.blend_series({"a": [350.0], "b": [10.0]}, "wind_direction_10m", {"models": ["a", "b"], "weights": {}}, "weighted")[0]
        self.assertTrue(out < 1 or out > 359)

    def test_no_table_falls_back_to_equal(self):
        self.assertEqual(B.blend_series({"a": [10.0], "b": [20.0]}, "temp", None, "weighted"), [15.0])
        self.assertIsNone(B.load("/nonexistent/file.json"))

    def test_index_selection(self):
        self.assertEqual(B.blend_series({"a": [1.0, 2.0, 3.0], "b": [3.0, 4.0, 5.0]}, "temp", None, "equal", idx=[1, 2]), [3.0, 4.0])

if __name__ == "__main__": unittest.main()
