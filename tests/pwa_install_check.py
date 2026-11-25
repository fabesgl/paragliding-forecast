"""Installability + offline test for the dashboard PWA, using Chromium with Android (Pixel 7) emulation.
Needs: pip install playwright; playwright install chromium   (kept out of requirements.txt: it is only for this check)
Usage: python tests/pwa_install_check.py [path-to-docs-folder]      (serves a copy locally, runs every check)
       python tests/pwa_install_check.py https://host/path/          (checks the live site: install + offline only)"""
import http.server, json, shutil, sys, tempfile, threading, time
from pathlib import Path
from playwright.sync_api import sync_playwright

LIVE = len(sys.argv) > 1 and sys.argv[1].startswith("http")
DOCS = Path("data/local_out/docs" if LIVE or len(sys.argv) < 2 else sys.argv[1]).resolve()
ROOT = Path(tempfile.mkdtemp()); SITE = ROOT / "paragliding-forecast"
if not LIVE: shutil.copytree(DOCS, SITE)   # served under a sub-path, like GitHub Pages
STATE = {"delay_index": 0.0}

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=str(ROOT), **k)
    def end_headers(self): self.send_header("Cache-Control", "max-age=600"); super().end_headers()     # what GitHub Pages sends
    def do_GET(self):
        if self.path.split("?")[0].endswith(("/paragliding-forecast/", "/index.html")): time.sleep(STATE["delay_index"])
        super().do_GET()
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = sys.argv[1] if LIVE else f"http://localhost:{srv.server_port}/paragliding-forecast/"
results = []
def check(name, ok, detail=""): results.append((name, bool(ok), detail)); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)

with sync_playwright() as p:
    # a persistent profile is needed: Chrome refuses to install from a private (incognito-style) context
    ctx = p.chromium.launch_persistent_context(tempfile.mkdtemp(), channel="chromium", **{k: v for k, v in p.devices["Pixel 7"].items() if k != "default_browser_type"}); page = ctx.new_page()
    page.add_init_script("window.__bip=null;addEventListener('beforeinstallprompt',e=>{e.preventDefault();window.__bip=true})")
    cdp = ctx.new_cdp_session(page); cdp.send("Page.enable")
    page.goto(URL); page.wait_for_load_state("networkidle")
    page.evaluate("navigator.serviceWorker.ready.then(()=>1)"); page.reload(); page.wait_for_load_state("networkidle")
    check("service worker active and controlling the page", page.evaluate("!!navigator.serviceWorker.controller"))
    check("android user agent", "Android" in page.evaluate("navigator.userAgent"))
    m = cdp.send("Page.getAppManifest"); data = json.loads(m.get("data") or "{}")
    check("manifest has no parse errors", not m.get("errors"), str(m.get("errors")))
    check("name / short_name", data.get("name") == "Mystic forecast" and data.get("short_name"), f'{data.get("name")}/{data.get("short_name")}')
    check("display fullscreen with standalone fallback", data.get("display") == "fullscreen" and data.get("display_override", [None, None])[:2] == ["fullscreen", "standalone"])
    check("theme and background colour match the page", data.get("theme_color") == "#0f1722" == data.get("background_color") == page.evaluate("document.querySelector('meta[name=theme-color]').content"))
    sizes = {(i["sizes"], i.get("purpose")) for i in data.get("icons", [])}
    check("192 and 512 icons, any + maskable", {("192x192", "any"), ("512x512", "any"), ("192x192", "maskable"), ("512x512", "maskable")} <= sizes)
    bad = page.evaluate("""async(u)=>{const m=await (await fetch('manifest.webmanifest')).json();const bad=[];
      for(const i of m.icons){const img=new Image();img.src=i.src;try{await img.decode()}catch(e){bad.push(i.src+' not loadable');continue}
      const w=parseInt(i.sizes);if(img.naturalWidth!==w)bad.push(i.src+' is '+img.naturalWidth+'px not '+w)}return bad}""", URL)
    check("every manifest icon loads at its declared size", not bad, str(bad))
    errs = cdp.send("Page.getInstallabilityErrors")["installabilityErrors"]
    check("Chrome reports NO installability errors (installable on Android)", not errs, str(errs))
    time.sleep(1); check("beforeinstallprompt fired (Android Chrome shows the install offer)", page.evaluate("window.__bip") is True, "informational: may not fire in automation")

    # offline
    ctx.set_offline(True); page.reload(); page.wait_for_load_state("domcontentloaded")
    check("offline: page still opens from the cache", page.locator(".dials").count() >= 2 and "Mystic" in page.title())
    page.wait_for_timeout(500)
    check("offline: banner says the forecast is offline and how old", page.locator("#net").is_visible() and "Offline" in page.locator("#net").inner_text(), page.evaluate("[navigator.onLine, document.getElementById('net').textContent]").__repr__())
    page.screenshot(path="data/local_out/pwa_offline.png")
    check("offline: forecast.json served from the cache", page.evaluate("fetch('forecast.json').then(r=>r.ok)"))
    ctx.set_offline(False)

    if not LIVE:
      # a new forecast replaces the cached one when online
      idx = SITE / "index.html"; old = idx.read_text(); new = old.replace("<h1>Mystic, Bright</h1>", "<h1>Mystic, Bright NEWFORECAST</h1>"); idx.write_text(new)
      (SITE / "forecast.json").write_text(json.dumps({"marker": "new"}))
      page.reload(); page.wait_for_load_state("networkidle")
      check("online: the new forecast replaces the cached page (despite max-age=600)", "NEWFORECAST" in page.locator("h1").inner_text())
      check("online: forecast.json refreshed", page.evaluate("fetch('forecast.json').then(r=>r.json())")["marker"] == "new")
      ctx.set_offline(True); page.reload(); page.wait_for_load_state("domcontentloaded")
      check("then offline: the NEW forecast is what the cache holds", "NEWFORECAST" in page.locator("h1").inner_text())
      ctx.set_offline(False)

      # slow network: cached copy appears fast, fresh copy lands in the cache for next time
      idx.write_text(new.replace("NEWFORECAST", "SLOWFORECAST")); STATE["delay_index"] = 8
      t0 = time.time(); page.goto(URL, wait_until="domcontentloaded"); dt = time.time() - t0
      check("slow network (8 s): cached page shown in under 5 s", dt < 5 and "NEWFORECAST" in page.locator("h1").inner_text(), f"{dt:.1f}s")
      time.sleep(10); STATE["delay_index"] = 0; page.reload(); page.wait_for_load_state("networkidle")
      check("slow network: the fresh page arrived in the background and shows next open", "SLOWFORECAST" in page.locator("h1").inner_text(), page.locator("h1").inner_text())

    ctx.close()
failed = [r for r in results if not r[1] and not r[0].startswith("beforeinstallprompt")]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + ("" if not failed else "  FAILED: " + "; ".join(r[0] for r in failed)))
sys.exit(1 if failed else 0)
