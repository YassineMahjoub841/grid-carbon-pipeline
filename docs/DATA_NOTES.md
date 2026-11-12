# DATA_NOTES: Phase 0 discovery

Snapshot taken **2026-11-12** (~22:30 UTC) against ODRÉ Explore API v2.1,
`https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/{dataset_id}/`.
Evidence comes from real calls: dataset metadata (`/`), aggregations on `/records`
(`select=…&group_by=…`), and two Parquet exports (cons-def 2025, all of tr) analysed in DuckDB.
Numbers below are what the API returned that day and will drift as data is published.

## Verdict on each "known fact / gotcha" in CLAUDE.md

| # | Claim in the spec | Verdict |
|---|---|---|
| 1 | National data has consumption, production by source, `taux_co2`, exchanges | **Confirmed**, but there is **no total-production column** and the two datasets have different columns (§3) |
| 2 | Data goes real-time → consolidated → definitive, flagged by `nature` | **Confirmed.** Exact values in §2. **The two datasets never overlap in time** (§2) |
| 3 | Quota 50,000 calls / user / month | **Not what the API reports.** Headers say 10,000,000 per day, reset at 00:00 UTC (§7). Stay frugal anyway |
| 4 | `/exports` is very slow (tens of KiB/s) | **Not reproduced.** 0.3–2.3 MiB/s; a full year is ~1.7 MB Parquet. No `Content-Length`, **no HTTP Range support** (§7) |
| 5 | Exchanges only every 30 min → nulls at :15/:45 | **Wrong way round.** In cons-def, *consumption, production and CO2* are null at :15/:45. It is a 30-min dataset. Commercial exchanges are the exception: 15-min since mid-2024. In tr, nothing is null at :15/:45 (§4) |
| 6 | `date_heure` is timezone-aware | **Confirmed.** UTC instant (`+00:00` in JSON, `TIMESTAMP WITH TIME ZONE` in Parquet). `date`/`heure` are local Paris text (§5) |
| 7 | DST days have 23 or 25 hours | **Not in this data.** Every local day has exactly 96 rows. Spring-forward has 4 *duplicated* UTC timestamps; fall-back is *missing* one UTC hour (§6) |
| 8 | Column names are French | **Confirmed.** Mapping proposal in §9 |

---

## 1. Datasets at a glance

| | `eco2mix-national-tr` | `eco2mix-national-cons-def` |
|---|---|---|
| rows | 7,104 | 514,272 |
| range (UTC) | 2026-08-31 22:00 → 2026-11-13 22:45 | 2011-12-31 23:00 → 2026-08-31 21:45 |
| range (local) | 2026-09-01 00:00 → **tomorrow** 23:45 | 2012-01-01 00:00 → 2026-08-31 23:45 |
| `nature` values | `Données temps réel` | `Données définitives` (≤ 2025-12-31), `Données consolidées` (2026-01-01 → 2026-08-31) |
| effective grain | 15 min, all fields | **30 min** for consumption / production / CO2 / physical exchanges |
| catalog `modified` | 2026-01-28 (schema) | 2026-10-06 (last consolidation?) |
| catalog `timezone` | `Europe/Paris` | *(empty)* |

Query: `select=nature, count(*) as n, min(date_heure), max(date_heure)&group_by=nature`.

## 2. `nature` values and the data lifecycle

Exact strings (UTF-8, with accents): `Données temps réel`, `Données consolidées`, `Données définitives`.
Staging should map them to `real_time`, `consolidated` and `definitive`.

**Key finding: tr and cons-def do not overlap.** tr starts exactly where cons-def ends
(2026-09-01 00:00 local). A timestamp lives in one dataset at a time. When RTE consolidates a
month, those rows **leave tr** and **appear in cons-def**. Consequences:

- The dedup rule *definitive > consolidated > real-time* still makes sense, but **across
  extractions over time**, not between the two datasets in one pull. Only our own raw history
  holds several versions of the same timestamp.
