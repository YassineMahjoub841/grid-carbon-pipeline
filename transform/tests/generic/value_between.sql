{#- Fails on every row whose value is outside [min_value, max_value]. Nulls pass (use not_null for those). -#}
{% test value_between(model, column_name, min_value, max_value) %}
select {{ column_name }}
from {{ model }}
where {{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }}
{% endtest %}
