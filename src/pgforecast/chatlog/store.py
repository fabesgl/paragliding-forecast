"""Local SQLite store for chat labels. It has no column that can hold message text or a name; senders appear only as salted hashes."""
import hashlib, secrets, sqlite3
from datetime import datetime, UTC
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_label (
  id INTEGER PRIMARY KEY, msg_key TEXT UNIQUE,        -- msg_key: salted hash, only used to skip messages already imported
  source TEXT,                                         -- 'whatsapp' or 'telegram'
  chat_hash TEXT, sender_hash TEXT,                    -- anonymous ids: salted hashes of the chat alias and of the sender within that chat
  site_id TEXT, kind TEXT, date TEXT, date_source TEXT,           -- date_source: 'stated' (in the message) or 'message' (day it was written)
  flown TEXT, wind_speed TEXT, wind_dir TEXT, turbulence TEXT, thermals TEXT, restitution TEXT,
  rating INTEGER, llm_confidence REAL,
  evidence TEXT DEFAULT 'chat_llm',                    -- how we know: 'chat_llm' is weaker than a flight track
  confidence_tier TEXT DEFAULT 'low',
  weight REAL,                                         -- training weight; flight tracks get 1.0
  model TEXT, prompt_version TEXT, imported_utc TEXT);
CREATE TABLE IF NOT EXISTS chat_seen (msg_key TEXT PRIMARY KEY, kept INTEGER, prompt_version TEXT);   -- messages already processed (resume support); redone when the labelling rules change
CREATE TABLE IF NOT EXISTS chat_day_activity (chat_hash TEXT, date TEXT, n_messages INTEGER, n_senders INTEGER, PRIMARY KEY (chat_hash, date));   -- weak signal: how busy the chat was that day (counts only)
CREATE INDEX IF NOT EXISTS chat_label_day ON chat_label(site_id, date);
-- one person reporting the same day several times shares ONE weight: effective_weight = weight / (their reports for that site and day)
CREATE VIEW IF NOT EXISTS chat_label_weighted AS
  SELECT *, weight * 1.0 / COUNT(*) OVER (PARTITION BY site_id, date, COALESCE(chat_hash,'') || COALESCE(sender_hash,'#' || id)) AS effective_weight
  FROM chat_label;
"""
CHAT_WEIGHT = 0.3     # chat reports count about a third as much as a flight track in training

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30); db.executescript(SCHEMA)
    have = {r[1] for r in db.execute("PRAGMA table_info(chat_label)")}
    for col in ("chat_hash", "sender_hash", "kind"):             # databases made before these columns existed
        if col not in have: db.execute(f"ALTER TABLE chat_label ADD COLUMN {col} TEXT")
    if "prompt_version" not in {r[1] for r in db.execute("PRAGMA table_info(chat_seen)")}: db.execute("ALTER TABLE chat_seen ADD COLUMN prompt_version TEXT")
    db.execute("UPDATE chat_label SET kind='past' WHERE kind IS NULL"); db.commit()
    return db

def salt(path):
    p = Path(path)
    if not p.exists(): p.write_text(secrets.token_hex(16)); p.chmod(0o600)
    return p.read_text().strip()

def _h(*parts): return hashlib.sha256("|".join(parts).encode()).hexdigest()

def chat_hash(salt_, chat_alias): return _h(salt_, "chat", chat_alias.strip().casefold())[:12]

def pilot_hash(salt_, name):
    """Anonymous id for a pilot across sources that name pilots outright (e.g. XContest). Same salt file as the chat importer; not chat-scoped."""
    return _h(salt_, "pilot", name.strip().casefold())[:16] if name and name.strip() else None

def sender_hash(salt_, chat_alias, sender):
    """Anonymous id for a person within one chat. Same person + same chat alias = same id; the name cannot be recovered without the salt."""
    return _h(salt_, "sender", chat_alias.strip().casefold(), sender.strip().casefold())[:16] if sender and sender.strip() else None

def msg_key(salt_, ts, text, sender_h=None):
    return _h(salt_, ts.isoformat(), sender_h or "", text)[:24]

def seen(db, key, version=None):
    """Already processed? With `version`, only counts if it was done under those labelling rules (older answers are redone)."""
    r = db.execute("SELECT prompt_version FROM chat_seen WHERE msg_key=?", (key,)).fetchone()
    return r is not None and (version is None or r[0] == version)

def save(db, key, source, label, model, prompt_version, weight=CHAT_WEIGHT, chat_h=None, sender_h=None, commit=True):
    if label:
        db.execute("""INSERT OR REPLACE INTO chat_label (msg_key,source,chat_hash,sender_hash,site_id,kind,date,date_source,flown,wind_speed,wind_dir,turbulence,thermals,
                      restitution,rating,llm_confidence,weight,model,prompt_version,imported_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (key, source, chat_h, sender_h, label["site_id"], label.get("kind", "past"), label["date"], label["date_source"], label["flown"], label["wind_speed"], label["wind_dir"],
                    label["turbulence"], label["thermals"], label["restitution"], label["rating"], label["llm_confidence"], weight, model,
                    prompt_version, datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")))
    else:
        db.execute("DELETE FROM chat_label WHERE msg_key=?", (key,))                         # a re-run under new rules may say 'not a report': drop the old label
    db.execute("INSERT OR REPLACE INTO chat_seen VALUES (?,?,?)", (key, 1 if label else 0, prompt_version))
    if commit: db.commit()

def save_activity(db, rows):
    """rows: {(chat_hash, 'YYYY-MM-DD'): (n_messages, n_senders)}. Keeps the larger count if a day was seen in two exports."""
    for (c, d), (n, k) in rows.items():
        db.execute("""INSERT INTO chat_day_activity VALUES (?,?,?,?) ON CONFLICT(chat_hash, date) DO UPDATE SET
                      n_messages=MAX(n_messages, excluded.n_messages), n_senders=MAX(n_senders, excluded.n_senders)""", (c, d, n, k))
    db.commit()
