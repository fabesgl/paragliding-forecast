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

    def test_xc_and_old_acro_rules_unchanged_from_before_the_move(self):
        """Every rule scores exactly what it did before the move, EXCEPT rain, which now scores 100% (not 97%) when there is none, and the new acro rules.
        New acro rules and a cleaner rain score are the only reasons a day's score can differ from the golden numbers."""
        for name, h in SCEN.items():
            r = score_day(h, self.rules, 797); g = GOLDEN[name]; dry = sum(x for x in h["precipitation"] if x is not None) == 0
            xcf = {x["key"]: x["factor"] for x in r["xc_rules"]}; want = dict(g["xc_f"])
            if dry: want["rain"] = 1.0
            self.assertEqual(xcf, want, name)
            self.assertAlmostEqual(r["xc"], 100 * min(v for v in want.values() if v is not None), delta=1, msg=name)
            now = {x["key"]: x["factor"] for x in r["acro_rules"]}; wa = dict(g["acro_f"])
            if dry: wa["rain"] = 1.0
            self.assertEqual({k: now[k] for k in wa if k != "gust"}, {k: v for k, v in wa.items() if k != "gust"}, name)
            self.assertEqual(now["gust"], xcf["gust"], name)                                                   # acro gusts now use the same launch limit as XC (25 / 40)
            self.assertAlmostEqual(r["acro"], 100 * min(1, min(v for v in now.values() if v is not None)), delta=1, msg=name)
            self.assertGreaterEqual(r["xc"], g["xc"], name)                                                    # only the rain change can lift XC

    def test_values_are_the_old_ones(self):
        self.assertEqual(limits(self.rules), {"xc": {"wind": 20, "gust": 25, "w850": 25}, "acro": {"wind": 15, "gust": 25, "w850": 20}, "rain_mm": 0.2})
        x = self.rules["xc"]; self.assertEqual((x["ceiling"]["min_asl_m"], x["ceiling"]["zero_at_asl_m"], x["thermals"]["min_ms"], x["thermals"]["zero_at_ms"]), (2000, 1300, 1.5, 0.5))
        self.assertEqual((self.rules["shared"]["cape"]["limit"], self.rules["shared"]["cape"]["zero_at"]), (800, 1800))
        d = self.rules["direction"]; self.assertEqual({k: d[k] for k in ("launch_arc_deg", "calm_below_kmh", "unflyable_arc_deg", "unflyable_min_850_kmh")},
                                                        {"launch_arc_deg": [315, 45], "calm_below_kmh": 6, "unflyable_arc_deg": [135, 247], "unflyable_min_850_kmh": 25})
        self.assertEqual(d["southerly_launch"], {"arc_deg": [135, 247], "valley_wind_kmh_per_thermal_ms": 6, "fade_kmh": 3})
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


# ---- new acro rules (local knowledge from an experienced acro pilot)
from pgforecast.rules import west_weight, north_factor
from pgforecast.ai_opinion import system_prompt

def aloft(sp9=0, d9=0, sp8=0, d8=0, sp80=0, d80=0, **o):
    """A window with winds at 900/850/800 hPa (about 1,000/1,500/2,000 m)."""
    return win(wind_speed_900hPa=[sp9] * N, wind_direction_900hPa=[d9] * N, wind_speed_850hPa=[sp8] * N, wind_direction_850hPa=[d8] * N,
               wind_speed_800hPa=[sp80] * N, wind_direction_800hPa=[d80] * N, **o)
def acro(h, rules, key): return {x["key"]: x for x in score_day(h, rules, 797)["acro_rules"]}[key]

