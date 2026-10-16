"""P-ETSS (Probabilistic Extra-Tropical Storm Surge, NWS MDL) station guidance
for the StormSense 72-hour forecast.

    python petss.py OUT_DIR              download the newest good cycle into OUT_DIR

P-ETSS runs 4 times a day (00, 06, 12, 18Z) and posts, per station, a CSV with
the harmonic tide every 6 minutes and the hourly storm tide (tide + surge +
an observation bias correction) for the ensemble mean and the 10% and 90%
exceedance levels, in feet above MLLW, out to 102 hours. Stations are keyed
by NOS id or an "est" id; the SHEF bulletins name the same series by NWS
location id, so the mean series is matched to tie each NWS id to its CSV.

forecast() turns one station into a 6-minute, 72-hour forecast: the hourly
non-tidal part (storm tide minus tide) is interpolated to 6 minutes, smoothed
with a 3-hour Hann low-pass filter (P-ETSS surge comes in 0.1 ft steps), and
added back to the 6-minute tide. The ensemble surge (mean, 10th and 90th
percentiles) and the applied bias correction are smoothed the same way.
"""
import csv
import io
import os
import re
import sys
import tarfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np

BASE = os.environ.get("PETSS_URL", "https://nomads.ncep.noaa.gov/pub/data/nccf/com/petss/prod/")
UA = {"User-Agent": "stormsense-latest (VIMS)"}
MISSING = 9000.0
STEP = timedelta(minutes=6)


def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def newest_cycle(now=None):
    """(cycle datetime, csv tarball bytes, shef tarball bytes) for the newest
    cycle whose files are posted and marked GOOD, looking back 30 hours."""
    now = now or datetime.now(timezone.utc)
    c = now.replace(minute=0, second=0, microsecond=0)
    c -= timedelta(hours=c.hour % 6)
    for _ in range(6):
        stem = f"{BASE}petss.{c:%Y%m%d}/petss.t{c:%H}z."
        try:
            meta = get(stem + "meta.txt", tries=1).decode()
            if "STATUS=GOOD" in meta:
                return c, get(stem + "csv.tar.gz"), get(stem + "shef.tar.gz")
        except Exception:
            pass
        c -= timedelta(hours=6)
    raise RuntimeError("no P-ETSS cycle found in the last 30 hours")


def oldest_csv(cycle):
    """(cycle, csv tarball) for the oldest cycle NOMADS still holds (it keeps
    today's and yesterday's), whose hindcast reaches about 6 days further back."""
    c = (cycle - timedelta(days=1)).replace(hour=0)
    while c < cycle:
        try:
            return c, get(f"{BASE}petss.{c:%Y%m%d}/petss.t{c:%H}z.csv.tar.gz", tries=1)
        except Exception:
            c += timedelta(hours=6)
    return None, None


def tide_series(csv_tars):
    """{station id: {datetime: tide}} from one or more csv tarballs, later ones winning."""
    out = {}
    for tar in csv_tars:
        if not tar:
            continue
        with tarfile.open(fileobj=io.BytesIO(tar)) as t:
            for m in t.getmembers():
                if m.name.endswith(".csv"):
                    st = read_csv(t.extractfile(m).read().decode())
                    d = out.setdefault(os.path.basename(m.name)[:-4], {})
                    d.update({tm: v for tm, v in zip(st["time"], st["TIDE"]) if not np.isnan(v)})
    return out


def read_csv(text):
    """P-ETSS station CSV -> dict of numpy arrays (times as datetimes)."""
    rows = list(csv.reader(io.StringIO(text)))
    head = [h.strip() for h in rows[0]]
    cols = {h: [] for h in head}
    for r in rows[1:]:
        if len(r) != len(head):
            continue
        for h, v in zip(head, r):
            cols[h].append(v.strip())
    out = {"time": np.array([datetime.strptime(t, "%Y%m%d%H%M").replace(tzinfo=timezone.utc) for t in cols["TIME"]])}
    for h in head[1:]:
        a = np.array([float(v) for v in cols[h]])
        a[a > MISSING] = np.nan
        out[h] = a
    return out


def read_shef_mean(text):
    """{LID: first six hourly mean values} from the ensemble-mean SHEF bulletin."""
    out, lid, vals = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^\.E (\w+) ", line)
        if m:
            if lid:
                out[lid] = vals[:6]
            lid, vals = m.group(1), []
        elif lid and re.match(r"^\.E\d", line):
            vals += [float(v) for v in re.findall(r"-?\d+\.\d+", line)]
    if lid:
        out[lid] = vals[:6]
    return out


