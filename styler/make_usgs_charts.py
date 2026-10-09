"""Build datum-annotated Tidewatch charts (MLLW and NAVD) for every USGS station
(and the StormSense stations in OTHER_SITES).

    python3 make_usgs_charts.py ARCHIVE_F36_DIR USGS_DIR VDATUM_GRID_DIR NOAA_DIR OUT_DIR

USGS_DIR holds <site>.rdb (USGS site service, for lat/lon). VDATUM_GRID_DIR
holds one folder per VDatum region with its .gtx grids (see vdatum_grid.py).
NOAA_DIR holds the NOAA stations' mdapi datums.json, used for HAT/LAT and to
check the NAVD88 tie.

Datums come from the VDatum region whose nearest water cell is closest to the
site (MDVAchb12 wins ties); NAVD88 handling is described on Grids.

HAT and LAT (not in VDatum) are estimated from the nearest NOAA Tidewatch
station, scaled by the ratio of great diurnal ranges (MHHW - MLLW):
    HAT = MHHW + (HAT_ref - MHHW_ref) * GT / GT_ref
    LAT = LAT_ref * GT / GT_ref                     (ft above MLLW)

Tidewatch's USGS MLLW charts are its NAVD88 data plus one fixed offset, so the
plotted MLLW series are moved onto VDatum's MLLW (--rebase) and the MLLW line
sits at zero. The NAVD charts are left as published.
"""
import csv, glob, gzip, json, math, os, pickle, subprocess, sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
import add_datums as ad
import vdatum_grid as vg

# NOAA Tidewatch stations used as HAT/LAT references (approximate positions)
NOAA_REF = {
    "8638610": ("SWPT", 36.9467, -76.3300), "8632200": ("KIPT", 37.1650, -75.9883),
    "8637689": ("YRCG", 37.2267, -76.4783), "8636580": ("WMPT", 37.6158, -76.2900),
    "8635750": ("LEWI", 37.9953, -76.4644), "8635027": ("DAHL", 38.3197, -77.0367),
    "8594900": ("WADC", 38.8730, -77.0217), "8571421": ("BISH", 38.2200, -76.0383),
    "8631044": ("WACH", 37.6078, -75.6858), "8638901": ("CBBT", 36.9667, -76.1133),
    "8639348": ("MNPT", 36.7783, -76.3017),
}
# Tidewatch stations that are not USGS sites: position and caveat for the chart
OTHER_SITES = {
    # VIMS StormSense station list (vims.edu/bayinfo/tidewatch/locations/)
    "GS01": (37.38845, -76.93164, "Tidewatch publishes GS02's (Pamunkey) observations under this ID too (confirmed)"),
    "GS02": (37.57611, -76.99236, "Tidewatch also publishes these observations under GS01 (confirmed)"),
}
TIDAL = ("MLLW", "MLW", "MTL", "MSL", "MHW", "MHHW")


def site_id(raw):
    raw = str(raw)
    return raw.zfill(8) if raw.isdigit() and len(raw) < 8 else raw


def latlon(rdb):
    rows = [l.rstrip("\n").split("\t") for l in open(rdb) if not l.startswith("#") and l.strip()]
    hdr, data = rows[0], rows[2]
    rec = dict(zip(hdr, data))
    return float(rec["dec_lat_va"]), float(rec["dec_long_va"])


OLD = "MDVAchb12_8301"  # 2019 release on NAD83: its tss is tied to NAVD88
NAVD_NATIVE = (OLD, "NCinner11_8301")
MAXKM = 15.0  # furthest grid cell accepted for a site's tidal datums
BIASKM = 15.0


