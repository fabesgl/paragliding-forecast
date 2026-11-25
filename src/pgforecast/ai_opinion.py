"""'AI second opinion': a language model reads one day's forecast data and gives its own XC and acro call.
It only ever ADDS a second opinion; it never touches the rule scores. Any failure returns None so the daily run carries on."""
import json, os, time, requests
from .rules_config import limits as _limits

MODEL = "claude-haiku-5-5"
URL = "https://api.anthropic.com/v1/messages"
CALLS = ["good", "marginal", "poor"]
TOOL = {"name": "give_opinion", "description": "Record your own flying call for the day.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["xc_call", "acro_call", "reasoning"],
                         "properties": {"xc_call": {"enum": CALLS}, "acro_call": {"enum": CALLS},
                                        "reasoning": {"type": "string", "maxLength": 450}}}}

NAMES = {"wind": "launch wind", "gust": "gusts", "w850": "850 hPa wind"}

def hard_limits(day, rules):
    """The rule limits the day breaches, per discipline: {'xc': [...], 'acro': [...]}. Uses the same model-averaged numbers the rules use."""
    LIMITS = _limits(rules); out = {}
    for d in ("xc", "acro"):
        rules = {r.get("key"): r.get("num") for r in day.get(f"{d}_rules", [])}; hit = []
        for k, lim in LIMITS[d].items():
            if rules.get(k) is not None and rules[k] > lim: hit.append(f"{NAMES[k]} {rules[k]:.0f} km/h is over the {lim} km/h limit")
        if rules.get("rain") is not None and rules["rain"] >= LIMITS["rain_mm"]: hit.append(f"rain is forecast in the flying window ({rules['rain']:.1f} mm)")
        out[d] = hit
    return out

def enforce(res, breaches):
    """Hard limits are not up to the model: a breached discipline is always 'poor'. If that changes the model's call, the reason is added
    to its text and res['forced'] lists which disciplines were changed ('xc', 'acro')."""
    forced = []
    for d, name in (("xc", "XC"), ("acro", "Acro")):
        if breaches[d] and res[f"{d}_call"] != "poor":
            res[f"{d}_call"] = "poor"; forced.append(d)
            res["reasoning"] = res["reasoning"].rstrip() + f" ({name} is poor by the hard limits: {breaches[d][0]}.)"
    res["forced"] = " ".join(forced)
    return res

def system_prompt(cfg):
    notes = (cfg.get("notes") or "").strip(); NOTES = ("LOCAL KNOWLEDGE from experienced pilots (use it to judge the data; the hard limits above still win):\n" + notes + "\n") if notes else ""
    LIMITS = _limits(cfg["rules"]); CEIL = f"{cfg['rules']['xc']['ceiling']['min_asl_m']:,.0f}"
    return f"""You are an experienced paragliding forecaster giving a second opinion for {cfg['name']}.
Site facts: the launch faces north at {cfg['launch_height_m']} m ASL, {cfg['launch_above_landing_m']} m above the landing. It is launchable in northerly valley wind
(roughly NW to NE); strong synoptic wind from S, SE or SW makes it unflyable. Times are local. Speeds are km/h, heights metres.
You receive one day of model forecast data (average of several models), hand-written rule scores with the rule that limits each, a wind-by-height table, and the bias corrections currently in use.
HARD LIMITS (these override your judgement): rate a discipline "poor" if ANY of these hold for it.
XC: rain forecast in the flying window ({LIMITS['rain_mm']} mm or more); launch wind over {LIMITS['xc']['wind']} km/h; gusts over {LIMITS['xc']['gust']} km/h; 850 hPa wind over {LIMITS['xc']['w850']} km/h.
Acro: rain forecast in the flying window ({LIMITS['rain_mm']} mm or more); launch wind over {LIMITS['acro']['wind']} km/h; gusts over {LIMITS['acro']['gust']} km/h; 850 hPa wind over {LIMITS['acro']['w850']} km/h.
The data lists any breaches under hard_limit_breaches. Inside these limits you are free to disagree with the rule scores, either way.
{NOTES}Give your OWN judgement from the data. The rule scores are one input and are untested; disagree with them when the data says so.
Calls: XC (cross-country) good = a realistic chance of long flights (usable thermals above ~{CEIL} m ASL, manageable wind and gusts, no rain);
marginal = flyable but with a clear problem; poor = unlikely. Acro: good = smooth, light and steady air (low wind, low gusts, little shear), marginal = some turbulence risk, poor = rough or unflyable.
Reasoning: at most 2 short sentences (under 60 words in total) naming the deciding factors, and say so if the models disagree. Use only the data given; do not invent numbers."""

