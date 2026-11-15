"""Page 1: when should a flexible job run? Reads mart_best_window and mart_carbon_by_hour."""

from datetime import date

import plotly.graph_objects as go
import streamlit as st

import marts
from style import PLOTLY_CONFIG, base_layout, colors

best_window = marts.load_mart("mart_best_window")
by_hour = marts.load_mart("mart_carbon_by_hour")
c = colors()

first_day, last_day = by_hour["first_date"].min(), by_hour["last_date"].max()

st.title("When is French electricity cleanest?")
st.caption(
    f"Average CO2 intensity of French electricity, {first_day.day} {first_day:%b %Y} → "
    f"{last_day.day} {last_day:%b %Y}, "
    "by local time in Paris. Pick how long your job runs and when, and get the best time to start it."
)

# ---- Controls -----------------------------------------------------------------------------------
col_duration, col_season = st.columns(2)
with col_duration:
    duration = st.segmented_control(
        "Job duration", [1, 2, 4, 8], default=4, required=True, format_func=lambda h: f"{h} h",
    )
with col_season:
    season = st.segmented_control(
        "Season", marts.SEASONS, default=marts.SEASON_OF_MONTH[date.today().month], required=True,
        format_func=str.capitalize,
    )

windows = best_window[(best_window["season"] == season) & (best_window["duration_hours"] == duration)]
windows = windows.sort_values("start_hour")
best = windows[windows["is_best"]].iloc[0]
worst = windows[windows["is_worst"]].iloc[0]

# ---- The answer ---------------------------------------------------------------------------------
t1, t2, t3 = st.columns(3)
t1.metric("Best time to start", marts.window_label(int(best["start_hour"]), duration), border=True)
t2.metric("Average CO2 during that window", f"{best['avg_co2_g_per_kwh']:.1f} g/kWh", border=True)
t3.metric(
    "Compared with the worst window",
    f"{best['pct_cleaner_than_worst']:.0%} cleaner",
    help="(worst - best) / worst, on the average CO2 intensity of the two windows.",
    border=True,
)
st.markdown(
    f"The worst {duration} h window in {season} starts at **{worst['start_time_local']}** "
    f"({marts.window_label(int(worst['start_hour']), duration)}, {worst['avg_co2_g_per_kwh']:.1f} g/kWh)."
)

# ---- Every start time, ranked -------------------------------------------------------------------
st.subheader(f"Average CO2 of a {duration} h job, by start time ({season})")
bar_colors = [
    c["highlight"] if b else c["contrast"] if w else c["neutral_bar"]
    for b, w in zip(windows["is_best"], windows["is_worst"])
]
fig = go.Figure(go.Bar(
    x=windows["start_time_local"],
    y=windows["avg_co2_g_per_kwh"],
    marker=dict(color=bar_colors, cornerradius=4),
    customdata=[[marts.window_label(int(h), duration), r] for h, r in zip(windows["start_hour"], windows["rank_cleanest"])],
    hovertemplate="Start %{x}  (%{customdata[0]})<br>%{y:.1f} g/kWh<br>Rank %{customdata[1]} of 24<extra></extra>",
))
for row, text in ((best, "Best"), (worst, "Worst")):
    fig.add_annotation(
        x=row["start_time_local"], y=row["avg_co2_g_per_kwh"], text=f"{text}<br>{row['avg_co2_g_per_kwh']:.1f}",
        showarrow=False, yshift=18, font=dict(color=c["ink"], size=12),
    )
base_layout(fig, height=340)
fig.update_yaxes(title="g CO2 / kWh", rangemode="tozero")
fig.update_xaxes(title="Start time (Paris)", tickangle=0, dtick=2)
st.plotly_chart(fig, config=PLOTLY_CONFIG)

# ---- Heatmap ------------------------------------------------------------------------------------
st.subheader("CO2 intensity by hour and month")
day_type_label = st.segmented_control(
    "Days", ["All days", "Weekdays", "Weekends"], default="All days", required=True, key="days",
)
day_type = {"All days": None, "Weekdays": "weekday", "Weekends": "weekend"}[day_type_label]
matrix = marts.heatmap_matrix(by_hour, day_type)
months = [marts.MONTH_ABBR[m - 1] for m in matrix.index]

heat = go.Figure(go.Heatmap(
    z=matrix.values,
    x=[f"{h:02d}:00" for h in matrix.columns],
    y=months,
    colorscale=[[i / (len(c["co2_scale"]) - 1), col] for i, col in enumerate(c["co2_scale"])],
    xgap=2, ygap=2,
    colorbar=dict(title=dict(text="g/kWh", side="top"), thickness=12, outlinewidth=0),
    hovertemplate="%{y}, %{x}<br>%{z:.1f} g/kWh<extra></extra>",
))
base_layout(heat, height=420)
heat.update_yaxes(autorange="reversed", showgrid=False)
heat.update_xaxes(title="Hour of day (Paris)", dtick=2)
st.plotly_chart(heat, config=PLOTLY_CONFIG)
st.caption(
    f"{c['co2_scale_reads']} = more CO2 per kWh. The midday dip is solar output; the winter-evening peak is when gas output "
    "peaks too (about 5.2 GW at 19:00 vs 3.4 GW at night, winters 2023+). Seasons: winter = Dec-Feb, spring = Mar-May, summer = Jun-Aug, autumn = Sep-Nov."
)

with st.expander("How this is calculated, and the data behind the charts"):
    st.markdown(
        "- **Period:** 2023 onwards only. Earlier years include coal-heavy years and the 2022 nuclear crisis, "
        "which describe a grid that no longer exists.\n"
        "- **Method:** average CO2 intensity per season and 30-minute slot of the day, then a job's window is the "
        "average of its slots. Starts are on the hour; windows past midnight wrap around.\n"
        "- **CO2 intensity** is RTE's own estimate (`taux_co2`) for electricity produced in France.\n"
        "- The most recent months are real-time data and can still be revised by RTE (see *Pipeline health*)."
    )
    table = windows[["start_time_local", "avg_co2_g_per_kwh", "rank_cleanest", "pct_cleaner_than_worst"]]
    st.dataframe(
        table, hide_index=True,
        column_config={
            "start_time_local": "Start (Paris)",
            "avg_co2_g_per_kwh": st.column_config.NumberColumn("Avg CO2 (g/kWh)", format="%.1f"),
            "rank_cleanest": "Rank (1 = cleanest)",
            "pct_cleaner_than_worst": st.column_config.NumberColumn("Cleaner than worst", format="percent"),
        },
    )
