-- Grain: one row per 30-minute interval of the French grid (interval_start_utc).
-- Grid-level measures only; production by source lives in fct_generation_interval.

select
    interval_start_utc,
    interval_start_utc + interval 30 minute as interval_end_utc,
    interval_start_local,
    local_date as date_key,
    local_time_of_day as time_of_day_key,
    consumption_mw,
    co2_intensity_g_per_kwh,
    nuclear_mw + hydro_mw + wind_mw + solar_mw + bioenergy_mw + gas_mw + coal_mw + oil_mw
        as total_production_mw,
    net_import_mw,                 -- > 0 France imports, < 0 France exports
    pumping_mw,                    -- <= 0
    battery_net_mw,                -- null before real-time data (not published in cons-def)
    data_status,
    source_dataset,
    extracted_at
from {{ ref('int_eco2mix__national') }}
