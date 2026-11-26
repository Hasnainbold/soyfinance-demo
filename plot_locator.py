"""
plot_locator.py -- find a farmer's plot on the map from his 7/12 or 8A, and cross-check it.

Why
---
A 7/12 tells you *which* plot (district / taluka / village / survey no.). An 8A tells
you *every* plot one khata (account) holds in that village. Neither says *where* the
land is. Maharashtra's cadastral map portal, Bhunaksha, does:

    mahabhunakasha.mahabhumi.gov.in      (public, no login, no captcha)

For a survey number it returns the plot's boundary polygon, its lat/long extent, and
the holder rows on that plot (survey/subdivision, khata no., owner name, area) in
CLEAN Unicode Marathi. That last part matters: the 7/12 / 8A PDFs from MahaBhumi use a
broken font that drops ~20% of the Devanagari, so their owner names are lossy.
Bhunaksha's names are not, which gives a second, cleaner ownership check.

Flow
----
    7/12 or 8A  -> district / taluka / village (+ village code) + survey no(s) + khata no.
                -> resolve_location()  : fuzzy-match those names to Bhunaksha's codes
                -> plot_info()         : polygon + holder rows for the survey no.
                -> extent()            : lat/long bounding box (the portal's own georef)
                -> cross_check()       : khata present? owner = borrower? area matches?

Bhunaksha endpoints (read from the portal's own georef.js, confirmed live 2026-09-26):
    POST /rest/VillageMapService/ListsAfterLevelGeoref  state, level, codes, hasmap=true
    POST /rest/VillageMapService/kidelistFromGisCodeMH  state, logedLevels=<giscode>
    POST /rest/MapInfo/getVVVVExtentGeoref             state, giscode, srs=4326
    POST /rest/MapInfo/getPlotInfo                     state, giscode, plotno, srs=4326
    POST /rest/MapInfo/getExtentGeoref                 state, giscode, plotid, srs=4326
    giscode = <category R|U> + ("VM" if R else "CM") + <district> + <taluka> + <village code>
              e.g. "RVM" + "28" + "05" + "272800050353410000"  (Tivatghal, Chakur, Latur)
    getPlotInfo returns 204 (empty) for an unknown survey no. Plots are mapped at the
    SURVEY level ("126"), not the subdivision ("126/1") -- the subdivisions appear as
    holder rows inside the survey's info text.
    the_geom is WKT in UTM zone 43N metres (EPSG:32643); the extent calls return WGS84.

Caveat (the portal's own disclaimer): Bhunaksha maps are "for viewing purposes" and
cannot be used for legal purposes. Treat the location as strong corroboration, not as
the title document -- the 7/12 verified against MahaBhumi remains the title check.
"""

from __future__ import annotations

import difflib
import math
import re
import unicodedata
from functools import lru_cache

BASE = "https://mahabhunakasha.mahabhumi.gov.in"
STATE_MH = "27"

# ─────────────────────────────────────────────────────────── name matching ──
# Devanagari dependent vowel signs, virama, nukta, anusvara etc. The MahaBhumi PDF font
# drops or re-orders exactly these (e.g. "तिवटघाळ" reads back as "ितटघा"), so comparing
# the consonant skeleton is far more robust than comparing the raw strings.
_DEV_MARKS = re.compile(r"[ऀ-ःऺ-ॏ॑-ॗॢॣ]")


def dev_skeleton(s: str) -> str:
    s = unicodedata.normalize("NFC", re.sub(r"\(cid:\d+\)", "", s or ""))
    s = re.sub(r"\(.*?\)", " ", s)                      # "(खु.)" / "(बु.)" qualifiers
    s = _DEV_MARKS.sub("", s)
    return re.sub(r"[^\w]", "", s).lower()


def _ratio(a: str, b: str) -> float:
    try:
        from rapidfuzz import fuzz
        return float(fuzz.ratio(a, b))
    except Exception:
        return 100.0 * difflib.SequenceMatcher(None, a, b).ratio()


