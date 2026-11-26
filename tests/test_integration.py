import os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import P712, P8A
sys.path[:0] = [HERE, os.path.join(HERE, "tests")]
import land_record_verify as lrv
from bhunaksha_fixtures import make_fake

p712 = open(P712, "rb").read()
p8a = open(P8A, "rb").read()
GOV = {"2805100001066211": p712, "0520000101457928": p8a}
lrv.MahaBhumiProvider.fetch_official_pdf = lambda self, vn: GOV[vn]

def show(res):
    print("VERDICT", res["verdict"], "failed:", res.get("failed_checks"))
    for c in res["checks"]:
        print(f"  {c['status']:8s} {c['check']:24s} {c['detail'][:95]}")

print("== 7/12, right borrower")
r = lrv.verify_land_ownership(p712, borrower_name_devanagari="रमेश तात्याराव बिडवे",
                              require_clear_encumbrance=False, bhunaksha_client=make_fake())
show(r)
st = {c["check"]: c["status"] for c in r["checks"]}
assert st["owner_is_borrower"] == "pass", st           # upgraded by the clean map name
assert st["map_khata_on_map"] == "pass" and st["map_owner_on_map"] == "pass"

print("\n== 7/12, stranger")
r = lrv.verify_land_ownership(p712, borrower_name_devanagari="सुरेश कैलास देशमुख",
                              require_clear_encumbrance=False, bhunaksha_client=make_fake())
show(r); assert r["verdict"] == "reject"

print("\n== 8A")
r = lrv.verify_8a_holding(p8a, borrower_name_devanagari="रमेश तात्याराव बिडवे",
                          bhunaksha_client=make_fake())
show(r)
st = {c["check"]: c["status"] for c in r["checks"]}
assert st["document_authentic"] == "pass" and st["map_owner_on_map"] == "pass"
assert r["location"]["plots"][0]["location"]["centroid"]["lat"] > 18.47

print("\n== 8A whose government copy differs (tampered)")
GOV["0520000101457928"] = p712
r = lrv.verify_8a_holding(p8a, borrower_name_devanagari="रमेश तात्याराव बिडवे",
                          bhunaksha_client=make_fake())
print("  authentic:", r["authenticity"]["authentic"], r["authenticity"]["reason"])
assert r["verdict"] == "reject"
GOV["0520000101457928"] = p8a

print("\n== Bhunaksha down -> info only, title verdict unaffected")
class Down(type(make_fake())):
    def _post(self, *a, **k): raise ConnectionError("timed out")
r = lrv.verify_8a_holding(p8a, borrower_name_devanagari="रमेश तात्याराव बिडवे", bhunaksha_client=Down())
show(r); assert r["verdict"] in ("verified", "confirm")
print("\nINTEGRATION OK")
