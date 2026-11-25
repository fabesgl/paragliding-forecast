"""Builds the single static dashboard page: phone-first, no JavaScript, no external files."""
import datetime as dt, html
from . import summary as S
from . import corrections as corr

E = html.escape
TONE = {"good": ("#639922", "#97c459", "#173404", "#c0dd97"), "marginal": ("#ef9f27", "#fac775", "#412402", "#fac775"),
        "poor": ("#e24b4a", "#f09595", "#501313", "#f7c1c1"), "n/a": ("#5f5e5a", "#8fa0b5", "#24334a", "#8fa0b5")}   # ring, text, pill bg, pill text
CSS = """
:root{color-scheme:dark;--bg:#0f1722;--card:#172233;--mu:#8fa0b5;--ln:#24334a;--tx:#e8edf3}
*{box-sizing:border-box}html{background:#0a111a}
body{margin:0 auto;max-width:460px;background:var(--bg);color:var(--tx);font:14px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;min-height:100vh;padding-bottom:calc(24px + env(safe-area-inset-bottom,0px))}
header{position:relative;height:104px;background:#13203a;overflow:hidden;padding-top:env(safe-area-inset-top,0px)}
header svg{position:absolute;left:0;bottom:0;width:100%;height:96px}
header div{position:relative;padding:16px}h1{margin:0;font-size:19px;font-weight:600}
.mu{color:var(--mu);font-size:12px}
#net{position:sticky;top:0;z-index:5;background:#412402;color:#fac775;font-size:12.5px;text-align:center;padding:calc(6px + env(safe-area-inset-top,0px)) 12px 6px}
.cd{background:var(--card);border-radius:14px;margin:10px 12px;padding:14px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}.top b{font-size:16px;font-weight:600}
.dials{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px;text-align:center}.dials svg{width:96px;height:96px;display:block;margin:0 auto}.dials div div{font-size:13px;font-weight:600}
.sum{margin:10px 0 4px;line-height:1.5;font-size:13px}.sum b{font-weight:600}
.pull{margin-top:8px;display:flex;gap:6px;align-items:flex-start}.pull svg{flex:none;margin-top:2px}
.strip{display:flex;gap:3px;height:34px;align-items:flex-end;margin-top:6px}.strip i{flex:1;border-radius:3px;min-height:3px}
.ticks{display:flex;justify-content:space-between;margin-top:3px}
h3{margin:16px 0 0;font-size:12px;font-weight:500;color:var(--mu)}
.row{display:grid;grid-template-columns:92px repeat(3,1fr);align-items:center;padding:6px 0;border-top:.5px solid var(--ln);font-size:13px}.row:first-of-type{border-top:0}
.cell{display:flex;align-items:center;gap:5px;white-space:nowrap}.cell svg{width:18px;height:18px;flex:none}.cell small{color:var(--mu);font-size:11px}
.ai{margin-top:14px;background:#1d2b40;border-radius:10px;padding:10px 12px;font-size:12.5px;line-height:1.5}.ai .mu{display:flex;align-items:center;gap:5px;margin-bottom:3px}.ai p{margin:4px 0 0}
details>summary{list-style:none;cursor:pointer}details>summary::-webkit-details-marker{display:none}
.day summary{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:2px 0}.day .nm{font-weight:600}.day .pills{display:flex;gap:6px;align-items:center;flex:none}
.pill{border-radius:6px;padding:3px 8px;font-size:12px;white-space:nowrap}.chev{width:16px;height:16px;transition:transform .15s}details[open]>summary .chev{transform:rotate(180deg)}
.day[open]>summary{margin-bottom:6px}.day .cd2{margin-top:6px}
.more{margin:10px 12px;background:var(--card);border-radius:14px;padding:12px 14px}.more summary{font-weight:600;display:flex;justify-content:space-between}
.more h4{margin:14px 0 4px;font-size:13px}.note{color:var(--mu);font-size:12px}.tbl{width:100%;border-collapse:collapse;font-size:12px}.tbl td,.tbl th{padding:4px 2px;border-top:.5px solid var(--ln);text-align:left;vertical-align:top}.tbl th{color:var(--mu);font-weight:500;border:0}
@media(max-width:400px){.row{grid-template-columns:84px repeat(3,1fr);font-size:12.5px}.cell{gap:3px}.cell svg{width:15px;height:15px}.cd{margin:10px 8px;padding:12px}.more{margin:10px 8px}}
@media(min-width:900px){body{max-width:1300px;font-size:15px}header{height:120px}header div{padding:22px 24px}h1{font-size:22px}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:0 4px;align-items:start;padding:0 8px}.dials svg{width:124px;height:124px}.dials div div{font-size:14px}
.sum{font-size:14px}.row{grid-template-columns:108px repeat(3,1fr);font-size:14px;padding:7px 0}.cell svg{width:20px;height:20px}.strip{height:42px}.more{margin:10px 20px}}
@media(min-width:1200px){body{max-width:1500px}.grid{grid-template-columns:repeat(3,1fr)}.row{grid-template-columns:96px repeat(3,1fr);font-size:13.5px}.dials svg{width:112px;height:112px}}
.warn{margin:10px 12px;padding:8px 12px;border-radius:10px;background:#412402;color:#fac775;font-size:12.5px}
.lc{color:#fac775;font-size:11.5px;margin-top:2px}.cd2>.lc{margin:0 0 6px}details[open]>summary .lc{display:none}
.row>*{min-width:0}.tbl small{display:block;color:var(--mu)}.on{background:#173404}.off{background:#412402}
"""
SPARK = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7z"/></svg>'
DOWN = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#8fa0b5" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 7l10 10M17 8v9H8"/></svg>'
CHEV = '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="#8fa0b5" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>'
MOUNTAIN = ('<svg viewBox="0 0 360 96" preserveAspectRatio="none" aria-hidden="true"><path d="M0 96L0 70L40 52L85 64L130 34L170 50L205 28L250 58L300 44L360 62L360 96Z" fill="#1c2c47"/>'
            '<path d="M0 96L0 82L60 70L110 78L160 60L220 74L280 66L360 80L360 96Z" fill="#0f1722"/></svg>')

