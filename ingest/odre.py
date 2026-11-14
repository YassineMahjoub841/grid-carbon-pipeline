"""Shared helpers for talking to the ODRÉ API and writing raw files.

Used by both ingest/backfill_cons_def.py and ingest/incremental.py, so the two scripts download,
verify and write files in exactly the same way.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx

API_BASE = "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets"
TR = "eco2mix-national-tr"
CONS_DEF = "eco2mix-national-cons-def"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw"

MAX_ATTEMPTS = 6
BACKOFF_BASE_SECONDS = 5  # waits 5, 10, 20, 40, 80 s between attempts
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
TIMEOUT = httpx.Timeout(connect=30, read=300, write=30, pool=30)


def log(msg: str) -> None:
    print(f"[{datetime.now(UTC):%H:%M:%S}Z] {msg}", flush=True)


def make_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "grid-carbon-pipeline"})


def with_retries(action):
    """Run action() and retry on network errors, 429 and 5xx, with exponential backoff.

    Other HTTP errors (400, 404...) are raised at once: retrying would not fix them.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return action()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            if status is not None and status not in RETRYABLE_STATUS:
                raise
            if attempt == MAX_ATTEMPTS:
                raise
            wait = BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
            retry_after = exc.response.headers.get("retry-after", "") if status else ""
            if retry_after.isdigit():
                wait = max(wait, int(retry_after))
            log(f"    attempt {attempt}/{MAX_ATTEMPTS} failed ({status or type(exc).__name__}); retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def get_json(client: httpx.Client, dataset: str, params: dict) -> dict:
    """One call to the /records endpoint (used for counts and fingerprints)."""
    def go():
        resp = client.get(f"{API_BASE}/{dataset}/records", params=params)
        resp.raise_for_status()
        return resp.json()
    return with_retries(go)


def count_rows(client: httpx.Client, dataset: str, where: str) -> int:
    params = {"select": "count(*) as n", "limit": 1}
    if where:
        params["where"] = where
    results = get_json(client, dataset, params)["results"]
    return int(results[0]["n"]) if results else 0


def download_export(client: httpx.Client, dataset: str, where: str, dest: Path) -> int:
    """Stream /exports/parquet to dest. The endpoint ignores Range headers, so every attempt restarts at byte 0."""
    params = {"where": where} if where else {}

    def go():
        with client.stream("GET", f"{API_BASE}/{dataset}/exports/parquet", params=params) as resp:
            resp.raise_for_status()
            n_bytes, t0 = 0, time.monotonic()
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
                    n_bytes += len(chunk)
        elapsed = max(time.monotonic() - t0, 1e-6)
        log(f"    downloaded {n_bytes / 1024:,.0f} KiB in {elapsed:.1f}s")
        return n_bytes
    return with_retries(go)


def sql_path(p: Path) -> str:
    return p.as_posix().replace("'", "''")


def save_export(client: httpx.Client, dataset: str, where: str, final: Path, extracted_at: datetime) -> dict:
    """Download one export into `final`, verified and with lineage columns. Returns what goes in the manifest.

    Steps: count rows via the API -> download to <final>.part -> check the row count ->
    add _source_dataset/_extracted_at -> write <final>.tmp.part -> atomic rename to <final>.
    A crash at any step leaves only *.part files (gitignored); `final` exists only when complete.
    """
    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_name(final.name + ".part")
    tmp_final = final.with_name(final.name + ".tmp.part")
    expected = count_rows(client, dataset, where)
    download_export(client, dataset, where, part)
    con = duckdb.connect()
    try:
        got = con.execute(f"select count(*) from read_parquet('{sql_path(part)}')").fetchall()[0][0]
        if got != expected:
            raise ValueError(f"row count mismatch: export has {got}, API count says {expected}")
        con.execute(f"""
            copy (
                select *, '{dataset}' as _source_dataset,
                       '{extracted_at.isoformat()}'::timestamptz as _extracted_at
                from read_parquet('{sql_path(part)}')
            ) to '{sql_path(tmp_final)}' (format parquet)
        """)
    finally:
        con.close()
    tmp_final.replace(final)
    part.unlink()
    return {
        "file": final.name,
        "where": where,
        "rows": got,
        "extracted_at": extracted_at.isoformat(),
        "sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
    }


def write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