- **Revisions are invisible unless we saved the real-time version before it disappeared.** The
  backfill contains no real-time values at all, so `mart_revisions` can only cover months
  for which we captured tr snapshots ourselves.
- Consolidation cadence (inferred from one snapshot, so treat it as approximate): August 2026 was
  consolidated by 2026-10-06 (catalog `modified`), and September is still real-time on
  2026-11-12. So consolidation is closer to **M+1 to M+2** than "mid M+1". **September 2026
  may be consolidated any day.** If we haven't captured tr by then, we lose the only
  real-time → consolidated comparison available before the deadline.
- Consolidated → definitive: 2025 is already definitive, which fits "Q2 of A+1". The 2026
  consolidated rows won't become definitive until ~Q2 2027, so that revision type **can't be
  observed before the deadline**.
- tr holds ~74 days today (everything since the last consolidation). The spec's **45-day lookback
  would miss the oldest unconsolidated days.** The whole of tr is small (≈ 7k rows, under 1 MB),
  so pulling all of it daily is one call.

## 3. Fields

37 columns in cons-def, 41 in tr. Columns **only in tr**: `eolien_terrestre`, `eolien_offshore`,
`stockage_batterie`, `destockage_batterie`.

Numeric fields typed as **text** in the catalog/Parquet (types differ between datasets!):

| field | tr | cons-def | non-numeric values seen |
|---|---|---|---|
| `hydraulique_fil_eau_eclusee` | text | int | none (just null) |
| `gaz_cogen` | int | text | `"ND"` (*non disponible*), 17,570 rows, all of 2012 |
| `ech_comm_allemagne_belgique` | int | text | none seen in top values |

→ Staging must `try_cast` every measure and treat `"ND"` as null. The Parquet schema can differ
between export chunks, so read raw with `union_by_name=true`.

Other field facts:
- **No total-production column.** The spec's test "sum of generation by source ≈ total
  production" can't be written as stated. What does hold is the **energy balance**
  (verified on the 2025 cons-def slice and all of tr):
  `consommation ≈ fioul + charbon + gaz + nucleaire + eolien + solaire + hydraulique + bioenergies + pompage + ech_physiques (+ stockage_batterie + destockage_batterie in tr)`

  | | mean abs residual | max residual | p99 abs residual / consumption |
  |---|---|---|---|
  | cons-def 2025 | 78 MW | −246 / +55 MW | 0.32 % |
  | tr, with battery | 10 MW | −1,219 / +3,228 MW | 0.04 % |
  | tr, without battery | 119 MW | | |

  A ~1% tolerance test on this balance is realistic.
- **Sub-components add up to their parent** (max abs diff, MW): gas 16–24, oil 4–12, hydro 1–9,
  bioenergy 1. They are null for all of 2012 (they start in 2013).
- `eolien` ≠ `eolien_terrestre + eolien_offshore` on some rows (max diff 725 MW). Use `eolien`.
- **Sign conventions** (from min/max and the balance):
  - `pompage` ≤ 0: energy *consumed* by pumped-storage pumping. Pumped-storage *generation*
    is **inside `hydraulique`** as `hydraulique_step_turbinage`.
  - `ech_physiques` > 0 = **net import**, < 0 = net export.
  - `stockage_batterie` ≤ 0 (charging), `destockage_batterie` ≥ 0 (discharging).
- `ech_physiques` (physical flows) ≠ sum of `ech_comm_*` (commercial schedules): diff up to
  7.5 GW. Use **`ech_physiques`** for "net imports".
- `taux_co2`: 5–141 g/kWh over the whole history (avg 41), 9–88 in tr. Never ≤ 0. Never null when
  `consommation` is non-null. The 0–300 range test is safe.
- `prevision_j1` / `prevision_j` (forecasts) are filled even on rows where actuals are null.

## 4. Where nulls appear

Non-null counts by `minute(date_heure)` on rows with `consommation is not null`:

