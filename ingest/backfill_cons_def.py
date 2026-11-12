"""One-time backfill of eco2mix-national-cons-def (2012 -> last consolidation) into data/raw/.

Run it once, locally, never in CI:

    uv run python ingest/backfill_cons_def.py

How it works
- The dataset is split into one export per calendar year (UTC). Chunk 2012 also takes the
  4 rows stamped 2011-12-31 23:xx UTC (= 2012-01-01 local), and the last chunk is open-ended,
  so the chunks cover the whole dataset with no overlap.
- Each chunk is downloaded with /exports/parquet to `<chunk>.parquet.part`.
- The row count is checked against a count(*) query for the same filter.
- Two lineage columns (`_source_dataset`, `_extracted_at`) are added, then the file is written
  to `<chunk>.parquet`. A finished chunk is never downloaded again.
- Resume: the export endpoint has no Content-Length and ignores HTTP Range headers (checked in
  Phase 0), so a byte-level resume is impossible. A chunk is small (~2 MB), so an interrupted
  chunk simply restarts from zero. Re-running the same command picks up the unfinished
  extraction folder and skips the finished chunks.
- Retries: network errors, 429 and 5xx are retried with exponential backoff (honours Retry-After).

Output: data/raw/eco2mix-national-cons-def/extracted_date=YYYY-MM-DD/
    cons_def_2012.parquet ... cons_def_<current year>.parquet
    _backfill_manifest.json   (status, filters, row counts, sha256 per chunk)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx

DATASET = "eco2mix-national-cons-def"
BASE_URL = f"https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/{DATASET}"
FIRST_YEAR = 2012
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw"
MANIFEST_NAME = "_backfill_manifest.json"

MAX_ATTEMPTS = 6
BACKOFF_BASE_SECONDS = 5  # 5, 10, 20, 40, 80 s between attempts
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
TIMEOUT = httpx.Timeout(connect=30, read=300, write=30, pool=30)


def log(msg: str) -> None:
    print(f"[{datetime.now(UTC):%H:%M:%S}Z] {msg}", flush=True)


def year_filter(year: int, last_year: int) -> str:
    """ODSQL where-clause for one chunk. Bounds are UTC instants."""
    clauses = []
    if year > FIRST_YEAR:
        clauses.append(f"date_heure >= date'{year}-01-01T00:00:00Z'")
    if year < last_year:
        clauses.append(f"date_heure < date'{year + 1}-01-01T00:00:00Z'")
    return " and ".join(clauses)


def request_with_retry(client: httpx.Client, url: str, params: dict, dest: Path | None = None) -> httpx.Response | int:
    """GET with retries. If dest is given, stream the body to dest and return the byte count."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if dest is None:
                resp = client.get(url, params=params)
                if resp.status_code in RETRYABLE_STATUS:
                    raise httpx.HTTPStatusError("retryable status", request=resp.request, response=resp)
                resp.raise_for_status()
                return resp
            with client.stream("GET", url, params=params) as resp:
                if resp.status_code in RETRYABLE_STATUS:
                    raise httpx.HTTPStatusError("retryable status", request=resp.request, response=resp)
                resp.raise_for_status()
                n_bytes, t0 = 0, time.monotonic()
                with dest.open("wb") as f:  # restart from zero: the endpoint does not support Range
                    for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                        f.write(chunk)
                        n_bytes += len(chunk)
                elapsed = max(time.monotonic() - t0, 1e-6)
                log(f"    downloaded {n_bytes / 1024:,.0f} KiB in {elapsed:.1f}s ({n_bytes / 1024 / elapsed:,.0f} KiB/s)")
                return n_bytes
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            if status is not None and status not in RETRYABLE_STATUS:
                raise  # 400/404 etc.: retrying will not help
            if attempt == MAX_ATTEMPTS:
                raise
            wait = BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
            if status is not None and exc.response.headers.get("retry-after", "").isdigit():
                wait = max(wait, int(exc.response.headers["retry-after"]))
            log(f"    attempt {attempt}/{MAX_ATTEMPTS} failed ({status or type(exc).__name__}); retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def expected_row_count(client: httpx.Client, where: str) -> int:
    params = {"select": "count(*) as n", "limit": 1}
    if where:
        params["where"] = where
    resp = request_with_retry(client, f"{BASE_URL}/records", params)
    results = resp.json()["results"]
    return int(results[0]["n"]) if results else 0


def sql_path(p: Path) -> str:
    return p.as_posix().replace("'", "''")


def find_or_create_run_dir(dataset_dir: Path, force_new: bool) -> Path:
    """Reuse an unfinished backfill folder; refuse to redo a finished one unless forced."""
    manifests = sorted(dataset_dir.glob(f"extracted_date=*/{MANIFEST_NAME}"))
    for manifest_path in reversed(manifests):
        status = json.loads(manifest_path.read_text(encoding="utf-8"))["status"]
        if status == "in_progress":
            log(f"Resuming unfinished backfill in {manifest_path.parent}")
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
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT, help="default: data/raw")
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

    dataset_dir = args.raw_root / DATASET
    run_dir = find_or_create_run_dir(dataset_dir, args.force_new)
    manifest_path = run_dir / MANIFEST_NAME
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists()
                else {"dataset": DATASET, "status": "in_progress", "chunks": {}})
    manifest["planned_chunks"] = sorted(set(manifest.get("planned_chunks", [])) | set(plan))

    def save_manifest() -> None:
        tmp = manifest_path.with_suffix(".json.part")
        tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        tmp.replace(manifest_path)

    save_manifest()
    failed = []
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "grid-carbon-pipeline-backfill"}) as client:
        for year, where in plan.items():
            final = run_dir / f"cons_def_{year}.parquet"
            if final.exists():
                log(f"{final.name}: already done, skipping")
                continue
            part = final.with_name(final.name + ".part")
            log(f"{final.name}: filter = {where or '(none)'}")
            extracted_at = datetime.now(UTC)
            try:
                expected = expected_row_count(client, where)
                params = {"where": where} if where else {}
                request_with_retry(client, f"{BASE_URL}/exports/parquet", params, dest=part)
                con = duckdb.connect()
                got = con.execute(f"select count(*) from read_parquet('{sql_path(part)}')").fetchone()[0]
                if got != expected:
                    raise ValueError(f"row count mismatch: export has {got}, API count says {expected}")
                tmp_final = final.with_name(final.name + ".tmp.part")
                con.execute(f"""
                    copy (
                        select *, '{DATASET}' as _source_dataset,
                               '{extracted_at.isoformat()}'::timestamptz as _extracted_at
                        from read_parquet('{sql_path(part)}')
                    ) to '{sql_path(tmp_final)}' (format parquet)
                """)
                con.close()
                tmp_final.replace(final)  # atomic: a chunk is either fully there or not at all
                part.unlink()
            except Exception as exc:  # keep going with the next chunk; a re-run retries this one
                log(f"    FAILED: {exc!r}")
                failed.append(year)
                continue
            manifest["chunks"][str(year)] = {
                "file": final.name,
                "where": where,
                "rows": got,
                "extracted_at": extracted_at.isoformat(),
                "sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
            }
            save_manifest()
            log(f"    ok: {got:,} rows")

    done = set(manifest["chunks"])
    if not failed and all(str(y) in done for y in manifest["planned_chunks"]) and not args.years:
        manifest["status"] = "complete"
        save_manifest()
        log(f"Backfill complete: {sum(c['rows'] for c in manifest['chunks'].values()):,} rows in {run_dir}")
        return 0
    if failed:
        log(f"Failed chunks: {failed}. Re-run the same command to resume.")
        return 1
    log("Partial run finished (--years given); manifest stays in_progress.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
