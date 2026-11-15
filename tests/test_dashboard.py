"""The dashboard: its pandas helpers, and a smoke run of every page against the committed marts.

The page runs use Streamlit's AppTest: each script is executed headless and must finish without an
exception. The revisions page is also run against a synthetic, non-empty mart_revisions, because the real
one is still empty and that branch would otherwise first run in production.
"""

import shutil
import tomllib
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit", reason="dashboard dependencies not installed (uv sync --group dashboard)")
from streamlit.testing.v1 import AppTest  # noqa: E402

import marts  # noqa: E402  (dashboard/ is on pythonpath, see pyproject)

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "dashboard" / "views"


# ---- helpers ------------------------------------------------------------------------------------

def test_window_label_wraps_past_midnight():
    assert marts.window_label(12, 4) == "12:00 → 16:00"
    assert marts.window_label(22, 4) == "22:00 → 02:00"


def test_heatmap_all_days_is_interval_weighted_mean():
    df = pd.DataFrame({
        "month": [1, 1], "local_hour": [0, 0], "day_type": ["weekday", "weekend"],
        "avg_co2_g_per_kwh": [10.0, 40.0], "n_intervals": [3, 1],
    })
    assert marts.heatmap_matrix(df, None).loc[1, 0] == pytest.approx((10 * 3 + 40 * 1) / 4)
    assert marts.heatmap_matrix(df, "weekend").loc[1, 0] == 40.0


def test_yearly_co2_is_production_weighted_like_the_mart():
    df = pd.DataFrame({
        "year": [2024, 2024], "nuclear_twh": [1.0, 1.0], "net_import_twh": [-1.0, 0.5],
        "production_twh": [30.0, 10.0], "avg_co2_g_per_kwh": [20.0, 60.0], "is_complete_month": [True, True],
    })
    row = marts.yearly_summary(df).iloc[0]
    assert row["avg_co2_g_per_kwh"] == pytest.approx((20 * 30 + 60 * 10) / 40)
    assert row["net_import_twh"] == pytest.approx(-0.5)
    assert not row["complete"]  # 2 months, not 12


def test_dbt_counts():
    health = {"dbt_build": {"counts": {"model:success": 13, "seed:success": 2, "test:pass": 50, "test:warn": 1}}}
    assert marts.dbt_counts(health) == {"tests_passed": 50, "tests_not_passed": 1, "models_built": 13, "models_failed": 0}


def test_requirements_txt_matches_uv_lock():
    """Community Cloud installs dashboard/requirements.txt; it must pin what uv.lock pins."""
    lock = {p["name"]: p["version"] for p in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]}
    pins = [line.split(";")[0].strip() for line in (ROOT / "dashboard" / "requirements.txt").read_text().splitlines()
            if line and not line.startswith((" ", "#"))]
    assert "streamlit" in {p.split("==")[0] for p in pins}
    for pin in pins:
        name, version = pin.split("==")
        assert lock.get(name) == version, f"{name}: requirements.txt has {version}, uv.lock has {lock.get(name)}"


# ---- pages --------------------------------------------------------------------------------------

def run_page(name: str) -> AppTest:
    at = AppTest.from_file(str(PAGES / name), default_timeout=30)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    return at


@pytest.mark.parametrize("page", ["best_time.py", "nuclear_2022.py", "pipeline_health.py"])
def test_page_runs(page):
    run_page(page)


def test_best_time_answer_matches_the_mart():
    at = run_page("best_time.py")
    at.segmented_control[0].set_value(4)
    at.segmented_control[1].set_value("summer")
    at.run()
    best = marts.load_mart("mart_best_window").query("season == 'summer' and duration_hours == 4 and is_best").iloc[0]
    assert at.metric[0].value == marts.window_label(int(best["start_hour"]), 4)


def test_revisions_page_with_data(tmp_path, monkeypatch):
    for f in marts.MARTS_DIR.iterdir():
        shutil.copy(f, tmp_path / f.name)
    template = pd.read_parquet(marts.MARTS_DIR / "mart_revisions.parquet")
    fake = pd.DataFrame([
        {"month_start": pd.Timestamp("2026-09-01").date(), "metric": m, "from_status": "real_time",
         "to_status": "consolidated", "n_intervals": 1440, "avg_from_value": 100.0, "avg_to_value": 101.0,
         "mean_diff": 1.0, "mean_abs_diff": 1.5, "max_abs_diff": 9.0, "mean_abs_pct_diff": pct}
        for m, pct in [("consumption_mw", 0.004), ("co2_intensity_g_per_kwh", 0.02)]
    ])[template.columns]
    fake.to_parquet(tmp_path / "mart_revisions.parquet", index=False)
    monkeypatch.setattr(marts, "MARTS_DIR", tmp_path)

    at = run_page("pipeline_health.py")
    assert not at.info, "the 'nothing to compare yet' message should not show when revisions exist"