- **cons-def:** only minutes **:00 and :30** have actuals. At :15/:45 *every* actual is null:
  consumption, CO2, all production, `pompage`, `ech_physiques`. `nature`, `date`, `heure`,
  forecasts and (since 2024) commercial exchanges are filled. **cons-def is a 30-min dataset padded
  to 15-min rows.**
- **Commercial exchanges** (`ech_comm_*`) in cons-def: half-hourly until
  **2024-06-26 22:15 UTC** (2024-06-27 local), every 15 min after that.
- **tr:** no nulls at :15/:45. All 1,751–1,752 rows per minute bucket are fully populated.
- **Rows that look real but are empty:**
  - tr includes **all of tomorrow** (96 rows) plus the last ~2 slots of today, with only
    `prevision_j1` filled. These are forecast placeholders, not data. Filter on
    `consommation is not null`.
  - cons-def's last row (2026-08-31 23:45 local) has only forecasts and commercial exchanges.
  - **2026-01-01 00:00 local** (first consolidated row) has null actuals: a real one-slot
    hole at the definitive/consolidated boundary.
- Some `ech_comm_angleterre` gaps in 2012 (626 slots) and a handful in 2013–2015.

## 5. Timestamps

- `date_heure`: JSON `"2026-08-31T22:00:00+00:00"` (UTC). Parquet: `TIMESTAMP WITH TIME ZONE`.
- `date` (`"2026-09-01"`) and `heure` (`"00:00"`) are **local Europe/Paris** text, so the first
  row of tr is `date=2026-09-01, heure=00:00, date_heure=2026-08-31T22:00Z`.
- ODSQL's `year(date_heure)` works in **UTC**: "2011" has 4 rows (2011-12-31 23:00–23:45 UTC).
- **DuckDB displays `TIMESTAMPTZ` in the machine's time zone** (it showed `+01` on this Windows
  machine; GitHub Actions runners are UTC). Any `::date`, `hour()` or string cast depends on it.
  **Set `TimeZone='UTC'` explicitly** and convert to Paris on purpose, or local and CI runs
  produce different marts.

## 6. DST behaviour (the important surprise)

Every local date has exactly **96 rows**, including DST days. RTE keys rows on a 24-hour local
clock, and the UTC conversion produces two artifacts:

**Spring forward (last Sunday of March):** local 02:00–02:45 doesn't exist, yet rows with
`heure` 02:00–02:45 are present. They map to the **same UTC instants** as 03:00–03:45, giving
**4 duplicated `date_heure` values per year** (found in all 7 spring-forward days checked between 2012 and 2026: 4 phantom rows each).
```
date_heure (UTC)      heure  consommation
2025-03-30 01:00      02:00  47832   <- phantom
2025-03-30 01:00      03:00  47832   <- real (01:00 UTC = 03:00 CEST)
2025-03-30 01:30      02:30  48415   <- phantom: same value as 01:30 local (00:30 UTC)
2025-03-30 01:30      03:30  48397   <- real
```
→ Dedup: keep the row whose `heure` equals `date_heure` converted to Paris time.

**Fall back (last Sunday of October):** local 02:00–02:45 happens twice, but only one copy
exists. UTC **00:00–00:45 is missing**, which is a real 1-hour hole in the UTC series (checked
on 2022-10-30, 2025-10-26 in cons-def and 2026-10-25 in tr):
```
2025-10-25 23:30 UTC  01:30
2025-10-26 01:00 UTC  02:00   <- gap: 00:00-00:45 UTC never published
```
→ The gap test must allow exactly one missing UTC hour on the last Sunday of October.
In practice: spring days have 92 distinct slots (23 h), fall days have 96 distinct slots but only 24 of
the 25 real hours.

Yearly row counts are still a clean 35,040 / 35,136 (leap), because the 4 extra rows in spring
and the 4 missing rows in autumn cancel out.

## 7. API behaviour

