"""Batched, parallel, stop-and-resume import for the Claude API backend.
About 20 numbered messages go in each call; a few calls run at once. Every finished batch is saved straight away, so you can stop at any
time (Ctrl-C) and re-run the SAME command: messages already done are skipped."""
import re, threading, time
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait
from pathlib import Path
from . import extract, parsers, store

def collect(files, salt_, sites, chat_names=None, date_order="auto", db=None, prefilter=True):
    """Parse every file into targets still to do. Returns (targets, counts). Text is scrubbed here, so nothing downstream ever sees raw text."""
    alias_rx = re.compile(r"\b(" + "|".join(re.escape(extract.fold(a)) for al in sites.values() for a in al) + r")\b")
    targets, c = [], dict(messages=0, skipped_seen=0, skipped_irrelevant=0)
    for path in files:
        msgs = parsers.parse_any(path, date_order); src = "telegram" if Path(path).suffix.lower() == ".json" else "whatsapp"
        alias = (chat_names or {}).get(path) or Path(path).stem; chat_h = store.chat_hash(salt_, alias); c["messages"] += len(msgs)
        for i, m in enumerate(msgs):
            sender_h = store.sender_hash(salt_, alias, m.sender); key = store.msg_key(salt_, m.ts, m.text, sender_h)
            if db is not None and store.seen(db, key): c["skipped_seen"] += 1; continue
            f = extract.fold(m.text)
            if prefilter and not (extract.KEYWORDS.search(f) or alias_rx.search(f)): c["skipped_irrelevant"] += 1; continue
            ctx = [extract.scrub(x.text)[:300] for x in msgs[max(0, i - 2):i] if 0 <= (m.ts - x.ts).total_seconds() <= 6 * 3600]
            targets.append(dict(key=key, src=src, chat_h=chat_h, sender_h=sender_h, ts=m.ts, text=extract.scrub(m.text), ctx=ctx))
    return targets, c

def estimate(targets, batch_size=20, parallel=4, sys_tokens=1400, secs_per_call=8.0, price=(0.10, 0.50), report_share=0.2):
    """Rough size, time and cost before spending anything: ~3.5 characters per token; the reply costs ~3 tokens per non-report and ~75 per report
    (assuming about 1 message in 5 is a report); ~1,400 tokens of instructions per call. Refine from a real trial."""
    n = len(targets); calls = -(-n // batch_size)
    chars = sum(len(t["text"]) + sum(len(x) for x in t["ctx"]) + 40 for t in targets)
    tin = int(chars / 3.5) + calls * sys_tokens; tout = int(n * (report_share * 75 + (1 - report_share) * 3)) + calls * 40
    return dict(messages=n, calls=calls, input_tokens=tin, output_tokens=tout, usd=(tin * price[0] + tout * price[1]) / 1e6,
                minutes=calls * secs_per_call / max(1, parallel) / 60)

def run_batched(targets, llm, db, sites, default_site=None, batch_size=20, parallel=4, limit=None, stop=None, out=print, retries=1):
    """Label targets in batches. `stop` is a threading.Event; set it to stop cleanly after the batches already running. Returns stats."""
    stop = stop or threading.Event(); sid = list(sites)
    sysmsg, sch = extract.batch_system_prompt(sites), extract.batch_schema(sid)
    if limit: targets = targets[:limit]
    batches = [targets[i:i + batch_size] for i in range(0, len(targets), batch_size)]
    stats = dict(batches=len(batches), done_batches=0, labelled=0, kept=0, unprocessed=0, stopped=False, capped=False)
    t0 = time.time()

    def work(batch):
        items = [dict(id=i + 1, ts=t["ts"], text=t["text"], ctx=t["ctx"]) for i, t in enumerate(batch)]
        user = extract.batch_user_prompt(items); got = {}
        for _ in range(retries + 1):
            if stop.is_set() and got: break
            raw = llm.complete(sysmsg, user, sch, max_tokens=600 + 90 * len(items))
            got.update({k: v for k, v in extract.validate_batch(raw, items, set(sid), default_site).items() if k not in got})
            if len(got) == len(items): break
        return got

    def finish(batch, got):
        for i, t in enumerate(batch):
            if i + 1 in got:                                         # an answered message is marked done, whether it was a report or not
                store.save(db, t["key"], t["src"], got[i + 1], llm.model, extract.PROMPT_VERSION, chat_h=t["chat_h"], sender_h=t["sender_h"], commit=False)
                stats["labelled"] += 1; stats["kept"] += bool(got[i + 1])
            else: stats["unprocessed"] += 1                           # left alone, so the next run tries it again
        db.commit(); stats["done_batches"] += 1
        el = time.time() - t0; eta = el / stats["done_batches"] * (stats["batches"] - stats["done_batches"]) / 60
        out(f"  batch {stats['done_batches']}/{stats['batches']} saved: {stats['kept']} labels kept so far · {llm.report()} · {el/60:.1f} min elapsed, ~{eta:.0f} min left")

    pending, queue = {}, list(batches)
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        try:
            while queue or pending:
                while queue and len(pending) < parallel and not stop.is_set():
                    if getattr(llm, "over_budget", lambda: False)(): stats["capped"] = True; queue.clear(); out("cost cap reached - finishing the batches already running"); break
                    b = queue.pop(0); pending[ex.submit(work, b)] = b
                if not pending: break
                done, _ = wait(list(pending), timeout=0.5, return_when=FIRST_COMPLETED)
                for fut in done:
                    b = pending.pop(fut)
                    try: finish(b, fut.result())
                    except SystemExit: stop.set(); queue.clear(); raise
                    except Exception as e: stats["unprocessed"] += len(b); out(f"  a batch failed ({type(e).__name__}); its messages will be retried on the next run")
                if stop.is_set() and queue: stats["stopped"] = True; queue.clear(); out("stopping: waiting for the batches already running...")
        except KeyboardInterrupt:
            stop.set(); stats["stopped"] = True; queue.clear(); out("\nCtrl-C: finishing the batches already running, then stopping...")
            for fut in list(pending):
                try: finish(pending.pop(fut), fut.result(timeout=120))
                except BaseException: pass
    return stats
