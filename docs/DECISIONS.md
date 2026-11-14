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

## D12: 30-minute grain everywhere
- **Decision:** facts are one row per 30-min interval. Real-time 15-min rows are averaged in pairs
  (:00+:15, :30+:45). A half hour with only one quarter published yet is dropped until the next snapshot.
- **Alternatives:** 15-min grain, with history filled only at :00/:30 (half the rows null for 14 years).
- **Why:** the history (cons-def) only has values every 30 min (DATA_NOTES §4). One grain for the whole
  period keeps every mart comparable. MW are average power over an interval, so the mean of two 15-min
  values is the 30-min value.
- **Assumption to check:** that a cons-def :00 value is the 30-min average, not a point reading.
  `mart_revisions` will show a systematic bias if it isn't.

## D13: 8 energy sources; pumped storage and batteries are not sources
- **Decision:** `dim_energy_source` = nuclear, hydro, wind, solar, bioenergy, gas, coal, oil.
- **Why:** pumped-storage *generation* is already inside `hydraulique`, and pumping (`pompage`) is a load.
  Making either a separate source double-counts or mixes loads with production. Batteries exist only
  in real-time data (Sept 2026+). They stay in `fct_grid_interval.battery_net_mw` for the energy balance.
- **Flags:** bioenergy is renewable but *not* low-carbon (it burns biomass, biogas and waste).
  Hydro counts as dispatchable (reservoirs); wind and solar don't.

## D14: Off-peak = 22:00-06:00 local
- A simplification of the French "heures creuses". Real windows vary by contract and area. It's only a label
  in `dim_time_of_day`; no mart depends on it.

## D15: "Best time" uses 2023 onwards, built from an average daily profile
- **Period:** `analysis_start_date = 2023-01-01`. 2012-2022 includes coal-heavy years and the 2022 crisis, which
  would describe a grid that no longer exists. It's a dbt var, so easy to change.
- **Method:** average CO2 by season × local 30-min slot, then a job's window average is the average of its
  slots. Starts are on the hour; windows past midnight wrap around within the same season profile.
- **Alternative:** compute every real window on every day, then average. More exact across DST days and
  season boundaries, but much heavier and harder to explain. With equal slot counts both give the same number.

## D16: Energy-balance test instead of "sources ≈ total production"
- **Why:** the dataset has no total-production column (DATA_NOTES §3). The physical identity
  consumption ≈ production + pumping + net imports (+ batteries) does exist.
- **Tolerance:** 2% for consolidated/definitive data (worst seen 1.5%) and 8% for real-time (rare spikes
  up to 5.1% in Sept 2026; p99 is 0.18%). A pipeline bug, such as a missing source or a flipped sign, shows
  up as tens of percent.

## D17: Marts are deterministic to the last bit
- **Found by:** `tests/test_idempotency.py`. Two builds on the same data differed at the 15th digit.
- **Cause:** float addition isn't associative, and DuckDB aggregates in parallel threads, so the
  summation order varies between runs.
- **Fix:** where we average arbitrary doubles (`mart_best_window`, `mart_revisions`), the aggregate gets an
  explicit order: `avg(x order by ...)`. All other sums are over integers or half-integers
  (averages of two integers), which doubles add exactly in any order.