- **Rate limit headers** (anonymous calls): `X-RateLimit-Limit: 10000000`,
  `X-RateLimit-Reset: <tomorrow> 00:00:00+00:00`, and `Remaining` drops by 1 per call (exports
  included). The 50k/month figure in the spec isn't visible anywhere. It may apply to
  authenticated users or be outdated. Design for a handful of calls per day regardless.
- **Exports** (`/exports/parquet`, `/exports/csv`): `Transfer-Encoding: chunked`, **no
  `Content-Length`, `Range` header ignored** (we got 200 with the full body). That makes
  byte-level resume impossible, so we resume per chunk. Measured: Parquet 1.7 MB/year at
  0.3–1.7 MiB/s; CSV 6.2 MB/year at 1.3 MiB/s. The whole cons-def dataset ≈ 25 MB Parquet.
  CSV is `;`-separated with a UTF-8 BOM.
- ODSQL quirks seen: equality on datetimes needs `date'…'` literals; `>=` on text fields
  errors; parentheses inside aggregate expressions (`max(abs(a-(b+c)))`) are a syntax error;
  an aggregate without `group_by` returns the same row repeated `limit` times; aliases on
  `group_by` expressions are ignored.

## 8. Sanity check of the 2022 story (claim checked, not assumed)

`sum(ech_physiques) × 0.5 h` per year (cons-def, 30-min rows), TWh, + = net import:

| year | net exchange | nuclear | avg CO2 g/kWh |
|---|---|---|---|
| 2019 | −58.5 | 379 | 34 |
| 2020 | −45.8 | 335 | 33 |
| 2021 | −45.1 | 361 | 34 |
| **2022** | **+14.8** | **279** | **52** |
| 2023 | −50.7 | 320 | 30 |
| 2024 | −89.6 | 362 | 19 |
| 2025 | −93.1 | 373 | 17 |

The story holds: 2022 is the only net-import year since 2012, nuclear output collapsed, and CO2
intensity rose. (Simple slot averages are not consumption-weighted; Phase 2 decides the metric.)

## 9. Proposed column mapping (for staging)

| French | English | | French | English |
|---|---|---|---|---|
| `date_heure` | `interval_start_utc` | | `nature` | `data_status` |
| `consommation` | `consumption_mw` | | `taux_co2` | `co2_intensity_g_per_kwh` |
| `prevision_j1` / `prevision_j` | `forecast_d1_mw` / `forecast_d0_mw` | | `ech_physiques` | `net_physical_exchange_mw` (+ = import) |
| `nucleaire` | `nuclear_mw` | | `eolien` | `wind_mw` |
| `solaire` | `solar_mw` | | `hydraulique` | `hydro_mw` |
| `gaz` | `gas_mw` | | `charbon` | `coal_mw` |
| `fioul` | `oil_mw` | | `bioenergies` | `bioenergy_mw` |
| `pompage` | `pumping_mw` (≤ 0) | | `hydraulique_step_turbinage` | `hydro_pumped_storage_mw` |
| `stockage_batterie` / `destockage_batterie` | `battery_charge_mw` / `battery_discharge_mw` | | `ech_comm_<pays>` | `commercial_exchange_<country>_mw` |
| `date`, `heure` | `local_date_text`, `local_time_text` (used only to drop DST phantoms) | | `perimetre` | dropped (always `France`) |

## 10. Open questions for later phases (not decided here)

1. **Grain of `fct_grid_interval`:** history is 30-min, tr is 15-min. Options: 30-min grain
   everywhere (aggregate tr), or 15-min grain with history only on :00/:30. We also don't know
   whether a cons-def :00 value is an instant or a 30-min average. That can be checked once we hold
   both versions of the same slot.
2. **"Pumped storage" as an energy source:** generation is a subset of `hydraulique`
   (`hydraulique_step_turbinage`); `pompage` is negative load. Unpivoting both would double count.
3. **Batteries** exist only in tr. Add a `battery` source, or leave it out of the generation fact and document that.
4. **Start capturing tr now.** Without saved tr snapshots, `mart_revisions` has nothing to compare.
