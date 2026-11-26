import os, sys, types
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import P712, P8A
sys.path[:0] = [HERE, os.path.join(HERE, "tests")]
log = []
class Ctx:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __getattr__(self, n): return getattr(st, n)
class Up:
    def __init__(self, path): self.path=path; self.name=os.path.basename(path); self.file_id=path
    def getvalue(self): return open(self.path,'rb').read()
    def read(self): return self.getvalue()
st = types.ModuleType("streamlit")
st.session_state = {}
FILES = {}
def _rec(n):
    def f(*a, **k): log.append((n, str(a)[:120])); return None
    return f
for n in ["markdown","info","warning","error","success","caption","write","dataframe","code","pydeck_chart","rerun"]:
    setattr(st, n, _rec(n))
class Col(Ctx):
    def metric(self,*a,**k): log.append(("metric", str(a)))
    def markdown(self,*a,**k): log.append(("md", str(a)[:80]))
    def text_input(self, label, key=None, **k): return st.session_state.get(key, "")
st.columns = lambda spec, **k: [Col() for _ in range(spec if isinstance(spec,int) else len(spec))]
st.tabs = lambda names: [Ctx() for _ in names]
st.expander = lambda *a, **k: Ctx()
st.spinner = lambda *a, **k: Ctx()
st.text_input = lambda label, key=None, **k: st.session_state.get(key, "")
st.checkbox = lambda label, value=False, key=None, **k: st.session_state.get(key, value)
st.file_uploader = lambda label, type=None, key=None, **k: FILES.get(key)
st.button = lambda label, key=None, **k: key in ("lr8_go",) or label.startswith("🔍 Verify against")
st.selectbox = lambda label, opts, index=0, key=None, **k: opts[index] if opts else None
sys.modules["streamlit"] = st
comp = types.ModuleType("streamlit.components"); v1 = types.ModuleType("streamlit.components.v1")
v1.html = lambda html, height=None, **k: log.append(("map_html", html[:60]))
comp.v1 = v1; st.components = comp
sys.modules["streamlit.components"] = comp; sys.modules["streamlit.components.v1"] = v1
pdk = types.ModuleType("pydeck")
pdk.Layer = lambda *a, **k: ("layer", a[0]); pdk.Deck = lambda **k: ("deck", k); pdk.ViewState = lambda **k: k
sys.modules["pydeck"] = pdk

import land_record_verify as lrv, plot_locator as pl
from bhunaksha_fixtures import make_fake
GOV = {"2805100001066211": open(P712,"rb").read(),
       "0520000101457928": open(P8A,"rb").read()}
lrv.MahaBhumiProvider.fetch_official_pdf = lambda self, vn: GOV[vn]
pl.BhunakshaClient._post = type(make_fake())._post; pl.BhunakshaClient.calls = []
lrv.AUDIT_DIR = "/tmp/claude_audit_test"

FILES["lr_pdf"] = Up(P712)
FILES["lr8_pdf"] = Up(P8A)
st.session_state.update({"lr_name_dev":"रमेश तात्याराव बिडवे","lr8_name_dev":"रमेश तात्याराव बिडवे","lr_allow_enc":True})
lrv.inject_css(); lrv.render()
lrv.render()   # rerun: must come from cache, no crash
for m in log:
    if "Where" in m[1] or m[0] in ("warning","caption"): print(m)
print("metrics:", [m for m in log if m[0]=="metric"][:10])
print("maps drawn:", sum(1 for m in log if m[0]=="map_html"))
print("verified_land.plot_location:", st.session_state["verified_land"]["plot_location"]["centroid"])
print("verified_8a total:", st.session_state["verified_8a"]["total_holding_ha"], st.session_state["verified_8a"]["verdict"])
print("errors:", [m for m in log if m[0]=="error"])
print("audits:", os.listdir(lrv.AUDIT_DIR))
