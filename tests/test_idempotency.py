"""Running the pipeline twice on the same raw data must produce identical marts (a project requirement).

Builds the whole dbt project twice, into two separate DuckDB files, from the committed data/raw,
compares every mart row by row, then checks the exported Parquet files are byte-identical. Together with test_ingest.py (a second ingestion on the same
day writes nothing), this covers "running the pipeline twice on the same day gives the same marts".

Slow (two full dbt builds, ~1 min). Run only this file with: uv run pytest tests/test_idempotency.py
"""

import os
import shutil
import subprocess
from pathlib import Path

import duckdb

from publish.export_marts import export_marts

ROOT = Path(__file__).resolve().parents[1]


def dbt_build(db_path: Path) -> None:
    dbt = shutil.which("dbt")
    assert dbt, "dbt not found: run through `uv run pytest`"
    result = subprocess.run(
        [dbt, "build", "--project-dir", "transform", "--profiles-dir", "transform"],
        cwd=ROOT, env={**os.environ, "DUCKDB_PATH": str(db_path)}, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout[-3000:]


def read_marts(db_path: Path) -> dict[str, list[tuple]]:
    with duckdb.connect(str(db_path), read_only=True) as con:
        tables = [t for (t,) in con.sql(
            "select table_name from information_schema.tables where table_name like 'mart_%' order by 1"
        ).fetchall()]
        return {t: con.sql(f"select * from {t} order by all").fetchall() for t in tables}


def test_two_builds_give_identical_marts(tmp_path):
    dbt_build(tmp_path / "first.duckdb")
    dbt_build(tmp_path / "second.duckdb")

    first, second = read_marts(tmp_path / "first.duckdb"), read_marts(tmp_path / "second.duckdb")

    assert set(first) == {"mart_carbon_by_hour", "mart_best_window", "mart_monthly_mix_exchanges", "mart_revisions"}
    for table in first:
        assert first[table] == second[table], f"{table} differs between two builds on the same data"

    # The files the dashboard reads must be byte-identical too, or git would see a "change" every day.
    files_a = export_marts(tmp_path / "first.duckdb", tmp_path / "export_a")
    files_b = export_marts(tmp_path / "second.duckdb", tmp_path / "export_b")
    for a, b in zip(files_a, files_b):
        assert a.read_bytes() == b.read_bytes(), f"{a.name} is not byte-identical across builds"