def name_score(a: str, b: str) -> float:
    """0-100 similarity of two (possibly lossy) Devanagari/Latin names."""
    if not a or not b:
        return 0.0
    full = _ratio(unicodedata.normalize("NFC", a.strip()), unicodedata.normalize("NFC", b.strip()))
    skel = _ratio(dev_skeleton(a), dev_skeleton(b))
    # token-wise: best average over the other string's tokens (handles word order)
    ta, tb = a.split(), b.split()
    tok = 0.0
    if ta and tb:
        tok = sum(max(_ratio(dev_skeleton(x), dev_skeleton(y)) for y in tb) for x in ta) / len(ta)
    return round(max(full, skel, tok), 1)


def best_match(query: str, options: list, key=lambda o: o["value"]) -> tuple:
    """(best option, score) of `query` among `options` by name_score."""
    best, score = None, 0.0
    for o in options:
        s = name_score(query, key(o))
        if s > score:
            best, score = o, s
    return best, score


# ─────────────────────────────────────────────────────── UTM 43N -> WGS84 ──
def utm_to_latlon(easting: float, northing: float, zone: int = 43,
                  northern: bool = True) -> tuple:
    """Inverse transverse Mercator on WGS84 (Snyder). Accurate to well under a metre."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    x = easting - 500000.0
    y = northing if northern else northing - 10000000.0
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    mu = (y / k0) / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    p1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
          + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
          + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
          + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    s, c, t = math.sin(p1), math.cos(p1), math.tan(p1)
    C1, T1 = ep2 * c * c, t * t
    N1 = a / math.sqrt(1 - e2 * s * s)
    R1 = a * (1 - e2) / (1 - e2 * s * s) ** 1.5
    D = x / (N1 * k0)
    lat = p1 - (N1 * t / R1) * (D ** 2 / 2
                                - (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * ep2) * D ** 4 / 24
                                + (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * ep2
                                   - 3 * C1 ** 2) * D ** 6 / 720)
    lon = lon0 + (D - (1 + 2 * T1 + C1) * D ** 3 / 6
                  + (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * ep2 + 24 * T1 ** 2)
                  * D ** 5 / 120) / c
    return math.degrees(lat), math.degrees(lon)


def parse_wkt_polygons(wkt: str) -> list:
    """MULTIPOLYGON/POLYGON WKT -> list of outer rings, each [(x, y), ...]."""
    rings = []
    for body in re.findall(r"\(\(\s*([^()]+?)\s*\)", wkt or ""):
        pts = []
        for pair in body.split(","):
            xy = pair.split()
            if len(xy) >= 2:
                pts.append((float(xy[0]), float(xy[1])))
        if len(pts) >= 3:
            rings.append(pts)
    return rings


def ring_area_m2(ring: list) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2)
                   in zip(ring, ring[1:] + ring[:1]))) / 2.0


def ring_centroid(ring: list) -> tuple:
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        cr = x1 * y2 - x2 * y1
        a += cr
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    if abs(a) < 1e-9:
        return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
    return cx / (3 * a), cy / (3 * a)


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# ──────────────────────────────────────────────────────── Bhunaksha info ──
_INFO_BLOCK_RE = re.compile(
    r"Survey No\.\s*:\s*(?P<survey>[^\n]+)\n\s*Total Area\s*:\s*(?P<area>[\d.]+)"
    r"(?:\n\s*Pot kharaba\s*:\s*(?P<pk>[\d.]+))?"
    r"(?:\n\s*Owner Name\s*:\s*(?P<owner>[^\n]*))?"
    r"(?:\n\s*Khata No\.\s*:\s*(?P<khata>[^\n]*))?", re.I)


def parse_plot_info_text(info: str) -> list:
    """Holder rows out of getPlotInfo's `info` text."""
    rows = []
    for m in _INFO_BLOCK_RE.finditer(info or ""):
        rows.append({
            "survey_no": m.group("survey").strip(),
            "area_total_ha": float(m.group("area")),
            "pot_kharab_ha": float(m.group("pk")) if m.group("pk") else 0.0,
            "owner": (m.group("owner") or "").strip(),
            "khata": (m.group("khata") or "").strip(),
        })
    return rows


