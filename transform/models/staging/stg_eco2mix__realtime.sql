-- Grain: one row per 30-minute interval (interval_start_utc) per extraction (_extracted_at)
--        of eco2mix-national-tr. Every daily snapshot is kept; int_eco2mix__national picks one.
--
-- tr is published every 15 min. The history (cons-def) is 30-min, so we average the two
-- quarter-hours of each half hour (MW are average power over the interval, so the mean of two
-- 15-min means is the 30-min mean). A half hour with only one quarter published yet (the latest
-- one) is dropped; the next day's snapshot completes it. See DECISIONS D12.

with source as (
    select * from {{ source('eco2mix', 'national_tr') }}
),

renamed as (
    select
        date_heure as quarter_start_utc,
        {{ eco2mix_data_status('nature') }} as data_status,
        {{ eco2mix_measures() }},
        try_cast(stockage_batterie as integer) + try_cast(destockage_batterie as integer) as battery_net_mw,
        _source_dataset as source_dataset,
        _extracted_at as extracted_at,
        date as local_date_text,
        heure as local_time_text
    from source
),

quarter_hours as (
    select * exclude (local_date_text, local_time_text)
    from renamed
    where consumption_mw is not null  -- tr also contains tomorrow's forecast-only rows
      and {{ is_real_local_time('quarter_start_utc', 'local_date_text', 'local_time_text') }}
)

select
    time_bucket(interval 30 minute, quarter_start_utc) as interval_start_utc,
    data_status,
    avg(consumption_mw) as consumption_mw,
    avg(co2_intensity_g_per_kwh) as co2_intensity_g_per_kwh,
    avg(nuclear_mw) as nuclear_mw,
    avg(hydro_mw) as hydro_mw,
    avg(wind_mw) as wind_mw,
    avg(solar_mw) as solar_mw,
    avg(bioenergy_mw) as bioenergy_mw,
    avg(gas_mw) as gas_mw,
    avg(coal_mw) as coal_mw,
    avg(oil_mw) as oil_mw,
    avg(pumping_mw) as pumping_mw,
    avg(net_import_mw) as net_import_mw,
    avg(battery_net_mw) as battery_net_mw,
    source_dataset,
    extracted_at
from quarter_hours
group by all
having count(*) = 2
