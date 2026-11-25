"""Turn one chat message into a small label record. Only enum values, a date, and numbers can come out."""
import json, re, unicodedata
from datetime import date, timedelta

PROMPT_VERSION = "chat-v2"
SPEED = ["calm", "light", "moderate", "strong", "unknown"]
DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW", "unknown"]
LEVEL4 = ["none", "light", "moderate", "strong", "unknown"]
THERM = ["none", "weak", "moderate", "strong", "unknown"]
RESTIT = ["none", "weak", "good", "unknown"]

def fold(t):
    """Lower-case and strip accents, so 'Organyà', 'organya' and 'ORGANYÀ' all match, and 'térmicas' = 'termicas'."""
    return "".join(c for c in unicodedata.normalize("NFKD", t.casefold()) if not unicodedata.combining(c))

# Words that suggest a message might be about flying. All accent-free (text is folded first). English, Spanish, Catalan.
_EN = r"fly|flew|flown|flying|flight|launch\w*|land\w*|wind\w*|thermal\w*|lift|sink|rotor|turbulen\w*|bump\w*|smooth|glass\w*|soar\w*|xc|cloud\s?base|restitution|epic|sled|blown out|cancel\w*|booming|gusty|gust\w*|nuking|cross.?country|ridge|hike\w*|shuttle"
_ES = r"volar|volamos|volaron|volado|volamos|vuelo|vuelos|despeg\w*|aterriz\w*|termic\w*|ascendenc\w*|ascendent\w*|viento|calma|brisa|turbulenc\w*|turbulent\w*|restitucion|base de nubes|cancelad\w*|rachas?|racheado|sin viento"
_CA = r"volat|volem|vols?|enlair\w*|aterrat\w*|termiques|vent|turbulent\w*|restitucio|base de nuvols|cancel\w*lat|ratxes?|ascendencies|sense vent|volarem|he volat|hem volat"
KEYWORDS = re.compile(rf"\b({_EN}|{_ES}|{_CA})\b", re.I)    # run on fold(text)