class BhunakshaClient:
    """Thin client for Maharashtra Bhunaksha. All calls are public POSTs."""

    def __init__(self, base: str = BASE, state: str = STATE_MH, timeout: float = 30.0):
        self.base, self.state, self.timeout = base.rstrip("/"), state, timeout
        self._session = None

    # overridable in tests
    def _post(self, path: str, data: dict):
        import requests
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": "Mozilla/5.0 (AgriCred land verification)",
                "Referer": f"{self.base}/{self.state}/index.html",
                "X-Requested-With": "XMLHttpRequest"})
            try:                     # pick up any session cookie, as the browser would
                self._session.get(f"{self.base}/{self.state}/index.html", timeout=(10, 20))
            except Exception:
                pass
        r = self._session.post(self.base + path, data=data, timeout=(10, self.timeout))
        if r.status_code == 204 or not r.content.strip():
            return None                                   # e.g. unknown survey number
        r.raise_for_status()
        return r.json()

    # ── administrative levels ────────────────────────────────────────────
    def _lists(self, level: int, codes: str) -> list:
        return self._post("/rest/VillageMapService/ListsAfterLevelGeoref",
                          {"state": self.state, "level": level, "codes": codes,
                           "hasmap": "true"}) or []

    def districts(self, category: str = "R") -> list:
        lists = self._lists(0, "")
        return lists[1] if len(lists) > 1 else []

    def talukas(self, district_code: str, category: str = "R") -> list:
        lists = self._lists(2, f"{category},{district_code},")
        return lists[0] if lists else []

    def villages(self, district_code: str, taluka_code: str, category: str = "R") -> list:
        lists = self._lists(3, f"{category},{district_code},{taluka_code},")
        return lists[0] if lists else []

    @staticmethod
    def giscode(district_code: str, taluka_code: str, village_code: str,
                category: str = "R") -> str:
        return category + ("VM" if category == "R" else "CM") + district_code + taluka_code + village_code

    def survey_numbers(self, giscode: str) -> list:
        return self._post("/rest/VillageMapService/kidelistFromGisCodeMH",
                          {"state": self.state, "logedLevels": giscode}) or []

    def village_extent(self, giscode: str) -> dict | None:
        return self._post("/rest/MapInfo/getVVVVExtentGeoref",
                          {"state": self.state, "giscode": giscode, "srs": "4326"})

    # ── plots ─────────────────────────────────────────────────────────────
    def plot_info(self, giscode: str, survey_no: str) -> dict | None:
        """Plot info for a survey no., falling back from '126/1' to the mapped '126'."""
        tried = []
        for cand in dict.fromkeys([survey_no.strip(),
                                   re.split(r"[/\-]", survey_no.strip())[0]]):
            tried.append(cand)
            d = self._post("/rest/MapInfo/getPlotInfo",
                           {"state": self.state, "giscode": giscode, "plotno": cand,
                            "srs": "4326"})
            if d and d.get("plotid"):
                d["_queried_plotno"] = cand
                return d
        return None

    def extent(self, giscode: str, plotid: str) -> dict | None:
        return self._post("/rest/MapInfo/getExtentGeoref",
                          {"state": self.state, "giscode": giscode, "plotid": plotid,
                           "srs": "4326"})

    def map_report_url(self, giscode: str, plotno: str) -> str:
        return (f"{self.base}/signplotreportpublic.jsp?state={self.state}"
                f"&giscode={giscode}&plotno={plotno}")

    # ── resolve names from a 7/12 / 8A to Bhunaksha codes ────────────────
    def resolve_location(self, *, district: str, taluka: str, village: str,
                         village_code: str | None = None, category: str = "R",
                         district_code: str | None = None, taluka_code: str | None = None,
                         village_bhunaksha_code: str | None = None) -> dict:
        """
        Map the (often lossy) names printed on a 7/12 / 8A to Bhunaksha codes.

        Explicit *_code arguments win (the UI passes them after an operator confirms).
        Otherwise names are fuzzy-matched on their Devanagari consonant skeleton.
        `village_code` is the 6-digit code printed next to the village on the 7/12; it
        is the LGD (2011) code, while Bhunaksha keys villages by the 2001 census code,
        so it rarely matches directly -- it is tried first, then the name.
        """
        out = {"scores": {}, "candidates": {}}
        dists = self.districts(category)
        if district_code:
            d = next((x for x in dists if x["code"] == district_code), None); ds = 100.0
        else:
            d, ds = best_match(district, dists)
        if not d:
            raise LookupError(f"district {district!r} not found on Bhunaksha")
        out["scores"]["district"] = ds
        tals = self.talukas(d["code"], category)
        if taluka_code:
            t = next((x for x in tals if x["code"] == taluka_code), None); ts = 100.0
        else:
            t, ts = best_match(taluka, tals)
        if not t:
            raise LookupError(f"taluka {taluka!r} not found in {d['value']}")
        out["scores"]["taluka"] = ts
        vils = self.villages(d["code"], t["code"], category)
        v, vs, how = None, 0.0, "name"
        if village_bhunaksha_code:
            v = next((x for x in vils if x["code"] == village_bhunaksha_code), None)
            vs, how = 100.0, "operator"
        if not v and village_code:
            v = next((x for x in vils if str(village_code) in x["code"]), None)
            if v:
                vs, how = 100.0, "code"
        if not v:
            v, vs = best_match(village, vils)
        if not v:
            raise LookupError(f"village {village!r} not found in {t['value']}")
        out["scores"]["village"] = vs
        ranked = sorted(vils, key=lambda o: -name_score(village, o["value"]))[:5]
        out["candidates"]["village"] = [
            {"code": o["code"], "name": o["value"], "score": name_score(village, o["value"])}
            for o in ranked]
        out.update({
            "category": category,
            "district_code": d["code"], "district_name": d["value"],
            "taluka_code": t["code"], "taluka_name": t["value"],
            "village_code": v["code"], "village_name": v["value"],
            "village_matched_by": how,
            "giscode": self.giscode(d["code"], t["code"], v["code"], category),
            "all_talukas": [{"code": x["code"], "name": x["value"]} for x in tals],
            "all_villages": [{"code": x["code"], "name": x["value"]} for x in vils],
        })
        # weakest link decides whether an operator should confirm the match
        out["confident"] = min(ds, ts, vs) >= 70 and (
            len(ranked) < 2 or vs - name_score(village, ranked[1]["value"]) >= 8
            or how != "name")
        return out

    # ── one-call locate ───────────────────────────────────────────────────
    def locate_plot(self, *, giscode: str, survey_no: str) -> dict:
        info = self.plot_info(giscode, survey_no)
        if not info:
            return {"found": False, "giscode": giscode, "survey_no": survey_no,
                    "reason": f"survey no. {survey_no} not on the Bhunaksha village map"}
        ext = self.extent(giscode, info["plotid"]) or {}
        rings_utm = parse_wkt_polygons(info.get("the_geom", ""))
        polygon, area_m2, centroid = [], None, None
        if rings_utm:
            main = max(rings_utm, key=ring_area_m2)
            area_m2 = sum(ring_area_m2(r) for r in rings_utm)
            polygon = [[round(lon, 7), round(lat, 7)]
                       for lat, lon in (utm_to_latlon(x, y) for x, y in main)]
            clat, clon = utm_to_latlon(*ring_centroid(main))
            centroid = (round(clat, 7), round(clon, 7))
        bbox = None
        if ext.get("xmin") is not None:
            bbox = [ext["xmin"], ext["ymin"], ext["xmax"], ext["ymax"]]   # lon/lat
            if not centroid:
                centroid = ((ext["ymin"] + ext["ymax"]) / 2, (ext["xmin"] + ext["xmax"]) / 2)
        # sanity: projected polygon must sit inside the portal's own georef box
        georef_ok = None
        if bbox and polygon:
            lons, lats = [p[0] for p in polygon], [p[1] for p in polygon]
            tol = 0.0005                                  # ~50 m
            georef_ok = (min(lons) >= bbox[0] - tol and max(lons) <= bbox[2] + tol and
                         min(lats) >= bbox[1] - tol and max(lats) <= bbox[3] + tol)
            if not georef_ok:                              # never show a misplaced shape
                polygon = [[bbox[0], bbox[1]], [bbox[2], bbox[1]],
                           [bbox[2], bbox[3]], [bbox[0], bbox[3]]]
        plotno = info.get("_queried_plotno", survey_no)
        lat, lon = centroid if centroid else (None, None)
        return {
            "found": True, "giscode": giscode, "survey_no": survey_no,
            "mapped_plotno": plotno, "plotid": info["plotid"],
            "location_label": (info.get("gisinfo") or "").strip().rstrip(","),
            "holders": parse_plot_info_text(info.get("info", "")),
            "polygon_lonlat": polygon, "bbox_lonlat": bbox,
            "centroid": {"lat": lat, "lon": lon} if lat is not None else None,
            "map_area_ha": round(area_m2 / 10000, 4) if area_m2 else (
                round(float(info["map_area"]) / 10000, 4) if info.get("map_area") else None),
            "polygon_matches_georef": georef_ok,
            "google_maps_url": (f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"
                                if lat is not None else None),
            "map_report_url": self.map_report_url(giscode, plotno),
            "source": "bhunaksha",
        }


