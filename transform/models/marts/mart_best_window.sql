-- Grain: one row per season × job duration (1, 2, 4, 8 h) × start hour (00:00 ... 23:00 local).
-- For each, the average CO2 intensity a job starting then would see, its rank, and how much
-- cleaner it is than the worst start for the same season and duration.
--
-- Method (DECISIONS D15): build the average CO2 profile of each season by local 30-min slot over the
-- analysis period, then average that profile over the window's slots. Windows that run past midnight
-- wrap around to the start of the same profile.

with profile as (
    select
        d.season,
        t.slot_index,
        avg(f.co2_intensity_g_per_kwh) as avg_co2
    from {{ ref('fct_grid_interval') }} f
    join {{ ref('dim_date') }} d using (date_key)
    join {{ ref('dim_time_of_day') }} t using (time_of_day_key)
    where f.date_key >= date '{{ var("analysis_start_date") }}'
    group by d.season, t.slot_index
),

durations as (
    select unnest([1, 2, 4, 8]) as duration_hours
),

starts as (
    select distinct season, slot_index as start_slot
    from profile
    where slot_index % 2 = 0                 -- jobs start on the hour
),

windows as (
    select
        s.season,
        w.duration_hours,
        s.start_slot,
        -- `order by` inside the aggregate: float addition is not associative and DuckDB aggregates in
        -- parallel, so without a fixed order two runs can differ in the last digit (DECISIONS D17).
        avg(p.avg_co2 order by p.slot_index) as window_avg_co2
    from starts s
    cross join durations w
    join profile p
      on p.season = s.season
     and (p.slot_index - s.start_slot + 48) % 48 < w.duration_hours * 2
    group by s.season, w.duration_hours, s.start_slot
)

select
    season,
    duration_hours,
    lpad(cast(start_slot // 2 as varchar), 2, '0') || ':00' as start_time_local,
    start_slot // 2 as start_hour,
    window_avg_co2 as avg_co2_g_per_kwh,
    row_number() over (partition by season, duration_hours order by window_avg_co2, start_slot) as rank_cleanest,
    row_number() over (partition by season, duration_hours order by window_avg_co2, start_slot) = 1 as is_best,
    row_number() over (partition by season, duration_hours order by window_avg_co2 desc, start_slot) = 1 as is_worst,
    1 - window_avg_co2 / max(window_avg_co2) over (partition by season, duration_hours) as pct_cleaner_than_worst
from windows
