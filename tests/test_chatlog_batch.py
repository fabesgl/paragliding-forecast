"""Run: .venv/bin/python -m unittest tests.test_chatlog_batch -v   (synthetic chat, fake Claude client: no network, no real messages)"""
import json, re, sys, tempfile, threading, time, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast.chatlog import batch, extract, store
from pgforecast.chatlog.llm import AnthropicLLM

SITES = {"bright": ["bright", "mystic"]}
NAMES = ["Zebediah Quill", "Marguerite Ostrowski"]

def make_chat(n=45):
    lines = []
    for i in range(n):
        who = NAMES[i % 2]
        if i % 3 == 0: txt = f"Mystic was epic on day {i}, strong thermals, flew 2 hours, call +61 400 123 45{i % 10} or me{i}@example.com https://x.com/{i}"
        elif i % 3 == 1: txt = f"anyone flying tomorrow? wind looks light {i}"
        else: txt = f"pizza after work? @{NAMES[0].split()[0]} {i}"
        lines.append(f"[{1 + i // 24:02d}/11/2026, {i % 24:02d}:05:00] {who}: {txt}")
    return "\n".join(lines) + "\n"

class FakeClient:
    """Plays the Claude API for batches: 'epic' messages are reports, everything else is not. Records every request."""
    def __init__(self, drop_first_id=False, per_call=0.0, stop=None, stop_after=None):
        self.sent, self.messages, self.active, self.max_active, self.lock = [], self, 0, 0, threading.Lock()
        self.drop, self.per_call, self.stop, self.stop_after = drop_first_id, per_call, stop, stop_after
    def create(self, **kw):
        with self.lock:
            self.sent.append(kw); self.active += 1; self.max_active = max(self.max_active, self.active); n = len(self.sent)
        try:
            time.sleep(self.per_call); user = kw["messages"][0]["content"]
            blocks = re.findall(r"#(\d+) \| (\d{4}-\d\d-\d\d) .*?\nmsg: (.*)", user.replace("ctx:", "\nctx:").replace("\nctx:", "\nctx:"))
            blocks = re.findall(r"#(\d+) \| (\d{4}-\d\d-\d\d)[^\n]*\n(?:ctx: [^\n]*\n)*msg: ([^\n]*)", user)
            reports, notr = [], []
            for i, d, t in blocks:
                if self.drop and int(i) == 1 and n == 1: continue                      # the model "forgets" id 1 on the very first call
                if "epic" in t.lower(): reports.append(dict(id=int(i), site="bright", date=d, flown="yes", wind_speed="unknown", wind_dir="unknown", turbulence="none",
                                                            thermals="strong", restitution="unknown", rating=5, confidence=0.9))
                else: notr.append(int(i))
            if self.stop and n == self.stop_after: self.stop.set()
            return R(dict(reports=reports, not_reports=notr), 1500, 80)
        finally:
            with self.lock: self.active -= 1
class R:
    def __init__(s, inp, i, o):
        s.content, s.usage = [type("B", (), {"type": "tool_use", "input": inp})()], type("U", (), {"input_tokens": i, "output_tokens": o})()

