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

## Stand-ins for stations Tidewatch doesn't publish yet

The VIMS station pages link Tidewatch charts for about 37 gauges that web4 has
never published (VDEM, StormSense, USGS, county and NOAA gauges such as SLIM,
SGSM, CRSM, WCKV, WSPV, JLMV, GTND). Until Tidewatch adds them, `noaa_override.py`
builds them under Tidewatch's file names from the NWS NWPS API (30 days of 5- to
15-minute stage): the gauge's stage moved to MLLW, a harmonic tide fitted to the
record (13 constituents until 6 months are archived), and Tidewatch's 36-hour
forecast with the surge of the nearest P-ETSS point. A station's stand-in stops
as soon as web4 publishes the station itself.

`nwps_stations.csv` holds, per station, the NWPS id, the datum its stage is on,
the offset to MLLW (with the time of any datum change), NAVD88 above MLLW, the
P-ETSS point and a note on how the datum was checked (NWPS gauge datums, NOAA
VDatum, and low waters at the nearest NOAA gauges). Their tidal datums for the
chart lines are in `styler/stations_usgs/_datums/`. `nwps_capture.py` (in the
floodvision workflow) archives their hourly stage in `nwps/<ST>_<YYYY-MM>.csv`.
GNTD (the spelling on the VIMS Georgetown page) repeats GTND.

Disclaimer: Tidewatch Charts(TM) are automated storm tide forecasts; User assumes all risk.
