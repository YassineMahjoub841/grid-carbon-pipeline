-- The DST test. A local (Paris) day must have:
--   46 slots on the spring-forward day (last Sunday of March: 23 hours),
--   48 slots on every other day, INCLUDING the fall-back day: it lasts 25 hours, but RTE publishes the
--   repeated hour only once (DATA_NOTES §6), so 48 is what correct handling of this data looks like.
-- Known upstream gaps lower the expected count. The last day in the data is skipped: it is still
-- being published.

with per_day as (
    select date_key, count(*) as slots
    from {{ ref('fct_grid_interval') }}
    group by date_key
),

known_gaps_per_day as (
    select cast(timezone('Europe/Paris', missing_interval_start_utc) as date) as date_key, count(*) as gaps
    from {{ ref('known_data_gaps') }}
    group by 1
),

expected as (
    select
        p.date_key,
        p.slots,
        case
            when month(p.date_key) = 3 and isodow(p.date_key) = 7 and day(p.date_key) >= 25 then 46
            else 48
        end - coalesce(k.gaps, 0) as expected_slots
    from per_day p
    left join known_gaps_per_day k using (date_key)
)

select *
from expected
where slots <> expected_slots
  and date_key < (select max(date_key) from per_day)
