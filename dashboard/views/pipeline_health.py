"""Page 3: is the pipeline healthy? Reads pipeline_health.json and mart_revisions.

pipeline_health.json is written by publish/export_marts.py, and the daily job only commits it after every
dbt test passed (DECISIONS D22). So the run it describes is, by construction, the last successful run.
"""

from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import marts
from style import PLOTLY_CONFIG, base_layout, colors

health = marts.load_health()
revisions = marts.load_mart("mart_revisions")
c = colors()

STATUS_LABEL = {"real_time": "Real-time", "consolidated": "Consolidated", "definitive": "Definitive"}
FRESHNESS_ICON = {"pass": "✅ fresh", "warn": "⚠️ late", "error": "❌ stale"}

st.title("Pipeline health")

# ---- Last successful run ------------------------------------------------------------------------
generated = datetime.fromisoformat(health["generated_at_utc"])
age_h = (datetime.now(timezone.utc) - generated).total_seconds() / 3600
counts = marts.dbt_counts(health)

t1, t2, t3 = st.columns(3)
t1.metric(
    "Last successful run (UTC)", f"{generated.day} {generated:%b, %H:%M}",
    delta=f"{age_h:.0f} h ago" if age_h < 48 else f"{age_h / 24:.0f} days ago",
    delta_color="off" if age_h < 30 else "inverse", delta_arrow="off", border=True,
    help="The daily job runs at 12:17 UTC. Over 30 h means at least one run was missed or failed.",
)
t2.metric(
    "dbt tests passed", f"{counts['tests_passed']} / {counts['tests_passed'] + counts['tests_not_passed']}",
    border=True, help="Data tests run by `dbt build`: uniqueness, not-null, accepted values, relationships, "
    "CO2 range, energy balance, no unexpected gaps, DST slots per day.",
)
t3.metric("Models built", counts["models_built"], border=True)

if health.get("run_url"):
    st.markdown(f"[Open this run in GitHub Actions]({health['run_url']})")
else:
    st.caption("This export was made by a local run, not by GitHub Actions.")
for problem in health.get("dbt_build", {}).get("problems", []):
    st.warning(f"{problem}")
if age_h >= 30:
    st.warning("No successful run in the last 30 hours. Check the Actions tab: the figures below are from the "
               "last run that passed every test.", icon="⚠️")

# ---- Freshness ----------------------------------------------------------------------------------
st.subheader("Data freshness")
fresh = pd.DataFrame(health.get("source_freshness", {}).get("sources", []))
if fresh.empty:
    st.info("No freshness results in this export.")
else:
    fresh = fresh.assign(
        status=fresh["status"].map(FRESHNESS_ICON).fillna(fresh["status"]),
        max_loaded_at=pd.to_datetime(fresh["max_loaded_at"], format="ISO8601").dt.strftime("%d %b %Y %H:%M UTC"),
        age_days=fresh["age_hours"] / 24,
    )
    st.dataframe(
        fresh[["source", "status", "max_loaded_at", "age_days"]], hide_index=True,
        column_config={
            "source": "Source", "status": "Status", "max_loaded_at": "Latest data",
            "age_days": st.column_config.NumberColumn("Age (days)", format="%.1f"),
        },
    )
    st.caption(
        "Real-time (`national_tr`): checked on extraction time; warns after 30 h, fails after 72 h. "
        "Consolidated/definitive (`national_cons_def`): checked on the newest timestamp; RTE consolidates "
        "with a lag of a few months, so it warns only after 100 days and fails after 150."
    )

# ---- What the warehouse holds -------------------------------------------------------------------
st.subheader("What the data is made of")
by_status = pd.DataFrame(health.get("data", {}).get("intervals_by_status", []))
if not by_status.empty:
    by_status = by_status.assign(
        data_status=by_status["data_status"].map(STATUS_LABEL).fillna(by_status["data_status"]),
        # Shown in Paris time: "1 Jan 2012" reads better than the UTC "31 Dec 2011 23:00".
        first_interval_utc=pd.to_datetime(by_status["first_interval_utc"], format="ISO8601")
                             .dt.tz_convert("Europe/Paris").dt.strftime("%d %b %Y"),
        last_interval_utc=pd.to_datetime(by_status["last_interval_utc"], format="ISO8601")
                            .dt.tz_convert("Europe/Paris").dt.strftime("%d %b %Y"),
    )
    st.dataframe(
        by_status, hide_index=True,
        column_config={
            "data_status": "Status",
            "n_intervals": st.column_config.NumberColumn("30-min intervals", format="localized"),
            "first_interval_utc": "From (Paris)", "last_interval_utc": "To (Paris)",
        },
    )
    st.caption("Each interval keeps its most final version: definitive > consolidated > real-time.")

