"""Drive fpo_verification.render() through a stub `streamlit` to catch page-code errors."""
import os, sys, types
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
log = []
class Ctx:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __getattr__(self, n): return getattr(st, n)
class Up:
    def __init__(self, p): self.name = os.path.basename(p); self._d = open(p, "rb").read()
    def getvalue(self): return self._d
    def read(self): return self._d
    def seek(self, *a): pass
st = types.ModuleType("streamlit"); st.session_state = {}
S = os.path.join(HERE, "samples")
VALUES = {"fpo_name": "Latur Soya Farmers Producer Company Limited", "fpo_cin": "U01403MH2018PTC300001",
          "fpo_pan": "AAACL1234K", "fpo_gstin": "", "fpo_mca": open(os.path.join(S, "DUMMY_mca_master_data.txt")).read(),
          "fpo_pledge": 3200.0, "fpo_yield": 1.0, "fpo_rev": 38e6, "fpo_nw": 6.2e6, "fpo_debt": 5.1e6,
          "fpo_ca": 9.8e6, "fpo_cl": 6.1e6, "fpo_members": Up(os.path.join(S, "DUMMY_fpo_members.csv"))}
import fpo_verification as f
VALUES["fpo_gstin"] = f._dummy_gstin("27", "AAACL1234K")
rec = lambda n: (lambda *a, **k: log.append((n, str(a)[:100])))
for n in ["markdown", "caption", "info", "warning", "error", "success", "json", "dataframe", "write"]:
    setattr(st, n, rec(n))
class Col(Ctx):
    def metric(self, *a, **k): log.append(("metric", str(a)))
    def text_input(self, l, key=None, **k): return VALUES.get(key, "")
    def number_input(self, l, key=None, value=0.0, **k): return VALUES.get(key, value)
    def checkbox(self, l, key=None, **k): return VALUES.get(key, False)
    def text_area(self, l, key=None, **k): return VALUES.get(key, "")
    def markdown(self, *a, **k): log.append(("md", str(a)[:60]))
st.columns = lambda spec, **k: [Col() for _ in range(spec if isinstance(spec, int) else len(spec))]
st.tabs = lambda n: [Ctx() for _ in n]
st.expander = lambda *a, **k: Ctx()
for n in ("text_input", "number_input", "checkbox", "text_area"):
    setattr(st, n, getattr(Col(), n))
st.file_uploader = lambda l, type=None, key=None, **k: VALUES.get(key)
st.radio = lambda l, opts, **k: opts[2]
st.button = lambda *a, **k: True
sys.modules["streamlit"] = st
f.RUNS_DIR = "/tmp/fpo_runs_test"
f.inject_css(); f.render()
print("verified_fpo:", {k: st.session_state["verified_fpo"][k] for k in ("grade", "score", "hard_flags", "pledge_to_capacity")})
print("errors:", [l for l in log if l[0] == "error"])
print("metrics shown:", sum(1 for l in log if l[0] == "metric"))
