"""Chart colours and the shared Plotly layout, for light and dark mode.

Colours have a job, not a look: one highlight colour for "the answer", a single-hue blue scale for
"how much CO2" (darker = more), and a two-sided pair for "import vs export". Everything else stays grey.
Dark mode uses its own steps of the same colours, chosen to stay readable on a dark background.
"""

import plotly.graph_objects as go
import streamlit as st

PALETTE = {
    "light": {
        "ink": "#0b0b0b", "ink_2": "#52514e", "muted": "#898781",
        "grid": "#e1e0d9", "axis": "#c3c2b7", "band": "#f0efec",
        "highlight": "#2a78d6",    # the best window
        "contrast": "#eb6834",     # the worst window
        "neutral_bar": "#c3c2b7",  # every other window
        "import": "#e34948", "export": "#2a78d6",
        "line": "#2a78d6",
        # sequential blue, light (little CO2) -> dark (more CO2)
        "co2_scale": ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
        "co2_scale_reads": "Darker",
    },
    "dark": {
        "ink": "#f0efec", "ink_2": "#c3c2b7", "muted": "#898781",
        "grid": "#2c2c2a", "axis": "#383835", "band": "#2c2c2a",
        "highlight": "#3987e5",
        "contrast": "#d95926",
        "neutral_bar": "#4a4a46",
        "import": "#e66767", "export": "#3987e5",
        "line": "#3987e5",
        # on a dark background "little" recedes into the dark surface, "more" is brighter
        "co2_scale": ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"],
        "co2_scale_reads": "Brighter",
    },
}


def colors() -> dict:
    mode = "light"
    try:
        if st.context.theme.type == "dark":
            mode = "dark"
    except Exception:  # no browser session (tests, first render): light is the default
        pass
    return PALETTE[mode]


def base_layout(fig: go.Figure, height: int = 380) -> go.Figure:
    """Thin, recessive axes and grid; tooltips on hover; no Plotly toolbar clutter."""
    c = colors()
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=24, b=8),
        font=dict(color=c["ink_2"], size=13),
        hoverlabel=dict(font_size=13),
        showlegend=False,
        bargap=0.15,
    )
    fig.update_xaxes(showgrid=False, linecolor=c["axis"], tickfont_color=c["muted"], title_font_color=c["ink_2"])
    fig.update_yaxes(gridcolor=c["grid"], zeroline=False, tickfont_color=c["muted"], title_font_color=c["ink_2"])
    return fig


PLOTLY_CONFIG = {"displayModeBar": False}
