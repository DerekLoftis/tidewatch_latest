"""Tidewatch-style products from NOAA CO-OPS for stations whose Tidewatch feed is
stale or mismatched, written over Tidewatch's own files so everything downstream
(styling, StormSense, the ensemble) uses them unchanged.

    CBBT  Chesapeake Bay Bridge Tunnel 8638901   Tidewatch stalled since 2022-05-27
    MNPT  Money Point 8639348                    Tidewatch stalled since 2022-05-27
    DAHL  Dahlgren 8635027                       Tidewatch shows another gauge's record
    WADC  Washington DC 8594900                  Tidewatch shows another gauge's record
    BRDH  Back River Dandy Haven (VIMS gauge)    Classic Tidewatch only; observations from
                                                 VIMS THREDDS, surge from Sewells Point P-ETSS

For each station and run:
- Observations: the last 31 days of 6-minute water levels from the CO-OPS Data API,
  feet above MLLW. NAVD88 heights are MLLW heights minus NAVD (NAVD88 zero above
  MLLW): NOAA's published value at DAHL and WADC; at CBBT and MNPT, where NOAA
  publishes none, NOAA VDatum (MDVAchb12 grid, about +/-0.25 ft).
- Astronomic tide: a 27-constituent least-squares harmonic analysis (Boon's HAMELS
  method; no Sa or Ssa, whose seasonal cycle m30 carries) of the 369 days of hourly
  heights in harmonics/<ST>_hourly.csv.gz, with nodal corrections (UTide).
- m30: the mean observed level over the last 30 days. The tide is referenced to it
  (its 30-day mean set equal to m30), so the 30-day mean residual is zero.
- 36-hour forecast (Tidewatch's formula): m30-referenced tide plus storm surge
  multiplied by 0.7 and offset to the latest observed residual (mean of the last
  30 minutes). The surge is the NWS P-ETSS ensemble-mean surge (GEFS-driven ETSS)
  at the station, since deterministic ETSS output isn't reachable; without P-ETSS
  the latest residual is held (persistence).

Files written, under Tidewatch's names: f36 MLLW/NAVD CSVs (time,pa30,anom,px30) and
chart pages, the f36 pickle (observations for m30 and the datum styler), 3d and 30d
CSVs (time,feet) and chart pages.

    python noaa_override.py DIR             rewrite DIR/{f36,3d,30d} (pickles in DIR/f36)
    python noaa_override.py --hourly OUT    download 369 days of hourly heights (refit)
"""
import csv
import gzip
import io
import json
import os
import pickle
import re
import sys
import tarfile
import time
import urllib.request
import warnings
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "VIMS StormSense/Tidewatch (noaa_override)"}
API = ("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?application=VIMS_StormSense&units=english"
       "&time_zone=gmt&format=json&datum=MLLW&station={id}&product={product}")
STATIONS = {
    "CBBT": {"noaa": "8638901", "name": "Chesapeake Bay Bridge Tunnel", "lat": 37.0329, "navd": 1.92,
             "navd_source": "NOAA VDatum (MDVAchb12 grid); NOAA publishes no NAVD88 here"},
    "MNPT": {"noaa": "8639348", "name": "Money Point", "lat": 36.7783, "navd": 1.77,
             "navd_source": "NOAA VDatum (MDVAchb12 grid); NOAA publishes no NAVD88 here"},
    "DAHL": {"noaa": "8635027", "name": "Dahlgren, VA", "lat": 38.3197, "navd": 1.04,
             "navd_source": "NOAA published datums (NTDE 1983-2001)"},
    "WADC": {"noaa": "8594900", "name": "Washington, DC", "lat": 38.8730, "navd": 1.40,
             "navd_source": "NOAA published datums (NTDE 1983-2001)"},
    # VIMS radar gauge: `values` is the distance down to the water (ft); MLLW is 9.71 ft below the gauge
    "BRDH": {"thredds": "https://tds.vims.edu/thredds/dodsC/daveAll/drf/Tidewatch/BRDH.nc", "gauge_mllw": 9.71,
             "surge_from": "8638610", "surge_name": "Sewells Point", "msl": 1.286,
             "name": "Back River Dandy Haven", "lat": 37.0976, "navd": 1.63,
             "navd_source": "NOAA VDatum (MDVAchb12 grid); VIMS radar gauge, MLLW 9.71 ft below the gauge"},
}
CONSTITUENTS = ["M2", "S2", "N2", "K1", "O1", "K2", "P1", "Q1", "NU2", "MU2", "2N2", "L2", "T2", "LDA2", "J1",
                "OO1", "NO1", "RHO1", "2Q1", "MK3", "MO3", "M3", "M4", "MS4", "MN4", "S4", "M6"]
