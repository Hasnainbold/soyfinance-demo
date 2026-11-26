import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plot_locator as pl
import land_record_verify as lrv
from bhunaksha_fixtures import make_fake, EXT_126

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import P712, P8A
t712 = lrv._pdf_text(open(P712, "rb").read())
t8a = lrv._pdf_text(open(P8A, "rb").read())

# ── UTM -> latlon lands inside the portal's own georef box
lat, lon = pl.utm_to_latlon(696400, 2044000)
assert EXT_126["ymin"] - .001 < lat < EXT_126["ymax"] + .001, lat
assert EXT_126["xmin"] - .001 < lon < EXT_126["xmax"] + .001, lon

# ── 8A parse
assert pl.is_8a(t8a) and not pl.is_8a(t712)
a = pl.parse_8a(t8a)
print("8A:", json.dumps(a, ensure_ascii=False))
assert a["khata_no"] == "311" and a["verification_no"] == "0520000101457928"
assert a["plots"] == [{"survey_no": "126/1", "area_cultivable_ha": 1.75, "pot_kharab_ha": 0.05,
                       "area_total_ha": 1.8, "assessment": 5.71, "total_levy": 51.39}], a["plots"]
assert a["taluka"] and a["district"] and a["village"]

# ── 7/12 via 8A: locate + cross-check
c = make_fake()
r = pl.locate_from_record(a, borrower_name_devanagari="रमेश तात्याराव बिडवे", client=c)
print("\n8A location:", r["location"]["district_name"], r["location"]["taluka_name"],
      r["location"]["village_name"], r["location"]["scores"], "confident", r["location"]["confident"])
for p in r["plots"]:
    L = p["location"]
    print(" plot", p["survey_no"], L["centroid"], L["map_area_ha"], "georef_ok", L["polygon_matches_georef"])
    for ck in p["checks"]: print("   ", ck["status"].ljust(8), ck["check"].ljust(20), ck["detail"])
print(" verdict:", r["verdict"])
assert r["location"]["village_code"] == "272800050353410000"
assert r["plots"][0]["location"]["polygon_matches_georef"] is True
st = {ck["check"]: ck["status"] for ck in r["plots"][0]["checks"]}
assert st["khata_on_map"] == "pass" and st["owner_on_map"] == "pass" and st["area_matches_map"] == "pass"

# ── wrong borrower is caught
r2 = pl.locate_from_record(a, borrower_name_devanagari="सुरेश कैलास देशमुख", client=make_fake())
st2 = {ck["check"]: ck["status"] for ck in r2["plots"][0]["checks"]}
print("\nwrong borrower ->", st2["owner_on_map"], r2["verdict"]); assert st2["owner_on_map"] == "fail"

# ── wrong khata is caught
r3 = pl.locate_from_record(a, borrower_name_devanagari="रमेश तात्याराव बिडवे", khata_no="999", client=make_fake())
assert {c["check"]: c["status"] for c in r3["plots"][0]["checks"]}["khata_on_map"] == "fail"

# ── 7/12 route
full = lrv.parse_full_record(t712)
print("\n7/12 keys:", full["district"], full["taluka"], full["village"], full["village_code"], full["survey_no"])
r4 = pl.locate_from_record(full, borrower_name_devanagari="रमेश तात्याराव बिडवे", client=make_fake())
print(" khata from 7/12 row:", r4["khata_no"], "verdict", r4["verdict"])
for ck in r4["plots"][0]["checks"]: print("   ", ck["status"].ljust(8), ck["check"].ljust(20), ck["detail"])
assert r4["khata_no"] == "311"

# ── unknown survey
loc = make_fake().locate_plot(giscode="RVM2805272800050353410000", survey_no="9999")
assert not loc["found"]
print("\nALL TESTS PASSED")
