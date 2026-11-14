-- Grain: one row per local month × metric × comparison (from_status -> to_status).
-- How much a slot's value changed between a less final and a more final version
-- (real_time -> consolidated, real_time -> definitive, consolidated -> definitive).
--
-- For each slot and status, the LATEST extraction of that status is used (the most refined
-- real-time value, just before RTE replaced it). Only slots for which we downloaded both versions
-- appear, so this mart starts empty and fills up as RTE consolidates months we captured in real time.

with latest_per_status as (
    select *
    from {{ ref('int_eco2mix__slot_versions') }}
    qualify row_number() over (partition by interval_start_utc, data_status order by extracted_at desc) = 1
),

long as (
    unpivot (
        select
            interval_start_utc,
            data_status,
            data_status_rank,
            cast(consumption_mw as double) as consumption_mw,
            cast(co2_intensity_g_per_kwh as double) as co2_intensity_g_per_kwh,
            cast(nuclear_mw as double) as nuclear_mw,
            cast(wind_mw as double) as wind_mw,
            cast(solar_mw as double) as solar_mw,
            cast(hydro_mw as double) as hydro_mw,
            cast(gas_mw as double) as gas_mw,
            cast(net_import_mw as double) as net_import_mw
        from latest_per_status
    )
    on consumption_mw, co2_intensity_g_per_kwh, nuclear_mw, wind_mw, solar_mw, hydro_mw, gas_mw, net_import_mw
    into name metric value value
),

pairs as (
    select
        a.interval_start_utc,
        a.metric,
        a.data_status as from_status,
        b.data_status as to_status,
        a.value as from_value,
        b.value as to_value,
        b.value - a.value as diff
    from long a
    join long b
      on a.interval_start_utc = b.interval_start_utc
     and a.metric = b.metric
     and b.data_status_rank > a.data_status_rank
)

select
    cast(date_trunc('month', timezone('Europe/Paris', interval_start_utc)) as date) as month_start,
    metric,
    from_status,
    to_status,
    count(*) as n_intervals,
    avg(from_value) as avg_from_value,
    avg(to_value) as avg_to_value,
    avg(diff) as mean_diff,                                         -- bias: > 0 means revised upwards
    avg(abs(diff)) as mean_abs_diff,
    max(abs(diff)) as max_abs_diff,
    -- Fixed order: these ratios are arbitrary doubles, so summation order matters (DECISIONS D17).
    -- The other averages sum integers or half-integers, which doubles add exactly in any order.
    avg(abs(diff) / nullif(abs(to_value), 0) order by interval_start_utc) as mean_abs_pct_diff
from pairs
group by all
