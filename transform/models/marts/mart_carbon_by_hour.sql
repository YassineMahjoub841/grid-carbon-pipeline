-- Grain: one row per local hour of day (0-23) × month (1-12) × day type (weekday / weekend),
-- over the analysis period (var analysis_start_date onwards: the post-2022 grid).
-- Feeds the heatmap "CO2 by local hour × month".

select
    t.hour as local_hour,
    d.month,
    any_value(d.month_name) as month_name,
    any_value(d.season) as season,
    case when d.is_weekend then 'weekend' else 'weekday' end as day_type,
    avg(f.co2_intensity_g_per_kwh) as avg_co2_g_per_kwh,
    quantile_cont(f.co2_intensity_g_per_kwh, 0.1) as p10_co2_g_per_kwh,
    quantile_cont(f.co2_intensity_g_per_kwh, 0.9) as p90_co2_g_per_kwh,
    count(*) as n_intervals,
    min(f.date_key) as first_date,
    max(f.date_key) as last_date
from {{ ref('fct_grid_interval') }} f
join {{ ref('dim_date') }} d using (date_key)
join {{ ref('dim_time_of_day') }} t using (time_of_day_key)
where f.date_key >= date '{{ var("analysis_start_date") }}'
group by t.hour, d.month, d.is_weekend
