"""Where the sample Bidve papers live: env vars, else the project folder above soyfinance_demo."""
import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
P712 = os.environ.get("LR_TEST_712", os.path.join(_ROOT, "Bidve Ramesh 7.12.pdf"))
P8A = os.environ.get("LR_TEST_8A", os.path.join(_ROOT, "Bidve Ramesh 8A.pdf"))
for p in (P712, P8A):
    if not os.path.exists(p):
        sys.exit(f"sample paper not found: {p} (set LR_TEST_712 / LR_TEST_8A)")
