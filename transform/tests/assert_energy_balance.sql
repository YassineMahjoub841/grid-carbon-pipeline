-- Consumption ≈ production + pumping + net imports (+ batteries).
--
-- This replaces the spec's "sum of generation by source ≈ total production": eCO2mix has no
-- total-production column to compare against (DATA_NOTES §3). The balance is the physical check
-- that does exist.
-- Tolerance (DECISIONS D16):
--   consolidated / definitive: 2% of consumption. The residual grows from 2021 (likely battery
--     charging, not published in cons-def); worst seen 1.5%.
--   real_time: 8%. Usually tighter than consolidated (p99 0.18%), but RTE's first estimates have
--     rare spikes (worst seen 5.1%, Sept 2026). Real pipeline bugs (a missing source, a wrong sign)
--     show up as tens of percent, so 8% still catches them.

with balance as (
    select
        interval_start_utc,
        data_status,
        consumption_mw,
        total_production_mw,
        pumping_mw,
        net_import_mw,
        battery_net_mw,
        consumption_mw - (total_production_mw + pumping_mw + net_import_mw + coalesce(battery_net_mw, 0)) as residual_mw
    from {{ ref('fct_grid_interval') }}
)

select *
from balance
where abs(residual_mw) > case when data_status = 'real_time' then 0.08 else 0.02 end * consumption_mw
