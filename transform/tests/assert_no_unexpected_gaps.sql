-- Every 30-min slot between the first and the last one must exist, except:
--   1. the DST fall-back hour: on the last Sunday of October, RTE publishes the repeated 02:00-03:00
--      local hour only once, so UTC 00:00 and 00:30 that day never exist (DATA_NOTES §6);
--   2. slots listed in the known_data_gaps seed (checked to be upstream gaps).
-- Returns the missing slots that are not explained.

with slots as (
    select interval_start_utc, lead(interval_start_utc) over (order by interval_start_utc) as next_start
    from {{ ref('fct_grid_interval') }}
),

missing as (
    select unnest(generate_series(
        interval_start_utc + interval 30 minute,
        next_start - interval 30 minute,
        interval 30 minute
    )) as missing_interval_start_utc
    from slots
    where next_start - interval_start_utc > interval 30 minute
)

select m.missing_interval_start_utc
from missing m
left join {{ ref('known_data_gaps') }} k using (missing_interval_start_utc)
where k.missing_interval_start_utc is null
  and not (
        month(m.missing_interval_start_utc) = 10
    and isodow(m.missing_interval_start_utc) = 7
    and day(m.missing_interval_start_utc) >= 25        -- last Sunday of October
    and hour(m.missing_interval_start_utc) = 0         -- UTC, session TimeZone is UTC
  )
