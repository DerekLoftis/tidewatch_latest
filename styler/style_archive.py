"""Add the datum lines, m30, disclaimer and logo to every archived Tidewatch
Highcharts page (f36 forecast, 3d and 30d hindcast; MLLW and NAVD).

    python3 style_archive.py ARCHIVE_TIDEWATCH_DIR OUT_DIR [--latest] [--force]

ARCHIVE_TIDEWATCH_DIR is .../tidewatch-archive/data/tidewatch (f36/, 3d/, 30d/).
For each run folder holding *_hc.html.gz pages, writes one bundle
    OUT_DIR/archive/<product>/<YYYY>/<MM>/<run>.zip
holding the styled pages plus one shared jslib/ (libraries and logo), so a run
is a single file. Runs already bundled are skipped unless --force.
With --latest, the newest run of each product is also written to
    OUT_DIR/latest/<product>/  as self-contained pages (libraries inlined).

Datums per station:
  NOAA stations   noaa_datums/<id>.json (see add_datums.load_noaa)
  USGS and GS     stations_usgs/_datums/<ST>.json from make_usgs_charts.py
                  (VDatum; MLLW pages moved onto VDatum's MLLW with --rebase)
Tidewatch's MLLW-NAVD offset is read from each run's own CSV pair, and m30
comes from the f36 pickle of the same cycle (or the latest earlier one).
"""
import csv, glob, gzip, json, os, pickle, shutil, subprocess, sys, tempfile, zipfile

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
import add_datums as ad
from make_usgs_charts import OTHER_SITES

PRODUCTS = ("f36", "3d", "30d")
LIBS = ("jquery.min.js", "highcharts.js", "highcharts-more.js", "heatmap.js", "exporting.js")


def runs(root, prod):
    return sorted(r for r in glob.glob(os.path.join(root, prod, "*", "*", "*"))
                  if glob.glob(os.path.join(r, "*_hc.html.gz")))


def cycle(run):
    return os.path.basename(run)[:13]  # YYYY-MM-DDTHH


def f36_pickles(root):
    """{cycle: run dir} for f36 runs that have pickles."""
    return {cycle(r): r for r in sorted(glob.glob(os.path.join(root, "f36", "*", "*", "*")))
            if glob.glob(os.path.join(r, "*_f36.pypickle.gz"))}


def pickle_for(st, cyc, pk_runs):
    for c in sorted(pk_runs, reverse=True):
        if c <= cyc:
            p = glob.glob(os.path.join(pk_runs[c], st + "_*_f36.pypickle.gz"))
            if p:
                return p[0]
    return None


def stations(dd, pk_runs):
    """{ST: dict(kind, datums file, note)} from the newest f36 pickles."""
    out = {}
    for c in sorted(pk_runs):
        for p in glob.glob(os.path.join(pk_runs[c], "*_f36.pypickle.gz")):
            st = os.path.basename(p).split("_")[0]
            out[st] = p
    table = {}
    for st, p in out.items():
        m = pickle.loads(gzip.open(p).read())["metadata"].iloc[0]
        if "tidesandcurrents" in str(m["scheme"]):
            j = os.path.join(dd, "noaa_datums", str(m["scheme_id"]) + ".json")
            if os.path.exists(j):
                table[st] = {"kind": "noaa", "file": j}
        else:
            j = os.path.join(dd, "stations_usgs", "_datums", st + ".json")
            if os.path.exists(j):
                d = json.load(open(j))
                table[st] = {"kind": "nontidal" if "_lmsl_below_navd" in d else "vdatum", "file": j,
                             "navd": d.get("NAVD88"), "note": OTHER_SITES.get(st, (0, 0, None))[2]}
    return table


