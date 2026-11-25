"""Run: .venv/bin/python -m unittest tests.test_rules_config -v
The GOLDEN numbers below were produced by the scoring code BEFORE the thresholds were moved into sites/bright/rules.yaml,
so these tests prove the move changed no score."""
import copy, re, sys, tempfile, unittest, yaml
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast.rules import score_day, below, above
from pgforecast.rules_config import load_rules, validate, limits
from pgforecast import summary as S

N = 6
def win(**o):
    base = dict(wind_speed_10m=[8]*N, wind_direction_10m=[0]*N, wind_gusts_10m=[15]*N, wind_speed_850hPa=[15]*N, wind_direction_850hPa=[0]*N,
                wind_speed_700hPa=[25]*N, temperature_2m=[24]*N, dew_point_2m=[8]*N, boundary_layer_height=[2200]*N, shortwave_radiation=[850]*N,
                cape=[100]*N, cloud_cover_low=[10]*N, precipitation=[0]*N)
    base.update(o); return base
SCEN = {
 "good": win(),
 "gusty": win(wind_gusts_10m=[30]*N, wind_speed_10m=[18]*N),
 "south_synoptic": win(wind_direction_10m=[200]*N, wind_speed_10m=[12]*N, wind_speed_850hPa=[30]*N, wind_direction_850hPa=[200]*N),
 "wrong_dir_light": win(wind_direction_10m=[180]*N, wind_speed_10m=[5]*N),
 "rain_low_base": win(precipitation=[0.5]*N, dew_point_2m=[20]*N),
 "weak_thermals": win(shortwave_radiation=[300]*N, boundary_layer_height=[700]*N),
 "acro_border": win(wind_speed_10m=[15]*N, wind_gusts_10m=[20]*N, wind_speed_850hPa=[20]*N, wind_direction_850hPa=[45]*N),
 "no_blh_no_cape": win(boundary_layer_height=[None]*N, cape=[None]*N),
 "stormy": win(cape=[1500]*N, wind_speed_850hPa=[40]*N),
 "mixed_dirs": win(wind_direction_10m=[300, 330, 350, 20, 50, 90], wind_speed_10m=[6, 9, 12, 14, 10, 8]),
}

