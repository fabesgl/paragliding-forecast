"""Local SQLite store for chat labels. It has no column that can hold message text or a name; senders appear only as salted hashes."""
import hashlib, secrets, sqlite3
from datetime import datetime, UTC
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_label (
  id INTEGER PRIMARY KEY, msg_key TEXT UNIQUE,        -- msg_key: salted hash, only used to skip messages already imported
  source TEXT,                                         -- 'whatsapp' or 'telegram'
  chat_hash TEXT, sender_hash TEXT,                    -- anonymous ids: salted hashes of the chat alias and of the sender within that chat
  site_id TEXT, date TEXT, date_source TEXT,           -- date_source: 'stated' (in the message) or 'message' (day it was written)
  flown TEXT, wind_speed TEXT, wind_dir TEXT, turbulence TEXT, thermals TEXT, restitution TEXT,
  rating INTEGER, llm_confidence REAL,
  evidence TEXT DEFAULT 'chat_llm',                    -- how we know: 'chat_llm' is weaker than a flight track
  confidence_tier TEXT DEFAULT 'low',
  weight REAL,                                         -- training weight; flight tracks get 1.0
  model TEXT, prompt_version TEXT, imported_utc TEXT);
CREATE TABLE IF NOT EXISTS chat_seen (msg_key TEXT PRIMARY KEY, kept INTEGER);   -- messages already processed (resume support)
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
    for col in ("chat_hash", "sender_hash"):                     # databases made before sender ids existed
        if col not in have: db.execute(f"ALTER TABLE chat_label ADD COLUMN {col} TEXT")
    return db

def salt(path):
    p = Path(path)
    if not p.exists(): p.write_text(secrets.token_hex(16)); p.chmod(0o600)
    return p.read_text().strip()

def _h(*parts): return hashlib.sha256("|".join(parts).encode()).hexdigest()

def chat_hash(salt_, chat_alias): return _h(salt_, "chat", chat_alias.strip().casefold())[:12]

def sender_hash(salt_, chat_alias, sender):
    """Anonymous id for a person within one chat. Same person + same chat alias = same id; the name cannot be recovered without the salt."""
    return _h(salt_, "sender", chat_alias.strip().casefold(), sender.strip().casefold())[:16] if sender and sender.strip() else None

def msg_key(salt_, ts, text, sender_h=None):
    return _h(salt_, ts.isoformat(), sender_h or "", text)[:24]

def seen(db, key): return db.execute("SELECT 1 FROM chat_seen WHERE msg_key=?", (key,)).fetchone() is not None

def save(db, key, source, label, model, prompt_version, weight=CHAT_WEIGHT, chat_h=None, sender_h=None):
    if label:
        db.execute("""INSERT OR IGNORE INTO chat_label (msg_key,source,chat_hash,sender_hash,site_id,date,date_source,flown,wind_speed,wind_dir,turbulence,thermals,
                      restitution,rating,llm_confidence,weight,model,prompt_version,imported_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (key, source, chat_h, sender_h, label["site_id"], label["date"], label["date_source"], label["flown"], label["wind_speed"], label["wind_dir"],
                    label["turbulence"], label["thermals"], label["restitution"], label["rating"], label["llm_confidence"], weight, model,
                    prompt_version, datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")))
    db.execute("INSERT OR REPLACE INTO chat_seen VALUES (?,?)", (key, 1 if label else 0)); db.commit()
