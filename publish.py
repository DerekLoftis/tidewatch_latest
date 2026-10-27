"""Copy the latest Tidewatch f36 forecasts and 3d/30d hindcasts (chart html
and CSV, MLLW and NAVD) into site/<product>/ under Tidewatch's own file
names, plus an index page, for GitHub Pages. CBBT, MNPT, DAHL and WADC are rebuilt
from NOAA CO-OPS observations by noaa_override.py (numpy, utide), which also builds BRDH, USLN-X,
CNRC and stand-ins from NWS NWPS observations for the stations in nwps_stations.csv.

    python publish.py [site_dir] [raw_dir]
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
# The NAVD chart page has no datum in its name (SWPT_2018_f36_hc.html).
NAME = re.compile(r"^([A-Z0-9-]+)_\d{4}_(f36|3d|30d)(?:_(MLLW|NAVD))?(_hc\.html|\.csv)$")
# f36 pickles: not published, but the datum styler needs them (m30, station list)
PICKLE = re.compile(r"^[A-Z0-9-]+_\d{4}_f36\.pypickle$")


def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def main(site="site", raw="raw"):
    listing = get(TIDEWATCH_URL).decode("utf-8", "replace")
    names = sorted({n for n in re.findall(r'href="([^"?/]+)"', listing) if NAME.match(n)})
    if not names:
        sys.exit("no Tidewatch files found in the listing")
    stations, failed = {}, []
    os.makedirs(os.path.join(raw, "f36"), exist_ok=True)
    for n in sorted({n for n in re.findall(r'href="([^"?/]+)"', listing) if PICKLE.match(n)}):
        try:
            with open(os.path.join(raw, "f36", n), "wb") as f:
                f.write(get(TIDEWATCH_URL + n))
        except Exception as e:
            failed.append(f"{n}: {e}")
    for n in names:
        st, product, datum, kind = NAME.match(n).groups()
        if datum is None:
            if kind != "_hc.html":
                continue
            datum = "NAVD"
        os.makedirs(os.path.join(site, product), exist_ok=True)
        try:
            data = get(TIDEWATCH_URL + n)
        except Exception as e:
            failed.append(f"{n}: {e}")
            continue
        with open(os.path.join(site, product, n), "wb") as f:
            f.write(data)
        stations.setdefault(st, {})[(product, datum, kind)] = f"{product}/{n}"
    # BRDH: added from the VIMS gauge on THREDDS. CBBT, MNPT, DAHL, WADC: rebuilt from NOAA CO-OPS observations with a Tidewatch-style
    # harmonic tide and 36-hour forecast (noaa_override.py); Tidewatch's are stale or aliased
    noaa_notes = {}
    try:
        import noaa_override
        noaa_notes = noaa_override.apply({p: os.path.join(site, p) for p in ("f36", "3d", "30d")},
                                         os.path.join(raw, "f36"))
    except Exception as e:
        print("noaa_override failed:", e)
    for st, note in noaa_notes.items():
        if "error" in note:
            continue
        stations[st] = {}
        for product in ("f36", "3d", "30d"):
            for n in os.listdir(os.path.join(site, product)):
                m = NAME.match(n)
                if m and m.group(1) == st:
                    stations[st][(product, m.group(3) or "NAVD", m.group(4))] = f"{product}/{n}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    write_index(site, stations, now)
    with open(os.path.join(site, "latest.json"), "w") as f:
        json.dump({"updated": now, "source": TIDEWATCH_URL, "files": sum(len(v) for v in stations.values()),
                   "noaa_observations": noaa_notes, "failed": failed}, f, indent=1)
    open(os.path.join(site, ".nojekyll"), "w").close()
    print(f"{sum(len(v) for v in stations.values())} files for {len(stations)} stations; {len(failed)} failed")
    for x in failed:
        print("  failed:", x)


def nwps_ids():
    try:
        import noaa_override
        return {st for st, i in noaa_override.STATIONS.items() if "nwps" in i}
    except Exception:
        return set()


NWPS_IDS = nwps_ids()


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
<p>CBBT, MNPT (Money Point), DAHL (Dahlgren) and WADC (Washington DC) are built here from NOAA CO-OPS
6-minute observations, because Tidewatch's records for them are stale (CBBT, MNPT since May 2022) or come from
another gauge (DAHL, WADC): a 27-constituent harmonic tide referenced to m30, and a Tidewatch-style 36-hour
forecast (tide plus 0.7 times the NWS P-ETSS mean surge, offset to the latest residual). NAVD88 at CBBT and
Money Point is converted from MLLW with NOAA VDatum (NAVD88 zero 1.92 and 1.77 ft above MLLW).</p>
<p>BRDH (Back River at Dandy Haven), a Classic Tidewatch station missing from the current Tidewatch, is built
the same way from the VIMS radar gauge's 1-minute record on the VIMS THREDDS server (MLLW 9.71 ft below the
gauge), with Sewells Point's P-ETSS surge and NAVD88 from NOAA VDatum (1.63 ft above MLLW).</p>
<p>USLN-X is the experimental FloodVISION-AI webcam water level at Little Neck Creek at Pinewood Road
(the same place as USLN, USGS 0204295505): water levels inferred by machine learning from the USGS webcam images
(the model retrained after the September 2026 nor'easter, live since 2026-10-08 9:12 PM EDT), archived every
6 hours in <a href="https://github.com/DerekLoftis/tidewatch_latest/tree/main/floodvision">floodvision/</a>, with
USLN's harmonic tide, datums and P-ETSS surge.</p>
<p>CNRC (Accokeek Creek at Crow's Nest Research Center) is the VIMS sensor on GreenStream's Virginia Flood
Monitoring System (VDEM), archived every 6 hours in
<a href="https://github.com/DerekLoftis/tidewatch_latest/tree/main/greenstream">greenstream/</a>. Its tide is
fitted to that archive (13 constituents until it holds 6 months, then 27), with Dahlgren's P-ETSS surge and
NAVD88 from NOAA VDatum (0.69 ft above MLLW).</p>
<p>Stand-ins: {", ".join(sorted(st for st in stations if st in NWPS_IDS))} are not on Tidewatch yet (their
VIMS pages link here), so until Tidewatch publishes them they are built the same way from the NWS NWPS
gauge observations (VDEM, StormSense, USGS, county and NOAA gauges; GNTD repeats GTND, Georgetown): the
gauge datum moved to MLLW (details in <a href="https://github.com/DerekLoftis/tidewatch_latest/blob/main/nwps_stations.csv">nwps_stations.csv</a>),
a harmonic tide fitted to the record, and the P-ETSS surge of the nearest P-ETSS point. Each steps aside as
soon as Tidewatch publishes the station.</p>
<p>Disclaimer: Tidewatch Charts(TM) are automated storm tide forecasts; User assumes all risk.</p>
<div style="overflow-x:auto"><table><tr><th>Station</th>{head}</tr>
{chr(10).join(rows)}
</table></div></body></html>
"""
    with open(os.path.join(site, "index.html"), "w") as f:
        f.write(page)


if __name__ == "__main__":
    main(*sys.argv[1:])
