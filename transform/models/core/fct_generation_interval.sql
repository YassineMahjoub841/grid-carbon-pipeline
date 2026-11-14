-- Grain: one row per 30-minute interval × energy source (8 sources per interval).
-- The wide source columns of eCO2mix, unpivoted so "generation by source" is a simple group by.

with wide as (
    select
        interval_start_utc,
        local_date as date_key,
        local_time_of_day as time_of_day_key,
        data_status,
        nuclear_mw as nuclear,
        hydro_mw as hydro,
        wind_mw as wind,
        solar_mw as solar,
        bioenergy_mw as bioenergy,
        gas_mw as gas,
        coal_mw as coal,
        oil_mw as oil
    from {{ ref('int_eco2mix__national') }}
),

long as (
    unpivot wide
    on nuclear, hydro, wind, solar, bioenergy, gas, coal, oil
    into name energy_source_key value generation_mw
)

select
    cast(interval_start_utc as varchar) || ' | ' || energy_source_key as generation_interval_key,
    interval_start_utc,
    date_key,
    time_of_day_key,
    energy_source_key,
    generation_mw,
    data_status
from long