def dial(p, name):
    lab = S.label(p); ring, txt = TONE[lab][0], TONE[lab][1]; c = 251.3
    val = "n/a" if p is None else f"{p}%"
    return (f'<div><svg viewBox="0 0 100 100" role="img" aria-label="{name} {val} {lab}"><circle cx="50" cy="50" r="40" fill="none" stroke="#24334a" stroke-width="9"/>'
            f'<circle cx="50" cy="50" r="40" fill="none" stroke="{ring}" stroke-width="9" stroke-linecap="round" stroke-dasharray="{(p or 0) / 100 * c:.0f} {c:.0f}" transform="rotate(-90 50 50)"/>'
            f'<text x="50" y="52" text-anchor="middle" font-size="22" font-weight="600" fill="#e8edf3">{val}</text><text x="50" y="68" text-anchor="middle" font-size="11" fill="#8fa0b5">{name}</text></svg>'
            f'<div style="color:{txt}">{lab.capitalize()}</div></div>')

BAR = {"good": "#639922", "marginal": "#ba7517"}           # hourly-strip bar colours; anything else is grey

def strip(hourly, key="xc", name="XC", score=None):
    bars = "".join(f'<i title="{S._h(x["h"])} {x.get(key)}%" style="height:{max(8, x.get(key) or 0)}%;background:{BAR.get(S.label(x.get(key) or 0), "#5f5e5a")}"></i>' for x in hourly)
    return f'<h3><span style="color:{TONE[S.label(score)][1]}">{name}</span> window</h3><div class="strip" role="img" aria-label="Hourly {name} score, 9am to 6pm">{bars}</div><div class="ticks mu"><span>9am</span><span>12</span><span>3pm</span><span>6pm</span></div>'