SURGE_FACTOR = 0.7
STEP = timedelta(minutes=6)
NY = ZoneInfo("America/New_York")


def get(url, tries=3, timeout=60):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def parse_coops(raw):
    js = json.loads(raw)
    if "error" in js:
        raise RuntimeError(js["error"].get("message", js["error"]))
    rows = js.get("data") or js.get("predictions") or []
    t = np.array([datetime.strptime(r["t"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc) for r in rows])
    v = np.array([float(r["v"]) if r["v"] not in ("", None) else np.nan for r in rows])
    ok = ~np.isnan(v)
    return t[ok], v[ok]


def observations(st):
    """Last 31 days of 6-minute water levels, ft above MLLW."""
    info = STATIONS[st]
    if "thredds" in info:
        return thredds_observations(info["thredds"], info["gauge_mllw"])
    return parse_coops(get(API.format(id=info["noaa"], product="water_level") + "&range=744"))


def thredds_observations(url, gauge_mllw, days=31):
    """A VIMS gauge's 1-minute record over OPeNDAP, quality-controlled and thinned to 6 minutes."""
    n = int(re.search(r"time\[time = (\d+)\]", get(url + ".dds").decode()).group(1))
    a = max(0, n - int(days * 1440 * 1.2))
    rng = f"%5B{a}:1:{n - 1}%5D"  # Tomcat refuses raw brackets in a query
    txt = get(url + f".ascii?time{rng},values{rng}", timeout=180).decode()
    cols = []
    for name in ("time", "values"):
        m = re.search(r"^" + name + r"\[\d+\]\s*\n(.*?)(?:\n\s*\n|\Z)", txt, re.S | re.M)
        cols.append(np.array([float(x) for x in m.group(1).replace("\n", ",").split(",") if x.strip()]))
    secs, dist = cols
    ok = (secs > 0) & (dist > -900)
    t = np.array([datetime.fromtimestamp(x, timezone.utc).replace(second=0, microsecond=0) for x in secs[ok]])
    v = gauge_mllw - dist[ok]
    o = np.argsort(secs[ok], kind="stable")
    t, v = t[o], v[o]
    keep = (v > -4) & (v < 10) & (t > t[-1] - timedelta(days=days))
    t, v = t[keep], v[keep]
    if len(v) > 15:  # spikes: more than 0.5 ft off the 15-minute running median
        import pandas as pd
        med = pd.Series(v).rolling(15, center=True, min_periods=5).median().to_numpy()
        keep = np.abs(v - med) <= 0.5
        t, v = t[keep], v[keep]
    six = np.array([x.minute % 6 == 0 for x in t], bool)
    t, v = t[six], v[six]
    _, first = np.unique(t, return_index=True)
    return t[first], v[first]


def source_label(st):
    info = STATIONS[st]
    return f"VIMS gauge {st}" if "thredds" in info else f"NOAA CO-OPS {info['noaa']}"


def surge_id(st):
    """NOS id of the P-ETSS station whose surge the forecast uses."""
    return STATIONS[st].get("surge_from") or STATIONS[st]["noaa"]


def now():
    return datetime.now(timezone.utc)


# --- astronomic tide -----------------------------------------------------------
_fits = {}


def harmonic_fit(st):
    import utide
    if st not in _fits:
        rows = list(csv.reader(io.StringIO(gzip.open(os.path.join(HERE, "harmonics", f"{st}_hourly.csv.gz"))
                                           .read().decode())))[1:]
        t = np.array([np.datetime64(r[0]) for r in rows])
        h = np.array([float(r[1]) for r in rows])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _fits[st] = utide.solve(t, h, lat=STATIONS[st]["lat"], constit=CONSTITUENTS, method="ols",
                                    conf_int="none", trend=False, nodal=True, verbose=False)
    return _fits[st]


def tide(st, times):
    """Harmonic tide oscillation (its own mean removed), ft."""
    import utide
    c = harmonic_fit(st)
    tt = np.array([np.datetime64(t.replace(tzinfo=None)) for t in times])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return utide.reconstruct(tt, c, verbose=False).h - c.mean


# --- surge guidance --------------------------------------------------------------
def petss_surge(nos_ids):
    """({NOS id: (hourly times, ensemble-mean surge ft)}, cycle) from the newest P-ETSS cycle."""
    sys.path.insert(0, HERE)
    import petss
    cycle, csv_tar, _ = petss.newest_cycle()
    out = {}
    with tarfile.open(fileobj=io.BytesIO(csv_tar)) as t:
        for m in t.getmembers():
            nos = os.path.basename(m.name)[:-4]
            if m.name.endswith(".csv") and nos in nos_ids:
                s = petss.read_csv(t.extractfile(m).read().decode())
                ok = ~np.isnan(s["SURGE"])
                out[nos] = (s["time"][ok], s["SURGE"][ok])
    return out, cycle


def surge_at(times, hourly_t, hourly_v):
    """Hourly surge interpolated to `times` and smoothed (3-hour Hann), NaN outside."""
    x = np.array([t.timestamp() for t in hourly_t])
    xi = np.array([t.timestamp() for t in times])
    v = np.interp(xi, x, hourly_v, left=np.nan, right=np.nan)
    w = np.hanning(31)
    ok = ~np.isnan(v)
    if ok.sum() > 31:
        sm = np.convolve(np.where(ok, v, 0), w, "same") / np.convolve(ok.astype(float), w, "same")
        v = np.where(ok, sm, np.nan)
    return v


def petss_proxies(pet, days=32):
    """StormSense: P-ETSS-like station dicts for gauges P-ETSS doesn't forecast, keyed by
    station: the surge_from station's non-tidal guidance (surge, bias, percentiles) on this
    station's own harmonic tide (mean at MSL). Returns ({ST: station dict}, {ST: {time: tide}})
    with a 6-minute tide series over the last `days` for the hindcast charts."""
    out, series = {}, {}
    for st, info in STATIONS.items():
        base = next((v for v in pet.values() if v.get("id") == info.get("surge_from")), None)
        if base is None:
            continue
        try:
            new, old = tide(st, list(base["time"])) + info["msl"], base["TIDE"]
            d = {k: (v.copy() if hasattr(v, "copy") else v) for k, v in base.items()}
            d["TIDE"] = np.where(np.isnan(old), np.nan, new)
            for k in [k for k in base if k.startswith("TWL")]:
                d[k] = base[k] - old + new
            d["id"] = st
            d["guidance"] = (f"Forecast guidance: NWS P-ETSS ensemble mean surge at {info['surge_name']} "
                             f"on the {st} harmonic tide (fixed P-ETSS override at all stations)")
            t1 = now().replace(minute=0, second=0, microsecond=0) + timedelta(days=4)
            tt = [t1 - k * STEP for k in range(int((days + 4) * 240))][::-1]
            series[st] = dict(zip(tt, tide(st, tt) + info["msl"]))
            out[st] = d
        except Exception as e:
            print(f"noaa_override: no P-ETSS proxy for {st}: {e}")
    return out, series


# --- products --------------------------------------------------------------------
def build(st, obs_t, obs_v, surge=None):
    """Arrays for one station: 30-day hindcast and 36-hour forecast (ft above MLLW)."""
    t0 = obs_t[-1]
    keep = obs_t > t0 - timedelta(days=30)
    ot, ov = obs_t[keep], obs_v[keep]
    m30 = float(np.mean(ov))
    ft = np.array([t0 + i * STEP for i in range(360)])
    h = tide(st, list(ot) + list(ft))
    ho, hf = h[:len(ot)], h[len(ot):]
    base = m30 - float(np.mean(ho))
    pa_o, pa_f = ho + base, hf + base
    anom_o = ov - pa_o
    r0 = float(np.mean(anom_o[ot > t0 - timedelta(minutes=30)]))
    guidance = "persistence of the latest residual (no P-ETSS surge available)"
    res_f = np.full(len(ft), r0)
    if surge is not None:
        s = surge_at(ft, *surge)
        if not np.isnan(s[0]):
            s = SURGE_FACTOR * s
            s = np.where(np.isnan(s), np.nan, s)
            res_f = s - s[0] + r0
            guidance = f"P-ETSS ensemble-mean surge x {SURGE_FACTOR}, offset to the latest residual"
    return {"t0": t0, "m30": m30, "obs_t": ot, "obs": ov, "pa": pa_o, "anom": anom_o,
            "ft": ft, "pa_f": pa_f, "res_f": res_f, "px": pa_f + res_f, "guidance": guidance}


def utc_js(t):
    return f"Date.UTC({t.year},{t.month - 1},{t.day},{t.hour},{t.minute},0,0.0)"


def series_js(times, vals, **style):
    pts = ", ".join(f"[{utc_js(t)}, {round(float(v), 3)}]" for t, v in zip(times, vals) if not np.isnan(v))
    extra = "".join(f', "{k}": {json.dumps(v)}' for k, v in style.items())
    return '{"data": [' + pts + "]" + extra + "}"


PAGE = """<!DOCTYPE html>
<html lang="en">
    <head>
        <meta charset="utf-8" />
        <script type="text/javascript" src="https://ajax.googleapis.com/ajax/libs/jquery/1.9.1/jquery.min.js"></script>
        <script type="text/javascript" src="https://code.highcharts.com/6/highcharts.js"></script>
        <script type="text/javascript" src="https://code.highcharts.com/6/highcharts-more.js"></script>
        <script type="text/javascript" src="https://code.highcharts.com/6/modules/exporting.js"></script>
    </head>
    <body style="margin:0;padding:0">
                <div id="container" style="">Loading....</div>
    <script>
        $(function(){
            Highcharts.setOptions({"global": {"timezoneOffset": %(tz)d}, "lang": {}});
            var option = {"chart": {"renderTo": "container", "zoomType": "x"}, "credits": {"enabled": false}, "plotOptions": {"area": {"marker": {"radius": 2}, "lineWidth": 1, "threshold": null}}, "title": {"text": "%(title)s"}, "subtitle": {"text": "%(subtitle)s"}, "tooltip": {"valueDecimals": 2}, "xAxis": {"type": "datetime", "title": {"text": "Latest Observation %(latest)s"}}, "yAxis": {"title": {"text": "Water Level (feet above %(datum)s)"}}};
            var chart = new Highcharts.Chart(option);
            var data = [%(data)s];
            var dataLen = data.length;
            for (var ix = 0; ix < dataLen; ix++) {
                chart.addSeries(data[ix]);
            }
    });
        </script>
    </body>
</html>
"""


def page(path, title, subtitle, t0, datum, series):
    loc = t0.astimezone(NY)
    open(path, "w", encoding="utf-8").write(PAGE % {
        "tz": int(-loc.utcoffset().total_seconds() // 60), "title": title, "subtitle": subtitle,
        "latest": loc.strftime("%Y-%m-%d %H:%M:%S ") + loc.tzname(), "datum": datum,
        "data": ", ".join(series)})


def write_csv(path, head, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(rows)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def write_station(st, b, dirs, pickles):
    info = STATIONS[st]
    sub = f"{source_label(st)} observations; harmonic tide referenced to m30"
    if info.get("surge_from"):
        sub += f"; surge from P-ETSS at NOAA {info['surge_from']}"
    for datum, label, shift in (("MLLW", "MLLW", 0.0), ("NAVD", "NAVD88", -info["navd"])):
        # f36: Tidewatch's CSV columns (pa30 = m30 tide, anom = forecast residual, px30 = forecast)
        if "f36" in dirs:
            d = dirs["f36"]
            write_csv(os.path.join(d, f"{st}_2018_f36_{datum}.csv"), ["time", "pa30", "anom", "px30"],
                      [[iso(t), "%.3f" % (p + shift), "%.3f" % r, "%.3f" % (x + shift)]
                       for t, p, r, x in zip(b["ft"], b["pa_f"], b["res_f"], b["px"]) if not np.isnan(x)])
            day = b["obs_t"] > b["t0"] - timedelta(hours=24)
            ot = b["obs_t"][day]
            page(os.path.join(d, f"{st}_2018_f36_MLLW_hc.html" if datum == "MLLW" else f"{st}_2018_f36_hc.html"),
                 f"36 Hour Forecast for {st}", sub + "; forecast surge " + b["guidance"], b["t0"], label, [
                     series_js(ot, b["obs"][day] + shift, type="line", color="red", name="Observed"),
                     series_js(ot, b["pa"][day] + shift, type="line", color="blue", name="Astronomic (m30)"),
                     series_js(ot, b["anom"][day], type="line", color="green", name="Residual"),
                     series_js(b["ft"], b["pa_f"] + shift, type="line", color="blue", name="Astronomic (m30) Forecast"),
                     series_js(b["ft"], b["px"] + shift, type="line", color="red", name="36hr Forecast", dashStyle="dash"),
                     series_js(b["ft"], b["res_f"], type="line", color="green", name="Residual", dashStyle="dash")])
        for prod, days, word in (("3d", 3, "3 Day"), ("30d", 30, "30 Day")):
            if prod not in dirs:
                continue
            d = dirs[prod]
            k = b["obs_t"] > b["t0"] - timedelta(days=days)
            ot = b["obs_t"][k]
            write_csv(os.path.join(d, f"{st}_2018_{prod}_{datum}.csv"), ["time", "feet"],
                      [[iso(t), "%.3f" % (v + shift)] for t, v in zip(ot, b["obs"][k])])
            page(os.path.join(d, f"{st}_2018_{prod}_MLLW_hc.html" if datum == "MLLW" else f"{st}_2018_{prod}_hc.html"),
                 f"{word} Water Levels for {st}", sub, b["t0"], label, [
                     series_js(ot, b["obs"][k] + shift, type="line", color="red", name="Observed"),
                     series_js(ot, b["pa"][k] + shift, type="line", color="blue", name="Astronomic (m30)"),
                     series_js(ot, b["anom"][k], type="line", color="green", name="Residual")])
            stale = os.path.join(d, f"{st}_2018_{prod}_NAVD_hc.html")
            if os.path.exists(stale):
                os.remove(stale)
    # f36 pickle: what the datum styler reads (metadata, NAVD88 observations for m30)
    if pickles:
        import pandas as pd
        p = os.path.join(pickles, f"{st}_2018_f36.pypickle")
        meta = None
        if os.path.exists(p):
            try:
                meta = pickle.loads(open(p, "rb").read())["metadata"]
            except Exception:
                meta = None
        if meta is None:
            meta = pd.DataFrame([{"station_name": st, "long_name": info["name"], "status": "active"}])
        meta = meta.copy()
        if "thredds" in info:  # the datum styler then reads styler/stations_usgs/_datums/<ST>.json
            meta["scheme"], meta["scheme_id"] = info["thredds"], st
        else:
            meta["scheme"] = ("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=water_level"
                              "&datum=MLLW&units=english&time_zone=gmt&format=json")
            meta["scheme_id"] = info["noaa"]
        meta["datum"] = "NAVD88"
        meta["navd88_above_mllw"] = info["navd"]
        meta["navd88_source"] = info["navd_source"]
        naive = lambda ts: [datetime(t.year, t.month, t.day, t.hour, t.minute) for t in ts]
        sh = -info["navd"]
        fidx = pd.DatetimeIndex(naive(b["ft"]))
        obj = {"observations": list(zip(naive(b["obs_t"]), b["obs"] + sh)),
               "astronomic": list(zip(naive(b["obs_t"]), b["pa"] + sh)),
               "anomaly": list(zip(naive(b["obs_t"]), b["anom"])),
               "pa30": list(zip(naive(b["ft"]), b["pa_f"] + sh)),
               "metadata": meta,
               "etss_pred": pd.Series(b["res_f"], index=fidx),
               "f36_pdf": pd.DataFrame({0: b["res_f"], "pa30": b["pa_f"] + sh, "anom": b["res_f"],
                                        "px30": b["px"] + sh}, index=fidx)}
        open(p, "wb").write(pickle.dumps(obj))


def apply(dirs, pickles=None, stations=None):
    """dirs: {"f36": path, "3d": path, "30d": path} (any subset). Returns {ST: note}."""
    stations = stations or list(STATIONS)
    for d in list(dirs.values()) + ([pickles] if pickles else []):
        os.makedirs(d, exist_ok=True)
    try:
        surges, cycle = petss_surge({surge_id(s) for s in stations})
        print(f"noaa_override: P-ETSS {cycle:%Y-%m-%d %HZ} surge at {len(surges)} of {len(stations)} stations")
    except Exception as e:
        surges, cycle = {}, None
        print("noaa_override: P-ETSS unavailable:", e)
    notes = {}
    for st in stations:
        try:
            t, v = observations(st)
            if len(t) < 240 or t[-1] < now() - timedelta(hours=12):
                raise RuntimeError(f"too few or stale observations ({len(t)}, last {t[-1] if len(t) else None})")
            b = build(st, t, v, surges.get(surge_id(st)))
            write_station(st, b, dirs, pickles)
            notes[st] = {"latest_obs": iso(b["t0"]), "m30_ft_mllw": round(b["m30"], 3),
                         "navd88_above_mllw_ft": STATIONS[st]["navd"], "forecast": b["guidance"],
                         "petss_cycle": cycle.strftime("%Y-%m-%dT%HZ") if cycle else None}
            print(f"  {st}: {source_label(st)} to {b['t0']:%Y-%m-%d %H:%MZ}, m30 {b['m30']:.2f} ft MLLW, "
                  f"{b['guidance']}")
        except Exception as e:
            notes[st] = {"error": str(e)}
            print(f"  {st}: left as Tidewatch's ({e})")
    return notes


def download_hourly(out, days=369):
    """harmonics/<ST>_hourly.csv.gz: the last `days` of hourly heights (verified, then 6-minute
    preliminary at the top of each hour), for refitting the constituents about once a year."""
    os.makedirs(out, exist_ok=True)
    end = datetime.now(timezone.utc)
    for st, info in STATIONS.items():
        if "noaa" not in info:  # VIMS gauges: harmonics/<ST>_hourly.csv.gz is made by hand from THREDDS
            continue
        rows = {}
        a = end - timedelta(days=days)
        while a < end:
            b = min(a + timedelta(days=180), end)
            url = API.format(id=info["noaa"], product="hourly_height") + f"&begin_date={a:%Y%m%d}&end_date={b:%Y%m%d}"
            try:
                rows.update(zip(*parse_coops(get(url))))
            except Exception as e:
                print(st, a, e)
            a = b + timedelta(days=1)
        try:  # verified hourly heights lag by a few weeks: fill with the 6-minute record
            t, v = observations(st)
            rows.update({x: y for x, y in zip(t, v) if x.minute == 0 and x not in rows})
        except Exception as e:
            print(st, "6-minute", e)
        ts = sorted(t for t in rows if t > end - timedelta(days=days))
        with gzip.open(os.path.join(out, f"{st}_hourly.csv.gz"), "wt") as f:
            f.write("time_z,ft_mllw\n" + "".join(f"{t:%Y-%m-%d %H:%M:%S},{rows[t]:.3f}\n" for t in ts))
        print(st, len(ts), "hours")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--hourly"]:
        download_hourly(sys.argv[2])
    else:
        root = sys.argv[1]
        print(json.dumps(apply({p: os.path.join(root, p) for p in ("f36", "3d", "30d")
                                if os.path.isdir(os.path.join(root, p))}, os.path.join(root, "f36")), indent=1))
