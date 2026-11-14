-- Grain: one row per local 30-minute slot of the day (48 rows), keyed 'HH:MM'.

with slots as (
    select unnest(generate_series(0, 47)) as slot_index
)

select
    lpad(cast(slot_index // 2 as varchar), 2, '0') || ':' || case when slot_index % 2 = 0 then '00' else '30' end
        as time_of_day_key,
    slot_index,
    slot_index // 2 as hour,
    (slot_index % 2) * 30 as minute,
    -- Off-peak = 22:00-06:00, the most common French "heures creuses" window. Real contracts vary
    -- by area, so this is a simplification (DECISIONS D14).
    (slot_index // 2 >= 22 or slot_index // 2 < 6) as is_off_peak,
    case
        when slot_index // 2 < 6 then 'night'
        when slot_index // 2 < 12 then 'morning'
        when slot_index // 2 < 18 then 'afternoon'
        else 'evening'
    end as day_part
from slots
