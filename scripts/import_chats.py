"""Import WhatsApp (.txt) and Telegram Desktop (.json) chat exports as weak pilot-report labels.
Each message goes to a LOCAL model; only labels are stored (data/chat_labels.sqlite). No text, no names.
Resumable: messages already processed are skipped.

  python scripts/import_chats.py chat.txt other.json --default-site bright --model qwen2.5:7b-instruct
  python scripts/import_chats.py chat.txt --dry-run        # parse + count only, no model, nothing stored
"""
import argparse, sys, yaml
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pgforecast.chatlog import extract, parsers, store, batch
from pgforecast.chatlog.llm import LocalLLM, AnthropicLLM

def load_sites():
    sites = {}
    for f in sorted((ROOT / "sites").glob("*.yaml")):
        c = yaml.safe_load(f.read_text()); sites[c["id"]] = c.get("chat_aliases") or [c["name"]]
    return sites

def run(files, llm, db, salt_, sites, chat_names=None, default_site=None, date_order="auto", limit=None, dry=False, out=print, prefilter=True):
    """chat_names: {file: alias}. The alias (default: the file name) and each sender are hashed together, so one person gets the same anonymous id
    every time the same chat is imported. Re-exports of one chat must use the same alias."""
    sid = list(sites); system = extract.system_prompt(sites); schema = extract.schema(sid)
    import re
    alias_rx = re.compile(r"\b(" + "|".join(re.escape(extract.fold(a)) for al in sites.values() for a in al) + r")\b")
    stats = dict(messages=0, skipped_seen=0, skipped_irrelevant=0, sent_to_model=0, kept=0, bad_reply=0)
    for path in files:
        msgs = parsers.parse_any(path, date_order); src = "telegram" if Path(path).suffix.lower() == ".json" else "whatsapp"
        stats["messages"] += len(msgs)
        alias = (chat_names or {}).get(path) or Path(path).stem; chat_h = store.chat_hash(salt_, alias)
        for i, m in enumerate(msgs):
            sender_h = store.sender_hash(salt_, alias, m.sender)
            key = store.msg_key(salt_, m.ts, m.text, sender_h)
            if db is not None and store.seen(db, key): stats["skipped_seen"] += 1; continue
            if prefilter and not (extract.KEYWORDS.search(extract.fold(m.text)) or alias_rx.search(extract.fold(m.text))): stats["skipped_irrelevant"] += 1; continue
            if dry: stats["sent_to_model"] += 1; continue
            if limit and stats["sent_to_model"] >= limit: out("limit reached"); return stats
            if getattr(llm, "over_budget", lambda: False)(): out("cost cap reached - stopping (re-run to continue where it left off)"); return stats
            ctx = [extract.scrub(c.text) for c in msgs[max(0, i - 3):i] if 0 <= (m.ts - c.ts).total_seconds() <= 6 * 3600]
            raw = None
            for _ in range(2):                                          # one retry if the reply is not valid JSON
                try: raw = llm.complete(system, extract.user_prompt(ctx, m.ts, extract.scrub(m.text)), schema)
                except Exception as e: out(f"model error: {type(e).__name__}"); raw = None
                label = extract.validate(raw, m.ts, set(sid), default_site) if raw else None
                if raw and (label is not None or '"is_report"' in raw): break
            stats["sent_to_model"] += 1
            if raw is None: stats["bad_reply"] += 1; continue          # not marked seen, so a re-run retries it
            store.save(db, key, src, label, llm.model, extract.PROMPT_VERSION, chat_h=chat_h, sender_h=sender_h); stats["kept"] += bool(label)
            if stats["sent_to_model"] % 25 == 0: out(f"  {stats['sent_to_model']} messages sent, {stats['kept']} labels kept")
    return stats

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+"); ap.add_argument("--default-site", help="site id to use when a message names no place (e.g. a Bright-only group)")
    ap.add_argument("--model", help="model name (default: qwen2.5:7b-instruct locally, claude-haiku-5-5 with --backend anthropic)")
    ap.add_argument("--backend", choices=["ollama", "openai", "anthropic"], default="ollama",
                    help="ollama/openai = a model on this machine (default). anthropic = the Claude API: the SCRUBBED message text is sent to Anthropic; key from the ANTHROPIC_API_KEY environment variable")
    ap.add_argument("--max-usd", type=float, default=2.0, help="with --backend anthropic: stop once the estimated cost reaches this (default 2.00)")
    ap.add_argument("--chat", help="name for this chat (any text; only a hash is stored). Needed to keep senders matched across re-exports with different file names. One file only.")
    ap.add_argument("--batch-size", type=int, default=20, help="with --backend anthropic: messages per API call (default 20)")
    ap.add_argument("--parallel", type=int, default=4, help="with --backend anthropic: calls running at once (default 4)")
    ap.add_argument("--base-url"); ap.add_argument("--date-order", choices=["auto", "dmy", "mdy"], default="auto")
    ap.add_argument("--limit", type=int, help="stop after this many messages go to the model (for a trial run: the first ones in the file)")
    ap.add_argument("--sample", type=int, help="with --backend anthropic: send only N messages spread evenly across the whole file (a representative trial)")
    ap.add_argument("--no-prefilter", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sites = load_sites()
    if a.default_site and a.default_site not in sites: sys.exit(f"unknown site {a.default_site}; have {list(sites)}")
    have = (ROOT / "data" / "chat_labels.sqlite").exists() and (ROOT / "data" / "chat_salt").exists()
    db = store.connect(ROOT / "data" / "chat_labels.sqlite") if (not a.dry_run or have) else None      # a dry run reads what is already done, and writes nothing new
    if a.backend == "anthropic":
        llm = None if a.dry_run else AnthropicLLM(a.model or "claude-haiku-5-5", a.max_usd)
        if llm: print("NOTE: --backend anthropic sends each message (after removing links, @mentions, phone numbers and emails) plus up to 2 earlier messages as context to the Claude API. "
                      "Names are not sent as a field, but a name written inside a message would be. Nothing but the labels is stored here.")
    else:
        llm = None if a.dry_run else LocalLLM(a.model or "qwen2.5:7b-instruct", a.backend, a.base_url)
    salt_ = store.salt(ROOT / "data" / "chat_salt") if (not a.dry_run or have) else "dry"
    (ROOT / "data").mkdir(exist_ok=True)
    if a.chat and len(a.files) > 1: sys.exit("--chat names one chat; pass one file, or leave it out to use each file's name")
    names = {a.files[0]: a.chat} if a.chat else None
    if a.backend == "anthropic":                                   # batched, parallel, resumable
        import signal, threading
        targets, c = batch.collect(a.files, salt_, sites, names, a.date_order, db, prefilter=not a.no_prefilter)
        if a.sample and a.sample < len(targets): step = len(targets) / a.sample; targets = [targets[int(i * step)] for i in range(a.sample)]
        if a.limit: targets = targets[:a.limit]
        est = batch.estimate(targets, a.batch_size, a.parallel)
        print(f"{c['messages']:,} messages read; {c['skipped_seen']:,} already done, {c['skipped_irrelevant']:,} skipped as not about flying; {est['messages']:,} to send in {est['calls']:,} calls of up to {a.batch_size}")
        print(f"estimate: ~{est['input_tokens']:,} input + {est['output_tokens']:,} output tokens, ~${est['usd']:.2f}, ~{est['minutes']:.0f} min with {a.parallel} calls at once (rough: refine after a trial)")
        if a.dry_run: sys.exit(0)
        stop = threading.Event(); signal.signal(signal.SIGTERM, lambda *_: stop.set())
        st = batch.run_batched(targets, llm, db, sites, a.default_site, a.batch_size, a.parallel, None, stop)
        print(", ".join(f"{k}={v}" for k, v in st.items())); print(llm.report())
        if st["stopped"] or st["capped"] or st["unprocessed"]: print("Not everything was finished. Run the SAME command again to continue where it left off.")
        sys.exit(0)
    s = run(a.files, llm, db, salt_, sites, chat_names={a.files[0]: a.chat} if a.chat else None, default_site=a.default_site, date_order=a.date_order, limit=a.limit, dry=a.dry_run, prefilter=not a.no_prefilter)
    print(("DRY RUN " if a.dry_run else "") + ", ".join(f"{k}={v}" for k, v in s.items()))
    if llm is not None and hasattr(llm, "report"): print(llm.report())
