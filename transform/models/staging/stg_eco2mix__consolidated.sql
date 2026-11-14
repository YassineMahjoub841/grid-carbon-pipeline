-- Grain: one row per 30-minute interval (interval_start_utc) per extraction (_extracted_at)
--        of eco2mix-national-cons-def. Every downloaded version is kept; int_eco2mix__national picks one.
--
-- cons-def has rows every 15 min, but actuals only at :00 and :30 (the :15/:45 rows are empty,
-- DATA_NOTES §4), so filtering on non-null consumption leaves a clean 30-min series.

with source as (
    select * from {{ source('eco2mix', 'national_cons_def') }}
),

renamed as (
    select
        date_heure as interval_start_utc,
        {{ eco2mix_data_status('nature') }} as data_status,
        {{ eco2mix_measures() }},
        cast(null as integer) as battery_net_mw,  -- batteries are only published in real-time data
        _source_dataset as source_dataset,
        _extracted_at as extracted_at,
        date as local_date_text,
        heure as local_time_text
    from source
)

select * exclude (local_date_text, local_time_text)
from renamed
where consumption_mw is not null
  and {{ is_real_local_time('interval_start_utc', 'local_date_text', 'local_time_text') }}
