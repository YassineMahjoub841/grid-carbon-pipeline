{#- Helpers shared by the two staging models, so both datasets get exactly the same treatment. -#}

{% macro eco2mix_data_status(nature_column) -%}
    case {{ nature_column }}
        when 'Données temps réel' then 'real_time'
        when 'Données consolidées' then 'consolidated'
        when 'Données définitives' then 'definitive'
        else 'unknown: ' || {{ nature_column }}  /* fails the accepted_values test instead of disappearing */
    end
{%- endmacro %}


{% macro eco2mix_measures() -%}
    {#- French source columns -> English, MW. try_cast because some arrive as text (e.g. 'ND'). -#}
    try_cast(consommation as integer)  as consumption_mw,
    try_cast(taux_co2 as integer)      as co2_intensity_g_per_kwh,
    try_cast(nucleaire as integer)     as nuclear_mw,
    try_cast(hydraulique as integer)   as hydro_mw,
    try_cast(eolien as integer)        as wind_mw,
    try_cast(solaire as integer)       as solar_mw,
    try_cast(bioenergies as integer)   as bioenergy_mw,
    try_cast(gaz as integer)           as gas_mw,
    try_cast(charbon as integer)       as coal_mw,
    try_cast(fioul as integer)         as oil_mw,
    try_cast(pompage as integer)       as pumping_mw,        /* <= 0: power used to pump water uphill */
    try_cast(ech_physiques as integer) as net_import_mw      /* > 0 import, < 0 export */
{%- endmacro %}


{% macro is_real_local_time(utc_column, date_text_column, time_text_column) -%}
    {#- False only for the DST "phantom" rows: on the spring-forward day RTE publishes 02:00-02:45
        local, a time that does not exist, on the same UTC instants as the real 03:00-03:45 rows. -#}
    strftime(timezone('Europe/Paris', {{ utc_column }}), '%Y-%m-%d %H:%M')
        = {{ date_text_column }} || ' ' || {{ time_text_column }}
{%- endmacro %}


{% macro data_status_rank(status_column) -%}
    case {{ status_column }} when 'definitive' then 3 when 'consolidated' then 2 when 'real_time' then 1 else 0 end
{%- endmacro %}