GOLDEN = {
 "acro_border": {
  "acro": 90,
  "acro_f": {
   "cape": 1.0,
   "direction": 1.0,
   "gust": 0.896,
   "rain": 0.966,
   "shear": 0.926,
   "w850": 0.896,
   "wind": 0.896
  },
  "xc": 97,
  "xc_f": {
   "cape": 1.0,
   "ceiling": 1.0,
   "direction": 1.0,
   "gust": 1.0,
   "rain": 0.966,
   "thermals": 1.0,
   "w850": 1.0,
   "wind": 1.0
  }
 },
 "good": {
  "acro": 97,
  "acro_f": {
   "cape": 1.0,
   "direction": 1.0,
   "gust": 1.0,
   "rain": 0.966,
   "shear": 1.0,
   "w850": 1.0,
   "wind": 1.0
  },
  "xc": 97,
  "xc_f": {
   "cape": 1.0,
   "ceiling": 1.0,
   "direction": 1.0,
   "gust": 1.0,
   "rain": 0.966,
   "thermals": 1.0,
   "w850": 1.0,
   "wind": 1.0
  }
 },
 "gusty": {
  "acro": 5,
  "acro_f": {
   "cape": 1.0,
   "direction": 1.0,
   "gust": 0.049,
   "rain": 0.966,
   "shear": 1.0,
   "w850": 1.0,
   "wind": 0.59
  },
  "xc": 55,
  "xc_f": {
   "cape": 1.0,
   "ceiling": 1.0,
   "direction": 1.0,
   "gust": 0.55,
   "rain": 0.966,
   "thermals": 1.0,
   "w850": 1.0,
   "wind": 0.987
  }
 },
 "mixed_dirs": {
  "acro": 50,
  "acro_f": {
   "cape": 1.0,
   "direction": 0.5,
   "gust": 1.0,
   "rain": 0.966,
   "shear": 1.0,
   "w850": 1.0,
   "wind": 1.0
  },
  "xc": 50,
  "xc_f": {
   "cape": 1.0,
   "ceiling": 1.0,
   "direction": 0.5,
   "gust": 1.0,
   "rain": 0.966,
   "thermals": 1.0,
   "w850": 1.0,
   "wind": 1.0
  }
 },
 "no_blh_no_cape": {
  "acro": 97,
  "acro_f": {
   "cape": None,
   "direction": 1.0,
   "gust": 1.0,
   "rain": 0.966,
   "shear": 1.0,
   "w850": 1.0,
   "wind": 1.0
  },
  "xc": 97,
  "xc_f": {
   "cape": None,
   "ceiling": 1.0,
   "direction": 1.0,
   "gust": 1.0,
   "rain": 0.966,
   "thermals": 0.992,
   "w850": 1.0,
   "wind": 1.0
  }
 },
 "rain_low_base": {
  "acro": 0,
  "acro_f": {
   "cape": 1.0,
   "direction": 1.0,
   "gust": 1.0,
   "rain": 0.0,
   "shear": 1.0,
   "w850": 1.0,
   "wind": 1.0
  },
  "xc": 0,
  "xc_f": {
   "cape": 1.0,
   "ceiling": 0.0,
   "direction": 1.0,
   "gust": 1.0,
   "rain": 0.0,
   "thermals": 1.0,
   "w850": 1.0,
   "wind": 1.0
  }
 },
 "south_synoptic": {
  "acro": 0,
  "acro_f": {
   "cape": 1.0,
   "direction": 0.0,
   "gust": 1.0,
   "rain": 0.966,
   "shear": 0.758,
   "w850": 0.289,
   "wind": 1.0
  },
  "xc": 0,
  "xc_f": {
   "cape": 1.0,
   "ceiling": 1.0,
   "direction": 0.0,
   "gust": 1.0,
   "rain": 0.966,
   "thermals": 1.0,
   "w850": 0.648,
   "wind": 1.0
  }
 },
 "stormy": {
  "acro": 0,
  "acro_f": {
   "cape": 0.145,
   "direction": 1.0,
   "gust": 1.0,
   "rain": 0.966,
   "shear": 0.04,
   "w850": 0.0,
   "wind": 1.0
  },
  "xc": 10,
  "xc_f": {
   "cape": 0.145,
   "ceiling": 1.0,
   "direction": 1.0,
   "gust": 1.0,
   "rain": 0.966,
   "thermals": 1.0,
   "w850": 0.104,
   "wind": 1.0
  }
 },
 "weak_thermals": {
  "acro": 97,
  "acro_f": {
   "cape": 1.0,
   "direction": 1.0,
   "gust": 1.0,
   "rain": 0.966,
   "shear": 1.0,
   "w850": 1.0,
   "wind": 1.0
  },
  "xc": 13,
  "xc_f": {
   "cape": 1.0,
   "ceiling": 0.129,
   "direction": 1.0,
   "gust": 1.0,
   "rain": 0.966,
   "thermals": 0.18,
   "w850": 1.0,
   "wind": 1.0
  }
 },
 "wrong_dir_light": {
  "acro": 65,
  "acro_f": {
   "cape": 1.0,
   "direction": 1.0,
   "gust": 1.0,
   "rain": 0.966,
   "shear": 0.648,
   "w850": 1.0,
   "wind": 1.0
  },
  "xc": 97,
  "xc_f": {
   "cape": 1.0,
   "ceiling": 1.0,
   "direction": 1.0,
   "gust": 1.0,
   "rain": 0.966,
   "thermals": 1.0,
   "w850": 1.0,
   "wind": 1.0
  }
 }
}

