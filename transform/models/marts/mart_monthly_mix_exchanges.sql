-- Grain: one row per local calendar month.
-- Energy by source and net imports, in TWh (MW × 0.5 h per 30-min interval / 1e6). The 2022 story.

with generation as (
    select
        cast(date_trunc('month', date_key) as date) as month_start,
        energy_source_key,
        sum(generation_mw) * 0.5 / 1e6 as twh
    from {{ ref('fct_generation_interval') }}
    group by all
),

generation_wide as (
    pivot generation
    on energy_source_key in ('nuclear', 'hydro', 'wind', 'solar', 'bioenergy', 'gas', 'coal', 'oil')
    using sum(twh)
    group by month_start
),

grid as (
    select
        cast(date_trunc('month', date_key) as date) as month_start,
        sum(consumption_mw) * 0.5 / 1e6 as consumption_twh,
        sum(total_production_mw) * 0.5 / 1e6 as production_twh,
        sum(net_import_mw) * 0.5 / 1e6 as net_import_twh,
        -- CO2 intensity is per kWh produced, so weight each interval by its production.
        sum(co2_intensity_g_per_kwh * total_production_mw) / sum(total_production_mw) as avg_co2_g_per_kwh,
        count(*) as n_intervals,
        max(data_status_rank) as best_status_rank,
        min(data_status_rank) as worst_status_rank
    from (
        select *, {{ data_status_rank('data_status') }} as data_status_rank
        from {{ ref('fct_grid_interval') }}
    )
    group by all
)

select
    g.month_start,
    year(g.month_start) as year,
    month(g.month_start) as month,
    g.consumption_twh,
    g.production_twh,
    g.net_import_twh,
    g.net_import_twh > 0 as is_net_importer,
    w.nuclear as nuclear_twh,
    w.hydro as hydro_twh,
    w.wind as wind_twh,
    w.solar as solar_twh,
    w.bioenergy as bioenergy_twh,
    w.gas as gas_twh,
    w.coal as coal_twh,
    w.oil as oil_twh,
    g.avg_co2_g_per_kwh,
    g.n_intervals,
    -- 48 slots a day, minus 2 on the spring-forward day: lets the dashboard flag partial months.
    g.n_intervals >= 48 * day(last_day(g.month_start)) - 2 as is_complete_month,
    case g.worst_status_rank when 3 then 'definitive' when 2 then 'consolidated' else 'real_time' end
        as least_final_status
from grid g
join generation_wide w using (month_start)
