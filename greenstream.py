"""Archive GreenStream (Virginia Flood Monitoring System, VDEM) water levels for CNRC, Accokeek
Creek at Crow's Nest Research Center (VIMS sensor, Stafford County).

The VDEM site (https://vdem.greenstream.io/) has no public download: its page receives each
site's history over a websocket. This renders the site's detail page headless (Playwright),
picks the 30-day graph range and reads the history message. The sensor reports `stage`, the
distance down from the sensor to the water (ft); water level (ft NAVD88) = sensorElev - stage,
as the page shows it. Rows are merged into greenstream/CNRC_<YYYY-MM>.csv, never shortened.

    time_utc, level_ft_navd88, stage_ft, sensor_elev_ft_navd88, sensor_temp_f, voltage

    python greenstream.py [OUT_DIR] [--frames saved_ws_frames.txt]
"""
import base64
import csv
import gzip
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SITES = {"CNRC": "Accokeek Creek at Crow's Nest Research Center"}
URL = "https://vdem.greenstream.io/detail?location=Sites:%20{}"
HEAD = ["time_utc", "level_ft_navd88", "stage_ft", "sensor_elev_ft_navd88", "sensor_temp_f", "voltage"]


def unpack(v):
    return json.loads(gzip.decompress(base64.b64decode(v)).decode()) if isinstance(v, str) and v.startswith("H4sI") else v


def render(title, wait_ms=20000):
    """Websocket messages (text) received while showing one site's page with the 30-day range."""
    from urllib.parse import quote
    from playwright.sync_api import sync_playwright
    frames = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1600, "height": 1200})
        page.on("websocket", lambda ws: ws.on("framereceived", lambda f: frames.append(f) if isinstance(f, str) else None))
        page.goto(URL.format(quote(title)), wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(wait_ms)
        for label in ("I Accept", "30 days"):
            try:
                page.get_by_text(label, exact=True).first.click(timeout=10000)
                page.wait_for_timeout(wait_ms)
            except Exception as e:
                print(f"greenstream: could not click {label!r}: {e}")
        b.close()
    return frames


def parse(frames, title):
    """(site metadata, rows) from the websocket messages: the longest history for the site."""
    site, hist = None, None
    for raw in frames:
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if d.get("action") == "init":
            for f in unpack(d["geoJson"]["features"]):
                if f["properties"]["title"] == title:
                    site = f
        elif d.get("action") == "history":
            g = unpack(d["graphData"])
            if hist is None or len(g["timestamps"]) > len(hist[1]["timestamps"]):
                hist = (d["id"], g)
    if site is None or hist is None or hist[0] != site["properties"]["id"]:
        raise RuntimeError("no history for %s in the page's messages" % title)
    st = site["properties"]["devices"]["stage"]["stage"]
    elev = float(st["sensorElev"])
    if st.get("referenceFrame") != "NAVD88":
        raise RuntimeError("unexpected reference frame %r" % st.get("referenceFrame"))
    g = hist[1]
    stage, temp = g["stage"].get("stage", []), g["stage"].get("sensorTemp", [])
    volt = g.get("deviceHealth", {}).get("voltage", [])
    rows = {}
    for i, ts in enumerate(g["timestamps"]):
        s = stage[i] if i < len(stage) else None
        if s is None:
            continue
        t = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        at = lambda a: "" if i >= len(a) or a[i] is None else "%.2f" % a[i]
        rows[t] = ["%.3f" % (elev - s), "%.3f" % s, "%.3f" % elev, at(temp), at(volt)]
    return site, rows


def merge(st, rows, out):
    os.makedirs(out, exist_ok=True)
    added = 0
    for month in sorted({k[:7] for k in rows}):
        path = os.path.join(out, f"{st}_{month}.csv")
        old = {}
        if os.path.exists(path):
            old = {r[0]: r[1:] for r in list(csv.reader(open(path)))[1:] if r}
        new = {k: v for k, v in rows.items() if k[:7] == month and k not in old}
        added += len(new)
        old.update(new)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(HEAD)
            w.writerows([k] + old[k] for k in sorted(old))
    return added


if __name__ == "__main__":
    args = sys.argv[1:]
    saved = None
    if "--frames" in args:
        i = args.index("--frames")
        saved = open(args[i + 1]).read().splitlines()
        del args[i:i + 2]
    out = args[0] if args else os.path.join(HERE, "greenstream")
    for st, title in SITES.items():
        site, rows = parse(saved or render(title), title)
        added = merge(st, rows, out)
        ks = sorted(rows)
        print(f"GreenStream {st}: {len(rows)} rows {ks[0] if ks else ''} to {ks[-1] if ks else ''}, {added} new")
