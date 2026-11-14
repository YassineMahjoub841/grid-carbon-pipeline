{#- mart_best_window: exactly one best and one worst start per season × duration, and 24 starts each. -#}
{% test assert_one_best_per_group(model) %}
select season, duration_hours, count(*) as n_starts,
       count(*) filter (where is_best) as n_best,
       count(*) filter (where is_worst) as n_worst
from {{ model }}
group by season, duration_hours
having count(*) <> 24
    or count(*) filter (where is_best) <> 1
    or count(*) filter (where is_worst) <> 1
{% endtest %}