class T(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); (self.d / "chat.txt").write_text(make_chat()); self.db = store.connect(self.d / "l.sqlite")
        self.targets, self.counts = batch.collect([self.d / "chat.txt"], "salt", SITES, db=self.db)

    def run_it(self, client, **kw):
        llm = AnthropicLLM("claude-haiku-5-5", client=client, max_usd=kw.pop("max_usd", 5.0)); out = []
        st = batch.run_batched(self.targets if kw.pop("fresh", False) is False else self.targets, llm, self.db, SITES, "bright", out=out.append, **kw)
        return st, llm, out

    def test_batches_of_20_scrubbed_and_labelled(self):
        self.assertEqual(len(self.targets), 30)                                  # 2 of every 3 messages mention flying words ("anyone flying tomorrow" and "epic")
        fc = FakeClient(); st, llm, _ = self.run_it(fc, batch_size=20, parallel=2)
        self.assertEqual((st["batches"], st["done_batches"], st["kept"], st["unprocessed"]), (2, 2, 15, 0)); self.assertEqual(len(fc.sent), 2)
        sent = json.dumps(fc.sent)
        for secret in ["+61 400", "400 123", "@example.com", "https://x.com", *NAMES, "Zebediah", "Marguerite"]: self.assertNotIn(secret, sent)
        for tok in ("[number]", "[email]", "[link]", "[person]"): self.assertIn(tok, sent)
        self.assertEqual(sorted(len(re.findall(r"^#\d+ \|", c["messages"][0]["content"], re.M)) for c in fc.sent), [10, 20])
        rows = self.db.execute("SELECT site_id, rating, flown, sender_hash FROM chat_label").fetchall(); self.assertEqual(len(rows), 15); self.assertTrue(all(r[:3] == ("bright", 5, "yes") for r in rows))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM chat_seen").fetchone()[0], 30); self.assertEqual(len({r[3] for r in rows}), 2)    # two anonymous senders
        self.db.close(); raw = (self.d / "l.sqlite").read_bytes()
        for secret in NAMES + ["400 123", "pizza", "epic on day"]: self.assertNotIn(secret.encode(), raw)

    def test_runs_calls_in_parallel_up_to_the_limit(self):
        fc = FakeClient(per_call=0.3); t = time.time(); self.run_it(fc, batch_size=3, parallel=4)
        self.assertEqual(len(fc.sent), 10); self.assertGreaterEqual(fc.max_active, 2); self.assertLessEqual(fc.max_active, 4); self.assertLess(time.time() - t, 3.0)   # serial would be 3.0 s

    def test_stop_then_resume_with_the_same_inputs(self):
        stop = threading.Event(); fc = FakeClient(stop=stop, stop_after=2)
        llm = AnthropicLLM("claude-haiku-5-5", client=fc); out = []
        st = batch.run_batched(self.targets, llm, self.db, SITES, "bright", batch_size=5, parallel=1, stop=stop, out=out.append)
        self.assertTrue(st["stopped"]); self.assertLess(st["done_batches"], 6); done = st["done_batches"]; self.assertGreaterEqual(done, 2)
        targets2, c2 = batch.collect([self.d / "chat.txt"], "salt", SITES, db=self.db)                   # the "same command" again
        self.assertEqual(c2["skipped_seen"], done * 5); self.assertEqual(len(targets2), 30 - done * 5)
        fc2 = FakeClient(); st2 = batch.run_batched(targets2, AnthropicLLM("claude-haiku-5-5", client=fc2), self.db, SITES, "bright", batch_size=5, parallel=2, out=lambda *_: None)
        self.assertEqual((st2["unprocessed"], st2["stopped"]), (0, False)); self.assertEqual(self.db.execute("SELECT COUNT(*) FROM chat_seen").fetchone()[0], 30)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM chat_label").fetchone()[0], 15)             # no duplicates, nothing lost

    def test_forgotten_ids_are_retried_then_left_for_next_run(self):
        fc = FakeClient(drop_first_id=True)                                       # forgets id 1 on call 1; the automatic retry gets it
        st, _, _ = self.run_it(fc, batch_size=30, parallel=1); self.assertEqual(st["unprocessed"], 0); self.assertEqual(len(fc.sent), 2)
        class Forgetful(FakeClient):
            def create(s, **kw):
                r = super().create(**kw); r.content[0].input["not_reports"] = r.content[0].input["not_reports"][1:]; return r    # never answers one id
        self.db.execute("DELETE FROM chat_seen"); self.db.execute("DELETE FROM chat_label"); self.db.commit()
        st, _, _ = self.run_it(Forgetful(), batch_size=30, parallel=1); self.assertEqual(st["unprocessed"], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM chat_seen").fetchone()[0], 29)                     # the unanswered one is NOT marked done

    def test_cost_cap_and_estimate(self):
        st, llm, out = self.run_it(FakeClient(), batch_size=3, parallel=1, max_usd=0.0004)
        self.assertTrue(st["capped"]); self.assertLess(st["done_batches"], 10); self.assertTrue(any("cost cap" in o for o in out))
        e = batch.estimate(self.targets, 20, 4); self.assertEqual((e["messages"], e["calls"]), (30, 2)); self.assertLess(e["usd"], 0.01)

    def test_schema_and_prompt(self):
        s = extract.batch_schema(["bright"]); self.assertEqual(s["required"], ["reports", "not_reports"]); self.assertNotIn("is_report", s["properties"]["reports"]["items"]["properties"])
        p = extract.batch_system_prompt(SITES); self.assertIn("not_reports", p); self.assertIn("Catalan", p); self.assertIn("bright = bright, mystic", p)
        self.assertEqual(extract.validate_batch("garbage", [{"id": 1, "ts": __import__("datetime").datetime(2026, 11, 25)}], {"bright"}, "bright"), {})




class Alias(unittest.TestCase):
    def test_a_place_word_from_the_model_is_mapped_to_the_site_id(self):
        import datetime
        items = [{"id": 1, "ts": datetime.datetime(2026, 11, 25, 12, 0)}, {"id": 2, "ts": datetime.datetime(2026, 11, 25, 12, 1)}]
        rep = lambda i, site: dict(id=i, site=site, date="2026-11-24", flown="yes", wind_speed="unknown", wind_dir="unknown", turbulence="unknown", thermals="unknown", restitution="unknown", rating=None, confidence=0.7)
        got = extract.validate_batch(json.dumps({"reports": [rep(1, "Mystic"), rep(2, "tawonga")], "not_reports": []}), items, {"bright"}, "bright", SITES)
        self.assertEqual(got[1]["site_id"], "bright"); self.assertIsNone(got[2])               # a place that is not one of ours is dropped, never guessed

class Live(unittest.TestCase):
    TS = __import__("datetime").datetime(2026, 11, 25, 13, 30)
    def v(self, **o):
        base = dict(is_report=True, site="bright", kind="live", date=None, flown="yes", wind_speed="unknown", wind_dir="unknown", turbulence="unknown", thermals="unknown", restitution="unknown", rating=None, confidence=0.8)
        base.update(o); return extract.validate(json.dumps(base), self.TS, {"bright"}, "bright")

    def test_live_messages_are_kept_and_belong_to_the_day_written(self):
        a = self.v(flown="yes", thermals="moderate", date="2026-11-01"); self.assertEqual((a["kind"], a["date"], a["date_source"], a["flown"]), ("live", "2026-11-25", "message", "yes"))   # any date the model gives is ignored
        self.assertEqual(self.v(flown="going")["flown"], "going")                                         # 'heading up now', 'on launch'
        b = self.v(flown="no", wind_speed="strong"); self.assertEqual((b["flown"], b["wind_speed"]), ("no", "strong"))   # 'blown out'
        self.assertEqual(self.v(flown="unknown", turbulence="strong")["turbulence"], "strong")              # 'rough today': conditions alone are enough
        self.assertIsNone(self.v(flown="unknown"))                                                         # nothing useful: dropped
        self.assertIsNone(self.v(is_report=False)); self.assertIsNone(self.v(site="other"))

    def test_past_reports_still_use_the_stated_date(self):
        p = self.v(kind="past", date="2026-11-23"); self.assertEqual((p["kind"], p["date"], p["date_source"]), ("past", "2026-11-23", "stated"))
        self.assertEqual(self.v(kind="past", date="2019-01-01")["date_source"], "message")
        self.assertEqual(extract.validate(json.dumps(dict(is_report=True, site="bright", date=None, flown="yes", rating=4)), self.TS, {"bright"}, "bright")["kind"], "past")   # old-style answers still work

    def test_batch_schema_and_prompt_know_live_and_planning(self):
        p = extract.batch_system_prompt(SITES)
        for w in ("on launch", "in the air", "it's working", "heading up now", "blown out", "packed up", "rough today", "anyone flying tomorrow?"): self.assertIn(w, p)
        props = extract.batch_schema(["bright"])["properties"]["reports"]["items"]["properties"]; self.assertEqual(props["kind"]["enum"], ["past", "live"]); self.assertIn("going", props["flown"]["enum"])
        self.assertEqual(extract.PROMPT_VERSION, "chat-v3")

    def test_old_answers_are_redone_and_stale_labels_removed(self):
        db = store.connect(Path(tempfile.mkdtemp()) / "l.sqlite")
        lab = dict(site_id="bright", kind="past", date="2026-11-24", date_source="stated", flown="yes", wind_speed="unknown", wind_dir="unknown", turbulence="unknown", thermals="unknown", restitution="unknown", rating=None, llm_confidence=0.5)
        store.save(db, "k1", "whatsapp", lab, "m", "chat-v2"); self.assertTrue(store.seen(db, "k1")); self.assertFalse(store.seen(db, "k1", "chat-v3")); self.assertTrue(store.seen(db, "k1", "chat-v2"))
        store.save(db, "k1", "whatsapp", None, "m", "chat-v3")                                             # new rules say: not a report
        self.assertEqual(db.execute("SELECT COUNT(*) FROM chat_label").fetchone()[0], 0); self.assertTrue(store.seen(db, "k1", "chat-v3"))

    def test_daily_activity_counts_only(self):
        d = Path(tempfile.mkdtemp()); (d / "c.txt").write_text("[22/11/2026, 09:00:00] A B: hi\n[22/11/2026, 09:05:00] C D: morning\n[22/11/2026, 10:00:00] A B: up\n[23/11/2026, 09:00:00] A B: x\n")
        act = batch.activity([d / "c.txt"], "salt", None); vals = sorted(act.values()); self.assertEqual(vals, [(1, 1), (3, 2)])
        db = store.connect(d / "l.sqlite"); store.save_activity(db, act); store.save_activity(db, {k: (1, 1) for k in act})           # a smaller second export never lowers a count
        self.assertEqual(sorted(db.execute("SELECT n_messages, n_senders FROM chat_day_activity").fetchall()), [(1, 1), (3, 2)]); db.close()
        raw = (d / "l.sqlite").read_bytes(); self.assertNotIn(b"hi", b""); 
        for secret in (b"A B", b"C D", b"morning"): self.assertNotIn(secret, raw)

if __name__ == "__main__": unittest.main()
