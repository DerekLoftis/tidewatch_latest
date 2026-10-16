"""Archive FloodVISION-AI's webcam water levels for USLN-X (Little Neck Creek at Pinewood Road,
Virginia Beach; USGS 0204295505), from its live inference site https://usgs-ngwos.vims.edu/.

The site is a Streamlit app: its chart data (the last ~30 days of ML water levels from the USGS
webcam, with the USGS radar gauge's reading at each image) only exist in the rendered page, so
this renders it headless (Playwright) and reads the chart payload. Rows are merged into
floodvision/USLN-X_<YYYY-MM>.csv (one per month, never shortened), which noaa_override.py reads.

    time_utc          image time (UTC)
    ml_ft_navd88      FloodVISION-AI ML water level, ft NAVD88
    usgs_ft_navd88    USGS radar (parameter 62620) matched to the image, ft NAVD88
    error_ft          ml - usgs as the site reports it
    model             ml model: v1 (to 2026-10-09 01:12Z), v2 (retrained after the Sept nor'easter)
    image_url         the webcam image the inference used

    python floodvision.py [OUT_DIR] [--payload saved.json]
"""
import csv
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

URL = "https://usgs-ngwos.vims.edu/"
HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_V2 = datetime(2026, 10, 9, 1, 12, tzinfo=timezone.utc)  # live since 2026-10-08 9:12 PM EDT
HEAD = ["time_utc", "ml_ft_navd88", "usgs_ft_navd88", "error_ft", "model", "image_url"]
NY = ZoneInfo("America/New_York")


def render(url=URL, wait_ms=45000):
    """The chart payload from the rendered app (it sits in a component iframe's srcdoc)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        for _ in range(int(wait_ms / 3000) + 10):
            page.wait_for_timeout(3000)
            for f in page.frames:
                try:
                    js = f.evaluate("() => (typeof payload !== 'undefined') ? JSON.stringify(payload) : null")
                except Exception:
                    js = None
                if js:
                    b.close()
                    return json.loads(js)
        b.close()
    raise RuntimeError("no FloodVISION payload found in the rendered page")


def utc(row):
    """Image time in UTC: from the image file name, else the page's local (New York) time."""
    m = re.search(r"___(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})Z\.jpg$", row.get("imageUrl") or "")
    if m:
        return datetime.strptime(" ".join(m.groups()), "%Y-%m-%d %H %M %S").replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(row["time"]).replace(tzinfo=NY).astimezone(timezone.utc)


def fmt(v):
    return "" if v is None else "%.4f" % v


def merge(payload, out):
    os.makedirs(out, exist_ok=True)
    new = {}
    for r in payload["rows"]:
        if r.get("predicted") is None:
            continue
        t = utc(r)
        new[t.strftime("%Y-%m-%dT%H:%M:%SZ")] = [fmt(r.get("predicted")), fmt(r.get("observed")), fmt(r.get("error")),
                                                 "v2" if t >= MODEL_V2 else "v1", r.get("imageUrl") or ""]
    added = 0
    for month in sorted({k[:7] for k in new}):
        path = os.path.join(out, f"USLN-X_{month}.csv")
        old = {}
        if os.path.exists(path):
            old = {r[0]: r[1:] for r in list(csv.reader(open(path)))[1:] if r}
        rows = dict(old)
        for k, v in new.items():
            if k[:7] == month:
                if k not in old:
                    added += 1
                # keep a value already archived; fill in a USGS reading that arrived later
                rows[k] = v if k not in old else [old[k][0], old[k][1] or v[1], old[k][2] or v[2]] + old[k][3:]
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(HEAD)
            w.writerows([k] + rows[k] for k in sorted(rows))
    return added, len(new)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    payload = None
    if "--payload" in args:
        i = args.index("--payload")
        payload = json.load(open(args[i + 1]))
        del args[i:i + 2]
    out = args[0] if args else os.path.join(HERE, "floodvision")
    payload = payload or render()
    added, seen = merge(payload, out)
    print(f"FloodVISION USLN-X: {seen} rows on the site, {added} new; latest {payload.get('latest', {}).get('time')}")
