"""Daily incremental ingestion: what the scheduled job runs.

    uv run python -m ingest.incremental

Two steps, each writing at most one file per dataset per UTC day:

1. Real-time (eco2mix-national-tr): download ALL of it, every day.
   tr only holds the slots since RTE's last consolidation (~2-3 months, ~7k rows, ~0.6 MB), and its
   rows disappear when RTE consolidates them. A daily full snapshot is the only way to keep the
   real-time version of every slot, which is what mart_revisions compares against.
   Cost: 2 API calls (count + export).

2. Consolidated/definitive (eco2mix-national-cons-def), recent window only:
   all rows from 1 January of the previous year (UTC). That window catches new consolidated months
   (published ~1-2 months after month end) and the definitive release of last year (Q2).
   cons-def changes only a few times a year, so we don't re-download it blindly. First one cheap
   "fingerprint" query (row count, first/last timestamp and the sum of every measure we use, per `nature`).
   If it equals the fingerprint saved with the last download, nothing changed upstream: skip.
   Cost: 1 API call on a quiet day, 3 when something changed.

Idempotency: the unit is (dataset, UTC extraction date). If today's file already exists, the step
is skipped, so running the job twice on the same day writes nothing new and the marts can't change.

Output: data/raw/<dataset>/extracted_date=YYYY-MM-DD/{tr.parquet | cons_def_recent.parquet, _manifest.json}
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ingest import odre

MANIFEST_NAME = "_manifest.json"
FINGERPRINT_COLUMNS = [
    "consommation", "taux_co2", "ech_physiques", "pompage", "nucleaire", "eolien", "solaire",
    "hydraulique", "gaz", "charbon", "fioul", "bioenergies",
]
FINGERPRINT_SELECT = "nature, count(*) as n, min(date_heure) as first_utc, max(date_heure) as last_utc, " + ", ".join(
    f"sum({c}) as sum_{c}" for c in FINGERPRINT_COLUMNS
)


def day_folder(raw_root: Path, dataset: str, now: datetime) -> Path:
    return raw_root / dataset / f"extracted_date={now:%Y-%m-%d}"


def cons_def_window(now: datetime) -> str:
    return f"date_heure >= date'{now.year - 1}-01-01T00:00:00Z'"


def fingerprint(client: httpx.Client, where: str) -> list[dict]:
    """A cheap summary of the window. If RTE revises, adds or re-labels rows, it changes."""
    rows = odre.get_json(client, odre.CONS_DEF, {"select": FINGERPRINT_SELECT, "where": where, "group_by": "nature"})
    return sorted(rows["results"], key=lambda r: r["nature"])


def last_saved_manifest(dataset_dir: Path) -> dict | None:
    """Most recent incremental manifest for this dataset (the backfill has its own and is ignored)."""
    manifests = sorted(dataset_dir.glob(f"extracted_date=*/{MANIFEST_NAME}"))
    return json.loads(manifests[-1].read_text(encoding="utf-8")) if manifests else None


def capture_tr(client: httpx.Client, raw_root: Path, now: datetime) -> str:
    folder = day_folder(raw_root, odre.TR, now)
    final = folder / "tr.parquet"
    if final.exists():
        odre.log(f"tr: today's snapshot already exists ({final.parent.name}), skipping")
        return "skipped"
    odre.log("tr: downloading full real-time dataset")
    info = odre.save_export(client, odre.TR, "", final, now)
    odre.write_json_atomic(folder / MANIFEST_NAME, {"dataset": odre.TR, **info})
    odre.log(f"    ok: {info['rows']:,} rows")
    return "written"


def capture_cons_def(client: httpx.Client, raw_root: Path, now: datetime) -> str:
    folder = day_folder(raw_root, odre.CONS_DEF, now)
    final = folder / "cons_def_recent.parquet"
    if final.exists():
        odre.log(f"cons-def: today's file already exists ({final.parent.name}), skipping")
        return "skipped"
    where = cons_def_window(now)
    current = fingerprint(client, where)
    previous = last_saved_manifest(raw_root / odre.CONS_DEF)
    if previous and previous["where"] == where and previous["fingerprint"] == current:
        odre.log(f"cons-def: unchanged since {previous['extracted_at'][:10]} (fingerprint match), skipping")
        return "unchanged"
    odre.log(f"cons-def: {'changed upstream' if previous else 'no previous incremental pull'}; downloading {where}")
    info = odre.save_export(client, odre.CONS_DEF, where, final, now)
    odre.write_json_atomic(folder / MANIFEST_NAME, {"dataset": odre.CONS_DEF, **info, "fingerprint": current})
    odre.log(f"    ok: {info['rows']:,} rows")
    return "written"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-root", type=Path, default=odre.DEFAULT_RAW_ROOT, help="default: data/raw")
    args = parser.parse_args()

    now = datetime.now(UTC)
    results, failed = {}, []
    with odre.make_client() as client:
        for name, step in (("tr", capture_tr), ("cons-def", capture_cons_def)):
            try:  # one dataset failing must not stop the other
                results[name] = step(client, args.raw_root, now)
            except Exception as exc:
                odre.log(f"{name}: FAILED: {exc!r}")
                failed.append(name)
    odre.log(f"Done: {results}" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
