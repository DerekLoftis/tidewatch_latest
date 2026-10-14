"""Add static tidal datum reference lines (and the moving m30 mean) to a
Tidewatch 3.0 Highcharts page.

    python3 add_datums.py STATION IN.html[.gz] PICKLE.pypickle[.gz] {MLLW,NAVD} OUT.html
        [--cdn | --inline] [--logo=PNG] [--datums=NOAA.json] [--twoffset=FT]
        [--datumsjson=FILE] [--rebase=DELTA] [--note=TEXT] [--jsdir=REL]
        [--brand=stormsense] [--thresholds=FILE] [--floodoffset=FT] [--navdlines=mllw]

Datums come from NOAA's metadata API (--datums, the raw response of
mdapi/prod/webapi/stations/<id>/datums.json?units=english) and are converted to
feet above MLLW; without --datums the built-in DATUMS table is used.
m30 is the 30-day mean of the observations in the run's pickle (NAVD88), moved
onto the MLLW chart with Tidewatch's own MLLW/NAVD offset (--twoffset, the
constant gap between its _MLLW and _NAVD CSVs) so it sits on the same basis as
the plotted data.

--brand=stormsense swaps in the StormSense logo and disclaimer. --thresholds
adds NWS flood category lines (Action, Minor, Moderate, Major) from a JSON
object {"datum": "MLLW"|"NAVD88", "action": ft, "minor": ..., ...} in the gauge's
own datum; --navdlines=mllw keeps only the MLLW line among the tidal datums on a
NAVD chart. Every reference line can be hidden or shown by clicking its label.
"""
import gzip
import json
import os
import math
import pickle
import sys

DISCLAIMER = "Disclaimer: Tidewatch Charts\u2122 are automated storm tide forecasts; User assumes all risk"
BRANDS = {
    "vims": (DISCLAIMER, "vims_logo.png"),
    "stormsense": ("Disclaimer: StormSense\u2122 Predictive Plots are computer-generated operational storm tide "
                   "forecasts; the user assumes any and all risk.", "stormsense_logo.png"),
}
# NWS flood categories, colors as on NWPS hydrographs (water.noaa.gov)
FLOOD = (("action", "Action", "#FFFF00"), ("minor", "Minor", "#FF9900"),
         ("moderate", "Moderate", "#FF0000"), ("major", "Major", "#CC33FF"))

DATUMS = {
    # ft above MLLW; navd88 = height of NAVD88 zero above MLLW
    "SWPT": {"HAT": 3.53, "MHHW": 2.76, "MSL": 1.35, "MLLW": 0.0, "LAT": -0.67, "NAVD88": 1.61},
}

# gray styles: (dash style, width, color)
STYLE = {
    "HAT": ("Dash", 1, "#8a8a8a"),
    "MHHW": ("ShortDash", 1, "#8a8a8a"),
    "MSL": ("LongDash", 1, "#8a8a8a"),
    "LAT": ("Dash", 1, "#8a8a8a"),
    "MLLW": ("Solid", 1, "#8a8a8a"),
    "NAVD88": ("Solid", 1, "#8a8a8a"),
    "m30": ("DashDot", 1.5, "#16687E"),  # VIMS teal
    "HAT+": ("Dot", 1, "#b0b0b0"),
}
STYLE.update({name: ("Solid", 2, color) for _, name, color in FLOOD})



def read(path, mode="rt"):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, mode) as f:
        return f.read()


def load_noaa(path):
    """NOAA mdapi datums.json -> {name: ft above MLLW} for the lines we draw."""
    j = json.load(open(path))
    vals = {d["name"]: d["value"] for d in j.get("datums", []) if d.get("value") is not None}
    for k in ("HAT", "LAT"):
        if k not in vals and j.get(k) is not None:
            vals[k] = j[k]
    if "MLLW" not in vals:
        raise SystemExit("%s: no MLLW in NOAA datums" % path)
    base = vals["MLLW"]
    out = {k: round(vals[k] - base, 3) for k in ("HAT", "MHHW", "MSL", "MLLW", "LAT", "NAVD88") if k in vals}
    out["_epoch"] = j.get("epoch")
    return out


def m30_navd(pickle_path):
    o = pickle.loads(read(pickle_path, "rb"))
    vals = [float(v) for _, v in o["observations"] if v is not None and not math.isnan(float(v))]
    return sum(vals) / len(vals)


