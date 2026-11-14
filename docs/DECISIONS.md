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

## D7: Daily real-time pull = the whole tr dataset, not a 45-day lookback
- **Decision:** `ingest/incremental.py` downloads all of `eco2mix-national-tr` once a day.
- **Alternatives:** the spec's 45-day lookback; a cursor ("only rows after the last one I have").
- **Why:** tr holds every slot since RTE's last consolidation (~74 days on 2026-11-14, ~7k rows, ~0.6 MB),
  so a 45-day window would miss the oldest slots, which are exactly the ones about to be revised.
  Rows *leave* tr when they are consolidated. A daily full snapshot is the only way to keep the
  real-time version of every slot, and it needs no state. A cursor would also miss intraday corrections.
- **Cost:** ~0.6 MB/day ≈ 220 MB/year in git. Fine until the deadline. Later: keep only rows that
  changed since the previous snapshot, or move raw to object storage (D6).

## D8: cons-def: a recent window, downloaded only when a fingerprint changes
- **Decision:** the window is everything from 1 January of the previous year (UTC). That covers new
  consolidated months and last year's definitive release (Q2). Before downloading, one `/records` call
  computes a fingerprint per `nature`: row count, first/last timestamp, and the sum of every measure we use.
  If it equals the fingerprint stored in the last `_manifest.json`, the step is skipped.
- **Alternatives:** re-download the window every day (2.7 MB/day ≈ 1 GB/year of identical copies in git);
  trust the catalog's `modified`/`data_processed` metadata (`data_processed` changes daily,
  and I haven't verified that `modified` moves only when data changes).
- **Why:** cons-def changes only a few times a year. 1 API call on a quiet day, 3 on a change day.
- **Limitation:** two revisions that cancel out exactly in every summed column would go unnoticed. That's
  unrealistic for real revisions. A consolidation always changes the row count, and a definitive release changes the per-`nature` counts.

## D9: Idempotency unit = (dataset, UTC extraction date)
- **Decision:** each dataset writes at most one file per UTC day
  (`data/raw/<dataset>/extracted_date=YYYY-MM-DD/`). If today's file exists, the step is skipped.
- **Alternatives:** overwrite today's file (breaks append-only, and a rerun could silently change history);
  one file per run, named by timestamp (a rerun adds a new snapshot, so the marts can change).
- **Why:** a second run on the same day writes nothing, so the raw inputs and the marts are identical. Checked:
  the second run logs `skipped` for both datasets.
- **Consequence:** we capture at most one real-time version per slot per day. That's enough for a
  real-time → consolidated comparison.

## D10: Failure modes of the daily job
- **Runs twice:** second run is a no-op (D9).
- **Fails for 3 days:** nothing is lost. tr still holds every unconsolidated slot (~2 months), and the
  cons-def fingerprint differs from the last saved one, so the next run catches up in one pass.
  What is lost: the real-time values of those 3 days *as they were then*. Only an outage longer
  than the time between a slot's publication and its consolidation (~1–2 months) would lose a slot's
  real-time version entirely.
- **One dataset fails:** the other still runs. The exit code is 1, so CI goes red. A rerun the same day
  retries only the missing dataset, because the other's file already exists.
- **Crash mid-download:** only `*.part` files are left (gitignored). A final file appears only after
  the row-count check and an atomic rename (D3).

## D11: One shared module for API calls and file writing
- **Decision:** `ingest/odre.py` holds retries, the row-count check and lineage columns.
  The backfill and the daily job both call `odre.save_export`.
- **Why:** the guarantees in D3/D4 are written once, so the two scripts can't drift apart. Scripts run
  as modules (`python -m ingest.incremental`) so `from ingest import odre` works on Windows and Linux alike.
