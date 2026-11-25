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
from pgforecast.chatlog import extract, parsers, store
from pgforecast.chatlog.llm import LocalLLM

def load_sites():
    sites = {}
    for f in sorted((ROOT / "sites").glob("*.yaml")):
        c = yaml.safe_load(f.read_text()); sites[c["id"]] = c.get("chat_aliases") or [c["name"]]
    return sites

def run(files, llm, db, salt_, sites, source_hint=None, default_site=None, date_order="auto", limit=None, dry=False, out=print, prefilter=True):
    sid = list(sites); system = extract.system_prompt(sites); schema = extract.schema(sid)
    alias_rx = __import__("re").compile("|".join(__import__("re").escape(a) for al in sites.values() for a in al), __import__("re").I)
    stats = dict(messages=0, skipped_seen=0, skipped_irrelevant=0, sent_to_model=0, kept=0, bad_reply=0)
    for path in files:
        msgs = parsers.parse_any(path, date_order); src = "telegram" if Path(path).suffix.lower() == ".json" else "whatsapp"
        stats["messages"] += len(msgs)
        for i, m in enumerate(msgs):
            key = store.msg_key(salt_, m.ts, m.text)
            if db is not None and store.seen(db, key): stats["skipped_seen"] += 1; continue
            if prefilter and not (extract.KEYWORDS.search(m.text) or alias_rx.search(m.text)): stats["skipped_irrelevant"] += 1; continue
            if dry: stats["sent_to_model"] += 1; continue
            if limit and stats["sent_to_model"] >= limit: out("limit reached"); return stats
            ctx = [extract.scrub(c.text) for c in msgs[max(0, i - 3):i] if 0 <= (m.ts - c.ts).total_seconds() <= 6 * 3600]
            raw = None
            for _ in range(2):                                          # one retry if the reply is not valid JSON
                try: raw = llm.complete(system, extract.user_prompt(ctx, m.ts, extract.scrub(m.text)), schema)
                except Exception as e: out(f"model error: {type(e).__name__}"); raw = None
                label = extract.validate(raw, m.ts, set(sid), default_site) if raw else None
                if raw and (label is not None or '"is_report"' in raw): break
            stats["sent_to_model"] += 1
            if raw is None: stats["bad_reply"] += 1; continue          # not marked seen, so a re-run retries it
            store.save(db, key, src, label, llm.model, extract.PROMPT_VERSION); stats["kept"] += bool(label)
            if stats["sent_to_model"] % 25 == 0: out(f"  {stats['sent_to_model']} messages sent, {stats['kept']} labels kept")
    return stats

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+"); ap.add_argument("--default-site", help="site id to use when a message names no place (e.g. a Bright-only group)")
    ap.add_argument("--model", default="qwen2.5:7b-instruct"); ap.add_argument("--backend", choices=["ollama", "openai"], default="ollama")
    ap.add_argument("--base-url"); ap.add_argument("--date-order", choices=["auto", "dmy", "mdy"], default="auto")
    ap.add_argument("--limit", type=int, help="stop after this many messages go to the model (for a trial run)")
    ap.add_argument("--no-prefilter", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sites = load_sites()
    if a.default_site and a.default_site not in sites: sys.exit(f"unknown site {a.default_site}; have {list(sites)}")
    db = None if a.dry_run else store.connect(ROOT / "data" / "chat_labels.sqlite")
    llm = None if a.dry_run else LocalLLM(a.model, a.backend, a.base_url)
    salt_ = store.salt(ROOT / "data" / "chat_salt") if not a.dry_run else "dry"
    (ROOT / "data").mkdir(exist_ok=True)
    s = run(a.files, llm, db, salt_, sites, default_site=a.default_site, date_order=a.date_order, limit=a.limit, dry=a.dry_run, prefilter=not a.no_prefilter)
    print(("DRY RUN " if a.dry_run else "") + ", ".join(f"{k}={v}" for k, v in s.items()))
