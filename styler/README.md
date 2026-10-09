# Tidewatch datum styler (self-contained)

Adds Derek's datum lines (HAT+n, HAT, MHHW, MSL, MLLW, LAT, NAVD88), the m30 line in VIMS teal, the disclaimer and
the 50% VIMS logo to Tidewatch 3.0 Highcharts pages, keeping Tidewatch's file names.

    pip install -r requirements.txt
    python3 style_pages.py --product f36 --pages RAW/f36 --csv RAW/f36 --pickles RAW/f36 --out SITE/f36 --jsdir=../jslib
    python3 style_pages.py --product 3d  --pages RAW/3d  --csv RAW/3d  --pickles RAW/f36 --out SITE/3d  --jsdir=../jslib
    python3 style_pages.py --product 30d --pages RAW/30d --csv RAW/30d --pickles RAW/f36 --out SITE/30d --jsdir=../jslib
    mkdir -p SITE/jslib && cp jslib/* vims_logo.png SITE/jslib/

Inputs: the run's pages (.html or .html.gz), its MLLW/NAVD CSV pair and the f36 pickles (m30 and station list).
Without --jsdir the libraries are inlined (about 0.5 MB per page). Pages without datums (stale station IDs) are copied
unchanged. Datum tables: noaa_datums/ (NOAA mdapi) and stations_usgs/_datums/ (VDatum grids, from make_usgs_charts.py
in the project's tidewatch-datum-charts folder). Rebuild this package there if datums change.
