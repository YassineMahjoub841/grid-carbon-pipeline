"""Reading the exported marts, plus the small pandas reshaping the pages need.

The dashboard reads only data/marts/ (the files the daily job commits after every dbt test passed),
never the DuckDB warehouse. No Streamlit UI in here, so these functions can be unit-tested.
"""

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

# MARTS_DIR can point elsewhere (the tests use it to feed synthetic marts).
MARTS_DIR = Path(os.environ.get("MARTS_DIR", Path(__file__).resolve().parents[1] / "data" / "marts"))

SEASONS = ["winter", "spring", "summer", "autumn"]
SEASON_OF_MONTH = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
                   6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@st.cache_data(show_spinner=False)
def _read_parquet(path: str, mtime: float) -> pd.DataFrame:
    # mtime is part of the cache key: a new daily commit (new file) invalidates the cache.
    return pd.read_parquet(path)


def load_mart(name: str) -> pd.DataFrame:
    path = MARTS_DIR / f"{name}.parquet"
    return _read_parquet(str(path), path.stat().st_mtime)


def load_health() -> dict:
    return json.loads((MARTS_DIR / "pipeline_health.json").read_text(encoding="utf-8"))


# ---- Page 1: best time to run a job -------------------------------------------------------------

def window_label(start_hour: int, duration_hours: int) -> str:
    """'12:00 → 16:00'. Windows past midnight wrap (mart_best_window does the same, DECISIONS D15)."""
    return f"{start_hour:02d}:00 → {(start_hour + duration_hours) % 24:02d}:00"


def heatmap_matrix(carbon_by_hour: pd.DataFrame, day_type: str | None) -> pd.DataFrame:
    """Average CO2 as a month (rows 1-12) × local hour (columns 0-23) matrix.

    day_type None = all days. The mart is split by weekday/weekend, so "all days" is the average of the
    two weighted by their number of intervals: exactly the mean over all intervals.
    """
    df = carbon_by_hour if day_type is None else carbon_by_hour[carbon_by_hour["day_type"] == day_type]
    weighted = (df.assign(w=df["avg_co2_g_per_kwh"] * df["n_intervals"])
                  .groupby(["month", "local_hour"], as_index=False)[["w", "n_intervals"]].sum())
    weighted["avg_co2_g_per_kwh"] = weighted["w"] / weighted["n_intervals"]
    return weighted.pivot(index="month", columns="local_hour", values="avg_co2_g_per_kwh").sort_index()


# ---- Page 2: the 2022 nuclear crisis ------------------------------------------------------------

def yearly_summary(monthly: pd.DataFrame) -> pd.DataFrame:
    """One row per calendar year. complete = all 12 months complete (the current year isn't).

    The mart's monthly CO2 is weighted by production (g per kWh produced), so the yearly figure is too:
    sum(monthly CO2 × monthly production) / yearly production. Same result as computing it from intervals.
    """
    df = monthly.assign(co2_x_prod=monthly["avg_co2_g_per_kwh"] * monthly["production_twh"])
    yearly = df.groupby("year").agg(
        nuclear_twh=("nuclear_twh", "sum"),
        production_twh=("production_twh", "sum"),
        net_import_twh=("net_import_twh", "sum"),
        co2_x_prod=("co2_x_prod", "sum"),
        n_complete_months=("is_complete_month", "sum"),
    )
    yearly["avg_co2_g_per_kwh"] = yearly["co2_x_prod"] / yearly["production_twh"]
    yearly["complete"] = yearly["n_complete_months"] == 12
    return yearly.drop(columns=["co2_x_prod", "n_complete_months"]).reset_index()


# ---- Page 3: pipeline health --------------------------------------------------------------------

def dbt_counts(health: dict) -> dict[str, int]:
    """Sum dbt's 'resource:status' counts into tests passed / tests not passed / models built."""
    counts = health.get("dbt_build", {}).get("counts", {})
    out = {"tests_passed": 0, "tests_not_passed": 0, "models_built": 0, "models_failed": 0}
    for key, n in counts.items():
        kind, _, status = key.partition(":")
        if kind == "test":
            out["tests_passed" if status == "pass" else "tests_not_passed"] += n
        elif kind == "model":
            out["models_built" if status == "success" else "models_failed"] += n
    return out
