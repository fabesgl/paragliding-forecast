"""Run: .venv/bin/python -m unittest tests.test_ai_opinion -v   (no network: the API is faked)"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import requests
from pgforecast import ai_opinion as A

CFG = {"name": "Bright", "launch_height_m": 797, "launch_above_landing_m": 477}
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
        self.assertEqual(st, "ok"); self.assertEqual(out["2026-11-26"], {"xc_call": "good", "acro_call": "poor", "reasoning": "Light wind. Gusty."})
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

if __name__ == "__main__": unittest.main()