def arrow(deg, kmh):
    col = "#97c459" if kmh < 20 else "#ef9f27" if kmh < 33 else "#e24b4a"
    return (f'<svg viewBox="0 0 24 24" style="transform:rotate({(deg + 180) % 360}deg);color:{col}" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20V5M6 11l6-6 6 6"/></svg>')

def ladder(rows, launch_m):
    out = ['<h3>Wind by height (km/h)</h3><div role="table"><div class="row mu"><span>Height</span><span>11am</span><span>1pm</span><span>3pm</span></div>']; partial = False
    for r in reversed(rows):
        launch = r["label"].startswith("Launch")
        name = f"Launch {launch_m} m" if launch else r["label"].lstrip("~") + (" *" if r["partial"] else "")
        partial |= r["partial"]
        cells = "".join('<span class="cell mu">n/a</span>' if c is None else f'<span class="cell">{arrow(c["deg"], c["kmh"])}{E(c["compass"])} <b>{c["kmh"]}</b></span>' for c in r["cells"])
        out.append(f'<div class="row"><span class="mu">{E(name)}</span>{cells}</div>')
    out.append("</div>" + ('<div class="note">* some models lack this level; the others are averaged</div>' if partial else ""))
    return "".join(out)

def ai_box(a, status):
    if status in ("off", "no key"): return ""
    if not a: return f'<div class="ai"><div class="mu">{SPARK} AI second opinion</div>Not available this update.</div>'
    tag = " · ".join(f'<span style="color:{TONE[a[k]][1]}">{n} {a[k]}</span>' for k, n in (("xc_call", "XC"), ("acro_call", "Acro")))
    return f'<div class="ai"><div class="mu">{SPARK} AI second opinion</div>{tag}<p>{E(a["reasoning"])}</p></div>'

def card_body(d, cfg, ai_status):
    pull = S.pulled_text(d)
    return (f'<div class="dials">{dial(d["xc"], "XC")}{dial(d["acro"], "Acro")}</div>'
            f'<p class="sum"><b style="color:{TONE[S.label(d["xc"])][1]}">XC:</b> {E(S.xc_text(d))}</p>'
            f'<p class="sum" style="margin-top:0"><b style="color:{TONE[S.label(d["acro"])][1]}">Acro:</b> {E(S.acro_text(d))}</p>'
            f'<div class="pull mu">{DOWN}<span>{("Pulled down by: " + E(pull)) if pull else "Nothing is pulling it down"}</span></div>'
            f'{strip(d["hourly"], "xc", "XC", d["xc"])}{strip(d["hourly"], "acro", "Acro", d["acro"]) if any(x.get("acro") is not None for x in d["hourly"]) else ""}{ladder(d["winds"], cfg["launch_height_m"])}{ai_box(d.get("ai"), ai_status)}')

def pill(n, p):
    lab = S.label(p); return f'<span class="pill" style="background:{TONE[lab][2]};color:{TONE[lab][3]}">{n} {"n/a" if p is None else f"{p}%"}</span>'

def day_name(d, today):
    x = dt.date.fromisoformat(d["date"]); tag = " · today" if x == today else ""
    return x.strftime("%A %-d %b"), tag