def main(argv):
    station, src, pkl, datum, out = argv[:5]
    cdn = "--cdn" in argv
    opt = dict(a[2:].split("=", 1) for a in argv if a.startswith("--") and "=" in a)
    if "datums" in opt:
        d = load_noaa(opt["datums"])
    elif "datumsjson" in opt:  # already in ft above MLLW, e.g. from VDatum (see make_usgs_charts.py)
        d = json.load(open(opt["datumsjson"]))
    else:
        d = DATUMS[station]
    est = set(d.get("_estimated", []))
    twoff = float(opt["twoffset"]) if "twoffset" in opt else d.get("NAVD88")
    navd = d.get("NAVD88")  # NAVD88 zero above MLLW for the datum lines
    if navd is None:
        # NOAA publishes no NAVD88 here: no NAVD88 line on the MLLW chart, and the
        # NAVD chart can only be built from Tidewatch's own (non-zero) offset
        if datum != "MLLW":
            if not twoff:
                raise SystemExit("%s: no NOAA NAVD88 and no usable Tidewatch offset" % station)
            navd = twoff
    shift = 0.0 if datum == "MLLW" else -navd  # MLLW heights -> NAVD heights
    m30 = m30_navd(pkl) + (twoff if datum == "MLLW" else 0.0)
    lines = []
    keep = ("MLLW",) if opt.get("navdlines") == "mllw" and datum != "MLLW" else ("HAT", "MHHW", "MSL", "MLLW", "LAT", "NAVD88")
    for name in keep:
        if name == "NAVD88":
            if navd is not None:
                lines.append({"name": name, "value": round(navd + shift, 2)})
        elif name in d:
            lines.append({"name": name + (" (est.)" if name in est else ""), "value": round(d[name] + shift, 2),
                          "key": name})
    if "thresholds" in opt:
        t = json.load(open(opt["thresholds"]))
        # gauge datum -> chart datum with Tidewatch's own MLLW-NAVD offset (--floodoffset,
        # from the run's CSV pair), so the lines sit on the same basis as the plotted data
        off = float(opt["floodoffset"]) if "floodoffset" in opt else (navd if navd is not None else twoff)
        if t.get("datum", "MLLW") == "MLLW":
            tshift = 0.0 if datum == "MLLW" else -off
        else:  # NAVD88 gauge
            tshift = 0.0 if datum != "MLLW" else off
        if tshift is not None:
            for key, name, _ in FLOOD:
                v = t.get(key)
                if v is not None and v > -999:
                    lines.append({"name": name, "value": round(v + tshift, 2)})
    lines.append({"name": "m30", "value": round(m30, 2)})
    hats = len(keep) > 1  # HAT+n steps only with the full set of datum lines
    hk = next((k for k in ("HAT", "MHHW", "MSL") if k in d), None)  # HAT+n steps from MHHW (or MSL) without a HAT
    disclaimer, logo_name = BRANDS[opt.get("brand", "vims")]
    cfg = {"lines": lines, "hat": round(d[hk] + shift, 2) if hats and hk else None, "hatName": hk, "style": STYLE,
           "datum": datum, "disclaimer": disclaimer, "logo": None, "logoAspect": 0.25, "note": opt.get("note")}
    logo = next((a.split("=", 1)[1] for a in argv if a.startswith("--logo=")),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), logo_name))
    if os.path.exists(logo):
        import base64, struct
        raw = open(logo, "rb").read()
        w, h = struct.unpack(">II", raw[16:24])  # PNG header
        cfg["logoAspect"] = h / w
        if "jsdir" in opt:  # shared library folder next to the pages (archive bundles)
            cfg["logo"] = opt["jsdir"].rstrip("/") + "/" + os.path.basename(logo)
        else:
            cfg["logo"] = "data:image/png;base64," + base64.b64encode(raw).decode()

    js = """
            // --- tidal datum reference lines (added) ---
            (function () {
                var cfg = %s;
                var dmax = -1e9, dmin = 1e9;
                chart.series.forEach(function (s) {  // residuals set the floor, not the HAT+n steps
                    var res = s.name === 'Residual';
                    s.yData.forEach(function (v) {
                        if (v !== null && !isNaN(v)) { if (!res) dmax = Math.max(dmax, v); dmin = Math.min(dmin, v); }
                    });
                });
                var hiLine = cfg.lines.reduce(function (a, l) { return Math.max(a, l.value); }, -1e9);
                var top = cfg.hat === null ? 0 : Math.max(2, Math.ceil(dmax - cfg.hat));
                var pl = [];
                function add(name, value, key, label) {
                    var st = cfg.style[key], flood = /^(Action|Minor|Moderate|Major)$/.test(key);
                    pl.push({id: 'ref-' + name, value: value, dashStyle: st[0], width: st[1], color: st[2],
                             zIndex: flood ? 2 : 1,
                             label: {text: label, align: 'right', textAlign: 'left', x: 6, y: 3,
                                     style: {color: key === 'Action' ? '#a39700' : (key === 'm30' || flood ? st[2] : '#6f6f6f'),
                                             fontSize: '10px', fontWeight: flood ? 'bold' : 'normal'}}});
                }
                cfg.lines.forEach(function (l) {
                    add(l.name, l.value, l.key || l.name, l.name + ' ' + l.value.toFixed(2));
                });
                for (var n = 1; cfg.hat !== null && n <= top; n++) {
                    var v = cfg.hat + n;
                    add(cfg.hatName + '+' + n, v, 'HAT+', cfg.hatName + '+' + n + ' ' + v.toFixed(2));
                }
                var lo = Math.min(dmin, cfg.lines.reduce(function (a, l) { return Math.min(a, l.value); }, 1e9));
                var ax = chart.yAxis[0];
                chart.update({chart: {marginRight: 92}}, false);
                var ymax = cfg.hat === null ? Math.ceil(Math.max(dmax, hiLine) * 2) / 2 + 0.25 : cfg.hat + top + 0.25;
                ax.update({max: ymax, min: Math.floor(lo * 2) / 2 - 0.25,
                           tickInterval: 1, startOnTick: false, endOnTick: false});
                // labels sit in the right margin; nudge apart any that would overlap
                var order = pl.slice().sort(function (a, b) { return b.value - a.value; });
                var prev = -1e9, gap = 11;
                order.forEach(function (p) {
                    var py = ax.toPixels(p.value), ly = Math.max(py, prev + gap);
                    p.label.y = 3 + (ly - py);
                    prev = ly;
                });
                ax.update({plotLines: pl});

                // click a line's label to hide or show the line (the label stays, faded)
                var hidden = {};
                var applyToggles = function () {
                    ax.plotLinesAndBands.forEach(function (p) {
                        var id = p.options.id, off = !!hidden[id];
                        if (!id) return;
                        if (p.svgElem) p.svgElem.attr({visibility: off ? 'hidden' : 'visible'});
                        if (p.label) {
                            p.label.attr({opacity: off ? 0.35 : 1}).css({cursor: 'pointer'});
                            if (!p.label.twToggle) {
                                p.label.twToggle = true;
                                p.label.on('click', function () { hidden[id] = !hidden[id]; applyToggles(); });
                            }
                        }
                    });
                };
                applyToggles();
                Highcharts.addEvent(chart, 'redraw', applyToggles);

                // disclaimer under the "Latest Observation" axis title
                var xa = chart.xAxis[0];
                chart.update({chart: {marginBottom: null}}, false);
                xa.setTitle({text: xa.options.title.text + '<br/><span style="font-size:10px;color:#6f6f6f">' +
                             cfg.disclaimer + '</span>'});

                if (cfg.note) {  // data caveat as a subtitle
                    chart.setTitle(null, {text: cfg.note, style: {color: '#a05a00', fontSize: '11px'}});
                }

                // faded logo in the first corner that no legend series crosses
                if (cfg.logo) {
                    var logoEl = null;
                    var placeLogo = function () {
                        if (logoEl) { logoEl.destroy(); logoEl = null; }
                        var w = Math.min(170, chart.plotWidth * 0.22), h = w * cfg.logoAspect, pad = 8;
                        var L = chart.plotLeft, T = chart.plotTop, R = L + chart.plotWidth, B = T + chart.plotHeight;
                        var corners = [[R - pad - w, T + pad], [L + pad, T + pad], [R - pad - w, B - pad - h], [L + pad, B - pad - h]];
                        var best = null, bestHits = 1e9;
                        corners.forEach(function (c) {
                            if (best && bestHits === 0) return;
                            var hits = 0, x0 = c[0] - 4, x1 = c[0] + w + 4, y0 = c[1] - 4, y1 = c[1] + h + 4;
                            chart.series.forEach(function (s) {
                                if (!s.visible) return;
                                s.points.forEach(function (pt) {
                                    if (pt.y === null || pt.plotX === undefined) return;
                                    var px = L + pt.plotX, py = T + pt.plotY;
                                    if (px >= x0 && px <= x1 && py >= y0 && py <= y1) hits++;
                                });
                            });
                            if (hits < bestHits) { best = c; bestHits = hits; }
                        });
                        logoEl = chart.renderer.image(cfg.logo, best[0], best[1], w, h)
                            .attr({opacity: 0.5, zIndex: 0}).add();
                    };
                    placeLogo();
                    Highcharts.addEvent(chart, 'redraw', placeLogo);
                }
            })();
""" % json.dumps(cfg)

    html = read(src)
    if "rebase" in opt and datum == "MLLW":
        # move the plotted MLLW series from Tidewatch's MLLW onto the datum file's MLLW
        import re
        delta = float(opt["rebase"])
        point = re.compile(r"(Date\.UTC\([^)]*\), )(-?[0-9.]+(?:[eE][-+]?[0-9]+)?)\]")

        def series(m):  # water levels move; the residual series does not
            if '"name": "Residual"' in m.group(2):
                return m.group(0)
            return point.sub(lambda p: p.group(1) + repr(round(float(p.group(2)) + delta, 4)) + "]", m.group(1)) + m.group(2)

        html = re.sub(r'(\{"data": \[.*?\]\])(, [^{}]*?"name": "[^"]*"[^{}]*\})', series, html, flags=re.S)
    anchor = "chart.addSeries(data[ix]);\n            }"
    assert anchor in html, "unexpected page layout"
    html = html.replace(anchor, anchor + "\n" + js, 1)
    if cdn:  # hosts allowed in published artifacts
        html = html.replace("https://ajax.googleapis.com/ajax/libs/jquery/1.9.1/jquery.min.js",
                            "https://code.jquery.com/jquery-1.9.1.min.js")
        html = html.replace("https://code.highcharts.com/6/", "https://cdn.jsdelivr.net/npm/highcharts@6/")
        html = html.replace('<link href="https://www.highcharts.com/highslide/highslide.css" rel="stylesheet" />', "")
    if "jsdir" in opt:  # point the scripts at a shared local copy, e.g. jslib/ inside a run's zip
        import re
        rel = opt["jsdir"].rstrip("/")
        names = {"jquery": "jquery.min.js", "highcharts.js": "highcharts.js", "highcharts-more.js": "highcharts-more.js",
                 "heatmap.js": "heatmap.js", "exporting.js": "exporting.js"}
        def local(m):
            for key, name in names.items():
                if m.group(1).endswith(key) or (key == "jquery" and "jquery" in m.group(1)):
                    return '<script type="text/javascript" src="%s/%s"></script>' % (rel, name)
            return m.group(0)
        html = re.sub(r'<script type="text/javascript" src="([^"]+)"></script>', local, html)
        html = html.replace('<link href="https://www.highcharts.com/highslide/highslide.css" rel="stylesheet" />', "")
    if "--inline" in argv:  # embed jQuery and Highcharts so the page needs no network
        import re
        lib = os.environ.get("TW_JSLIB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "jslib"))
        files = {"jquery": "jquery.min.js", "highcharts.js": "highcharts.js", "highcharts-more.js": "highcharts-more.js",
                 "heatmap.js": "heatmap.js", "exporting.js": "exporting.js"}
        def inline(m):
            src = m.group(1)
            for key, name in files.items():
                if src.endswith(key) or (key == "jquery" and "jquery" in src):
                    with open(os.path.join(lib, name)) as f:
                        return "<script>" + f.read().replace("</script", "<\\/script") + "</script>"
            return m.group(0)
        html = re.sub(r'<script type="text/javascript" src="([^"]+)"></script>', inline, html)
        html = html.replace('<link href="https://www.highcharts.com/highslide/highslide.css" rel="stylesheet" />', "")
    with open(out, "w") as f:
        f.write(html)
    print(out, json.dumps(lines))


if __name__ == "__main__":
    main(sys.argv[1:])