- **Alternatives:** rounding (a value near a rounding boundary can still flip); `threads: 1` (works in
  practice but isn't a documented guarantee, and is slower).

## D18: Model layout differs slightly from the spec
- **Spec:** one staging model `stg_eco2mix__national` that unions and dedups.
- **Built:** `stg_eco2mix__consolidated` + `stg_eco2mix__realtime` (one per source: typing, renaming,
  DST phantom removal, 15→30 min), then `int_eco2mix__slot_versions` (every version of every slot) and
  `int_eco2mix__national` (the dedup).
- **Why:** staging stays 1:1 with sources (the dbt convention). The all-versions table is needed twice:
  once to pick the best version, once by `mart_revisions` to compare versions.

## D19: dbt reads the raw Parquet files directly; the warehouse is disposable
- **Decision:** sources are `read_parquet(...)` over `data/raw/` (dbt-duckdb `external_location`), with
  `hive_partitioning=false` (DATA_NOTES §10). `data/warehouse.duckdb` is gitignored and fully rebuilt by
  `dbt build`; the committed raw files are the source of truth.
- **Why:** no load step to maintain; a full rebuild takes ~10 s at this size. At 100× volume this becomes
  incremental models and a persistent warehouse.
- **DuckDB session time zone is pinned to UTC** in `transform/profiles.yml`, so local and CI builds match.

## D20: DST test asserts what correct handling of *this* data looks like
- The spec says "DST days have 23 or 25 hours". In this data the spring day has 23 hours (46 slots), but
  the autumn day has 24 published hours, not 25: RTE publishes the repeated hour once (DATA_NOTES §6).
  `assert_slots_per_local_day` expects 46 / 48. `assert_no_unexpected_gaps` allows exactly that missing
  hour plus the slots listed in the `known_data_gaps` seed.
- Both tests were checked against deliberately broken code (phantom rows kept, known gap removed):
  they fail as they should.

## D21: The daily job runs at 12:17 UTC, chosen by the pipeline's own results
- `mart_carbon_by_hour` (2023+) ranks 13:00 Paris as the cleanest hour of the year (18.3 g/kWh), 14:00 second.
  GitHub cron is UTC and ignores DST: 12:17 UTC = 13:17 in winter, 14:17 in summer, both in the top two.
- `:17`, not `:00`: GitHub delays scheduled runs at the top of the hour, when everyone's crons fire.
- Honest caveat: the job's own energy use is negligible. The point is to show the answer being used.

## D22: Two commits per run: raw first, marts only if every test passed
- **Order:** ingest → commit `data/raw` → `dbt build` (models + tests) → freshness → export → commit `data/marts`.
- **Why raw first:** a real-time snapshot can never be downloaded again later. If a data test fails
  (e.g. a new real-time spike), the run goes red, but the snapshot is already saved.
  The raw commit runs even if one dataset failed (`if: !cancelled()`), to keep what the other downloaded.
- **Why marts last:** if any test fails, the export and its commit don't run. The dashboard keeps yesterday's
  marts rather than showing numbers built from data that failed a check.
- **Alternatives:** one commit at the end (a failed test loses the day's snapshot); commit everything
  regardless (bad numbers reach the dashboard).

## D23: Data commits are authored by `github-actions[bot]`
- Automated commits are labelled as automation, not as Yassine, and not as Claude. Code commits stay Yassine's.
- **Alternative:** a personal identity for the bot (misleading: it would look like hand-made daily commits).

## D24: Exported marts are deterministic files; the dashboard reads only those
- `publish/export_marts.py` writes each mart with `order by all` to Parquet, then renames it into place (atomic).
  `tests/test_idempotency.py` checks that two independent builds export **byte-identical** files.
  Without that, git would record a "change" every day even when nothing changed.
- `pipeline_health.json` sits next to them: run time, run URL, dbt results, freshness, row counts.
  It changes on every run by design: it's the run log for dashboard page 3.
- `data/warehouse.duckdb` stays uncommitted. The dashboard doesn't need a 100 MB database, only ~40 KB of marts.

## D25: CI is a separate workflow; versions are pinned
- `ci.yml` runs `pytest` (unit tests + the two-build idempotency test) on every push and pull request. Pushes
  made by `daily.yml` don't trigger it: GitHub never starts workflows from its own token's pushes, by
  design (no infinite loops). `daily.yml` runs the dbt tests itself.
- `uv sync --locked`: CI fails instead of silently installing versions different from `uv.lock`. The uv version
  is pinned (0.12.5), and actions are pinned to major versions (`@v7`). Hardening for production: pin actions
  to commit SHAs.

## D26: What a failed or missed run looks like
- **Failed step:** the run is red in the Actions tab (and GitHub emails the repo owner). dbt logs and results are
  uploaded as a run artifact for 14 days. The dashboard shows the last good marts, and the run URL in
  `pipeline_health.json` points at the last successful run.
- **Missed days:** caught up on the next run (D10).
- **GitHub disables scheduled workflows in public repos after 60 days without activity.** The daily data commits
  are activity, so this only bites if the job stays broken for 2 months.
- **Concurrency group:** a manual run and the scheduled run can never overlap and race on commits.
