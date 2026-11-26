"""FPO verification tests. All data is DUMMY (see samples/README_DUMMY.txt)."""
import os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import fpo_verification as f
S = os.path.join(HERE, "samples")

# GSTIN check digit: two published example GSTINs pass, an altered one fails
assert f.validate_gstin("27AAPFU0939F1ZV")["checksum_ok"]
assert f.validate_gstin("29AAGCB7383J1Z4")["checksum_ok"]
assert not f.validate_gstin("27AAPFU0939F1ZX")["checksum_ok"]
assert not f.validate_gstin("27AAPFU0939F1Z")["valid_format"]

# CIN decode (format of a real producer company's CIN)
c = f.validate_cin("U01100MH2021PTC366051")
assert c["producer_company_class"] and c["agri_industry"] and c["gst_state_code"] == "27"
assert not f.validate_cin("U01100MH2021PTC36605")["valid_format"]
assert f.validate_cin("U01611PN2025PTC246587")["roc_state"] == "Maharashtra"   # Pune RoC

# PAN
p = f.validate_pan("AAACL1234K", "Latur Soya Farmers Producer Company Limited")
assert p["is_company"] and p["name_initial_ok"]
assert f.validate_pan("AAACK1234K", "Latur Soya")["name_initial_ok"] is False
assert f.validate_pan("ABCPD1234E")["entity_type"] == "Individual"

# MCA paste
mca = f.parse_mca_master_text(open(os.path.join(S, "DUMMY_mca_master_data.txt")).read())
assert mca["status"] == "Active" and mca["cin"] == "U01403MH2018PTC300001"
assert mca["paid_up_capital_rs"] == 742000 and mca["last_agm_parsed"] == "2025-09-28"
assert len([d for d in mca["directors"] if d["designation"] == "Director"]) == 5
assert any(d["designation"] == "CEO" for d in mca["directors"])

# members: catches exactly the 3 planted duplicates; namesakes are NOT duplicates
df, _ = f.read_member_file(os.path.join(S, "DUMMY_fpo_members.csv"))
m = f.analyze_members(df, pledged_quintals=3200, director_names=[
    d["name"] for d in mca["directors"] if d["designation"] == "Director"])
assert m["members"] == 368 and m["duplicate_rows"] == 3, m
assert m["directors_who_are_members"] == 5
assert 0.6 < m["pledge_to_capacity"] < 0.7
over = f.analyze_members(df, pledged_quintals=9000)
assert over["pledge_to_capacity"] > 1

# samples land in the intended bands
r = {k: f.run_checks(v) for k, v in f.SAMPLE_FPOS.items()}
assert r["Healthy FPO (DUMMY)"]["grade"] == "Low"
assert r["Young FPO, thin financials (DUMMY)"]["grade"] in ("Medium", "Elevated")
red = r["Red flags (DUMMY)"]
assert red["grade"] == "High" and "GSTIN_PAN_MISMATCH" in red["hard_flags"]
assert "PLEDGE_EXCEEDS_MEMBER_CAPACITY" in red["hard_flags"]

# a struck-off company is a hard fail even with good numbers
bad = dict(f.SAMPLE_FPOS["Healthy FPO (DUMMY)"]); bad["mca"] = dict(bad["mca"], status="Strike Off")
assert "COMPANY_NOT_ACTIVE" in f.run_checks(bad)["hard_flags"]
# PAN that isn't a company's
bad2 = dict(f.SAMPLE_FPOS["Healthy FPO (DUMMY)"], pan="AAAPL1234K", gstin=f._dummy_gstin("27", "AAAPL1234K"))
assert "PAN_NOT_COMPANY" in f.run_checks(bad2)["hard_flags"]
print("FPO TESTS PASSED")