# ─────────────────────────────────────────────────────────── 8A parsing ──
# 8A rows: survey | cultivable | pot-kharab | total | assessment | ... | total levy
_8A_ROW_RE = re.compile(
    r"^\s*(?P<survey>\d{1,4}(?:/\d{1,3})?[A-Za-z]?)\s+(?P<cult>\d+\.\d{2,4})\s+"
    r"(?P<pk>\d+\.\d{2,4})\s+(?P<total>\d+\.\d{2,4})\s+(?P<assess>\d+(?:\.\d+)?)"
    r"(?P<rest>(?:\s+\d+(?:\.\d+)?)*)\s*$", re.M)


def parse_8a(text: str) -> dict:
    """
    Everything useful on a Maharashtra 8A (खाते उतारा / holding extract).

    Mapped from a real MahaBhumi 8A (Tivatghal, khata 311, downloaded 2026-09-06).
    Village/taluka/district/holder name come through lossy (broken PDF font); the
    numbers -- khata no., survey nos., areas, verification no. -- come through intact.
    """
    t = re.sub(r"\(cid:\d+\)", "", text or "")
    flat = re.sub(r"[ \t]+", " ", t)
    g = lambda pat: (m.group(1).strip() if (m := re.search(pat, flat)) else None)
    rec = {
        "doc": "8A",
        "year": g(r"(20\d{2}-\d{2})\s*गा"),
        "village": g(r"गा\S*\s*:-?\s*(\S+)\s+ता"),
        "taluka": g(r"ता\S*ुका\s*:-?\s*(\S+)"),
        "district": g(r"िज\S*ा\s*:-?\s*(\S+)"),
        "khata_no": g(r"खाते\s*\S*मांक\s*:\s*(\d+)"),
        "holder_names_lossy": None,
        "plots": [], "totals": None,
        "verification_no": g(r"\b(\d{16})\b"),
        "downloaded_on": g(r"डाउन\S*\s*\S*\s*\S*\s*िद\.\s*:\s*([\d\-]+)"),
    }
    m = re.search(r"खातेदारांची\s*ना\S*\s*:\s*([^\n]+?)\s*\.?\s*$", flat, re.M)
    if m:
        rec["holder_names_lossy"] = [n.strip(" .") for n in re.split(r",|;", m.group(1))
                                     if n.strip(" .")]
    for m in _8A_ROW_RE.finditer(t):
        rest = [float(x) for x in m.group("rest").split()]
        rec["plots"].append({
            "survey_no": m.group("survey"),
            "area_cultivable_ha": float(m.group("cult")),
            "pot_kharab_ha": float(m.group("pk")),
            "area_total_ha": float(m.group("total")),
            "assessment": float(m.group("assess")),
            "total_levy": rest[-1] if rest else None,
        })
    m = re.search(r"एकूण\s+(\d+\.\d{2,4})\s+(\d+\.\d{2,4})\s+(\d+\.\d{2,4})", flat)
    if m:
        rec["totals"] = {"area_cultivable_ha": float(m.group(1)),
                         "pot_kharab_ha": float(m.group(2)),
                         "area_total_ha": float(m.group(3))}
    return rec