class Grids:
    """VDatum regions under root, with the NAVD88 fix for the 2024 IGS14 releases.

    The 2024 Chesapeake/Delmarva releases (horz=IGS14) give NAVD88-to-MLLW
    about 1 ft higher than NOAA's bench marks at every Tidewatch NOAA gauge,
    while the 2019 MDVAchb12 release and NCinner11 (NAD83) agree with NOAA.
    Their tidal ranges agree with NOAA, so only tss is off. For those regions
    tss is corrected by the local mean of (tss_2024 - tss_MDVAchb12) over
    MDVAchb12 cells within BIASKM, or, with none nearby, calibrated at the
    nearest NOAA gauge in the same region."""

    def __init__(self, root, noaa):
        self.root, self.noaa = root, noaa  # noaa: {st: (lat, lon, datums)}
        self.reg = vg.regions(root)
        self._g = {}

    def g(self, reg, name):
        if (reg, name) not in self._g:
            self._g[reg, name] = vg.Gtx(self.reg[reg][name])
        return self._g[reg, name]

    def local_bias(self, reg, la, lo):
        o, n = self.g(OLD, "tss"), self.g(reg, "tss")
        y, x = o._yx(la, lo)
        ky, kx = 111.2 * o.dlat, 111.2 * math.cos(math.radians(la)) * o.dlon
        k = int(BIASKM / min(kx, ky)) + 1
        s = []
        for rr in range(int(y) - k, int(y) + k + 2):
            for cc in range(int(x) - k, int(x) + k + 2):
                v = o._v(rr, cc)
                if v is None or math.hypot((rr - y) * ky, (cc - x) * kx) > BIASKM:
                    continue
                w, d = n.at(o.lat0 + rr * o.dlat, o.lon0 + cc * o.dlon - 360.0)
                if w is not None and d == 0:
                    s.append(w - v)
        return sum(s) / len(s) if len(s) >= 20 else None

    def noaa_bias(self, reg, la, lo):
        best = None
        for st, (rla, rlo, d) in self.noaa.items():
            if "NAVD88" not in d:
                continue
            m, dm = self.g(reg, "mllw").at(rla, rlo, 1.0)
            t, dt = self.g(reg, "tss").at(rla, rlo, 1.0)
            if m is None or t is None:
                continue
            km = 111.2 * dist((la, lo), (rla, rlo))
            if best is None or km < best[0]:
                best = (km, (t - m) - d["NAVD88"] / vg.FT, st)
        return best

    def datums(self, la, lo):
        """ft above MLLW at a point, plus notes on how they were found."""
        cands = []
        for reg in self.reg:
            m, d = self.g(reg, "mllw").at(la, lo, MAXKM)
            if m is None:
                continue
            # a site inside a region's water cells beats an edge or nearest-cell
            # value, and cells within 0.5 km count as equally close; then prefer
            # the firmest NAVD88 tie: native, then a local offset, then a NOAA gauge
            tie = 0 if reg in NAVD_NATIVE else 1 if self.local_bias(reg, la, lo) is not None else 2
            cands.append((0.0 if d == 0 else 0.5 if d <= 0.5 else round(d, 1), tie, d, reg))
        if not cands:
            return None, {"note": "no VDatum grid cell within %g km" % MAXKM}
        _, _, km, reg = min(cands)
        info = {"region": reg, "grid_km": round(km, 2)}
        vals = {}
        for name in ("mllw", "mlw", "mtl", "mhw", "mhhw", "tss"):
            v, _ = self.g(reg, name).at(la, lo, MAXKM)
            vals[name] = v
        m = vals["mllw"]
        d = {"MLLW": 0.0}
        for name, key in (("mlw", "MLW"), ("mtl", "MTL"), ("mhw", "MHW"), ("mhhw", "MHHW")):
            if vals[name] is not None:
                d[key] = round((vals[name] - m) * vg.FT, 3)
        d["MSL"] = round(-m * vg.FT, 3)  # LMSL is the grids' zero
        tss = vals["tss"]
        if tss is None:  # tss is smooth; reach further for it than for the tidal datums
            tss, tkm = self.g(reg, "tss").at(la, lo, 2 * MAXKM)
            if tss is None:
                return None, dict(info, note="no tss grid cell near the site")
        if reg in NAVD_NATIVE:
            info["navd_from"] = reg + " tss"
        else:
            b = self.local_bias(reg, la, lo)
            if b is not None:
                info["navd_from"] = "%s tss - %.2f ft (local offset to MDVAchb12)" % (reg, b * vg.FT)
            else:
                nb = self.noaa_bias(reg, la, lo)
                if nb is None:
                    return None, dict(info, note="no NAVD88 tie for " + reg)
                b = nb[1]
                info["navd_from"] = "%s tss - %.2f ft (calibrated at NOAA %s, %.0f km)" % (reg, b * vg.FT, nb[2], nb[0])
            tss -= b
        d["NAVD88"] = round((tss - m) * vg.FT, 3)
        return d, info