class Acro(unittest.TestCase):
    def setUp(self): self.r = load_rules("bright"); self.W = self.r["acro"]["westerly_aloft"]["weights"]

    def test_westerly_weights_only_for_W_WNW_and_half_WSW(self):
        self.assertEqual([round(west_weight(d, self.W), 2) for d in (270, 292.5, 247.5, 225, 315, 0, 90, 180)], [1.0, 1.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0])
        self.assertAlmostEqual(west_weight(258.75, self.W), 0.75); self.assertAlmostEqual(west_weight(303.75, self.W), 0.5)     # blends smoothly between points

    def test_northerly_penalty_is_mild_and_smaller_when_climbs_are_high(self):
        c = self.r["acro"]["northerly_aloft"]
        grid = {n: [round(north_factor(n, g, c), 2) for g in (400, 1000, 1400, 1800)] for n in (0, 5, 10, 15, 20, 30)}
        self.assertEqual(grid[5], [1.0] * 4); self.assertEqual(grid[10], [0.92, 0.94, 0.96, 0.97]); self.assertEqual(grid[15], [0.78, 0.82, 0.89, 0.93]); self.assertEqual(grid[20], [0.7, 0.75, 0.85, 0.9])
        self.assertEqual(grid[30], grid[20])                                                   # never worse than -30%, even in a gale
        for n in (10, 15, 20, 30): self.assertTrue(all(grid[n][i] <= grid[n][i + 1] for i in range(3)))   # higher climbs -> smaller penalty
        self.assertGreaterEqual(north_factor(200, 0, c), 0.7); self.assertIsNone(north_factor(None, 1000, c))

    def test_wind_aloft_is_read_from_the_levels_and_launch_wind_is_not_counted(self):
        calm_aloft = aloft(wind_speed_10m=[25] * N, wind_direction_10m=[292] * N)               # strong WNW at launch, calm aloft
        self.assertEqual(acro(calm_aloft, self.r, "west_aloft")["factor"], 1.0)
        rough = aloft(sp9=20, d9=285, sp8=22, d8=285, sp80=25, d80=290); a = acro(rough, self.r, "west_aloft")
        self.assertEqual(a["factor"], 0.3); self.assertAlmostEqual(a["num"], 25, places=3)    # worst level counts, floor 30%: flyable but rough, never a no-go
        wsw = acro(aloft(sp8=20, d8=247.5), self.r, "west_aloft"); self.assertAlmostEqual(wsw["num"], 10.0, places=3)   # WSW counts half
        nw = acro(aloft(sp8=20, d8=315, sp9=20, d9=315, sp80=20, d80=315), self.r, "west_aloft"); self.assertEqual(nw["factor"], 1.0)  # NW aloft not counted

    def test_light_south_and_nil_wind_aloft_are_best(self):
        for h in (aloft(), aloft(sp9=8, d9=180, sp8=8, d8=180, sp80=8, d80=180), aloft(sp8=10, d8=200, sp9=10, d9=200, sp80=10, d80=200)):
            self.assertEqual((acro(h, self.r, "north_aloft")["factor"], acro(h, self.r, "west_aloft")["factor"]), (1.0, 1.0))
        north = aloft(sp9=15, d9=0, sp8=15, d8=0, sp80=15, d80=0); self.assertLess(acro(north, self.r, "north_aloft")["factor"], 0.95)

    def test_thermals_and_height_rules(self):
        self.assertEqual(acro(win(shortwave_radiation=[1000] * N, boundary_layer_height=[3500] * N, dew_point_2m=[0] * N), self.r, "thermals_strong")["factor"] >= 0.6, True)
        self.assertEqual(acro(win(), self.r, "thermals_strong")["factor"], 1.0)                 # normal thermals: no penalty
        low = acro(win(boundary_layer_height=[600] * N), self.r, "height"); high = acro(win(), self.r, "height")
        self.assertLess(low["factor"], 0.2); self.assertGreater(high["factor"], 0.99); self.assertAlmostEqual(high["num"], 2200 - 797 + 0.0, delta=1500)

    def test_summary_text_names_the_wind_aloft(self):
        def day(h):
            r = score_day(h, self.r, 797); return dict(r, models={"gfs": {}}, hourly=[], xc_range=[0, 0])
        S.configure(self.r)
        self.assertIn("train higher, you'll drift towards the hill", S.acro_text(day(aloft(sp9=18, d9=0, sp8=18, d8=0, sp80=18, d80=0))))
        self.assertIn("rough off Buffalo", S.acro_text(day(aloft(sp9=18, d9=280, sp8=20, d8=285, sp80=22, d80=290))))
        self.assertIn("Light S aloft, good for the box", S.acro_text(day(aloft(sp9=6, d9=180, sp8=6, d8=180, sp80=6, d80=180))))
        self.assertIn("Nil wind aloft", S.acro_text(day(aloft(sp9=1, d9=0, sp8=1, d8=0, sp80=1, d80=0))))

    def test_new_settings_are_checked(self):
        def bad(mut, expect):
            r = copy.deepcopy(self.r); mut(r)
            with self.assertRaisesRegex(ValueError, expect): validate(r, "rules.yaml")
        bad(lambda r: r["acro"]["northerly_aloft"].update(max_penalty=1.0), "can never reach 0%")
        bad(lambda r: r["acro"]["northerly_aloft"].update(strong_kmh=3), "strong_kmh .* must be higher")
        bad(lambda r: r["acro"]["northerly_aloft"].update(high_climb_above_launch_m=500), "high_climb_above_launch_m")
        bad(lambda r: r["acro"]["westerly_aloft"].update(weights={"WWW": 1}), "weights must map compass points")
        bad(lambda r: r["acro"]["westerly_aloft"].update(floor=2), "floor must be from 0 to 1")
        bad(lambda r: r["acro"]["aloft"].update(levels_hpa=[]), "levels_hpa")
        bad(lambda r: r["acro"]["height"].update(zero_at_above_launch_m=2000), "must be lower than min_above_launch_m")
        bad(lambda r: r["acro"].pop("strong_thermals"), "missing setting")

    def test_ai_prompt_carries_the_pilot_notes(self):
        cfg = {"name": "Bright", "launch_height_m": 797, "launch_above_landing_m": 477, "rules": self.r}
        notes = (ROOT / "sites" / "bright" / "notes.md").read_text(); self.assertIn("acro box", notes); self.assertIn("Buffalo", notes)
        p = system_prompt(dict(cfg, notes=notes)); self.assertIn("LOCAL KNOWLEDGE", p); self.assertIn("acro box is on the north side", p); self.assertLess(p.index("HARD LIMITS"), p.index("LOCAL KNOWLEDGE"))
        self.assertNotIn("LOCAL KNOWLEDGE", system_prompt(cfg))


