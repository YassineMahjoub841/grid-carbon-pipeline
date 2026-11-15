"""Entry point: `uv run --group dashboard streamlit run dashboard/app.py`.

Three pages, each a plain script in dashboard/views/. They read only data/marts/ (see marts.py).
The folder is not called pages/: Streamlit would treat that name as its older automatic page list.
"""

import streamlit as st

st.set_page_config(page_title="When is French electricity cleanest?", page_icon=":material/bolt:", layout="wide")

pages = [
    st.Page("views/best_time.py", title="Best time to run a job", icon=":material/schedule:", default=True),
    st.Page("views/nuclear_2022.py", title="2022: the nuclear crisis", icon=":material/factory:"),
    st.Page("views/pipeline_health.py", title="Pipeline health", icon=":material/monitor_heart:"),
]
page = st.navigation(pages)

st.sidebar.caption(
    "Data: [RTE éCO2mix](https://odre.opendatasoft.com/explore/dataset/eco2mix-national-cons-def/) "
    "via ODRÉ, Licence Ouverte v2.0. CO2 intensity is RTE's own estimate."
)
page.run()
