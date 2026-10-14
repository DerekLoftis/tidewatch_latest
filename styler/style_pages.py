"""Style one folder of Tidewatch Highcharts pages in place of the originals,
keeping Tidewatch's own file names (for the public tidewatch_latest site).

    python3 style_pages.py --product {f36,3d,30d} --pages DIR --csv DIR --pickles DIR --out DIR
                           [--jsdir=REL | --inline]

--pages    folder with <ST>_2018_<product>[_MLLW]_hc.html (or .html.gz)
--csv      folder with that product's <ST>_2018_<product>_{MLLW,NAVD}.csv
--pickles  folder with the f36 pickles <ST>_2018_f36.pypickle[.gz] (for m30)
--out      where styled pages go, under the same names (.html)
--jsdir    relative URL of a folder holding jquery.min.js, highcharts*.js,
           heatmap.js, exporting.js and vims_logo.png (copy them from this
           package's jslib/ and vims_logo.png); default --inline embeds them.
--brand    vims (default) or stormsense: logo and disclaimer
--thresholds  JSON {ST: {"datum": "MLLW"|"NAVD88", "action": ft, ...}} of NWS flood
           categories, drawn on the NAVD charts (see make_thresholds.py)
--navdlines=mllw  on NAVD charts keep only the MLLW datum line

Pages for stations without datums (stale IDs) are copied unchanged, so every
original name still exists in --out. Exit status 0 even when a page fails;
failures are printed and the original page is copied in its place.
"""
import argparse, glob, gzip, os, shutil, sys, tempfile

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
os.environ.setdefault("TW_DATUMS", here)
import style_archive as sa


def read(p):
    return gzip.open(p, "rb").read() if p.endswith(".gz") else open(p, "rb").read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True, choices=sa.PRODUCTS)
    ap.add_argument("--pages", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--pickles", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jsdir")
    ap.add_argument("--inline", action="store_true")
    ap.add_argument("--brand", default="vims")
    ap.add_argument("--thresholds")
    ap.add_argument("--navdlines")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    work = tempfile.mkdtemp()
    try:
        # gather everything into one gz-named run folder, the layout style_archive expects
        run = os.path.join(work, "run", "0000-00-00T00Z")
        pkd = os.path.join(work, "pk", "0000-00-00T00Z")
        os.makedirs(run); os.makedirs(pkd)
        pages = {}
        for p in glob.glob(os.path.join(a.pages, "*_hc.html*")):
            base = os.path.basename(p).replace(".gz", "")
            if "_%s_" % a.product not in base:
                continue
            # Tidewatch's 30d folder also holds stale <ST>_2018_30d_NAVD_hc.html pages; the
            # current NAVD page has no datum in its name, and both would style to one file
            if "_NAVD_hc.html" in base and os.path.exists(os.path.join(a.pages, base.replace("_NAVD_hc", "_hc"))):
                continue
            open(os.path.join(run, base + ".gz"), "wb").write(gzip.compress(read(p)))
            pages[base] = p
        for p in glob.glob(os.path.join(a.csv, "*_%s_*.csv" % a.product)):
            shutil.copy(p, run)
        for p in glob.glob(os.path.join(a.pickles, "*_f36.pypickle*")):
            dst = os.path.join(pkd, os.path.basename(p).replace(".gz", "") + ".gz")
            open(dst, "wb").write(gzip.compress(read(p)))
        pk_runs = {"0000-00-00T00": pkd}
        table = sa.stations(os.environ["TW_DATUMS"], pk_runs)
        tmp = os.path.join(work, "styled")
        os.makedirs(tmp)
        flood = None
        if a.thresholds:
            import json
            flood = {}
            for st, t in json.load(open(a.thresholds)).items():
                flood[st] = os.path.join(work, "flood_%s.json" % st)
                json.dump(t, open(flood[st], "w"))
        options = ["--brand=" + a.brand] + (["--navdlines=" + a.navdlines] if a.navdlines else [])
        done, probs = sa.style_run(run, a.product, table, pk_runs, tmp, jsdir=a.jsdir, options=options, flood=flood)
        for base, src in sorted(pages.items()):
            st, dat = base.split("_")[0], ("MLLW" if "_MLLW_hc" in base else "NAVD")
            tag = base.split("_%s_" % a.product)[0]
            styled = os.path.join(tmp, "%s_%s_%s_hc_datums.html" % (tag, a.product, dat))
            if os.path.exists(styled):
                shutil.move(styled, os.path.join(a.out, base))
            else:
                open(os.path.join(a.out, base), "wb").write(read(src))
        print("%s: %d of %d pages styled%s" % (a.product, done, len(pages), ("; " + "; ".join(probs)) if probs else ""))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
