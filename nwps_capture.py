"""Archive hourly water levels for the stand-in stations in nwps_stations.csv (gauges Tidewatch
doesn't publish yet), from the NWS NWPS API, so their harmonic tide can be fitted to more than
the 30 days NWPS keeps (noaa_override.py reads these files).

Rows are the gauge's stage as NWPS reports it (the gauge's own datum, no offset applied), at the
top of each hour, interpolated between readings no more than 20 minutes away. They are merged
into nwps/<ST>_<YYYY-MM>.csv, never shortened.

    time_utc, stage_ft

    python nwps_capture.py [OUT_DIR]
"""
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.water.noaa.gov/nwps/v1/gauges/{lid}/stageflow/observed"
UA = {"User-Agent": "tidewatch-latest (VIMS)"}
HEAD = ["time_utc", "stage_ft"]


def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def hourly(data):
    """{'YYYY-MM-DDTHH:00:00Z': stage} from NWPS readings."""
    pts = sorted((datetime.strptime(d["validTime"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp(),
                  float(d["primary"])) for d in data if d.get("primary") not in (None, -999) and float(d["primary"]) > -900)
    out = {}
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        h = -(-t0 // 3600) * 3600  # first hour mark at or after t0
        while h <= t1:
            if min(h - t0, t1 - h) <= 1200 and t1 - t0 <= 3 * 3600:
                v = v0 if t1 == t0 else v0 + (v1 - v0) * (h - t0) / (t1 - t0)
                out[datetime.fromtimestamp(h, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")] = round(v, 3)
            h += 3600
    return out


def merge(path, rows):
    old = {}
    if os.path.exists(path):
        old = {r["time_utc"]: r["stage_ft"] for r in csv.DictReader(open(path))}
    new = {k: v for k, v in rows.items() if k not in old}
    if not new:
        return 0
    old.update({k: "%.3f" % v for k, v in new.items()})
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        w.writerows([k, old[k]] for k in sorted(old))
    return len(new)


def main(out=os.path.join(HERE, "nwps")):
    os.makedirs(out, exist_ok=True)
    bad = 0
    for r in csv.DictReader(open(os.path.join(HERE, "nwps_stations.csv"))):
        st = r["station"]
        try:
            rows = hourly(json.loads(get(API.format(lid=r["nwps_lid"]))).get("data") or [])
        except Exception as e:
            print(f"{st}: {e}")
            bad += 1
            continue
        added = 0
        for month in sorted({k[:7] for k in rows}):
            added += merge(os.path.join(out, f"{st}_{month}.csv"), {k: v for k, v in rows.items() if k[:7] == month})
        print(f"{st}: {len(rows)} hours from NWPS, {added} new")
    if bad:
        print(f"{bad} stations failed")


if __name__ == "__main__":
    main(*sys.argv[1:])