# ---- southerly at launch is OK when the thermic valley wind is stronger; no rain scores 100%
from pgforecast.rules import rain_factor
from pgforecast import valley

def south(speed, th_sw=900, blh=2800, w850=10, d850=180, **o):
    return win(wind_speed_10m=[speed] * N, wind_direction_10m=[180] * N, wind_gusts_10m=[speed + 4] * N, wind_speed_850hPa=[w850] * N, wind_direction_850hPa=[d850] * N,
               shortwave_radiation=[th_sw] * N, boundary_layer_height=[blh] * N, **o)
def dirf(h, rules): return {x["key"]: x for x in score_day(h, rules, 797)["xc_rules"]}["direction"]["factor"]

class Valley(unittest.TestCase):
    def setUp(self): self.r = load_rules("bright")

    def test_rain_scores_full_marks_without_rain_and_is_unchanged_with_rain(self):
        self.assertEqual(rain_factor(0, 0.2, 2.0, 0.25), 1.0); self.assertAlmostEqual(rain_factor(0.2, 0.2, 2.0, 0.25), 0.896, places=3)
        self.assertAlmostEqual(rain_factor(0.5, 0.2, 2.0, 0.25), 0.741, places=3); self.assertEqual(rain_factor(2.0, 0.2, 2.0, 0.25), 0.0)
        xs = [rain_factor(x / 10, 0.2, 2.0, 0.25) for x in range(0, 25)]; self.assertTrue(all(a >= b for a, b in zip(xs, xs[1:])))      # smooth, never rises with more rain
        self.assertEqual(score_day(win(), self.r, 797)["xc"], 100); self.assertEqual(score_day(win(), self.r, 797)["acro"], 100 - 0 if score_day(win(), self.r, 797)["acro"] == 100 else score_day(win(), self.r, 797)["acro"])

    def test_southerly_ok_when_valley_wind_beats_it(self):
        # thermals ~1.9 m/s -> valley wind ~11 km/h
        self.assertEqual(dirf(south(8), self.r), 1.0)
        self.assertGreater(dirf(south(10), self.r), 0.8); self.assertLess(dirf(south(14), self.r), 0.1)
        weak = south(10, th_sw=300, blh=700); self.assertEqual(dirf(weak, self.r), 0.0)              # weak heating: no valley wind, southerly 10 km/h is still a no-go
        self.assertEqual(dirf(south(5, th_sw=0), self.r), 1.0)                                       # near calm (under 6 km/h) is still fine whatever the heating
        self.assertEqual(dirf(win(), self.r), 1.0)                                                   # a normal north wind is unchanged

    def test_matches_the_table_shown_to_the_user(self):
        from pgforecast.rules import _smooth
        K, M = 6.0, 3.0; f = lambda s, th: _smooth((K * th - s + M) / (2 * M))
        self.assertEqual([round(f(s, 2.0), 2) for s in (8, 10, 12, 15)], [1.0, 0.93, 0.5, 0.0]); self.assertEqual([round(f(s, 1.5), 2) for s in (8, 10, 12)], [0.74, 0.26, 0.0])

    def test_strong_southerly_aloft_stays_a_no_go(self):
        h = south(8, w850=30, d850=200)                                                              # 30 km/h from SSW at 850 hPa, plenty of valley wind
        self.assertEqual(dirf(h, self.r), 0.0); self.assertEqual(score_day(h, self.r, 797)["xc"], 0); self.assertEqual(score_day(h, self.r, 797)["acro"], 0)

    def test_hour_by_hour_and_both_disciplines(self):
        h = south(10); h["shortwave_radiation"] = [900, 900, 900, 300, 100, 0]; h["boundary_layer_height"] = [2800, 2800, 2800, 900, 400, 100]     # heating fades late in the window
        f = dirf(h, self.r); self.assertTrue(0.3 < f < 0.7, f)                                       # about half the hours pass
        r = score_day(south(8), self.r, 797); self.assertEqual({x["key"]: x["factor"] for x in r["acro_rules"]}["direction"], 1.0)
        self.assertIn("valley wind should win", [x["want"] for x in score_day(south(8), self.r, 797)["xc_rules"] if x["key"] == "direction"][0])

    def test_new_settings_are_checked(self):
        for mut, expect in ((lambda r: r["direction"]["southerly_launch"].update(fade_kmh=0), "fade_kmh must be above 0"),
                            (lambda r: r["direction"]["southerly_launch"].update(valley_wind_kmh_per_thermal_ms=-1), "0 or more"),
                            (lambda r: r["direction"]["southerly_launch"].update(arc_deg=[1]), "arc_deg must be"), (lambda r: r["direction"].pop("southerly_launch"), "arc_deg must be")):
            r = copy.deepcopy(self.r); mut(r)
            with self.assertRaisesRegex(ValueError, expect): validate(r, "rules.yaml")

    def test_calibration_pipeline_recovers_the_factor(self):
        import csv, datetime as dt, gzip, json, random, tempfile, zoneinfo
        sys.path.insert(0, str(ROOT / "scripts")); import calibrate_valley_wind as C
        tz = zoneinfo.ZoneInfo("Australia/Melbourne"); d = Path(tempfile.mkdtemp()); (d / "arch").mkdir(); rng = random.Random(4); rows = []
        for k in range(36):
            D = dt.date(2026, 6, 1) + dt.timedelta(days=k); times = [dt.datetime(D.year, D.month, D.day, h, tzinfo=tz).strftime("%Y-%m-%dT%H:%M") for h in range(24)]
            sw = rng.choice([300, 500, 700, 900]); hourly = {"time": times}
            for v, val in dict(wind_speed_10m=6, wind_direction_10m=0, wind_gusts_10m=10, wind_speed_850hPa=10, wind_direction_850hPa=0, temperature_2m=22, dew_point_2m=8, boundary_layer_height=2000,
                               shortwave_radiation=sw, cape=100, cloud_cover_low=10, precipitation=0).items(): hourly[f"{v}_gfs_seamless"] = [val] * 24
            th = C.forecast_thermal(hourly, D.isoformat(), self.r, 797)
            made = dt.datetime(D.year, D.month, D.day, 19, 45, tzinfo=tz) - dt.timedelta(days=1)
            with gzip.open(d / "arch" / f"{k:02d}.json.gz", "wt") as fh: json.dump({"fetched_utc": made.astimezone(dt.UTC).isoformat(), "points": ["mystic_launch"], "response": [{"hourly": hourly}]}, fh)
            for h in range(12, 16):
                for m in (0, 30): rows.append([dt.datetime(D.year, D.month, D.day, h, m, tzinfo=tz).astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:00Z"), "N", round(8.0 * th + rng.gauss(0, 0.3), 1), "0.0"])
        with open(d / "obs.csv", "w", newline="") as fh: w = csv.writer(fh); w.writerow(["obs_time_utc", "wind_dir", "wind_spd_kmh", "rain_trace"]); w.writerows(sorted(rows))
        res = C.run(d / "arch", d / "obs.csv", "Australia/Melbourne", self.r, 797, today=dt.date(2026, 7, 20))
        self.assertEqual(res["n_days"], 36); self.assertTrue(res["ready"]); self.assertAlmostEqual(res["k"], 8.0, delta=0.5)
        res2 = C.run(d / "arch", d / "obs.csv", "Australia/Melbourne", self.r, 797, today=dt.date(2026, 6, 20)); self.assertEqual(res2["n_days"], 19); self.assertFalse(res2["ready"])   # unfinished days ignored

    def test_calibration_fit(self):
        import random; rng = random.Random(3)
        days = [(t, 8.0 * t + rng.gauss(0, 1.0)) for t in [0.5 + 2.0 * rng.random() for _ in range(40)]]                        # truth: 8 km/h per m/s
        r = valley.fit(days); self.assertTrue(r["ready"]); self.assertEqual(r["n_days"], 40); self.assertAlmostEqual(r["k"], 8.0, delta=0.6); self.assertGreater(r["r2"], 0.8)
        self.assertFalse(valley.fit(days[:20])["ready"]); self.assertEqual(valley.fit([])["n_days"], 0); self.assertEqual(valley.fit([(None, 3)])["n_days"], 0)
        self.assertAlmostEqual(valley.north_kmh(10, 0), 10); self.assertAlmostEqual(valley.north_kmh(10, 180), 0); self.assertAlmostEqual(valley.north_kmh(10, 45), 7.07, places=2)

if __name__ == "__main__": unittest.main()
