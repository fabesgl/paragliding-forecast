"""Parse chat exports into (local_time, text) messages. Sender names are dropped here and never leave this function."""
import json, re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

@dataclass
class Message:
    ts: datetime          # local time as written in the export
    text: str

_HDR = re.compile(r"^[‎‏]?\[?(\d{1,2})[/.](\d{1,2})[/.](\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp]\.?[Mm]\.?)?)\]?\s*(?:-\s*)?(.*)$")
_SKIP = re.compile(r"(<Media omitted>|\b(image|video|sticker|audio|document|GIF|voice message) omitted\b|This message was deleted|You deleted this message|\(file attached\)|<This message was edited>|^null$)", re.I)

def _time(t):
    t = t.replace(" ", " ").replace(".", "").strip().upper()
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p", "%I:%M:%S%p", "%I:%M%p"):
        try: return datetime.strptime(t, fmt).time()
        except ValueError: pass
    raise ValueError("bad time")

def parse_whatsapp(path, date_order="auto"):
    """Handles the iOS '[25/11/2026, 14:03:21] Name: text' and Android '25/11/2026, 14:03 - Name: text' layouts."""
    raw = []                                   # (a, b, y, time, rest) in file order; multi-line messages are joined
    for line in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
        m = _HDR.match(line.replace(" ", " "))
        if m: raw.append([*m.groups(), []])
        elif raw: raw[-1][-1].append(line)
    if date_order == "auto":                    # a first number >12 means day-first; a second >12 means month-first; default day-first (Australia)
        date_order = "dmy"
        for a, b, *_ in raw:
            if int(a) > 12: date_order = "dmy"; break
            if int(b) > 12: date_order = "mdy"; break
    out = []
    for a, b, y, t, rest, more in raw:
        if ": " not in rest: continue           # system message (no sender), e.g. "X joined"
        text = "\n".join([rest.split(": ", 1)[1]] + more).strip()    # sender name is discarded here
        if not text or _SKIP.search(text): continue
        d, mo = (int(a), int(b)) if date_order == "dmy" else (int(b), int(a))
        y = int(y) + (2000 if int(y) < 100 else 0)
        try: out.append(Message(datetime.combine(datetime(y, mo, d).date(), _time(t)), _SKIP.sub("", text).strip()))
        except ValueError: continue
    return out

def parse_telegram(path):
    """Telegram Desktop 'Export chat history' as JSON (single chat, or a full export with chats.list)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    chats = data["chats"]["list"] if "chats" in data else [data]
    out = []
    for chat in chats:
        for m in chat.get("messages", []):
            if m.get("type") != "message": continue                   # skip service messages
            t = m.get("text", "")
            if isinstance(t, list): t = "".join(x if isinstance(x, str) else x.get("text", "") for x in t)
            t = t.strip()
            if not t or _SKIP.search(t): continue
            try: out.append(Message(datetime.fromisoformat(m["date"]), t))   # 'from' (the name) is never read
            except (KeyError, ValueError): continue
    return out

def parse_any(path, date_order="auto"):
    p = Path(path)
    head = p.read_text(encoding="utf-8-sig", errors="replace")[:200].lstrip()
    return parse_telegram(p) if (p.suffix.lower() == ".json" or head.startswith("{")) else parse_whatsapp(p, date_order)
