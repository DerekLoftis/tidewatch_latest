# Tidewatch latest

The newest Tidewatch storm tide forecasts (f36) and hindcasts (3d, 30d), chart
pages and CSVs in MLLW and NAVD88, copied from
https://web4.vims.edu/physical/tidewatch/ every 6 hours and served on GitHub
Pages at fixed addresses that are overwritten each update:

    https://derekloftis.github.io/tidewatch_latest/                      index
    https://derekloftis.github.io/tidewatch_latest/f36/SWPT_2018_f36_MLLW_hc.html
    https://derekloftis.github.io/tidewatch_latest/3d/SWPT_2018_3d_NAVD.csv

File names match Tidewatch's own. The chart pages are restyled by `styler/`
(datum lines HAT to LAT and NAVD88, the m30 line, disclaimer and VIMS logo;
libraries served from `jslib/`, so no CDN is needed). Pages for stations
without datums are published unchanged. The full history is archived separately.
`.github/workflows/publish.yml` runs `publish.py` at 00:20, 06:20, 12:20 and
18:20 UTC (and on demand) and deploys the result; nothing is committed, so the
repository stays small.

Disclaimer: Tidewatch Charts(TM) are automated storm tide forecasts; User assumes all risk.
