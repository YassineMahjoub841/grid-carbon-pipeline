-- Grain: one row per 30-minute interval × extraction (source_dataset, extracted_at).
-- Every version of every slot we ever downloaded. Used by int_eco2mix__national (to pick the best
-- version) and by mart_revisions (to compare versions).

with unioned as (
    select * from {{ ref('stg_eco2mix__consolidated') }}
    union all by name
    select * from {{ ref('stg_eco2mix__realtime') }}
)

select
    cast(interval_start_utc as varchar) || ' | ' || source_dataset || ' | ' || cast(extracted_at as varchar) as version_key,
    *,
    {{ data_status_rank('data_status') }} as data_status_rank
from unioned
