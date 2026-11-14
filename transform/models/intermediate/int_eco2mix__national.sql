-- Grain: one row per 30-minute interval (interval_start_utc): the best available version.
--
-- Dedup rule (CLAUDE.md "Incremental strategy"): definitive > consolidated > real-time, then the
-- latest extraction. Rerunning on the same raw files always picks the same row: the ordering is total
-- because (data_status, extracted_at) identifies one extraction of one slot.
-- Also adds the Europe/Paris local time the core models join on.

with ranked as (
    select
        *,
        row_number() over (
            partition by interval_start_utc
            order by data_status_rank desc, extracted_at desc, source_dataset
        ) as version_rank
    from {{ ref('int_eco2mix__slot_versions') }}
)

select
    interval_start_utc,
    timezone('Europe/Paris', interval_start_utc) as interval_start_local,
    cast(timezone('Europe/Paris', interval_start_utc) as date) as local_date,
    strftime(timezone('Europe/Paris', interval_start_utc), '%H:%M') as local_time_of_day,
    data_status,
    consumption_mw,
    co2_intensity_g_per_kwh,
    nuclear_mw,
    hydro_mw,
    wind_mw,
    solar_mw,
    bioenergy_mw,
    gas_mw,
    coal_mw,
    oil_mw,
    pumping_mw,
    net_import_mw,
    battery_net_mw,
    source_dataset,
    extracted_at
from ranked
where version_rank = 1