# Words that make a message LIKELY to be a report of a past day (used only to pick a more informative trial sample, never to decide what is kept).
REPORT_CUES = re.compile(r"\b(flew|flown|launched|landed|yesterday|last (weekend|saturday|sunday)|cloud ?base|xc|km flight|(was|were) (a |an )?(epic|great|good|amazing|awesome|sick|fun|rowdy|bumpy|smooth|strong|light|booming|dead|blown|cooking|glass)"
                         r"|hours? (in the air|airtime)|volamos|vam volar|he volat|hem volat|ayer|ahir)\b", re.I)

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
Messages may be in English, Spanish or Catalan (or a mix): understand them, but answer in the English values below. Resolve relative days (yesterday/ayer/ahir, today/hoy/avui, the day before yesterday/anteayer/abans d'ahir, weekday names such as sabado/dissabte) from the message date.
Output ONE JSON object and nothing else. Never output names, quotes or any message text: only the allowed values.
Fields:
- is_report: true only if the message reports how a past day (today, yesterday, a named weekday) was for flying or not flying. Questions, forecasts, plans, banter and 'will fly tomorrow' are false.
- site: one of [{names}], or 'other' if it is a different place, or 'unknown' if not stated.
- date: the day being reported as YYYY-MM-DD, worked out from the message date given. null if unclear.
- flown: 'yes' if the writer or the group flew, 'no' if it was not flown (too windy, rain, cancelled), else 'unknown'.
- wind_speed: calm, light, moderate, strong or unknown. wind_dir: compass point the wind came from, or unknown.
- turbulence: none, light, moderate, strong, unknown. thermals: none, weak, moderate, strong, unknown.
- restitution: evening lift when slopes release stored heat late in the day: none, weak, good, unknown.
- rating: 1 (awful) to 5 (epic) for the flying quality of the day (convert other scales, e.g. 8/10 -> 4), or null if not stated or implied.
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


# ---- batches: about 20 numbered messages per call ---------------------------------------------------------------------------------------
def batch_schema(site_ids):
    """Compact reply: ids that are not flying reports go in a short list; only real reports get a full entry (cheaper and faster than 20 full entries)."""
    one = schema(site_ids); props = {k: v for k, v in one["properties"].items() if k != "is_report"}; props["id"] = {"type": "integer"}
    req = ["id"] + [k for k in one["required"] if k != "is_report"]
    return {"type": "object", "additionalProperties": False, "required": ["reports", "not_reports"],
            "properties": {"reports": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": req, "properties": props}},
                           "not_reports": {"type": "array", "items": {"type": "integer"}}}}

def batch_system_prompt(sites):
    names = "; ".join(f"{sid} = {', '.join(a)}" for sid, a in sites.items())
    return f"""You label paragliding chat messages. You receive several numbered messages; the 'ctx' lines under a message are earlier messages shown only for context and are never labelled.
Messages may be in English, Spanish or Catalan (or a mix): understand them, but answer in the English values below. Resolve relative days (yesterday/ayer/ahir, today/hoy/avui, the day before yesterday/anteayer/abans d'ahir, weekday names such as sabado/dissabte) from each message's own date.
Decide for EACH numbered message. A message is a REPORT only if it says how a past day (today, yesterday, a named weekday) was for flying or not flying. Questions, forecasts, plans, banter and 'will fly tomorrow' are NOT reports.
Put the id of every message that is not a report in `not_reports`. Add a full entry to `reports` for every report. Every id must appear exactly once. Never output names, quotes or any message text: only ids and the allowed values.
Fields of a report entry:
- site: one of [{names}], or 'other' if it is a different place, or 'unknown' if not stated.
- date: the day being reported as YYYY-MM-DD, worked out from the message date. null if unclear.
- flown: 'yes' if the writer or the group flew, 'no' if it was not flown (too windy, rain, cancelled), else 'unknown'.
- wind_speed: calm, light, moderate, strong or unknown. wind_dir: compass point the wind came from, or unknown.
- turbulence: none, light, moderate, strong, unknown. thermals: none, weak, moderate, strong, unknown.
- restitution: evening lift when slopes release stored heat late in the day: none, weak, good, unknown.
- rating: 1 (awful) to 5 (epic) for the flying quality of the day (convert other scales, e.g. 8/10 -> 4), or null if not stated or implied.
- confidence: 0 to 1, how sure you are of these labels."""

def batch_user_prompt(items):
    """items: list of dict(id, ts, text, ctx) with text already scrubbed."""
    out = []
    for it in items:
        out.append(f"#{it['id']} | {it['ts']:%Y-%m-%d %a %H:%M}")
        out += [f"ctx: {c}" for c in it["ctx"]]
        out.append(f"msg: {it['text']}")
    return "\n".join(out)

def validate_batch(raw, items, site_ids, default_site, aliases=None):
    """Returns {id: label dict, or None for 'not a usable report'} for every id the model answered properly. Ids it skipped or botched are absent
    (the caller retries them once, then leaves them for the next run)."""
    try: j = json.loads(raw)
    except (TypeError, ValueError): return {}
    if not isinstance(j, dict): return {}
    ids = {it["id"]: it for it in items}; got = {}
    for i in j.get("not_reports") or []:
        if isinstance(i, int) and i in ids and i not in got: got[i] = None
    amap = {fold(a): sid for sid, al in (aliases or {}).items() for a in al}          # the model sometimes answers with a place word ('mystic') instead of the site id
    for e in j.get("reports") or []:
        if not isinstance(e, dict) or e.get("id") not in ids or e["id"] in got: continue
        if isinstance(e.get("site"), str) and e["site"] not in site_ids and fold(e["site"]) in amap: e = {**e, "site": amap[fold(e["site"])]}
        got[e["id"]] = validate(json.dumps({**{k: v for k, v in e.items() if k != "id"}, "is_report": True}), ids[e["id"]]["ts"], site_ids, default_site)
    return got
