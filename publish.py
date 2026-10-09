"""Copy the latest Tidewatch f36 forecasts and 3d/30d hindcasts (chart html
and CSV, MLLW and NAVD) into site/<product>/ under Tidewatch's own file
names, plus an index page, for GitHub Pages. Standard library only.

    python publish.py [site_dir]
"""

import html
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

TIDEWATCH_URL = os.environ.get("TIDEWATCH_URL", "https://web4.vims.edu/physical/tidewatch/")
UA = {"User-Agent": "tidewatch-latest (VIMS)"}
NAME = re.compile(r"^([A-Z0-9]+)_\d{4}_(f36|3d|30d)_(MLLW|NAVD)(_hc\.html|\.csv)$")


def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def main(site="site"):
    listing = get(TIDEWATCH_URL).decode("utf-8", "replace")
    names = sorted({n for n in re.findall(r'href="([^"?/]+)"', listing) if NAME.match(n)})
    if not names:
        sys.exit("no Tidewatch files found in the listing")
    stations, failed = {}, []
    for n in names:
        st, product, datum, kind = NAME.match(n).groups()
        os.makedirs(os.path.join(site, product), exist_ok=True)
        try:
            data = get(TIDEWATCH_URL + n)
        except Exception as e:
            failed.append(f"{n}: {e}")
            continue
        with open(os.path.join(site, product, n), "wb") as f:
            f.write(data)
        stations.setdefault(st, {})[(product, datum, kind)] = f"{product}/{n}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    write_index(site, stations, now)
    with open(os.path.join(site, "latest.json"), "w") as f:
        json.dump({"updated": now, "source": TIDEWATCH_URL, "files": sum(len(v) for v in stations.values()),
                   "failed": failed}, f, indent=1)
    open(os.path.join(site, ".nojekyll"), "w").close()
    print(f"{sum(len(v) for v in stations.values())} files for {len(stations)} stations; {len(failed)} failed")
    for x in failed:
        print("  failed:", x)


def write_index(site, stations, now):
    cols = [(p, d) for p in ("f36", "3d", "30d") for d in ("MLLW", "NAVD")]
    head = "".join(f"<th>{p} {d}</th>" for p, d in cols)
    rows = []
    for st in sorted(stations):
        cells = []
        for p, d in cols:
            h = stations[st].get((p, d, "_hc.html"))
            c = stations[st].get((p, d, ".csv"))
            links = " ".join(f'<a href="{html.escape(u)}">{t}</a>' for u, t in ((h, "chart"), (c, "csv")) if u)
            cells.append(f"<td>{links}</td>")
        rows.append(f"<tr><th>{st}</th>{''.join(cells)}</tr>")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tidewatch latest</title>
<style>body{{font-family:system-ui,sans-serif;margin:16px;color:#222}}table{{border-collapse:collapse}}
th,td{{border:1px solid #ccc;padding:4px 8px;text-align:left}}a{{color:#16687E}}</style></head>
<body><h1>Tidewatch latest forecasts and hindcasts</h1>
<p>Copied from <a href="{TIDEWATCH_URL}">Tidewatch</a> every 6 hours; updated {now}.
Links keep the same address and are overwritten each update.</p>
<p>Disclaimer: Tidewatch Charts(TM) are automated storm tide forecasts; User assumes all risk.</p>
<div style="overflow-x:auto"><table><tr><th>Station</th>{head}</tr>
{chr(10).join(rows)}
</table></div></body></html>
"""
    with open(os.path.join(site, "index.html"), "w") as f:
        f.write(page)


if __name__ == "__main__":
    main(*sys.argv[1:])
