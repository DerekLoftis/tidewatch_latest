# Tidewatch latest

The newest Tidewatch storm tide forecasts (f36) and hindcasts (3d, 30d), chart
pages and CSVs in MLLW and NAVD88, served on GitHub Pages at fixed addresses
that are overwritten every 6 hours:

    https://derekloftis.github.io/tidewatch_latest/                      index
    https://derekloftis.github.io/tidewatch_latest/f36/SWPT_2018_f36_MLLW_hc.html
    https://derekloftis.github.io/tidewatch_latest/3d/SWPT_2018_3d_NAVD.csv
    https://derekloftis.github.io/tidewatch_latest/sources.csv

File names match Tidewatch's own (https://web4.vims.edu/physical/tidewatch/).
The site is built elsewhere and pushed to this repository's `site` branch;
`.github/workflows/deploy.yml` deploys it when told to. This repository holds
no build code.

`floodvision/` and `greenstream/` are the USLN-X and CNRC observation archives
up to the move (October 2026); new rows are published on the site under
`obs/floodvision/` and `obs/greenstream/`.

Disclaimer: Tidewatch Charts(TM) are automated storm tide forecasts; User assumes all risk.