def corrections_block(state, cfg, calib=None):
    names = {"gfs_seamless": "GFS", "ecmwf_ifs025": "ECMWF", "icon_seamless": "ICON"}
    units = {v: u for v, (_, _, u) in corr.VARS.items()}
    labels = {"temperature_2m": "Temperature", "dew_point_2m": "Dew point", "wind_speed_10m": "Wind speed", "wind_gusts_10m": "Gusts", "pressure_msl": "Pressure"}
    c = corr.summary(state); days = max([b["n_days"] for b in state["bins"].values()], default=0)
    out = [f'<p class="note">{c["active"]} in use, {c["validating"]} being checked, {c["learning"]} still learning. A correction needs {corr.MIN_DAYS} days of data, then {corr.MIN_EVAL} checked days with at least {round(100 * corr.MIN_GAIN)}% less error. Most days of data so far: {days}.</p>']
    for k, p in [(k, p) for k, p in cfg["points"].items() if p.get("bom_station")]:
        rows = []
        for var in corr.VARS:
            cells = []
            for m in names:
                b = state["bins"].get(corr.bin_key(m, k, var, "all"))
                if b is None: cells.append('<td><small>waiting for data</small></td>'); continue
                st = corr.status(b); g = corr.gain(b)
                val = f'{b["corr"]:+.1f} {units[var]}' if b["kind"] == "add" else f'x{b["corr"]:.2f}'
                note = (f'{round(100 * g):+d}% error, in use' if st == "active" else f'no gain ({round(100 * g):+d}%), not used' if st == "off" else
                        f'checking {b["eval_days"]}/{corr.MIN_EVAL}' if st == "validating" else f'learning {b["n_days"]}/{corr.MIN_DAYS} d')
                cells.append(f'<td class="{"on" if st == "active" else "off" if st == "off" else ""}">{val}<small>{note}</small></td>')
            rows.append(f'<tr><td>{labels[var]}</td>{"".join(cells)}</tr>')
        sect = sorted({kk.split("|")[3] for kk, b in state["bins"].items() if kk.split("|")[1] == k and kk.split("|")[3] != "all" and corr.status(b) == "active"})
        out.append(f'<h4>{E(p.get("note") or k.replace("_", " ").title())} (BOM {p["bom_station"]})</h4><table class="tbl"><tr><th></th>' + "".join(f"<th>{n}</th>" for n in names.values()) + "</tr>" + "".join(rows) + "</table>" +
                   (f'<p class="note">Direction-specific corrections in use for: {", ".join(sect)}.</p>' if sect else ""))
    if calib:
        k = calib.get("k"); n = calib.get("n_days", 0)
        out.append(f'<h4>Valley wind estimate</h4><p class="note">The scoring assumes {calib.get("current_setting")} km/h of valley wind per 1 m/s of thermal strength (a default). '
                   f'Checking it against {E(str(calib.get("station")))} ({E(calib.get("station_note", "")[:60])}): {n} of {calib.get("min_days", 30)} usable dry days collected'
                   + (f'; so far the observed figure is {k} km/h per m/s (R² {calib.get("r2")}). Nothing is changed automatically.' if k is not None and calib.get("ready") else ". Nothing is changed automatically.") + '</p>')
    out.append('<p class="note">Mystic has no weather station, so nothing is corrected at the launch point yet. Tables show observed minus forecast at the nearest BOM stations, 09:00-17:00.</p>')
    return "".join(out)

JS = """<script>
if("serviceWorker"in navigator)addEventListener("load",function(){navigator.serviceWorker.register("sw.js").catch(function(){})});
(function(){var g=(+document.querySelector('meta[name=generated]').content)*1e3,n=document.getElementById("net"),fc=false;if(!g)return;
function age(){var h=(Date.now()-g)/36e5;return h<1?"under an hour":h<36?Math.round(h)+" h":Math.round(h/24)+" days"}
function f(){var off=!navigator.onLine||fc,old=(Date.now()-g)/36e5>14;if(off||old){n.hidden=false;n.textContent=(off?"Offline. ":"")+"This forecast is "+age()+" old."}else n.hidden=true}
function chk(){fetch("forecast.json",{cache:"no-store"}).then(function(r){fc=!!r.headers.get("x-sw-cache");f()}).catch(function(){fc=true;f()})}
addEventListener("online",chk);addEventListener("offline",chk);chk();f();
document.addEventListener("visibilitychange",function(){if(document.hidden||!navigator.onLine||Date.now()-g<36e5)return;
try{var t=+sessionStorage.getItem("r")||0;if(Date.now()-t<18e5)return;sessionStorage.setItem("r",Date.now())}catch(e){}location.reload()})})();
</script>"""