def is_8a(text: str) -> bool:
    t = re.sub(r"\(cid:\d+\)", "", text or "")
    return bool(re.search(r"नमुना\s*आठ|८-अ|8-?अ|खाते\s*उतारा", t))


# ──────────────────────────────────────────────────────────── cross-check ──
OWNER_STRONG = 85.0      # clean-Unicode names: a genuine match scores 90-100
OWNER_CONFIRM = 70.0


def cross_check(loc: dict, *, survey_no: str, khata_no: str | None = None,
                borrower_name_devanagari: str = "", expected_area_ha: float | None = None,
                record_owner_lossy: str | None = None, area_tol_pct: float = 3.0) -> dict:
    """
    Check the Bhunaksha plot against what the 7/12 / 8A and the borrower claim.

    Statuses: pass / warn / confirm / fail, same bands as land_record_verify, plus
    "info" -- shown to the operator but never moves the verdict.
    """
    checks = []
    add = lambda **k: checks.append(k)
    if not loc.get("found"):
        add(check="plot_on_map", status="warn",
            detail=loc.get("reason", "plot not found on Bhunaksha"))
        return {"checks": checks, "verdict": "review", "row": None}
    add(check="plot_on_map", status="pass",
        detail=f"found at {loc['centroid']['lat']:.5f}, {loc['centroid']['lon']:.5f} "
               f"({loc['location_label']})" if loc.get("centroid") else "found")

    rows = loc.get("holders") or []
    sub = [r for r in rows if r["survey_no"].replace(" ", "") == survey_no.replace(" ", "")]
    pool = sub or rows
    row = None
    if khata_no:
        row = next((r for r in pool if r["khata"] == str(khata_no)), None)
        add(check="khata_on_map", status="pass" if row else "fail",
            detail=(f"khata {khata_no} holds {row['area_total_ha']} ha of {row['survey_no']}"
                    if row else f"khata {khata_no} is not a holder of survey {survey_no} "
                                f"on the map (holders: "
                                f"{', '.join(r['khata'] for r in pool) or 'none'})"))

    if borrower_name_devanagari:
        cand = [row] if row else pool
        best, score = best_match(borrower_name_devanagari, cand, key=lambda r: r["owner"])
        status = ("pass" if score >= OWNER_STRONG else
                  "confirm" if score >= OWNER_CONFIRM else "fail")
        add(check="owner_on_map", status=status,
            detail=f"{best['owner'] if best else '—'} (khata {best['khata'] if best else '—'})"
                   f" vs borrower {borrower_name_devanagari}: {score:.0f}%")
        if not row and best and score >= OWNER_CONFIRM:
            row = best
    else:
        add(check="owner_on_map", status="warn",
            detail="no Devanagari borrower name given — owner not checked")

    if record_owner_lossy and row:
        s = name_score(record_owner_lossy, row["owner"])
        add(check="paper_name_vs_map", status="pass" if s >= 60 else "warn",
            detail=f"name on the paper '{record_owner_lossy}' vs map '{row['owner']}': {s:.0f}%"
                   " (paper text is lossy)")

    if expected_area_ha and row:
        # 8A 'total' includes pot-kharab; map 'Total Area' is the cultivable part
        cands = [row["area_total_ha"], row["area_total_ha"] + row["pot_kharab_ha"]]
        diff = min(abs(expected_area_ha - c) / c * 100 for c in cands if c)
        add(check="area_matches_map", status="pass" if diff <= area_tol_pct else "warn",
            detail=f"paper {expected_area_ha} ha vs map row {row['area_total_ha']} ha "
                   f"(+{row['pot_kharab_ha']} pot-kharab) — {diff:.1f}% apart")

    # whole-survey sanity: surveyed polygon vs sum of all holder rows (each subdivision
    # counted once). Old village maps often disagree with the record by 10-20%, so this
    # is only ever a warning.
    if loc.get("map_area_ha") and rows:
        per_sub = {}
        for r in rows:
            per_sub.setdefault(r["survey_no"], {})[r["khata"]] = r["area_total_ha"] + r["pot_kharab_ha"]
        rec_total = 0.0
        for sub_no, by_khata in per_sub.items():
            vals = list(by_khata.values())
            # co-holders of an undivided subdivision repeat its area; distinct areas add up
            rec_total += vals[0] if len(set(vals)) == 1 and len(vals) > 1 else sum(vals)
        dev = abs(loc["map_area_ha"] - rec_total) / rec_total * 100 if rec_total else 0
        add(check="map_vs_record_area", status="pass" if dev <= 15 else "info",
            detail=f"polygon {loc['map_area_ha']:.2f} ha vs record {rec_total:.2f} ha "
                   f"for survey {loc['mapped_plotno']} ({dev:.0f}% apart)")

    return {"checks": checks, "row": row, "verdict": verdict_of(checks)}


