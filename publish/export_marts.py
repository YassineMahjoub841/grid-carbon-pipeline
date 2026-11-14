"""Export the marts and a pipeline-health summary to data/marts/ (small files, committed, read by the dashboard).

    uv run python -m publish.export_marts

Run after `dbt build` and `dbt source freshness`. Writes:
    data/marts/<mart>.parquet       one file per mart, rows sorted, so the same data gives the same bytes
    data/marts/pipeline_health.json when the pipeline ran, test results, freshness, row counts

The dashboard never reads the warehouse (data/warehouse.duckdb is not committed): only these files.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "warehouse.duckdb"
DEFAULT_OUT = PROJECT_ROOT / "data" / "marts"
DBT_TARGET = PROJECT_ROOT / "transform" / "target"
MARTS = ["mart_carbon_by_hour", "mart_best_window", "mart_monthly_mix_exchanges", "mart_revisions"]


def sql_path(p: Path) -> str:
    return p.as_posix().replace("'", "''")


def export_marts(db_path: Path, out_dir: Path) -> list[Path]:
    """One Parquet file per mart. `order by all` fixes the row order, so identical data -> identical files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    with duckdb.connect(str(db_path), read_only=True) as con:
        con.execute("set TimeZone='UTC'")
        for mart in MARTS:
            final = out_dir / f"{mart}.parquet"
            tmp = final.with_name(final.name + ".part")
            con.execute(f"copy (select * from {mart} order by all) to '{sql_path(tmp)}' (format parquet)")
            tmp.replace(final)  # atomic: the dashboard never reads a half-written file
            written.append(final)
    return written


def dbt_results(target: Path) -> dict:
    """Summarise transform/target/run_results.json, which `dbt build` writes."""
    path = target / "run_results.json"
    if not path.exists():
        return {"available": False}
    run = json.loads(path.read_text(encoding="utf-8"))
    counts: Counter = Counter()
    problems = []
    for r in run["results"]:
        kind = r["unique_id"].split(".")[0]  # model, test, seed...
        counts[f"{kind}:{r['status']}"] += 1
        if r["status"] not in ("success", "pass"):
            problems.append({"node": r["unique_id"], "status": r["status"], "failures": r.get("failures")})
    return {
        "available": True,
        "generated_at": run["metadata"]["generated_at"],
        "elapsed_seconds": round(run["elapsed_time"], 1),
        "counts": dict(sorted(counts.items())),
        "problems": problems,
    }


def freshness_results(target: Path) -> dict:
    """Summarise transform/target/sources.json, which `dbt source freshness` writes."""
    path = target / "sources.json"
    if not path.exists():
        return {"available": False}
    run = json.loads(path.read_text(encoding="utf-8"))
    return {
        "available": True,
        "sources": [
            {"source": r["unique_id"].split(".")[-1], "status": r["status"],
             "max_loaded_at": r.get("max_loaded_at"), "age_hours": round(r.get("max_loaded_at_time_ago_in_s", 0) / 3600, 1)}
            for r in sorted(run["results"], key=lambda r: r["unique_id"])
        ],
    }


def data_summary(db_path: Path) -> dict:
    with duckdb.connect(str(db_path), read_only=True) as con:
        con.execute("set TimeZone='UTC'")

        def rows(sql: str) -> list[dict]:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            return [{c: (v.isoformat() if hasattr(v, "isoformat") else v) for c, v in zip(cols, row)}
                    for row in cur.fetchall()]

        return {
            "intervals_by_status": rows("""
                select data_status, count(*) as n_intervals,
                       min(interval_start_utc) as first_interval_utc, max(interval_start_utc) as last_interval_utc
                from fct_grid_interval group by 1 order by min(interval_start_utc)"""),
            "latest_extraction_by_dataset": rows("""
                select source_dataset, max(extracted_at) as latest_extracted_at, count(distinct extracted_at) as n_extractions
                from int_eco2mix__slot_versions group by 1 order by 1"""),
            "row_counts": rows("""
                select 'int_eco2mix__slot_versions' as table_name, count(*) as n from int_eco2mix__slot_versions
                union all select 'fct_grid_interval', count(*) from fct_grid_interval
                union all select 'fct_generation_interval', count(*) from fct_generation_interval
                """ + "".join(f" union all select '{m}', count(*) from {m}" for m in MARTS)),
        }


def pipeline_health(db_path: Path, target: Path) -> dict:
    server, repo, run_id = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "run_url": f"{server}/{repo}/actions/runs/{run_id}" if run_id else None,  # None for local runs
        "dbt_build": dbt_results(target),
        "source_freshness": freshness_results(target),
        "data": data_summary(db_path),
    }


def main() -> int:
    written = export_marts(DEFAULT_DB, DEFAULT_OUT)
    health = pipeline_health(DEFAULT_DB, DBT_TARGET)
    health_path = DEFAULT_OUT / "pipeline_health.json"
    tmp = health_path.with_name(health_path.name + ".part")
    tmp.write_text(json.dumps(health, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(health_path)
    for p in [*written, health_path]:
        print(f"wrote {p.relative_to(PROJECT_ROOT).as_posix()} ({p.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