def enabled(env=os.environ):
    v = (env.get("AI_OPINION_ENABLED") or "").strip().lower()
    return bool(env.get("ANTHROPIC_API_KEY")) and v not in ("false", "0", "off", "no", "disabled")

def day_payload(day, wind_rows, active_corrections, counts, rules):
    r = lambda x: None if x is None else round(x, 1)
    return {"date": day["date"], "rule_scores": {"xc_pct": day["xc"], "acro_pct": day["acro"], "xc_pct_range_across_models": day["xc_range"], "per_model": day["models"]},
            "key_numbers": {k: (r(v) if isinstance(v, float) else v) for k, v in day["key"].items()},
            "xc_rules": [{"rule": q["label"], "value": q["value"], "score_0_to_1": q["factor"]} for q in day["xc_rules"]],
            "acro_rules": [{"rule": q["label"], "value": q["value"], "score_0_to_1": q["factor"]} for q in day["acro_rules"]],
            "wind_by_height": [{"height": w["label"] + (f" ({w['height_m']} m)" if w["height_m"] else ""),
                                "at_11_13_15": [None if c is None else f"{c['compass']} {c['kmh']}" for c in w["cells"]], "only_some_models": w["partial"]} for w in wind_rows],
            "corrections_in_use": active_corrections or "none yet (still learning)", "correction_status_counts": counts,
            "hard_limit_breaches": hard_limits(day, rules) or None}

def _trim(t, n=450):
    """Cut at a sentence end if the text is too long, so it never stops mid-word."""
    if len(t) <= n: return t
    cut = t[:n]; k = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[:k + 1] if k > 150 else cut.rsplit(" ", 1)[0] + "…")

def ask(system, payload, key, session=requests, retries=2):
    """One call. Returns dict(xc_call, acro_call, reasoning), or raises PermissionError for a bad key, or returns None on other failures."""
    body = {"model": MODEL, "max_tokens": 500, "system": system, "tools": [TOOL], "tool_choice": {"type": "tool", "name": TOOL["name"]},
            "messages": [{"role": "user", "content": "Forecast data for the day (JSON):\n" + json.dumps(payload, separators=(",", ":"))}]}
    for attempt in range(retries + 1):
        try:
            r = session.post(URL, headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json=body, timeout=60)
        except requests.RequestException as e:
            print("AI opinion: network error", type(e).__name__); time.sleep(3 * (attempt + 1)); continue
        if r.status_code in (401, 403): raise PermissionError(f"HTTP {r.status_code}")
        if r.status_code == 429 or r.status_code >= 500:
            print("AI opinion: HTTP", r.status_code, "- retrying"); time.sleep(5 * (attempt + 1)); continue
        if r.status_code != 200:
            print("AI opinion: HTTP", r.status_code, r.text[:200].replace(key, "***")); return None
        for block in r.json().get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == TOOL["name"]:
                i = block["input"]
                if i.get("xc_call") in CALLS and i.get("acro_call") in CALLS and isinstance(i.get("reasoning"), str):
                    return dict(xc_call=i["xc_call"], acro_call=i["acro_call"], reasoning=_trim(" ".join(i["reasoning"].split())), forced="")
        print("AI opinion: reply had no valid opinion"); return None
    return None

def opinions(days, winds_by_date, active_corrections, counts, cfg, env=os.environ, session=requests):
    """Returns ({date: opinion}, status). Never raises."""
    if not enabled(env):
        return {}, ("off" if env.get("ANTHROPIC_API_KEY") else "no key")
    out, failed = {}, 0
    try:
        sysmsg = system_prompt(cfg)
        for d in days:
            try: res = ask(sysmsg, day_payload(d, winds_by_date.get(d["date"], []), active_corrections, counts, cfg["rules"]), env["ANTHROPIC_API_KEY"], session)
            except PermissionError as e: print("AI opinion: API key rejected", e, "- skipping the rest"); return out, "key rejected"
            if res: out[d["date"]] = enforce(res, hard_limits(d, cfg["rules"]))
            else: failed += 1
    except Exception as e:                                       # whatever happens, the run must go on
        print("AI opinion: unexpected error", type(e).__name__); return out, "error"
    return out, ("ok" if not failed else f"{failed} of {len(days)} failed")
