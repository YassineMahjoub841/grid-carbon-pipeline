-- Grain: one row per energy source (the 8 top-level eCO2mix production columns).

select
    energy_source_key,
    energy_source_name,
    is_renewable,
    is_low_carbon,
    is_dispatchable,
    sort_order
from {{ ref('energy_sources') }}
