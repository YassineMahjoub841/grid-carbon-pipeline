-- Grain: one row per local (Europe/Paris) calendar date, fixed range from dbt vars.

with days as (
    select cast(unnest(generate_series(
        date '{{ var("dim_date_start") }}',
        date '{{ var("dim_date_end") }}',
        interval 1 day
    )) as date) as date_day
)

select
    date_day as date_key,
    year(date_day) as year,
    quarter(date_day) as quarter,
    month(date_day) as month,
    monthname(date_day) as month_name,
    strftime(date_day, '%Y-%m') as year_month,
    isodow(date_day) as day_of_week,          -- 1 = Monday ... 7 = Sunday
    dayname(date_day) as day_name,
    isodow(date_day) in (6, 7) as is_weekend,
    case
        when month(date_day) in (12, 1, 2) then 'winter'
        when month(date_day) in (3, 4, 5) then 'spring'
        when month(date_day) in (6, 7, 8) then 'summer'
        else 'autumn'
    end as season                              -- meteorological seasons
from days
