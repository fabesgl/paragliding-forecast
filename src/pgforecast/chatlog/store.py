"""Local SQLite store for chat labels. It has no column that can hold message text or a name."""
import hashlib, secrets, sqlite3
from datetime import datetime, UTC
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_label (
  id INTEGER PRIMARY KEY, msg_key TEXT UNIQUE,        -- msg_key: salted hash, only used to skip messages already imported
  source TEXT,                                         -- 'whatsapp' or 'telegram'
  site_id TEXT, date TEXT, date_source TEXT,           -- date_source: 'stated' (in the message) or 'message' (day it was written)
  flown TEXT, wind_speed TEXT, wind_dir TEXT, turbulence TEXT, thermals TEXT, restitution TEXT,
  rating INTEGER, llm_confidence REAL,
  evidence TEXT DEFAULT 'chat_llm',                    -- how we know: 'chat_llm' is weaker than a flight track
  confidence_tier TEXT DEFAULT 'low',
  weight REAL,                                         -- training weight; flight tracks get 1.0
  model TEXT, prompt_version TEXT, imported_utc TEXT);
CREATE TABLE IF NOT EXISTS chat_seen (msg_key TEXT PRIMARY KEY, kept INTEGER);   -- messages already processed (resume support)
CREATE INDEX IF NOT EXISTS chat_label_day ON chat_label(site_id, date);
"""
CHAT_WEIGHT = 0.3     # chat reports count about a third as much as a flight track in training

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30); db.executescript(SCHEMA); return db

def salt(path):
    p = Path(path)
    if not p.exists(): p.write_text(secrets.token_hex(16)); p.chmod(0o600)
    return p.read_text().strip()

def msg_key(salt_, ts, text):
    return hashlib.sha256(f"{salt_}|{ts.isoformat()}|{text}".encode()).hexdigest()[:24]

def seen(db, key): return db.execute("SELECT 1 FROM chat_seen WHERE msg_key=?", (key,)).fetchone() is not None

def save(db, key, source, label, model, prompt_version, weight=CHAT_WEIGHT):
    if label:
        db.execute("""INSERT OR IGNORE INTO chat_label (msg_key,source,site_id,date,date_source,flown,wind_speed,wind_dir,turbulence,thermals,
                      restitution,rating,llm_confidence,weight,model,prompt_version,imported_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (key, source, label["site_id"], label["date"], label["date_source"], label["flown"], label["wind_speed"], label["wind_dir"],
                    label["turbulence"], label["thermals"], label["restitution"], label["rating"], label["llm_confidence"], weight, model,
                    prompt_version, datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")))
    db.execute("INSERT OR REPLACE INTO chat_seen VALUES (?,?)", (key, 1 if label else 0)); db.commit()
