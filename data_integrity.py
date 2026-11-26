"""
data_integrity.py -- refuse to serve a price series that isn't real.

Written after 2026-09-05, when `soybean_mandi_dec2025_aug2026.csv` turned out to be a
single day's 19-district snapshot replicated across 231 dates (2025-12-22 -> 2026-08-22).
Every downstream number -- the forecast, the volatility pillar, LTV, and the
DEPOSIT_PRICE_SANITY fraud rule -- was quietly computed on a constant.

The rule this module enforces: a synthetic block must never reach the risk engine
silently. It either gets quarantined, or it gets labelled `estimated` so
data_provenance.py can show it to the operator.

    from data_integrity import audit_price_series, assert_clean

    rep = audit_price_series(df)          # df: date, price  (+ optional detail cols)
    if not rep.ok:
        print(rep.summary())

Checks
------
frozen_runs        consecutive dates carrying an identical price
duplicate_blocks   the same multi-row fingerprint repeated on different dates
zero_dispersion    a day where every mandi reports the same price
staleness          the series doesn't reach today
outliers           quotes far outside the plausible band for the commodity
gaps               missing calendar stretches
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# A soybean mandi quote outside this band is not a representative soybean quote.
# (Tamil Nadu routinely posts Rs.9,000-10,500 lots that are a different product.)
PLAUSIBLE_INR_QTL = (2000.0, 9000.0)

MAX_FROZEN_RUN = 5          # >5 identical consecutive daily prices is not a real market
MAX_STALE_DAYS = 21         # a live series should reach within ~3 weeks of today


@dataclass
class Finding:
    check: str
    severity: str            # "fail" | "warn"
    detail: str
    span: tuple | None = None
    n: int = 0


@dataclass
class IntegrityReport:
    n_rows: int
    date_min: object = None
    date_max: object = None
    findings: list = field(default_factory=list)

    @property
    def failures(self) -> list:
        return [f for f in self.findings if f.severity == "fail"]

    @property
    def ok(self) -> bool:
        return not self.failures

    def bad_dates(self) -> set:
        """Every date implicated by a `fail` finding -- what to quarantine."""
        out = set()
        for f in self.failures:
            if f.span:
                a, b = f.span
                out |= set(pd.date_range(a, b))
        return out

    def summary(self) -> str:
        head = (f"IntegrityReport  rows={self.n_rows}  "
                f"{self.date_min} -> {self.date_max}  "
                f"{'OK' if self.ok else 'FAILED'}")
        lines = [head, "-" * len(head)]
        if not self.findings:
            lines.append("  no issues found")
        for f in self.findings:
            mark = "FAIL" if f.severity == "fail" else "warn"
            lines.append(f"  [{mark}] {f.check}: {f.detail}")
        return "\n".join(lines)


def _frozen_runs(s: pd.Series, max_run: int) -> list:
    """(start_date, end_date, value, length) for every run of identical prices."""
    if s.empty:
        return []
    grp = (s != s.shift()).cumsum()
    out = []
    for _, blk in s.groupby(grp):
        if len(blk) > max_run:
            out.append((blk.index[0], blk.index[-1], float(blk.iloc[0]), len(blk)))
    return out


def audit_price_series(df: pd.DataFrame, *, date_col="date", price_col="price",
                       detail: pd.DataFrame | None = None,
                       max_frozen_run: int = MAX_FROZEN_RUN,
                       max_stale_days: int = MAX_STALE_DAYS,
                       today=None) -> IntegrityReport:
    """
    Audit a daily price series.

    df      the aggregated series (one row per date)
    detail  optional per-mandi rows for the same period, with columns
            date + modal_price (+ district_name); enables the dispersion and
            duplicate-block checks that catch a replicated snapshot.
    """
    d = df[[date_col, price_col]].dropna().copy()
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
    d = d.dropna().sort_values(date_col)
    s = d.set_index(date_col)[price_col].astype(float)

    rep = IntegrityReport(n_rows=len(s),
                          date_min=s.index.min().date() if len(s) else None,
                          date_max=s.index.max().date() if len(s) else None)
    if s.empty:
        rep.findings.append(Finding("empty", "fail", "series has no usable rows"))
        return rep

    # 1. frozen runs -- the check that catches a flat-filled block
    for a, b, val, n in _frozen_runs(s, max_frozen_run):
        rep.findings.append(Finding(
            "frozen_run", "fail",
            f"price stuck at {val:,.1f} for {n} consecutive days "
            f"({a.date()} -> {b.date()})", span=(a, b), n=n))

    # 2. staleness
    now = pd.Timestamp(today or datetime.now().date())
    stale = (now - s.index.max()).days
    if stale > max_stale_days:
        rep.findings.append(Finding(
            "staleness", "warn",
            f"series ends {s.index.max().date()}, {stale} days behind {now.date()}"))

    # 3. calendar gaps
    full = pd.date_range(s.index.min(), s.index.max(), freq="D")
    missing = full.difference(s.index)
    if len(missing):
        runs, start, prev = [], None, None
        for dt in missing:
            if start is None:
                start = prev = dt
            elif (dt - prev).days == 1:
                prev = dt
            else:
                runs.append((start, prev)); start = prev = dt
        if start is not None:
            runs.append((start, prev))
        big = [(a, b) for a, b in runs if (b - a).days >= 13]
        for a, b in big:
            rep.findings.append(Finding(
                "gap", "warn",
                f"no data {a.date()} -> {b.date()} ({(b-a).days+1} days)", span=(a, b)))

    # 4. implausible levels
    lo, hi = PLAUSIBLE_INR_QTL
    bad = s[(s < lo) | (s > hi)]
    if len(bad):
        rep.findings.append(Finding(
            "outlier", "warn",
            f"{len(bad)} day(s) outside Rs.{lo:,.0f}-{hi:,.0f} "
            f"(e.g. {bad.iloc[0]:,.0f} on {bad.index[0].date()})", n=len(bad)))

    # 5+6. per-mandi checks -- only these can prove a replicated snapshot
    if detail is not None and len(detail):
        det = detail.copy()
        det["date"] = pd.to_datetime(det["date"], errors="coerce")
        det["modal_price"] = pd.to_numeric(det["modal_price"], errors="coerce")
        det = det.dropna(subset=["date", "modal_price"])

        g = det.groupby("date")["modal_price"]
        flat = g.nunique()[g.nunique() == 1]
        if len(flat):
            rep.findings.append(Finding(
                "zero_dispersion", "fail",
                f"{len(flat)} day(s) where every mandi reports one identical price",
                span=(flat.index.min(), flat.index.max()), n=len(flat)))

        # fingerprint each day by its sorted price vector; a repeat = copied snapshot
        fp = det.groupby("date")["modal_price"].apply(
            lambda x: hash(tuple(sorted(round(float(v), 2) for v in x))))
        dupes = fp[fp.duplicated(keep=False)]
        if len(dupes):
            worst = dupes.value_counts().iloc[0]
            rep.findings.append(Finding(
                "duplicate_block", "fail",
                f"{len(dupes)} day(s) share an identical per-mandi price vector "
                f"(one block repeated on {worst} dates) "
                f"{dupes.index.min().date()} -> {dupes.index.max().date()}",
                span=(dupes.index.min(), dupes.index.max()), n=len(dupes)))

    return rep


def assert_clean(df: pd.DataFrame, **kw) -> None:
    """Raise if the series would poison the risk engine."""
    rep = audit_price_series(df, **kw)
    if not rep.ok:
        raise ValueError("price series failed integrity audit:\n" + rep.summary())


def quarantine(df: pd.DataFrame, rep: IntegrityReport, *, date_col="date") -> pd.DataFrame:
    """Drop every date implicated by a `fail` finding. Real data in, real data out."""
    bad = rep.bad_dates()
    if not bad:
        return df
    d = df.copy()
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
    return d[~d[date_col].isin(bad)].reset_index(drop=True)


if __name__ == "__main__":
    import os
    _DIR = os.path.dirname(os.path.abspath(__file__))
    det = pd.read_csv(os.path.join(_DIR, "soybean_mandi_COMPLETE.csv"),
                      usecols=["date", "modal_price"])
    det["date"] = pd.to_datetime(det["date"], errors="coerce")
    ser = (det.dropna().groupby("date", as_index=False)["modal_price"].mean()
           .rename(columns={"modal_price": "price"}))
    rep = audit_price_series(ser, detail=det)
    print(rep.summary())
    clean = quarantine(ser, rep)
    print(f"\nquarantine: {len(ser)} -> {len(clean)} days "
          f"({len(ser)-len(clean)} removed)")
    if len(clean):
        print(f"clean series now ends {clean['date'].max().date()}")