OPEN_DAYS = 3          # days shown as open cards; later days are collapsed rows marked lower confidence

def render_page(days, cfg, local_now, today, cstate, runs, ai_status, track_rows, generated_utc=None, calib=None, failed_models=()):
    faces = {0: "N", 90: "E", 180: "S", 270: "W"}.get(cfg.get("launch_facing_deg", 0), "N")
    upd = local_now.strftime("%-I:%M%p %a").replace("AM", "am").replace("PM", "pm")
    names = {"ecmwf_ifs025": "ECMWF", "ncep_gfs025": "GFS", "dwd_icon": "ICON"}
    runs_txt = ", ".join(f"{names[m]} {v['run_init_utc'][11:13]}Z" for m, v in runs.items() if m in names) or "unknown"
    cards = []
    nm_ = {"gfs_seamless": "GFS", "ecmwf_ifs025": "ECMWF", "icon_seamless": "ICON", "bom_access_global": "BOM ACCESS-G"}
    warn = f'<div class="warn">{E(", ".join(nm_.get(m, m) for m in failed_models))} unavailable this update; scores use the other models.</div>' if failed_models else ""
    for i, d in enumerate(days):
        nm, tag = day_name(d, today)
        if i < OPEN_DAYS:
            cards.append(f'<section class="cd"><div class="top"><b>{nm}</b><span class="mu">{E(S.agreement(d))}{tag}</span></div>{card_body(d, cfg, ai_status)}</section>')
        else:
            cards.append(f'<details class="cd day"><summary><div><div class="nm">{nm}</div><div class="mu">{E(S.headline(d))}</div><div class="lc">Lower confidence: further out</div></div>'
                         f'<div class="pills">{pill("XC", d["xc"])}{pill("Acro", d["acro"])}{CHEV}</div></summary><div class="cd2"><div class="lc">Lower confidence: forecasts get less reliable further out</div>{card_body(d, cfg, ai_status)}</div></details>')
    track = ('<table class="tbl"><tr><th>Day</th><th>XC</th><th>Acro</th><th>Lead</th></tr>' + "".join(track_rows) + "</table>") if track_rows else '<p class="note">Predictions are logged every update. Outcomes will be compared here once flight data or pilot notes are added.</p>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0f1722"><meta name="color-scheme" content="dark"><meta name="generated" content="{int(generated_utc.timestamp()) if generated_utc else 0}">
<link rel="manifest" href="manifest.webmanifest"><link rel="icon" type="image/png" sizes="32x32" href="icons/favicon-32.png"><link rel="apple-touch-icon" href="icons/apple-touch-icon.png">
<meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Mystic"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>Mystic forecast</title><style>{CSS}</style></head><body>
<div id="net" role="status" hidden></div>
<header>{MOUNTAIN}<div><h1>Mystic, Bright</h1><div class="mu">Updated {upd} · {cfg["launch_height_m"]} m · faces {faces}</div></div></header>
{warn}<div class="grid">{"".join(cards)}</div>
<details class="more"><summary>Forecast corrections {CHEV}</summary>{corrections_block(cstate, cfg, calib)}</details>
<details class="more"><summary>Track record {CHEV}</summary>{track}</details>
<details class="more"><summary>How the scores work {CHEV}</summary><p class="note">Scores come from hand-written rules, not yet learned from flights or calibrated. Each rule scores 0-100% smoothly and the day's score is set by the weakest rule. Good is {S.GOOD_MIN}% or more, marginal {S.MARGINAL_MIN}-{S.GOOD_MIN - 1}%, poor under {S.MARGINAL_MIN}%. Thermal strength is an estimate from sunshine and boundary-layer depth. The hourly strips show the XC and acro scores for each hour. Days 4-6 are marked lower confidence because forecasts get less reliable further out. Averages of {len(days[0]["models"]) if days else 0} models; model runs: {E(runs_txt)}. Forecast data: Open-Meteo. The AI note is experimental and never changes the scores.</p></details>
{JS}</body></html>"""
