"""Turn one chat message into a small label record. Only enum values, a date, and numbers can come out."""
import json, re
from datetime import date, timedelta

PROMPT_VERSION = "chat-v1"
SPEED = ["calm", "light", "moderate", "strong", "unknown"]
DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW", "unknown"]
LEVEL4 = ["none", "light", "moderate", "strong", "unknown"]
THERM = ["none", "weak", "moderate", "strong", "unknown"]
RESTIT = ["none", "weak", "good", "unknown"]

KEYWORDS = re.compile(r"\b(fly|flew|flown|flying|flight|launch\w*|land\w*|wind\w*|thermal\w*|lift|sink|rotor|turbulen\w*|bump\w*|smooth|glass\w*|soar\w*|xc|cloud\s?base|restitution|epic|sled|blown out|cancel\w*|booming|booked|stuffed|dead|gusty|gust\w*|nuking|cross.?country|ridge|ridges|hike\w*|walked up|drive up|shuttle)\b", re.I)

def schema(site_ids):
    return {"type": "object", "additionalProperties": False,
        "required": ["is_report", "site", "date", "flown", "wind_speed", "wind_dir", "turbulence", "thermals", "restitution", "rating", "confidence"],
        "properties": {
            "is_report": {"type": "boolean"}, "site": {"enum": list(site_ids) + ["other", "unknown"]},
            "date": {"type": ["string", "null"]}, "flown": {"enum": ["yes", "no", "unknown"]},
            "wind_speed": {"enum": SPEED}, "wind_dir": {"enum": DIRS}, "turbulence": {"enum": LEVEL4},
            "thermals": {"enum": THERM}, "restitution": {"enum": RESTIT},
            "rating": {"type": ["integer", "null"], "minimum": 1, "maximum": 5}, "confidence": {"type": "number"}}}

def system_prompt(sites):
    names = "; ".join(f"{sid} = {', '.join(a)}" for sid, a in sites.items())
    return f"""You label paragliding chat messages. Read the LAST message only; earlier messages are context.
Output ONE JSON object and nothing else. Never output names, quotes or any message text: only the allowed values.
Fields:
- is_report: true only if the message reports how a past day (today, yesterday, a named weekday) was for flying or not flying. Questions, forecasts, plans, banter and 'will fly tomorrow' are false.
- site: one of [{names}], or 'other' if it is a different place, or 'unknown' if not stated.
- date: the day being reported as YYYY-MM-DD, worked out from the message date given. null if unclear.
- flown: 'yes' if the writer or the group flew, 'no' if it was not flown (too windy, rain, cancelled), else 'unknown'.
- wind_speed: calm, light, moderate, strong or unknown. wind_dir: compass point the wind came from, or unknown.
- turbulence: none, light, moderate, strong, unknown. thermals: none, weak, moderate, strong, unknown.
- restitution: evening lift when slopes release stored heat late in the day: none, weak, good, unknown.
- rating: 1 (awful) to 5 (epic) for the flying quality of the day, or null if not stated or implied.
- confidence: 0 to 1, how sure you are of these labels.
If is_report is false, set the other fields to unknown/null."""

def user_prompt(ctx, ts, text):
    c = "\n".join(f"- {t}" for t in ctx) or "- (none)"
    return f"Context (earlier messages):\n{c}\n\nMessage date: {ts:%Y-%m-%d} ({ts:%A}), time {ts:%H:%M}\nLast message: {text}"

_URL, _MENTION, _PHONE, _EMAIL = re.compile(r"https?://\S+|www\.\S+"), re.compile(r"@\w+"), re.compile(r"\+?\d[\d\s().-]{7,}\d"), re.compile(r"\S+@\S+\.\S+")
def scrub(t):
    """Remove links, @mentions, phone numbers and emails before the text reaches the model."""
    for rx, tok in ((_EMAIL, "[email]"), (_URL, "[link]"), (_MENTION, "[person]"), (_PHONE, "[number]")): t = rx.sub(tok, t)
    return re.sub(r"\s+", " ", t).strip()[:600]

def validate(raw, ts, site_ids, default_site):
    """Parse and strictly check the model reply. Returns a label dict, or None if it is not a usable report."""
    try: j = json.loads(raw)
    except (TypeError, ValueError): return None
    if not isinstance(j, dict) or j.get("is_report") is not True: return None
    pick = lambda k, allowed: j.get(k) if j.get(k) in allowed else "unknown"
    site = j.get("site")
    site = site if site in site_ids else (default_site if site == "unknown" and default_site else None)
    if site is None: return None                                   # other place, or no site and no default
    flown = pick("flown", ["yes", "no", "unknown"])
    if flown == "unknown" and not isinstance(j.get("rating"), int): return None   # nothing useful in it
    d, src = None, "message"
    try:
        dd = date.fromisoformat(str(j.get("date"))[:10])
        if ts.date() - timedelta(days=21) <= dd <= ts.date(): d, src = dd, "stated"
    except ValueError: pass
    if d is None: d = ts.date()                                    # fall back to the day it was written, flagged as such
    r = j.get("rating"); r = r if isinstance(r, int) and 1 <= r <= 5 else None
    c = j.get("confidence"); c = max(0.0, min(1.0, float(c))) if isinstance(c, (int, float)) else 0.5
    return dict(site_id=site, date=d.isoformat(), date_source=src, flown=flown, wind_speed=pick("wind_speed", SPEED), wind_dir=pick("wind_dir", DIRS),
                turbulence=pick("turbulence", LEVEL4), thermals=pick("thermals", THERM), restitution=pick("restitution", RESTIT), rating=r, llm_confidence=round(c, 2))