ORDER = ["info", "pass", "warn", "confirm", "fail"]
VERDICT = {"info": "verified", "pass": "verified", "warn": "review",
           "confirm": "confirm", "fail": "reject"}


def verdict_of(checks: list) -> str:
    """Worst status wins; 'info' never counts against."""
    sts = [c["status"] for c in checks if c.get("status") in ORDER] or ["warn"]
    return VERDICT[max(sts, key=ORDER.index)]



# ───────────────────────────────────────────────────────── one-call APIs ──
def locate_from_record(rec: dict, *, borrower_name_devanagari: str = "",
                       khata_no: str | None = None, client: BhunakshaClient | None = None,
                       overrides: dict | None = None) -> dict:
    """
    Locate + cross-check from a parsed 7/12 (land_record_verify.parse_full_record) or a
    parsed 8A (parse_8a). For an 8A every plot on the khata is located.
    """
    client = client or BhunakshaClient()
    ov = overrides or {}
    where = client.resolve_location(
        district=rec.get("district") or "", taluka=rec.get("taluka") or "",
        village=rec.get("village") or "", village_code=rec.get("village_code"),
        district_code=ov.get("district_code"), taluka_code=ov.get("taluka_code"),
        village_bhunaksha_code=ov.get("village_code"))
    out = {"location": {k: v for k, v in where.items()
                        if k not in ("all_talukas", "all_villages")},
           "all_villages": where["all_villages"], "plots": []}

    if rec.get("doc") == "8A":
        khata = khata_no or rec.get("khata_no")
        lossy = (rec.get("holder_names_lossy") or [None])[0]
        targets = [(p["survey_no"], p["area_total_ha"]) for p in rec.get("plots") or []]
    else:
        khata = khata_no
        lossy = None
        targets = [(rec.get("survey_no"), None)] if rec.get("survey_no") else []
        # 7/12: borrower's own khata + area come from his holder row when we can find it
        if khata and rec.get("holders"):
            own = next((h for h in rec["holders"] if h["khata"] == str(khata)), None)
            if own:
                lossy = own["name"]
                targets = [(rec.get("survey_no"), own.get("area_hectare"))]
        elif borrower_name_devanagari and rec.get("holders"):
            best, s = best_match(borrower_name_devanagari, rec["holders"],
                                 key=lambda h: h["name"])
            if best and s >= 60:
                khata, lossy = best["khata"], best["name"]
                targets = [(rec.get("survey_no"), best.get("area_hectare"))]

    for survey, area in targets:
        loc = client.locate_plot(giscode=where["giscode"], survey_no=survey)
        cc = cross_check(loc, survey_no=survey, khata_no=khata,
                         borrower_name_devanagari=borrower_name_devanagari,
                         expected_area_ha=area, record_owner_lossy=lossy)
        out["plots"].append({"survey_no": survey, "location": loc, **cc})

    if not where["confident"]:
        for p in out["plots"]:
            p["checks"].insert(0, {
                "check": "village_match", "status": "confirm",
                "detail": f"matched '{rec.get('village')}' to {where['village_name']} "
                          f"({where['scores']['village']:.0f}%) — confirm the village"})
    out["verdict"] = verdict_of([c for p in out["plots"] for c in p["checks"]])
    out["khata_no"] = khata
    out["total_area_ha"] = round(sum(a for _, a in targets if a), 4) or None
    located = [p["location"]["centroid"] for p in out["plots"]
               if p["location"].get("centroid")]
    out["centroid"] = ({"lat": sum(c["lat"] for c in located) / len(located),
                        "lon": sum(c["lon"] for c in located) / len(located)}
                       if located else None)
    return out


