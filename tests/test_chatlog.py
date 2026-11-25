"""Run: .venv/bin/python -m unittest tests.test_chatlog -v   (uses synthetic chats and a fake local model; no real data)"""
import json, re, sqlite3, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
from pgforecast.chatlog import parsers, store, extract
from pgforecast.chatlog.llm import LocalLLM
import import_chats

NAMES = ["Zebediah Quill", "Marguerite Ostrowski"]            # fake names that must never reach the database
WA_IOS = f"""[22/11/2026, 18:02:11] {NAMES[0]}: Mystic was epic yesterday, strong thermals, smooth, flew 2 hours
[22/11/2026, 18:03:40] {NAMES[1]}: Agree, wind was light from the north and
the restitution was good till sunset
[22/11/2026, 18:05:00] {NAMES[1]}: <Media omitted>
[22/11/2026, 18:06:00] Messages and calls are end-to-end encrypted.
[23/11/2026, 07:10:00] {NAMES[0]}: anyone keen for pizza tonight? call me on +61 400 123 456
[24/11/2026, 09:00:00] {NAMES[1]}: Too windy today at Bright, blown out, nobody flew. Rotor everywhere
"""
WA_ANDROID = f"""25/11/2026, 2:03 pm - {NAMES[0]}: Mystic today was bumpy, only a 2 out of 5, wind strong from the south
"""
TG = {"name": "x", "messages": [
    {"id": 1, "type": "service", "date": "2026-11-20T10:00:00", "text": ""},
    {"id": 2, "type": "message", "date": "2026-11-21T19:30:00", "from": NAMES[0], "text": ["Bright was ", {"type": "bold", "text": "great"}, " yesterday, 4/5, weak thermals, no turbulence, flew"]},
    {"id": 3, "type": "message", "date": "2026-11-21T19:31:00", "from": NAMES[1], "text": "forecast looks good for tomorrow, will fly"}]}

def fake_model(user):
    """Stands in for a local LLM: a few keyword rules that return the JSON the real prompt asks for."""
    last = user.split("Last message:", 1)[1].lower()
    if "pizza" in last or "forecast looks" in last or "agree" in last and "restitution" not in last: return json.dumps({"is_report": False})
    j = dict(is_report=True, site="bright" if ("mystic" in last or "bright" in last) else "unknown", date=None, flown="yes", wind_speed="unknown", wind_dir="unknown",
             turbulence="unknown", thermals="unknown", restitution="unknown", rating=None, confidence=0.8)
    if "epic" in last: j.update(rating=5, thermals="strong", turbulence="none", date="2026-11-21")
    if "restitution was good" in last: j.update(restitution="good", wind_speed="light", wind_dir="N", date="2026-11-21")
    if "blown out" in last: j.update(flown="no", wind_speed="strong", turbulence="strong")
    if "bumpy" in last: j.update(rating=2, turbulence="moderate", wind_speed="strong", wind_dir="S")
    if "great" in last: j.update(rating=4, thermals="weak", turbulence="none", date="2026-11-20")
    return json.dumps(j)

class Fake:
    model = "fake"
    def complete(self, system, user, schema): return fake_model(user)

