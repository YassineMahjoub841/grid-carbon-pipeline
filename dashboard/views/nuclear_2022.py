"""Page 2: the 2022 nuclear crisis and the recovery. Reads mart_monthly_mix_exchanges.

Every number on this page is computed from the mart, so it stays right as new months arrive.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import marts
from style import PLOTLY_CONFIG, base_layout, colors

monthly = marts.load_mart("mart_monthly_mix_exchanges")
monthly["month_start"] = pd.to_datetime(monthly["month_start"])
yearly = marts.yearly_summary(monthly)
full_years = yearly[yearly["complete"]].set_index("year")
c = colors()

st.title("2022: the nuclear crisis, and the recovery")

y2019, y2022 = full_years.loc[2019], full_years.loc[2022]
latest_year = int(full_years.index.max())
latest = full_years.loc[latest_year]
import_years = [int(y) for y in full_years.index[full_years["net_import_twh"] > 0]]
first_year = int(full_years.index.min())

st.caption(
    f"In 2022 France imported more electricity than it exported, for the only time since {first_year} "
    "(the start of this data)." if import_years == [2022] else
    f"Net-import years since {first_year}: {', '.join(map(str, import_years))}."
)

t1, t2, t3 = st.columns(3)
t1.metric(
    "Nuclear output in 2022", f"{y2022['nuclear_twh']:.0f} TWh",
    delta=f"{y2022['nuclear_twh'] / y2019['nuclear_twh'] - 1:.0%} vs 2019",
    delta_color="off", border=True,
)
t2.metric(
    "Net imports in 2022", f"{y2022['net_import_twh']:+.1f} TWh",
    help="Physical exchanges summed over the year. Positive = France imported more than it exported.",
    border=True,
)
t3.metric(
    f"Nuclear output in {latest_year}", f"{latest['nuclear_twh']:.0f} TWh",
    delta=f"net exports {-latest['net_import_twh']:.1f} TWh",
    delta_color="off", border=True,
)

# ---- Monthly chart: nuclear output on top, net exchanges below (two charts, one time axis) ----------
start = pd.Timestamp("2019-01-01")
shown = monthly[(monthly["month_start"] >= start) & monthly["is_complete_month"]].sort_values("month_start")
in_progress = monthly[~monthly["is_complete_month"]]

fig = make_subplots(
    rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.55, 0.45],
    subplot_titles=("Nuclear output (TWh per month)", "Net imports (TWh per month; below zero = net exports)"),
)
fig.add_trace(go.Scatter(
    x=shown["month_start"], y=shown["nuclear_twh"], mode="lines",
    line=dict(color=c["line"], width=2),
    hovertemplate="%{x|%b %Y}<br>Nuclear %{y:.1f} TWh<extra></extra>",
), row=1, col=1)
fig.add_trace(go.Bar(
    x=shown["month_start"], y=shown["net_import_twh"],
    marker=dict(color=[c["import"] if v > 0 else c["export"] for v in shown["net_import_twh"]], cornerradius=2),
    hovertemplate="%{x|%b %Y}<br>Net %{customdata} %{y:+.1f} TWh<extra></extra>",
    customdata=["imports" if v > 0 else "exports" for v in shown["net_import_twh"]],
), row=2, col=1)

# Shade 2022, and the months that are still real-time (may be revised by RTE).
fig.add_vrect(x0="2022-01-01", x1="2023-01-01", fillcolor=c["band"], opacity=1, layer="below", line_width=0)
fig.add_annotation(x="2022-07-01", y=1.0, yref="paper", text="<b>2022</b>", showarrow=False,
                   font=dict(color=c["ink"]), yshift=-28)
realtime = shown[shown["least_final_status"] == "real_time"]
if not realtime.empty:
    x0 = realtime["month_start"].min()
    fig.add_vrect(x0=x0, x1=shown["month_start"].max() + pd.offsets.MonthBegin(1),
                  fillcolor=c["band"], opacity=1, layer="below", line_width=0)
    fig.add_annotation(x=x0, y=1.0, yref="paper", xanchor="left", text="real-time,<br>may be revised",
                       showarrow=False, font=dict(color=c["muted"], size=11), yshift=-28)

lowest = shown.loc[shown["nuclear_twh"].idxmin()]
fig.add_annotation(
    x=lowest["month_start"], y=lowest["nuclear_twh"], row=1, col=1,
    text=f"{lowest['month_start']:%b %Y}: {lowest['nuclear_twh']:.1f} TWh", showarrow=True, arrowhead=0,
    arrowcolor=c["muted"], ax=0, ay=36, font=dict(color=c["ink"], size=12),
)
fig.add_hline(y=0, line=dict(color=c["axis"], width=1), row=2, col=1)
base_layout(fig, height=560)
for title in fig.layout.annotations[:2]:  # the two subplot titles: same style
    title.font = dict(color=c["ink_2"], size=14)
fig.update_yaxes(rangemode="tozero", row=1, col=1)
fig.update_xaxes(dtick="M12", tickformat="%Y")
st.plotly_chart(fig, config=PLOTLY_CONFIG)

notes = []
if not in_progress.empty:
    notes.append(f"{in_progress['month_start'].max():%B %Y} is still in progress and isn't shown.")
st.caption(
    "Red bars: France imported more than it exported that month; blue: it exported more. " + " ".join(notes)
)

st.markdown(
    "**Context** (from public reporting, not from this data): in 2022 many reactors were offline at the "
    "same time, for stress-corrosion inspections and repairs found from late 2021, on top of maintenance "
    "delayed by the pandemic. The data above shows the effect: output fell, and France had to import."
)

# ---- Year by year -------------------------------------------------------------------------------
with st.expander("Year by year"):
    table = yearly.assign(year=yearly["year"].astype(str))
    st.dataframe(
        table[["year", "nuclear_twh", "production_twh", "net_import_twh", "avg_co2_g_per_kwh", "complete"]],
        hide_index=True,
        column_config={
            "year": "Year",
            "nuclear_twh": st.column_config.NumberColumn("Nuclear (TWh)", format="%.1f"),
            "production_twh": st.column_config.NumberColumn("Total production (TWh)", format="%.1f"),
            "net_import_twh": st.column_config.NumberColumn("Net imports (TWh)", format="%+.1f"),
            "avg_co2_g_per_kwh": st.column_config.NumberColumn("Avg CO2 (g/kWh)", format="%.1f"),
            "complete": st.column_config.CheckboxColumn("Full year"),
        },
    )
    st.caption("Total production = sum of the 8 sources. Net imports = physical exchanges; negative = net exports.")