def load(cycle, csv_tar, shef_tar, lids):
    """{LID: station dict} for the NWS ids wanted, matched through the SHEF mean."""
    shef = {}
    with tarfile.open(fileobj=io.BytesIO(shef_tar)) as t:
        for m in t.getmembers():
            if ".mean.totalwater." in m.name:
                shef.update(read_shef_mean(t.extractfile(m).read().decode()))
    want = {lid: shef[lid] for lid in lids if lid in shef}
    hours = [cycle + timedelta(hours=h) for h in range(6)]
    found = {}
    with tarfile.open(fileobj=io.BytesIO(csv_tar)) as t:
        for m in t.getmembers():
            if not m.name.endswith(".csv"):
                continue
            st = read_csv(t.extractfile(m).read().decode())
            idx = {tm: i for i, tm in enumerate(st["time"])}
            sig = [st["TWL"][idx[h]] if h in idx else np.nan for h in hours]
            for lid, ref in want.items():
                if lid not in found and len(ref) == 6 and np.allclose(sig, ref, atol=0.0015):
                    st["id"] = os.path.basename(m.name)[:-4]
                    found[lid] = st
    return found


def _lowpass(x, width):
    w = np.hanning(width + 2)[1:-1]
    w /= w.sum()
    pad = width // 2
    xp = np.concatenate([x[pad:0:-1], x, x[-2:-pad - 2:-1]])
    return np.convolve(xp, w, mode="valid")


def forecast(st, t0, cycle, hours=72):
    """6-minute forecast from t0 for `hours`: dict of astro_tide, residual,
    SSforecast, low (10th percentile, P-ETSS's 90% exceedance) and high (90th
    percentile, 10% exceedance), surge, surge_low, surge_high and bias; feet MLLW."""
    tt = st["time"]
    grid = np.array([t0 + k * STEP for k in range(hours * 10)])
    sec = lambda a: np.array([x.timestamp() for x in a])
    tide_all = st["TIDE"]
    ok = ~np.isnan(tide_all)
    tide = np.interp(sec(grid), sec(tt[ok]), tide_all[ok])
    hourly = np.array([t.minute == 0 and t >= cycle for t in tt]) & ok
    out = {"time": grid, "astro_tide": tide}
    fine = np.array([t0 - timedelta(hours=3) + k * STEP for k in range((hours + 6) * 10)])
    for key, col in (("SSforecast", "TWL"), ("low", "TWL90p"), ("high", "TWL10p"),
                     ("surge", "SURGE"), ("surge_low", "SURGE90p"), ("surge_high", "SURGE10p"), ("bias", None)):
        # bias: the observation-based correction P-ETSS actually added (TWL - TIDE - SURGE);
        # its BIAS column also carries a diagnostic that is not applied at the "est" sites
        vals = st["TWL"] - tide_all - st["SURGE"] if col is None else st[col]
        sel = hourly & ~np.isnan(vals)
        if sel.sum() < 2:
            out[key] = np.full(len(grid), np.nan)
            continue
        twl = col is not None and col.startswith("TWL")
        nt = vals[sel] - (tide_all[sel] if twl else 0)  # non-tidal part, hourly
        smooth = _lowpass(np.interp(sec(fine), sec(tt[sel]), nt), 31)  # 3-hour Hann window
        out[key] = (tide if twl else 0) + np.interp(sec(grid), sec(fine), smooth)
    out["low"] = np.minimum(out["low"], out["SSforecast"])
    out["high"] = np.maximum(out["high"], out["SSforecast"])
    out["residual"] = out["SSforecast"] - tide
    if tt[hourly][-1] < grid[-1]:
        raise ValueError("P-ETSS cycle %s ends before %s" % (cycle, grid[-1]))
    return out


def hindcast_tide(st, times):
    tt = st["time"]
    ok = ~np.isnan(st["TIDE"])
    return np.interp([t.timestamp() for t in times], [t.timestamp() for t in tt[ok]], st["TIDE"][ok])


if __name__ == "__main__":
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    c, a, b = newest_cycle()
    open(os.path.join(out, "csv.tar.gz"), "wb").write(a)
    open(os.path.join(out, "shef.tar.gz"), "wb").write(b)
    print("P-ETSS cycle", c.isoformat())