class Handler(BaseHTTPRequestHandler):                          # fake Ollama server so the real HTTP client is exercised
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = json.dumps({"message": {"content": fake_model(body["messages"][1]["content"])}}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass

class T(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); (self.d / "ios.txt").write_text(WA_IOS); (self.d / "and.txt").write_text(WA_ANDROID)
        (self.d / "tg.json").write_text(json.dumps(TG))

    def test_parsers(self):
        a = parsers.parse_whatsapp(self.d / "ios.txt"); self.assertEqual(len(a), 4)          # media, system message dropped; multi-line joined
        self.assertIn("restitution was good till sunset", a[1].text); self.assertEqual(a[0].ts.day, 22)
        b = parsers.parse_whatsapp(self.d / "and.txt"); self.assertEqual(b[0].ts.hour, 14)
        c = parsers.parse_telegram(self.d / "tg.json"); self.assertEqual(len(c), 2); self.assertIn("great yesterday", c[0].text)
        for m in a + b + c: self.assertNotIn("Quill", m.text)

    def test_scrub(self):
        self.assertEqual(extract.scrub("call +61 400 123 456 or @bob see https://x.com/a"), "call [number] or [person] see [link]")

    def test_pipeline_stores_only_labels(self):
        db = store.connect(self.d / "labels.sqlite")
        s = import_chats.run([self.d / "ios.txt", self.d / "and.txt", self.d / "tg.json"], Fake(), db, "salt", {"bright": ["bright", "mystic"]}, default_site="bright", out=lambda *_: None)
        rows = db.execute("SELECT site_id,date,date_source,flown,wind_speed,wind_dir,turbulence,thermals,restitution,rating,evidence,confidence_tier,weight FROM chat_label ORDER BY date,id").fetchall()
        self.assertEqual(len(rows), 5, rows); self.assertEqual(s["kept"], 5)
        self.assertTrue(all(r[10] == "chat_llm" and r[11] == "low" and r[12] == 0.3 for r in rows))
        epic = [r for r in rows if r[9] == 5][0]; self.assertEqual(epic[:9], ("bright", "2026-11-21", "stated", "yes", "unknown", "unknown", "none", "strong", "unknown"))
        self.assertTrue(any(r[3] == "no" and r[6] == "strong" for r in rows))
        rest = [r for r in rows if r[8] == "good"][0]; self.assertEqual((rest[4], rest[5]), ("light", "N"))
        db.close()
        raw = (self.d / "labels.sqlite").read_bytes()
        for secret in NAMES + ["Quill", "pizza", "epic yesterday", "400 123", "rotor everywhere", "blown out"]: self.assertNotIn(secret.encode(), raw)
        db = store.connect(self.d / "labels.sqlite")
        s2 = import_chats.run([self.d / "ios.txt"], Fake(), db, "salt", {"bright": ["bright"]}, default_site="bright", out=lambda *_: None)   # resume: nothing new
        self.assertEqual(s2["sent_to_model"], 0); self.assertEqual(db.execute("SELECT COUNT(*) FROM chat_label").fetchone()[0], 5)

    def test_http_client_and_local_only(self):
        srv = HTTPServer(("127.0.0.1", 0), Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
        llm = LocalLLM("fake", "ollama", f"http://127.0.0.1:{srv.server_port}")
        db = store.connect(self.d / "l2.sqlite")
        s = import_chats.run([self.d / "tg.json"], llm, db, "salt", {"bright": ["bright"]}, out=lambda *_: None); srv.shutdown()
        self.assertEqual(s["kept"], 1)
        with self.assertRaises(SystemExit): LocalLLM("m", "ollama", "http://example.com:11434")

    def test_bad_model_output_rejected(self):
        ts = parsers.datetime(2026, 11, 25, 12, 0)
        self.assertIsNone(extract.validate("not json", ts, {"bright"}, "bright"))
        j = json.dumps(dict(is_report=True, site="bright", date="2019-01-01", flown="yes", rating=9, wind_speed="hurricane", confidence=3))
        v = extract.validate(j, ts, {"bright"}, None)
        self.assertEqual((v["date"], v["date_source"], v["rating"], v["wind_speed"], v["llm_confidence"]), ("2026-11-25", "message", None, "unknown", 1.0))


ES_IOS = """[25/11/26 14:03:21] Jordi Puig: Ayer en Organyà fue brutal, térmicas fuertes, volamos 3 horas, un 5
[25/11/26 14:05:00] Jordi Puig: Y la restitución buenísima hasta el atardecer
[25/11/26 14:06:10] Núria Vidal: <Multimedia omitido>
[25/11/26 14:07:00] Núria Vidal: ¿Quién viene a cenar el viernes?
"""
CA_ANDROID = """26/11/26, 8:15 p. m. - Núria Vidal: Avui a Organyà no hem volat, massa vent, rotors per tot arreu
26/11/26, 8:16 p. m. - Núria Vidal: <Multimèdia omès>
26/11/26, 8:20 p. m. - Jordi Puig: Qui ve a sopar divendres?
"""
ORG = {"organya": ["Organyà", "organya"]}

def fake_es_ca(user):
    last = user.split("Last message:", 1)[1].lower()
    j = dict(is_report=True, site="organya", date=None, flown="yes", wind_speed="unknown", wind_dir="unknown", turbulence="unknown", thermals="unknown",
             restitution="unknown", rating=None, confidence=0.8)
    if "ayer en organy" in last: j.update(date="2026-11-24", thermals="strong", rating=5)
    elif "restitución" in last: j.update(date="2026-11-24", restitution="good")
    elif "no hem volat" in last: j.update(date="2026-11-26", flown="no", wind_speed="strong", turbulence="strong")
    else: j = dict(is_report=False)
    return json.dumps(j)

class FakeEsCa:
    model = "fake"
    def complete(self, system, user, schema): return fake_es_ca(user)

class T2(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); (self.d / "es.txt").write_text(ES_IOS); (self.d / "ca.txt").write_text(CA_ANDROID)

    def test_spanish_catalan_parsing(self):
        es = parsers.parse_whatsapp(self.d / "es.txt"); ca = parsers.parse_whatsapp(self.d / "ca.txt")
        self.assertEqual([m.text.startswith(("Ayer", "Y la", "¿Quién")) for m in es], [True] * 3)       # '<Multimedia omitido>' dropped
        self.assertEqual(len(ca), 2); self.assertEqual((ca[0].ts.year, ca[0].ts.hour, ca[0].ts.minute), (2026, 20, 15))   # '8:15 p. m.' = 20:15
        self.assertEqual(es[0].sender, "Jordi Puig")

    def test_prefilter_accents_and_languages(self):
        f = extract.fold
        for t in ["Ayer fue brutal, térmicas fuertes", "Avui no hem volat, massa vent", "restitución buenísima", "Organyà ayer"]:
            self.assertTrue(extract.KEYWORDS.search(f(t)) or "organya" in f(t), t)
        self.assertFalse(extract.KEYWORDS.search(f("¿Quién viene a cenar el viernes?")))
        self.assertEqual(f("ORGANYÀ"), "organya")

    def test_sender_hash_groups_repeat_reports_without_names(self):
        db = store.connect(self.d / "l.sqlite")
        s = import_chats.run([self.d / "es.txt", self.d / "ca.txt"], FakeEsCa(), db, "salt", ORG, chat_names={self.d / "es.txt": "club", self.d / "ca.txt": "club"}, out=lambda *_: None)
        self.assertEqual(s["kept"], 3)
        rows = db.execute("SELECT site_id,date,flown,restitution,rating,sender_hash,chat_hash FROM chat_label ORDER BY id").fetchall()
        jordi1, jordi2, nuria = rows
        self.assertEqual(jordi1[5], jordi2[5]); self.assertNotEqual(jordi1[5], nuria[5]); self.assertEqual(jordi1[6], nuria[6])   # same person -> same id, same chat
        self.assertEqual((jordi1[0], jordi1[1], jordi1[4], jordi2[3], nuria[2]), ("organya", "2026-11-24", 5, "good", "no"))
        w = dict(db.execute("SELECT date || ':' || COALESCE(rating,0) || ':' || restitution, effective_weight FROM chat_label_weighted").fetchall())
        self.assertAlmostEqual(w["2026-11-24:5:unknown"], 0.15); self.assertAlmostEqual(w["2026-11-24:0:good"], 0.15)   # Jordi's two reports share one 0.3
        self.assertAlmostEqual(w["2026-11-26:0:unknown"], 0.3)
        self.assertNotEqual(store.sender_hash("salt", "other chat", "Jordi Puig"), jordi1[5])                  # different chat -> different id
        self.assertEqual(store.sender_hash("salt", " CLUB ", " jordi puig "), jordi1[5])                       # tolerant of case and spacing
        self.assertNotEqual(store.sender_hash("other salt", "club", "Jordi Puig"), jordi1[5])
        db.close(); raw = (self.d / "l.sqlite").read_bytes()
        for secret in ["Jordi", "Puig", "Vidal", "Núria", "N\u00faria", "brutal", "rotors", "cenar", "sopar"]: self.assertNotIn(secret.encode(), raw)
        self.assertIsNone(store.sender_hash("salt", "club", ""))




# ---- Claude API backend (no network: a fake client stands in for the SDK)
class FakeUsage: 
    def __init__(s, i, o): s.input_tokens, s.output_tokens = i, o
class FakeBlock:
    def __init__(s, inp): s.type, s.input = "tool_use", inp
class FakeResp:
    def __init__(s, inp, i=900, o=60): s.content, s.usage = [FakeBlock(inp)], FakeUsage(i, o)
class FakeClient:
    def __init__(s, fn=None): s.sent, s.messages = [], s
    def create(s, **kw):
        s.sent.append(kw); user = kw["messages"][0]["content"]
        return FakeResp(json.loads(fake_model(user)))

class T3(unittest.TestCase):
    def setUp(self):
        from pgforecast.chatlog.llm import AnthropicLLM; self.A = AnthropicLLM
        self.d = Path(tempfile.mkdtemp())

    def test_key_only_from_environment(self):
        import os
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(SystemExit) as c: self.A("claude-haiku-5-5")
            self.assertIn("ANTHROPIC_API_KEY", str(c.exception))
        finally:
            if old: os.environ["ANTHROPIC_API_KEY"] = old
        with self.assertRaises(SystemExit): self.A("some-unpriced-model", client=FakeClient())

    def test_scrubbed_text_only_and_cost_tracked(self):
        (self.d / "c.txt").write_text(f"[22/11/2026, 18:02:11] {NAMES[0]}: Mystic was epic yesterday, call me +61 400 123 456 or mail jo@example.com, see https://x.com/a @{NAMES[1].split()[0]}\n"
                                      f"[22/11/2026, 18:03:11] {NAMES[1]}: agreed, restitution was good, flew 2 hours\n")
        fc = FakeClient(); llm = self.A("claude-haiku-5-5", client=fc); db = store.connect(self.d / "l.sqlite")
        s = import_chats.run([self.d / "c.txt"], llm, db, "salt", {"bright": ["bright", "mystic"]}, default_site="bright", out=lambda *_: None)
        sent = json.dumps(fc.sent)
        for secret in ("+61 400", "400 123", "jo@example.com", "https://x.com", NAMES[0], NAMES[1], "Zebediah", "Marguerite"): self.assertNotIn(secret, sent)
        for token in ("[number]", "[email]", "[link]", "[person]"): self.assertIn(token, sent)
        self.assertEqual(fc.sent[0]["model"], "claude-haiku-5-5"); self.assertEqual(fc.sent[0]["tool_choice"], {"type": "tool", "name": "label_message"})
        self.assertEqual(llm.usage["calls"], 2); self.assertAlmostEqual(llm.cost(), (1800 * 0.10 + 120 * 0.50) / 1e6, places=9)
        self.assertIn("estimated cost", llm.report()); self.assertEqual(s["kept"], 2)
        db.close(); raw = (self.d / "l.sqlite").read_bytes()
        for secret in NAMES + ["400 123", "jo@example.com"]: self.assertNotIn(secret.encode(), raw)

    def test_cost_cap_stops_the_run(self):
        lines = "".join(f"[22/11/2026, 18:{i:02d}:00] A B: Mystic was epic day {i}, thermals strong\n" for i in range(10)); (self.d / "c.txt").write_text(lines)
        llm = self.A("claude-haiku-5-5", client=FakeClient(), max_usd=0.0001)             # 0.0001 USD: reached after a couple of messages
        msgs = []; db = store.connect(self.d / "l.sqlite")
        s = import_chats.run([self.d / "c.txt"], llm, db, "salt", {"bright": ["bright", "mystic"]}, default_site="bright", out=msgs.append)
        self.assertLess(s["sent_to_model"], 10); self.assertTrue(any("cost cap" in m for m in msgs))

if __name__ == "__main__": unittest.main()
