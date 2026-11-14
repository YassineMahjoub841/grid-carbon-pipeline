"""One-time backfill of eco2mix-national-cons-def (2012 -> last consolidation) into data/raw/.

Run it once, locally, never in CI:

    uv run python -m ingest.backfill_cons_def

How it works
- The dataset is split into one export per calendar year (UTC). Chunk 2012 also takes the
  4 rows stamped 2011-12-31 23:xx UTC (= 2012-01-01 local), and the last chunk is open-ended,
  so the chunks cover the whole dataset with no overlap.
- Each chunk goes through odre.save_export: API row count, download, row-count check, lineage
  columns, atomic rename. A finished chunk is never downloaded again.
- Resume: the export endpoint ignores HTTP Range headers, so a byte-level resume is impossible.
  A chunk is small (~2 MB), so an interrupted chunk simply restarts from zero. Re-running the same
  command picks up the unfinished extraction folder and skips the finished chunks.

Output: data/raw/eco2mix-national-cons-def/extracted_date=YYYY-MM-DD/
    cons_def_2012.parquet ... cons_def_<current year>.parquet
    _backfill_manifest.json   (status, filters, row counts, sha256 per chunk)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from ingest import odre

FIRST_YEAR = 2012
MANIFEST_NAME = "_backfill_manifest.json"


def year_filter(year: int, last_year: int) -> str:
    """ODSQL where-clause for one chunk. Bounds are UTC instants."""
    clauses = []
    if year > FIRST_YEAR:
        clauses.append(f"date_heure >= date'{year}-01-01T00:00:00Z'")
    if year < last_year:
        clauses.append(f"date_heure < date'{year + 1}-01-01T00:00:00Z'")
    return " and ".join(clauses)


def find_or_create_run_dir(dataset_dir: Path, force_new: bool) -> Path:
    """Reuse an unfinished backfill folder; refuse to redo a finished one unless forced."""
    manifests = sorted(dataset_dir.glob(f"extracted_date=*/{MANIFEST_NAME}"))
    for manifest_path in reversed(manifests):
        status = json.loads(manifest_path.read_text(encoding="utf-8"))["status"]
        if status == "in_progress":
            odre.log(f"Resuming unfinished backfill in {manifest_path.parent}")
            return manifest_path.parent
        if status == "complete" and not force_new:
            sys.exit(f"A complete backfill already exists in {manifest_path.parent}. "
                     "The backfill is meant to run once. Use --force-new to take a new snapshot anyway.")
    run_dir = dataset_dir / f"extracted_date={datetime.now(UTC):%Y-%m-%d}"
    if run_dir.exists() and any(run_dir.iterdir()):
        sys.exit(f"{run_dir} exists and is not an unfinished backfill; refusing to write into it.")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-root", type=Path, default=odre.DEFAULT_RAW_ROOT, help="default: data/raw")
    parser.add_argument("--years", type=int, nargs="*", help="only these chunk years (testing)")
    parser.add_argument("--force-new", action="store_true", help="take a new snapshot even if one is complete")
    parser.add_argument("--dry-run", action="store_true", help="print the chunk plan; no API calls")
    args = parser.parse_args()

    last_year = datetime.now(UTC).year
    years = args.years or list(range(FIRST_YEAR, last_year + 1))
    plan = {y: year_filter(y, last_year) for y in years}
    if args.dry_run:
        for y, where in plan.items():
            print(f"cons_def_{y}.parquet  where: {where or '(none)'}")
        return 0

    run_dir = find_or_create_run_dir(args.raw_root / odre.CONS_DEF, args.force_new)
    manifest_path = run_dir / MANIFEST_NAME
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists()
                else {"dataset": odre.CONS_DEF, "status": "in_progress", "chunks": {}})
    manifest["planned_chunks"] = sorted(set(manifest.get("planned_chunks", [])) | set(plan))
    odre.write_json_atomic(manifest_path, manifest)

    failed = []
    with odre.make_client() as client:
        for year, where in plan.items():
            final = run_dir / f"cons_def_{year}.parquet"
            if final.exists():
                odre.log(f"{final.name}: already done, skipping")
                continue
            odre.log(f"{final.name}: filter = {where or '(none)'}")
            try:
                info = odre.save_export(client, odre.CONS_DEF, where, final, datetime.now(UTC))
            except Exception as exc:  # keep going with the next chunk; a re-run retries this one
                odre.log(f"    FAILED: {exc!r}")
                failed.append(year)
                continue
            manifest["chunks"][str(year)] = info
            odre.write_json_atomic(manifest_path, manifest)
            odre.log(f"    ok: {info['rows']:,} rows")

    done = set(manifest["chunks"])
    if not failed and all(str(y) in done for y in manifest["planned_chunks"]) and not args.years:
        manifest["status"] = "complete"
        odre.write_json_atomic(manifest_path, manifest)
        odre.log(f"Backfill complete: {sum(c['rows'] for c in manifest['chunks'].values()):,} rows in {run_dir}")
        return 0
    if failed:
        odre.log(f"Failed chunks: {failed}. Re-run the same command to resume.")
        return 1
    odre.log("Partial run finished (--years given); manifest stays in_progress.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