class T(unittest.TestCase):
    def setUp(self): self.rules = load_rules("bright")

    def test_scores_unchanged_from_before_the_move(self):
        for name, h in SCEN.items():
            r = score_day(h, self.rules, 797); g = GOLDEN[name]
            self.assertEqual((r["xc"], r["acro"]), (g["xc"], g["acro"]), name)
            self.assertEqual({x["key"]: x["factor"] for x in r["xc_rules"]}, g["xc_f"], name)
            self.assertEqual({x["key"]: x["factor"] for x in r["acro_rules"]}, g["acro_f"], name)

    def test_values_are_the_old_ones(self):
        self.assertEqual(limits(self.rules), {"xc": {"wind": 20, "gust": 25, "w850": 25}, "acro": {"wind": 15, "gust": 20, "w850": 20}, "rain_mm": 0.2})
        x = self.rules["xc"]; self.assertEqual((x["ceiling"]["min_asl_m"], x["ceiling"]["zero_at_asl_m"], x["thermals"]["min_ms"], x["thermals"]["zero_at_ms"]), (2000, 1300, 1.5, 0.5))
        self.assertEqual((self.rules["shared"]["cape"]["limit"], self.rules["shared"]["cape"]["zero_at"]), (800, 1800))
        self.assertEqual(self.rules["direction"], {"launch_arc_deg": [315, 45], "calm_below_kmh": 6, "unflyable_arc_deg": [135, 247], "unflyable_min_850_kmh": 25})
        self.assertEqual(self.rules["ratings"], {"good_min_pct": 65, "marginal_min_pct": 35, "pulled_down_below": 0.9})

    def test_editing_the_file_changes_the_score(self):
        loose = copy.deepcopy(self.rules); loose["xc"]["gusts"].update(limit_kmh=40, zero_at_kmh=60)
        self.assertGreater(score_day(SCEN["gusty"], loose, 797)["xc"], score_day(SCEN["gusty"], self.rules, 797)["xc"])
        self.assertEqual(limits(loose)["xc"]["gust"], 40)                                  # the AI hard limits follow the same file
        flip = copy.deepcopy(self.rules); flip["direction"]["launch_arc_deg"] = [135, 247]     # make south the launchable side
        self.assertLess(score_day(SCEN["wrong_dir_light"], self.rules, 797)["xc"] , 101); self.assertEqual(score_day(SCEN["good"], flip, 797)["xc"], 0)
        S.configure({"ratings": {"good_min_pct": 90, "marginal_min_pct": 50, "pulled_down_below": 0.5}})
        try: self.assertEqual((S.label(80), S.label(60), S.label(40)), ("marginal", "marginal", "poor"))
        finally: S.configure(self.rules)
        self.assertEqual((S.label(65), S.label(64), S.label(35), S.label(34)), ("good", "marginal", "marginal", "poor"))

    def test_mistakes_are_caught_with_a_clear_message(self):
        def bad(mut, expect):
            r = copy.deepcopy(self.rules); mut(r)
            with self.assertRaisesRegex(ValueError, expect): validate(r, "rules.yaml")
        bad(lambda r: r["xc"]["gusts"].update(zero_at_kmh=20), "xc.gusts.zero_at_kmh .* must be higher than limit_kmh")
        bad(lambda r: r["xc"]["ceiling"].update(zero_at_asl_m=2500), "xc.ceiling.zero_at_asl_m .* must be lower")
        bad(lambda r: r["xc"]["thermals"].update(min_ms="fast"), "must be a number")
        bad(lambda r: r["acro"].pop("shear"), "missing setting 'acro.shear")
        bad(lambda r: r.pop("ratings"), "missing section 'ratings'")
        bad(lambda r: r["ratings"].update(good_min_pct=30), "marginal_min_pct < good_min_pct")
        bad(lambda r: r["direction"].update(launch_arc_deg=[315]), "launch_arc_deg must be")
        bad(lambda r: r["smoothing"].update(full_score_margin=1), "full_score_margin")
        with self.assertRaises(FileNotFoundError): load_rules("nowhere")

    def test_every_setting_line_has_a_comment(self):
        lines = (ROOT / "sites" / "bright" / "rules.yaml").read_text().splitlines()
        missing = [f"line {i + 1}: {l.strip()}" for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith("#") and "#" not in l]
        self.assertEqual(missing, [])

    def test_smooth_shape_matches_before(self):
        self.assertAlmostEqual(below(25, 25, 40), 0.896, places=3); self.assertEqual(below(10, 25, 40), 1.0); self.assertEqual(below(40, 25, 40), 0.0)
        self.assertAlmostEqual(above(2000, 2000, 1300), 0.896, places=3)

if __name__ == "__main__": unittest.main()
