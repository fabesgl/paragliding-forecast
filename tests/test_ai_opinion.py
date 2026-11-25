"""Run: .venv/bin/python -m unittest tests.test_ai_opinion -v   (no network: the API is faked)"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import requests
from pgforecast import ai_opinion as A

from pgforecast.rules_config import load_rules
RULES = load_rules("bright")
CFG = {"name": "Bright", "launch_height_m": 797, "launch_above_landing_m": 477, "rules": RULES}
DAY = {"date": "2026-11-26", "xc": 57, "acro": 34, "xc_range": [20, 97], "models": {"gfs_seamless": {"xc": 88, "acro": 72}},
       "key": {"wind_kmh": 8.2, "gust_kmh": 26.0, "wind_dir": "N"}, "xc_rules": [{"label": "Gusts", "value": "26 km/h", "factor": 0.58}], "acro_rules": []}

class Resp:
    def __init__(s, code, body=None, text=""): s.status_code, s._b, s.text = code, body, text
    def json(s): return s._b
def ok(xc="good"): return Resp(200, {"content": [{"type": "tool_use", "name": "give_opinion", "input": {"xc_call": xc, "acro_call": "poor", "reasoning": "Light wind.  Gusty."}}]})

class Fake:
    def __init__(s, *resps): s.resps, s.calls = list(resps), []
    def post(s, url, **kw):
        s.calls.append(kw); r = s.resps.pop(0) if s.resps else ok()
        if isinstance(r, Exception): raise r
        return r

class T(unittest.TestCase):
    def test_switch(self):
        self.assertFalse(A.enabled({})); self.assertFalse(A.enabled({"ANTHROPIC_API_KEY": "k", "AI_OPINION_ENABLED": "false"}))
        self.assertTrue(A.enabled({"ANTHROPIC_API_KEY": "k"})); self.assertTrue(A.enabled({"ANTHROPIC_API_KEY": "k", "AI_OPINION_ENABLED": "true"}))
        self.assertEqual(A.opinions([DAY], {}, [], {}, CFG, env={"ANTHROPIC_API_KEY": "k", "AI_OPINION_ENABLED": "false"}, session=Fake()), ({}, "off"))

    def test_success_and_request_shape(self):
        s = Fake(ok()); out, st = A.opinions([DAY], {}, [], {}, CFG, env={"ANTHROPIC_API_KEY": "k"}, session=s)
        self.assertEqual(st, "ok"); self.assertEqual(out["2026-11-26"], {"xc_call": "good", "acro_call": "poor", "reasoning": "Light wind. Gusty.", "forced": ""})
        c = s.calls[0]; self.assertEqual(c["json"]["model"], "claude-haiku-5-5"); self.assertEqual(c["headers"]["x-api-key"], "k")
        self.assertEqual(c["json"]["tool_choice"]["name"], "give_opinion")

    def test_failures_never_raise(self):
        A.time.sleep = lambda *_: None
        for resps in ([Resp(500)] * 3, [requests.ConnectTimeout()] * 3, [Resp(400, text="bad")], [Resp(200, {"content": [{"type": "text", "text": "hi"}]})],
                      [Resp(200, {"content": [{"type": "tool_use", "name": "give_opinion", "input": {"xc_call": "great", "acro_call": "poor", "reasoning": "x"}}]})]):
            out, st = A.opinions([DAY], {}, [], {}, CFG, env={"ANTHROPIC_API_KEY": "k"}, session=Fake(*resps)); self.assertEqual(out, {}); self.assertIn("failed", st)
        out, st = A.opinions([DAY, DAY], {}, [], {}, CFG, env={"ANTHROPIC_API_KEY": "k"}, session=Fake(Resp(401)))
        self.assertEqual((out, st), ({}, "key rejected"))
        out, st = A.opinions([DAY, dict(DAY, date="2026-11-27")], {}, [], {}, CFG, env={"ANTHROPIC_API_KEY": "k"}, session=Fake(Resp(400), ok("marginal")))
        self.assertEqual(list(out), ["2026-11-27"]); self.assertEqual(st, "1 of 2 failed")        # one bad day does not stop the others


# ---- hard limits
def day_with(wind=8, gust=20, w850=15, rain=0.0, acro_wind=None):
    r = lambda k, n: {"key": k, "num": n, "label": k, "value": str(n), "factor": 1.0}
    return dict(DAY, xc_rules=[r("wind", wind), r("gust", gust), r("w850", w850), r("rain", rain)],
                acro_rules=[r("wind", wind), r("gust", gust), r("w850", w850), r("shear", 5), r("rain", rain)])

class Limits(unittest.TestCase):
    def test_breaches_per_discipline(self):
        self.assertEqual(A.hard_limits(day_with(), RULES), {"xc": [], "acro": []})
        h = A.hard_limits(day_with(gust=22), RULES); self.assertEqual(h["xc"], []); self.assertEqual(len(h["acro"]), 1)          # 22 is fine for XC (<25), over the acro limit (20)
        h = A.hard_limits(day_with(wind=18, w850=30), RULES); self.assertEqual(len(h["xc"]), 1); self.assertEqual(len(h["acro"]), 2)
        h = A.hard_limits(day_with(rain=0.2), RULES); self.assertEqual((len(h["xc"]), len(h["acro"])), (1, 1)); self.assertIn("rain", h["xc"][0])
        self.assertEqual(A.hard_limits(day_with(rain=0.1, wind=20, gust=25, w850=25), RULES)["xc"], [])                         # exactly at the limit is allowed

    def test_enforce_forces_poor_and_explains(self):
        res = A.enforce(dict(xc_call="good", acro_call="good", reasoning="Looks great."), A.hard_limits(day_with(gust=28), RULES))
        self.assertEqual((res["xc_call"], res["acro_call"], res["forced"]), ("poor", "poor", "xc acro")); self.assertIn("hard limits", res["reasoning"])
        res = A.enforce(dict(xc_call="good", acro_call="good", reasoning="x"), A.hard_limits(day_with(gust=22), RULES))
        self.assertEqual((res["xc_call"], res["acro_call"], res["forced"]), ("good", "poor", "acro"))                      # inside the XC limits it may say good
        res = A.enforce(dict(xc_call="poor", acro_call="poor", reasoning="x"), A.hard_limits(day_with(rain=2), RULES))
        self.assertEqual((res["forced"], res["reasoning"]), ("", "x"))                                                     # already poor: nothing changed, nothing added

    def test_limits_in_prompt_and_payload_and_end_to_end(self):
        p = A.system_prompt(CFG); self.assertIn("gusts over 25 km/h", p); self.assertIn("gusts over 20 km/h", p); self.assertIn("rain forecast", p)
        s = Fake(ok("good")); out, st = A.opinions([day_with(gust=28)], {}, [], {}, CFG, env={"ANTHROPIC_API_KEY": "k"}, session=s)
        self.assertEqual(out["2026-11-26"]["xc_call"], "poor"); self.assertIn("hard_limit_breaches", s.calls[0]["json"]["messages"][0]["content"])
        s = Fake(ok("good")); out, _ = A.opinions([day_with()], {}, [], {}, CFG, env={"ANTHROPIC_API_KEY": "k"}, session=s)
        self.assertEqual(out["2026-11-26"]["xc_call"], "good")                                                              # within limits the model is free

if __name__ == "__main__": unittest.main()
