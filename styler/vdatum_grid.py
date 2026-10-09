"""Read NOAA VDatum .gtx grids and look up tidal datums at a point.

GTX layout: big-endian header (south lat, west lon, dlat, dlon as float64;
rows, cols as int32), then rows*cols float32 values from the south-west
corner, row by row northward. -88.8888 marks no data. Values are metres.

Grids: tss is NAVD88 relative to local mean sea level (LMSL); mllw, mhhw,
mtl, ... are relative to LMSL. So, in metres,
    NAVD88 zero above MLLW = tss - mllw,   datum X above MLLW = x - mllw.
"""
import glob
import math
import os
import struct

NODATA = -88.0
FT = 3.2808333  # US survey feet per metre


class Gtx:
    def __init__(self, path):
        raw = open(path, "rb").read()
        self.lat0, self.lon0, self.dlat, self.dlon = struct.unpack(">4d", raw[:32])
        self.rows, self.cols = struct.unpack(">2i", raw[32:40])
        self.vals = struct.unpack(">%df" % (self.rows * self.cols), raw[40:40 + 4 * self.rows * self.cols])
        self.path = path

    def _v(self, r, c):
        if 0 <= r < self.rows and 0 <= c < self.cols:
            v = self.vals[r * self.cols + c]
            return None if v < NODATA else v
        return None

    def _yx(self, lat, lon):
        if self.lon0 > 180 and lon < 0:
            lon += 360.0
        return (lat - self.lat0) / self.dlat, (lon - self.lon0) / self.dlon

    def at(self, lat, lon, maxkm=0.0):
        """Bilinear value inside water cells; otherwise the nearest valid cell
        within maxkm. Returns (value_m, km_to_cell) or (None, None)."""
        y, x = self._yx(lat, lon)
        r, c = int(math.floor(y)), int(math.floor(x))
        fy, fx = y - r, x - c
        q = [self._v(r, c), self._v(r, c + 1), self._v(r + 1, c), self._v(r + 1, c + 1)]
        if all(v is not None for v in q):
            return q[0] * (1 - fx) * (1 - fy) + q[1] * fx * (1 - fy) + q[2] * (1 - fx) * fy + q[3] * fx * fy, 0.0
        ky = 111.2 * self.dlat
        kx = 111.2 * math.cos(math.radians(lat)) * self.dlon
        n = int(maxkm / min(kx, ky)) + 1
        best = (None, None)
        for rr in range(max(0, r - n), min(self.rows, r + n + 2)):
            for cc in range(max(0, c - n), min(self.cols, c + n + 2)):
                v = self._v(rr, cc)
                if v is None:
                    continue
                d = math.hypot((rr - y) * ky, (cc - x) * kx)
                if d <= maxkm and (best[1] is None or d < best[1]):
                    best = (v, d)
        return best


def regions(root):
    """{region: {grid name: path}} for every region folder under root."""
    out = {}
    for d in sorted(glob.glob(os.path.join(root, "*", ""))):
        reg = os.path.basename(os.path.dirname(d))
        g = {}
        for p in glob.glob(os.path.join(d, "*.gtx")):
            name = os.path.basename(p)[:-4].replace("_crop", "").split("_")[-1]
            g[name] = p
        if g:
            out[reg] = g
    return out


def met(root, region):
    vals = {}
    for p in glob.glob(os.path.join(root, region, "*.met")):
        for line in open(p):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                vals[k] = v
    return vals