def dist(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(a[0] - b[0], (a[1] - b[1]) * k)


def main(f36, udir, gdir, ndir, out):
    os.makedirs(out, exist_ok=True)
    noaa = {}
    for nid, (st, la, lo) in NOAA_REF.items():
        p = os.path.join(ndir, nid + ".json")
        if os.path.exists(p):
            noaa[st] = (la, lo, ad.load_noaa(p))
    refs = {k: v for k, v in noaa.items() if "HAT" in v[2] and "LAT" in v[2]}
    grids = Grids(gdir, noaa)
    latest = {}
    for run in sorted(glob.glob(os.path.join(f36, "*", "*", "*"))):
        for p in glob.glob(os.path.join(run, "*_f36.pypickle.gz")):
            latest[os.path.basename(p).split("_")[0]] = run
    rows = []
    for st, run in sorted(latest.items()):
        pk = glob.glob(os.path.join(run, st + "_*_f36.pypickle.gz"))[0]
        m = pickle.loads(gzip.open(pk).read())["metadata"].iloc[0]
        if "usgs" not in str(m["scheme"]).lower() and st not in OTHER_SITES:
            continue
        sid = site_id(m["scheme_id"])
        tag = os.path.basename(pk).replace("_f36.pypickle.gz", "")
        row = {"station": st, "usgs_id": sid, "name": m["long_name"], "run": os.path.basename(run)}
        rows.append(row)
        try:
            a, b = [list(csv.reader(open(os.path.join(run, "%s_f36_%s.csv" % (tag, x)))))[1] for x in ("MLLW", "NAVD")]
            tw = round(float(a[1]) - float(b[1]), 3)
        except Exception:
            tw = None
        row["tidewatch_navd_above_mllw"] = tw
        caveat = None
        if st in OTHER_SITES:
            row["lat"], row["lon"], caveat = OTHER_SITES[st]
            row["usgs_id"] = ""
        else:
            rdb = os.path.join(udir, sid + ".rdb")
            if not os.path.exists(rdb):
                row["note"] = "no USGS site file"; continue
            row["lat"], row["lon"] = latlon(rdb)
        d, info = grids.datums(row["lat"], row["lon"])
        row.update(info)
        if d is None:
            continue
        if info["grid_km"] > 1.0:
            row["note"] = "site is %.1f km from the nearest VDatum water cell; datums extrapolated" % info["grid_km"]
        nontidal = d.get("MHHW", 0) < 0.05
        if nontidal:
            # VDatum treats the Back Bay / Currituck waters as nontidal (every
            # tidal datum = LMSL). Keep Tidewatch's MLLW basis (NAVD88 + its
            # offset) and draw only LMSL and NAVD88 on it.
            if tw is None:
                row["note"] = "nontidal and no Tidewatch offset"; continue
            d = {"MSL": round(tw - d["NAVD88"], 3), "NAVD88": tw, "_lmsl_below_navd": d["NAVD88"]}
            row["note"] = ("nontidal in VDatum (MLLW = MHHW = LMSL); MLLW chart kept on Tidewatch's basis. "
                           + row.get("note", "")).strip()
        if refs and not nontidal:
            rs = min(refs, key=lambda k: dist((row["lat"], row["lon"]), refs[k][:2]))
            rd = refs[rs][2]
            ratio = d["MHHW"] / (rd["MHHW"] - rd["MLLW"])
            d["HAT"] = round(d["MHHW"] + (rd["HAT"] - rd["MHHW"]) * ratio, 3)
            d["LAT"] = round(rd["LAT"] * ratio, 3)
            d["_estimated"] = ["HAT", "LAT"]
            row["hat_lat_ref"] = rs
        row.update({k: d.get(k) for k in ("HAT", "MHHW", "MSL", "MTL", "LAT")})
        row["vdatum_navd_above_mllw"] = None if nontidal else d["NAVD88"]
        row["m30_navd"] = round(ad.m30_navd(pk), 2)
        dj = os.path.join(out, "_datums", st + ".json")
        os.makedirs(os.path.dirname(dj), exist_ok=True)
        json.dump(d, open(dj, "w"), indent=1)
        extra = ["--datumsjson=" + dj, "--inline", "--twoffset=%s" % d["NAVD88"]]
        if caveat:
            extra.append("--note=" + caveat)
            row["note"] = (caveat + ". " + row.get("note", "")).strip()
        if tw is not None and not nontidal:
            row["mllw_rebase"] = round(d["NAVD88"] - tw, 3)
            extra.append("--rebase=%s" % row["mllw_rebase"])
        for dat, src in (("MLLW", tag + "_f36_MLLW_hc.html.gz"), ("NAVD", tag + "_f36_hc.html.gz")):
            src = os.path.join(run, src)
            if not os.path.exists(src):
                row["note"] = (row.get("note", "") + " missing %s chart" % dat).strip(); continue
            dst = os.path.join(out, "%s_f36_%s_hc_datums.html" % (tag, dat))
            r = subprocess.run([sys.executable, os.path.join(here, "add_datums.py"), st, src, pk, dat, dst] + extra,
                               capture_output=True, text=True)
            if r.returncode:
                row["note"] = (row.get("note", "") + " %s: %s" % (dat, (r.stderr.strip().splitlines() or ["error"])[-1])).strip()
    keys = ["station", "usgs_id", "name", "lat", "lon", "run", "HAT", "MHHW", "MSL", "MTL", "LAT", "hat_lat_ref",
            "vdatum_navd_above_mllw", "tidewatch_navd_above_mllw", "mllw_rebase", "m30_navd", "region", "grid_km",
            "navd_from", "note"]
    with open(os.path.join(out, "summary_usgs.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, keys, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    for r in rows:
        print(r)


if __name__ == "__main__":
    main(*sys.argv[1:6])
