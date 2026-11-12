# DECISIONS

One entry per non-obvious choice: decision, alternatives, why. Evidence lives in `docs/DATA_NOTES.md`.

## D1: Backfill cons-def as one Parquet export per calendar year
- **Decision:** `ingest/backfill_cons_def.py` downloads 15 yearly chunks (UTC year bounds; the
  first chunk has no lower bound, the last none upper), each with `/exports/parquet`.
- **Alternatives:** one single export (simplest, but any interruption restarts all of it);
  byte-range resume (impossible: the endpoint sends no `Content-Length` and ignores `Range`);
  the `/records` endpoint (paginated and capped, so thousands of calls instead of ~30).
- **Why:** a year is ~2 MB, so restarting a failed chunk costs seconds. Resume becomes "skip the
  files that already exist", which is easy to explain and to test.

## D2: Parquet export, not CSV
- **Decision:** use `/exports/parquet`.
- **Alternatives:** CSV was ~4× bigger and slightly faster to download in Phase 0.
- **Why:** Parquet keeps types, including `date_heure` as a real `TIMESTAMP WITH TIME ZONE`, so
  no locale/BOM/`;` parsing. DuckDB reads it natively. Size is irrelevant at ~25 MB total.

## D3: A chunk only counts as done after a row-count check and an atomic rename
- **Decision:** download to `*.part`, compare row count with a `count(*)` API call on the same
  filter, then write the final file and rename it into place. Retries: 6 attempts, exponential
  backoff (5 → 80 s), honour `Retry-After`, retry only network errors / 429 / 5xx.
- **Alternatives:** trust the HTTP 200 (a truncated stream can still look like success);
  checksums (the API doesn't publish any).
- **Why:** a final file either exists complete and verified or doesn't exist at all, so a rerun
  never has to guess. Cost: one extra API call per chunk.

## D4: Lineage columns added at download time; data otherwise as received
- **Decision:** each raw row gets `_source_dataset` and `_extracted_at` (UTC). Nothing else is
  changed: no renaming, no casting. `nature` is already in the source.
- **Alternatives:** keep files byte-identical and store lineage only in a manifest; clean types
  during ingestion.
- **Why:** the raw-layer rule says every row carries its lineage. A row read in isolation still
  says where and when it came from. All cleaning stays in dbt staging, where it's tested.
  `_backfill_manifest.json` additionally records filters, row counts and sha256 per file.

## D5: The backfill refuses to run twice
- **Decision:** if a manifest with `status: complete` exists, the script exits unless
  `--force-new` is passed. An unfinished folder is resumed, even on a later day.
- **Why:** "download the backfill once, never in CI" is enforced by the code, not just a README note.

## D6: Committing raw Parquet to the repo (deliberate trade-off)
- **Decision:** `data/raw/` is committed (cons-def backfill ≈ 25 MB, then small daily files).
- **Alternatives:** object storage (GCS/S3), Git LFS.
- **Why:** free, zero infrastructure, CI can read it, and reviewers can see it. **In production
  this would be object storage (GCS/S3)**, because git is a poor fit for growing binary data.