# ───────────────────────────────────────────────────────── streamlit map ──
def map_html(plots: list, *, warehouse: tuple | None = None, height: int = 460) -> str:
    """
    Self-contained Leaflet page: satellite imagery (Esri World Imagery, no API key),
    village/road labels on top, a street-map toggle, each plot's boundary + a pin.
    """
    import html as _h
    import json
    feats = []
    for p in plots:
        loc = p.get("location") or p
        if not loc.get("found"):
            continue
        c = loc.get("centroid") or {}
        row = p.get("row") or {}
        popup = (f"<b>Survey {_h.escape(str(loc['survey_no']))}</b><br>"
                 f"{c.get('lat', 0):.6f}, {c.get('lon', 0):.6f}<br>"
                 + (f"Khata {_h.escape(str(row.get('khata')))} · "
                    f"{_h.escape(str(row.get('owner')))} · {row.get('area_total_ha')} ha<br>"
                    if row else "")
                 + (f"Surveyed polygon: {loc['map_area_ha']:.2f} ha<br>" if loc.get("map_area_ha") else "")
                 + f"<a href='{loc.get('google_maps_url')}' target='_blank'>Open in Google Maps</a>")
        feats.append({"polygon": [[pt[1], pt[0]] for pt in loc.get("polygon_lonlat") or []],
                      "lat": c.get("lat"), "lon": c.get("lon"), "popup": popup,
                      "label": f"Survey {loc['survey_no']}"})
    wh = ({"lat": warehouse[0], "lon": warehouse[1]} if warehouse else None)
    data = json.dumps({"plots": feats, "warehouse": wh}, ensure_ascii=False)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>html,body,#m{{height:{height}px;margin:0;background:#0f172a;}}