col_rows, col_extract = st.columns(2)
with col_rows:
    rows = pd.DataFrame(health.get("data", {}).get("row_counts", []))
    if not rows.empty:
        st.markdown("**Rows per table**")
        st.dataframe(rows, hide_index=True, column_config={
            "table_name": "Table", "n": st.column_config.NumberColumn("Rows", format="localized")})
with col_extract:
    extractions = pd.DataFrame(health.get("data", {}).get("latest_extraction_by_dataset", []))
    if not extractions.empty:
        extractions["latest_extracted_at"] = (
            pd.to_datetime(extractions["latest_extracted_at"], format="ISO8601").dt.strftime("%d %b %Y %H:%M UTC"))
        st.markdown("**Downloads from the API**")
        st.dataframe(extractions, hide_index=True, column_config={
            "source_dataset": "Dataset", "latest_extracted_at": "Latest download",
            "n_extractions": st.column_config.NumberColumn("Downloads kept", format="%d")})

# ---- Revisions ----------------------------------------------------------------------------------
st.subheader("How much RTE revises its real-time data")
if revisions.empty:
    spans = {r["data_status"]: r for r in health.get("data", {}).get("intervals_by_status", [])}
    detail = ""
    if "real_time" in spans and "consolidated" in spans:
        rt_from = pd.to_datetime(spans["real_time"]["first_interval_utc"], format="ISO8601").tz_convert("Europe/Paris")
        cons_to = pd.to_datetime(spans["consolidated"]["last_interval_utc"], format="ISO8601").tz_convert("Europe/Paris")
        detail = (f" Real-time capture starts on {rt_from:%d %B %Y}, and the consolidated data currently "
                  f"stops on {cons_to:%d %B %Y}, so no half-hour has both versions yet.")
    st.info(
        "Nothing to compare yet. A revision is measured when the same half-hour exists in two versions: the "
        "real-time value this pipeline captured, and the consolidated value RTE publishes later." + detail +
        " Comparisons will appear here as soon as RTE consolidates a month that was captured in real time."
    )
else:
    rt = revisions[revisions["from_status"] == "real_time"].copy()
    rt["month_start"] = pd.to_datetime(rt["month_start"])
    latest_month = rt["month_start"].max()
    latest = rt[rt["month_start"] == latest_month].sort_values("mean_abs_pct_diff")
    latest = latest.assign(label=latest["metric"] + " → " + latest["to_status"])
    st.markdown(
        f"Mean absolute change between the real-time value and the more final one, per half-hour, "
        f"**{latest_month:%B %Y}** (real-time → {', '.join(sorted(latest['to_status'].unique()))})."
    )
    fig = go.Figure(go.Bar(
        x=latest["mean_abs_pct_diff"], y=latest["label"], orientation="h",
        marker=dict(color=c["line"], cornerradius=4),
        customdata=latest[["mean_abs_diff", "max_abs_diff", "n_intervals"]].values,
        hovertemplate="%{y}<br>Mean change %{x:.2%}<br>Mean |diff| %{customdata[0]:.1f}"
                      "<br>Max |diff| %{customdata[1]:.1f}<br>%{customdata[2]} half-hours<extra></extra>",
    ))
    base_layout(fig, height=60 + 36 * len(latest))
    fig.update_xaxes(tickformat=".1%", title="Mean absolute change (% of the final value)", showgrid=True,
                     gridcolor=c["grid"], rangemode="tozero")
    fig.update_yaxes(showgrid=False)
    st.plotly_chart(fig, config=PLOTLY_CONFIG)
    with st.expander("All months and metrics"):
        st.dataframe(revisions, hide_index=True)