def tw_offset(run, tag, prod):
    """Median MLLW - NAVD over times present in both CSVs (the 3d and 30d
    files do not start at the same time)."""
    try:
        a, b = [{r[0]: r[1] for r in list(csv.reader(open(os.path.join(run, "%s_%s_%s.csv" % (tag, prod, x)))))[1:]}
                for x in ("MLLW", "NAVD")]
        diffs = []
        for t in a.keys() & b.keys():
            try:
                diffs.append(float(a[t]) - float(b[t]))
            except ValueError:
                continue
        diffs.sort()
        return round(diffs[len(diffs) // 2], 3) if diffs else None
    except Exception:
        return None


def style_run(run, prod, table, pk_runs, out_dir, jsdir=None, options=(), flood=None, flood_datums=("NAVD",)):
    """Style every page in one run into out_dir. Returns (done, problems).
    options: extra add_datums.py flags (e.g. --brand=stormsense); flood: {ST: thresholds
    json path}, drawn on the charts whose datum is in flood_datums."""
    done, probs = 0, []
    for src in sorted(glob.glob(os.path.join(run, "*_hc.html.gz"))):
        name = os.path.basename(src)
        st, dat = name.split("_")[0], ("MLLW" if "_MLLW_hc" in name else "NAVD")
        tag = name.split("_%s_" % prod)[0]
        s = table.get(st)
        if s is None:
            continue  # stale or unknown station: no datums
        pk = pickle_for(st, cycle(run), pk_runs)
        if pk is None:
            probs.append("%s: no f36 pickle for m30" % name); continue
        tw = tw_offset(run, tag, prod)
        if s["kind"] == "noaa":
            extra = ["--datums=" + s["file"]] + (["--twoffset=%s" % tw] if tw is not None else [])
        elif s["kind"] == "vdatum":
            extra = ["--datumsjson=" + s["file"], "--twoffset=%s" % s["navd"]]
            if tw is not None:
                extra.append("--rebase=%s" % round(s["navd"] - tw, 4))
        else:  # nontidal: datums are already on Tidewatch's MLLW basis
            extra = ["--datumsjson=" + s["file"]] + (["--twoffset=%s" % tw] if tw is not None else [])
        if s.get("note"):
            extra.append("--note=" + s["note"])
        if flood and st in flood and dat in flood_datums:
            extra.append("--thresholds=" + flood[st])
            if tw is not None:
                extra.append("--floodoffset=%s" % tw)
        extra += list(options)
        extra += ["--jsdir=" + jsdir] if jsdir else ["--inline"]
        dst = os.path.join(out_dir, "%s_%s_%s_hc_datums.html" % (tag, prod, dat))
        r = subprocess.run([sys.executable, os.path.join(here, "add_datums.py"), st, src, pk, dat, dst] + extra,
                           capture_output=True, text=True)
        if r.returncode:
            probs.append("%s: %s" % (name, (r.stderr.strip().splitlines() or ["error"])[-1]))
        else:
            done += 1
    return done, probs


def main(argv):
    root, out = argv[0], argv[1]
    dd = os.environ.get("TW_DATUMS", here)  # folder holding noaa_datums/ and stations_usgs/
    pk_runs = f36_pickles(root)
    table = stations(dd, pk_runs)
    jslib = os.environ.get("TW_JSLIB", os.path.join(here, "jslib"))
    for prod in PRODUCTS:
        rs = runs(root, prod)
        for run in rs:
            yyyy, mm = os.path.basename(run)[:4], os.path.basename(run)[5:7]
            z = os.path.join(out, "archive", prod, yyyy, mm, os.path.basename(run) + ".zip")
            if os.path.exists(z) and "--force" not in argv:
                continue
            tmp = tempfile.mkdtemp()
            try:
                done, probs = style_run(run, prod, table, pk_runs, tmp, jsdir="jslib")
                os.makedirs(os.path.dirname(z), exist_ok=True)
                with zipfile.ZipFile(z + ".part", "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in sorted(os.listdir(tmp)):
                        zf.write(os.path.join(tmp, f), f)
                    for lib in LIBS:
                        zf.write(os.path.join(jslib, lib), "jslib/" + lib)
                    zf.write(os.path.join(here, "vims_logo.png"), "jslib/vims_logo.png")
                os.replace(z + ".part", z)
                print("%s %s: %d pages%s" % (prod, os.path.basename(run), done,
                                             ("; " + "; ".join(probs)) if probs else ""))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        if "--latest" in argv and rs:
            dst = os.path.join(out, "latest", prod)
            tmp = dst + ".new"
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp)
            done, probs = style_run(rs[-1], prod, table, pk_runs, tmp)
            with open(os.path.join(tmp, "RUN.txt"), "w") as f:
                f.write("%s run %s, %d pages\n" % (prod, os.path.basename(rs[-1]), done))
            shutil.rmtree(dst, ignore_errors=True)
            os.replace(tmp, dst)
            print("latest %s %s: %d pages%s" % (prod, os.path.basename(rs[-1]), done,
                                                ("; " + "; ".join(probs)) if probs else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