.lbl{{background:rgba(15,23,42,.85);color:#fff;border:1px solid #10b981;font:600 12px sans-serif;}}</style>
</head><body><div id="m"></div><script>
(function () {{
const D = {data};
const map = L.map('m', {{zoomControl: true}});
const sat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',
  {{maxZoom: 19, attribution: 'Imagery © Esri, Maxar, Earthstar Geographics'}}).addTo(map);
const labels = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{{z}}/{{y}}/{{x}}',
  {{maxZoom: 19}});      // off by default: Esri's place names are not from the land record
const street = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
  {{maxZoom: 19, attribution: '© OpenStreetMap contributors'}});
L.control.layers({{'Satellite': sat, 'Street map': street}}, {{'Place names': labels}}).addTo(map);
L.control.scale({{metric: true, imperial: false}}).addTo(map);
const bounds = [];
for (const p of D.plots) {{
  if (p.polygon.length > 2) {{
    const poly = L.polygon(p.polygon, {{color: '#22c55e', weight: 3, fillColor: '#22c55e', fillOpacity: 0.18}})
      .addTo(map).bindPopup(p.popup);
    poly.bindTooltip(p.label, {{permanent: true, direction: 'center', className: 'lbl'}});
    p.polygon.forEach(pt => bounds.push(pt));
  }}
  if (p.lat != null) {{ L.marker([p.lat, p.lon]).addTo(map).bindPopup(p.popup); bounds.push([p.lat, p.lon]); }}
}}
if (D.warehouse) {{
  L.circleMarker([D.warehouse.lat, D.warehouse.lon], {{radius: 8, color: '#38bdf8', fillOpacity: .9}})
    .addTo(map).bindPopup('<b>Warehouse</b>');
  bounds.push([D.warehouse.lat, D.warehouse.lon]);
}}
if (bounds.length) map.fitBounds(bounds, {{padding: [40, 40], maxZoom: 18}});
else map.setView([19.5, 76.0], 7);
window.__plotsDrawn = D.plots.length;
}})();
</script></body></html>"""


def render_map(plots: list, *, warehouse: tuple | None = None, height: int = 460) -> None:
    """Satellite map of the located plots inside Streamlit (no extra packages)."""
    import streamlit as st
    import streamlit.components.v1 as components
    if not any((p.get("location") or p).get("found") for p in plots):
        st.info("Nothing to draw — no plot was located.")
        return
    components.html(map_html(plots, warehouse=warehouse, height=height), height=height + 10)
