"""
Training performance dashboard — pre-test vs post-test.

Data sources supported (pick one in the sidebar):
  1. Google service account  - works with private sheets (recommended)
  2. Published CSV export    - works if the sheet is shared "anyone with the link"
  3. File upload             - CSV / XLSX exported from the sheet

Run:  streamlit run app.py
"""

from __future__ import annotations

import io
import re
import unicodedata
from difflib import get_close_matches

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

SHEET_ID = "1sKbhgKtrznvuOxtFI6Bum6rmIvnz7y34LYvU6WIAe5Y"
PRE_GID = "295127114"           # Form Responses 1
POST_GID = "1831648219"         # Form Responses 2

PRE_TAB = "Form Responses 1"
POST_TAB = "Form Responses 2"

# Column guesses tuned to this specific form export — still overridable in the sidebar.
ID_COL_GUESS = "Enter your Unique ID in the format TB-x00x"
SCORE_COL_PRIORITY = ("corrected score", "score")   # checked in this order
CLASS_COL_GUESS = "class"

REFRESH_SECONDS = 300  # how long a live fetch is cached before re-querying Google

INK = "#17222B"
PRE_C = "#8FA3B0"
POST_C = "#12726B"
UP_C = "#12726B"
DOWN_C = "#B0443C"
FLAT_C = "#B9C2C8"

st.set_page_config(page_title="Training performance", page_icon="📈", layout="wide")

st.markdown(
    """
    <style>
      .block-container {padding-top: 2.2rem; max-width: 1400px;}
      [data-testid="stMetricValue"] {font-size: 1.9rem;}
      h1 {letter-spacing: -0.02em;}
      /* Hide Streamlit Community Cloud's hosted-app toolbar (Share, star, edit, GitHub, menu) */
      [data-testid="stToolbar"] {visibility: hidden; height: 0; position: fixed;}
      [data-testid="stDecoration"] {visibility: hidden; height: 0;}
      #MainMenu {visibility: hidden;}
      /* Hide the default footer and the floating "Hosted with Streamlit" badge */
      footer {visibility: hidden; height: 0;}
      [data-testid="stBottomBlockContainer"] {visibility: hidden; height: 0;}
      a[href*="streamlit.io"], a[href*="github.com/streamlit"] {display: none !important;}
      .viewerBadge_container__1QSob, .viewerBadge_link__1S137 {display: none !important;}
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _csv_export_url(sheet_id: str, gid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def load_public_csv(sheet_id: str, gid: str) -> pd.DataFrame:
    return pd.read_csv(_csv_export_url(sheet_id, gid))


# --------------------------------------------------------------------------
# Cleaning helpers
# --------------------------------------------------------------------------

def normalise_key(value) -> str:
    """Make a join key that survives casing, spacing and punctuation drift.

    Two regimes:
      - Looks like a short participant code (e.g. "TB-011", "*110#", "011") ->
        key on the digits alone, so "TB-011", "011" and "11" all collapse to "11".
      - Otherwise (a name or email) -> word-normalised text key.
    """
    if pd.isna(value):
        return ""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().strip()
    if not text:
        return ""
    if "@" in text.lower():
        return text.lower()
    digits = re.sub(r"\D", "", text)
    letters = re.sub(r"[^A-Za-z]", "", text)
    if digits and len(letters) <= 3:   # short alnum code such as TB-011 / 011 / *110#
        return str(int(digits))
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    parts = sorted(p for p in cleaned.split() if p)   # sorted: handles surname-first entries
    return " ".join(parts)


SCORE_PAIR = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(?:/|out of)\s*([0-9]*\.?[0-9]+)\s*$", re.I)


def parse_score(value) -> tuple[float, float]:
    """Return (score, max). Handles 8, '8', '8 / 10', '80%'."""
    if pd.isna(value):
        return np.nan, np.nan
    text = str(value).strip()
    if not text:
        return np.nan, np.nan
    match = SCORE_PAIR.match(text)
    if match:
        return float(match.group(1)), float(match.group(2))
    if text.endswith("%"):
        try:
            return float(text[:-1]), 100.0
        except ValueError:
            return np.nan, np.nan
    try:
        return float(re.sub(r"[^0-9.\-]", "", text)), np.nan
    except ValueError:
        return np.nan, np.nan


def score_series(frame: pd.DataFrame, column: str) -> tuple[pd.Series, float | None]:
    parsed = frame[column].map(parse_score)
    scores = parsed.map(lambda t: t[0])
    denominators = parsed.map(lambda t: t[1]).dropna().unique()
    detected = float(denominators[0]) if len(denominators) == 1 else None
    return scores.astype(float), detected


def guess(columns: list[str], wanted: tuple[str, ...], fallback: int = 0) -> str:
    for want in wanted:
        for col in columns:
            if want in col.lower():
                return col
    return columns[fallback] if columns else ""


def guess_score_col(columns: list[str]) -> str:
    """Prefer 'Corrected score' over a raw 'Score' column when both exist —
    Google Forms' auto-grade is often 0/blank while the corrected column holds
    the real mark."""
    for want in SCORE_COL_PRIORITY:
        for col in columns:
            if want in col.lower():
                return col
    return guess(columns, ("score", "total", "mark", "result", "points"), -1)


def guess_id_col(columns: list[str]) -> str:
    for col in columns:
        if col.strip().lower() == ID_COL_GUESS.lower():
            return col
    return guess(columns, ("unique id", "id", "email", "name"), 0)


def cohens_dz(diff: pd.Series) -> float:
    sd = diff.std(ddof=1)
    return float(diff.mean() / sd) if sd and not np.isnan(sd) else np.nan


# --------------------------------------------------------------------------
# Sidebar — source
# --------------------------------------------------------------------------

st.sidebar.header("Data")
st.sidebar.button("↻ Refresh now", on_click=st.cache_data.clear,
                  help=f"Data is cached for {REFRESH_SECONDS // 60} min between refreshes")

try:
    pre_raw = load_public_csv(SHEET_ID, PRE_GID)
    post_raw = load_public_csv(SHEET_ID, POST_GID)
    error = None
except Exception as exc:  # noqa: BLE001
    pre_raw = post_raw = None
    error = f"Couldn't read the data: {exc}"

st.title("Training performance")
st.caption("Pre-test and post-test scores, matched participant by participant.")

if error:
    st.info(error)
    st.stop()
if pre_raw is None or post_raw is None or pre_raw.empty or post_raw.empty:
    st.warning("One of the two response tabs came back empty.")
    st.stop()

pre_raw = pre_raw.rename(columns=lambda c: str(c).strip())
post_raw = post_raw.rename(columns=lambda c: str(c).strip())

# --------------------------------------------------------------------------
# Sidebar — column mapping
# --------------------------------------------------------------------------

st.sidebar.header("Columns")

pre_cols, post_cols = list(pre_raw.columns), list(post_raw.columns)
id_words = ("email", "e-mail", "name", "participant", "phone", "id")
score_words = ("score", "total", "mark", "result", "points")

with st.sidebar.expander("Column mapping", expanded=False):
    st.caption("Auto-detected from the sheet — only change these if the form is edited.")
    pre_id = st.selectbox("Pre-test identifier", pre_cols,
                          index=pre_cols.index(guess_id_col(pre_cols)))
    pre_score_col = st.selectbox("Pre-test score", pre_cols,
                                 index=pre_cols.index(guess_score_col(pre_cols)))
    post_id = st.selectbox("Post-test identifier", post_cols,
                           index=post_cols.index(guess_id_col(post_cols)))
    post_score_col = st.selectbox("Post-test score", post_cols,
                                  index=post_cols.index(guess_score_col(post_cols)))
    extra_cols = st.multiselect(
        "Breakdown fields (from the pre-test tab)",
        [c for c in pre_cols if c not in {pre_id, pre_score_col}],
        default=[c for c in pre_cols if CLASS_COL_GUESS in c.lower()],
        help="Class, county, facility, cadre — whatever you want to slice by.",
    )

fuzzy = st.sidebar.checkbox("Match near-identical names", value=True,
                            help="Catches typos when the same person types their name twice.")
keep = st.sidebar.selectbox("If someone submitted twice", ["Keep first", "Keep last", "Keep best"])

# --------------------------------------------------------------------------
# Build the matched dataset
# --------------------------------------------------------------------------

pre = pre_raw.copy()
post = post_raw.copy()
pre["_score"], pre_max_detected = score_series(pre, pre_score_col)
post["_score"], post_max_detected = score_series(post, post_score_col)

observed_max = float(np.nanmax([pre["_score"].max(), post["_score"].max()]) or 1.0)
detected = pre_max_detected or post_max_detected or observed_max
max_score = st.sidebar.number_input(
    "Maximum possible score", min_value=1.0, value=float(detected), step=1.0,
    help="Auto-set from the data's own 'x / y' format if present, otherwise from the highest score seen.",
)
as_percent = st.sidebar.checkbox("Show scores as %", value=bool(pre_max_detected or post_max_detected))
pass_mark = st.sidebar.slider("Pass mark (%)", 0, 100, 80, step=5)

pre["_key"] = pre[pre_id].map(normalise_key)
post["_key"] = post[post_id].map(normalise_key)
pre = pre[(pre["_key"] != "") & pre["_score"].notna()]
post = post[(post["_key"] != "") & post["_score"].notna()]


def dedupe(frame: pd.DataFrame) -> pd.DataFrame:
    if keep == "Keep best":
        frame = frame.sort_values("_score", ascending=False)
        return frame.drop_duplicates("_key")
    return frame.drop_duplicates("_key", keep="first" if keep == "Keep first" else "last")


pre, post = dedupe(pre), dedupe(post)

if fuzzy:
    pre_keys = set(pre["_key"])
    remap = {}
    for key in post["_key"]:
        if key not in pre_keys:
            close = get_close_matches(key, pre_keys, n=1, cutoff=0.9)
            if close:
                remap[key] = close[0]
    post["_key"] = post["_key"].replace(remap)
    post = dedupe(post)

matched = pre.merge(
    post[["_key", "_score", post_id]].rename(columns={"_score": "post", post_id: "_post_label"}),
    on="_key", how="left",
).rename(columns={"_score": "pre"})

if matched.empty:
    st.error("No usable pre-test rows found — check the identifier and score columns.")
    st.stop()

if as_percent:
    matched["pre"] = matched["pre"] / max_score * 100
    matched["post"] = matched["post"] / max_score * 100
    unit, ceiling = "%", 100.0
else:
    unit, ceiling = "", max_score

matched["gain"] = matched["post"] - matched["pre"]
matched["pre_pct"] = matched["pre"] / ceiling * 100
matched["post_pct"] = matched["post"] / ceiling * 100

# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------

view = matched
if extra_cols:
    st.sidebar.header("Filters")
    for col in extra_cols:
        options = sorted(view[col].dropna().astype(str).unique())
        if 1 < len(options) <= 60:
            chosen = st.sidebar.multiselect(col, options, default=options)
            view = view[view[col].astype(str).isin(chosen)]

if view.empty:
    st.warning("No participants left after filtering.")
    st.stop()

# --------------------------------------------------------------------------
# Headline numbers
# --------------------------------------------------------------------------

fmt = lambda v: f"{v:.1f}{unit}"  # noqa: E731

paired = view.dropna(subset=["post"])
pre_only_n = len(view) - len(paired)

if paired.empty:
    # No post-test data anywhere in this filtered selection (e.g. a class that
    # hasn't sat the post-test yet) — show what we do have: the pre-test.
    st.info("No post-test scores yet for this selection — showing pre-test results only.")

    c1, c2, c3 = st.columns(3)
    c1.metric("Participants (pre-test)", f"{len(view):,}")
    c2.metric("Mean pre-test", fmt(view["pre"].mean()))
    c3.metric(f"Passing at {pass_mark}%", f"{(view['pre_pct'] >= pass_mark).mean() * 100:.0f}%")

    st.subheader("Pre-test score distribution")
    bins = dict(start=0, end=ceiling * 1.0001, size=max(ceiling / 20, 1))
    dist = go.Figure()
    dist.add_trace(go.Histogram(x=view["pre"], marker_color=PRE_C, xbins=bins))
    dist.add_vline(x=pass_mark / 100 * ceiling, line_dash="dot", line_color=INK,
                   annotation_text="Pass mark", annotation_position="top")
    dist.update_layout(height=380, margin=dict(t=10, b=10, l=10, r=10),
                       xaxis_title=f"Score {unit}".strip(), yaxis_title="Participants",
                       plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(dist, use_container_width=True)

    st.subheader("Participants")
    table = (view[[pre_id] + extra_cols + ["pre"]]
             .rename(columns={pre_id: "Participant", "pre": "Pre-test"})
             .sort_values("Pre-test", ascending=False)
             .round(1))
    st.dataframe(table, use_container_width=True, height=430)
    buffer = io.StringIO()
    table.to_csv(buffer, index=False)
    st.download_button("Download this table (CSV)", buffer.getvalue(),
                       file_name="pre_test_results.csv", mime="text/csv")
    st.stop()

if pre_only_n:
    st.caption(
        f"{pre_only_n} of {len(view):,} participants shown have a pre-test score only "
        "(no post-test yet) — they're included in the participant table below but not "
        "in the pre/post comparisons above it."
    )

full_view = view   # keep the full filtered set (incl. pre-only rows) for the table below
view = paired      # headline metrics and charts below need both scores
improved = (view["gain"] > 0).mean() * 100
pre_pass = (view["pre_pct"] >= pass_mark).mean() * 100
post_pass = (view["post_pct"] >= pass_mark).mean() * 100

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Matched participants", f"{len(view):,}",
          help=f"Pre-test {len(pre):,} · post-test {len(post):,} submissions")
c2.metric("Mean pre-test", fmt(view["pre"].mean()))
c3.metric("Mean post-test", fmt(view["post"].mean()), delta=fmt(view["gain"].mean()))
c4.metric(f"Passing at {pass_mark}%", f"{post_pass:.0f}%",
          delta=f"{post_pass - pre_pass:+.0f} pts vs pre")
c5.metric("Improved", f"{improved:.0f}%",
          help="Share whose post-test score is higher than their pre-test score")

stat_line = f"Mean gain {view['gain'].mean():+.1f}{unit} (SD {view['gain'].std(ddof=1):.1f}) · effect size dz = {cohens_dz(view['gain']):.2f}"
try:
    from scipy import stats

    t_stat, p_val = stats.ttest_rel(view["post"], view["pre"])
    stat_line += f" · paired t = {t_stat:.2f}, p {'< 0.001' if p_val < 0.001 else f'= {p_val:.3f}'}"
except Exception:  # noqa: BLE001
    pass
st.caption(stat_line)

st.divider()

# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------

left, right = st.columns([1, 1])

with left:
    st.subheader("Where scores moved")
    slope = go.Figure()
    sample = view.sort_values("gain")
    if len(sample) > 150:
        sample = sample.sample(150, random_state=1)
    for _, row in sample.iterrows():
        colour = UP_C if row["gain"] > 0 else DOWN_C if row["gain"] < 0 else FLAT_C
        slope.add_trace(go.Scatter(
            x=["Pre-test", "Post-test"], y=[row["pre"], row["post"]],
            mode="lines+markers", line=dict(color=colour, width=1),
            marker=dict(size=5, color=colour), opacity=0.5, showlegend=False,
            hovertemplate=f"{row[pre_id]}<br>%{{x}}: %{{y:.1f}}{unit}<extra></extra>",
        ))
    slope.add_trace(go.Scatter(
        x=["Pre-test", "Post-test"], y=[view["pre"].mean(), view["post"].mean()],
        mode="lines+markers+text", line=dict(color=INK, width=4),
        marker=dict(size=11, color=INK), name="Mean",
        text=[fmt(view["pre"].mean()), fmt(view["post"].mean())],
        textposition="middle right", textfont=dict(color=INK, size=13),
    ))
    slope.update_layout(height=420, margin=dict(t=10, b=10, l=10, r=40),
                        yaxis_title=f"Score {unit}".strip(), showlegend=False,
                        plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(slope, use_container_width=True)
    if len(view) > 150:
        st.caption("150 participants shown; the heavy line is the mean of everyone in view.")

with right:
    st.subheader("Score distribution")
    bins = dict(start=0, end=ceiling * 1.0001, size=max(ceiling / 20, 1))
    dist = go.Figure()
    dist.add_trace(go.Histogram(x=view["pre"], name="Pre-test", marker_color=PRE_C,
                                xbins=bins, opacity=0.75))
    dist.add_trace(go.Histogram(x=view["post"], name="Post-test", marker_color=POST_C,
                                xbins=bins, opacity=0.75))
    dist.add_vline(x=pass_mark / 100 * ceiling, line_dash="dot", line_color=INK,
                   annotation_text="Pass mark", annotation_position="top")
    dist.update_layout(barmode="overlay", height=420, margin=dict(t=10, b=10, l=10, r=10),
                       xaxis_title=f"Score {unit}".strip(), yaxis_title="Participants",
                       plot_bgcolor="rgba(0,0,0,0)",
                       legend=dict(orientation="h", y=1.02, x=0))
    st.plotly_chart(dist, use_container_width=True)

st.subheader("Individual movement")
scatter = go.Figure()
scatter.add_trace(go.Scatter(
    x=view["pre"], y=view["post"], mode="markers",
    marker=dict(size=9, color=view["gain"], colorscale=[[0, DOWN_C], [0.5, FLAT_C], [1, UP_C]],
                cmid=0, line=dict(width=0.5, color="white")),
    text=view[pre_id].astype(str),
    hovertemplate="%{text}<br>Pre %{x:.1f}<br>Post %{y:.1f}<extra></extra>",
))
scatter.add_trace(go.Scatter(x=[0, ceiling], y=[0, ceiling], mode="lines",
                             line=dict(color=INK, dash="dash", width=1),
                             hoverinfo="skip", showlegend=False))
scatter.update_layout(height=430, margin=dict(t=10, b=10, l=10, r=10),
                      xaxis_title=f"Pre-test {unit}".strip(),
                      yaxis_title=f"Post-test {unit}".strip(),
                      plot_bgcolor="rgba(0,0,0,0)", showlegend=False)
st.plotly_chart(scatter, use_container_width=True)
st.caption("Points above the dashed line improved. Below it, scores dropped.")

if extra_cols:
    st.subheader("Gain by group")
    by = st.selectbox("Group by", extra_cols, label_visibility="collapsed")
    grouped = (view.groupby(view[by].astype(str))
               .agg(n=("gain", "size"), pre=("pre", "mean"),
                    post=("post", "mean"), gain=("gain", "mean"))
               .sort_values("gain"))
    grouped = grouped[grouped["n"] >= 2]
    if grouped.empty:
        st.caption("Not enough participants per group to compare.")
    else:
        bar = go.Figure()
        bar.add_trace(go.Bar(y=grouped.index, x=grouped["pre"], name="Pre-test",
                             orientation="h", marker_color=PRE_C))
        bar.add_trace(go.Bar(y=grouped.index, x=grouped["post"], name="Post-test",
                             orientation="h", marker_color=POST_C))
        bar.update_layout(barmode="group", height=max(320, 42 * len(grouped)),
                          margin=dict(t=10, b=10, l=10, r=10),
                          xaxis_title=f"Mean score {unit}".strip(),
                          plot_bgcolor="rgba(0,0,0,0)",
                          legend=dict(orientation="h", y=1.04, x=0))
        st.plotly_chart(bar, use_container_width=True)
        st.dataframe(
            grouped.rename(columns={"n": "Participants", "pre": "Mean pre",
                                    "post": "Mean post", "gain": "Mean gain"}).round(1),
            use_container_width=True,
        )

# --------------------------------------------------------------------------
# Participant table
# --------------------------------------------------------------------------

st.subheader("Participants")
only_dropped = st.checkbox("Show only those who did not improve")
table = full_view[full_view["gain"] <= 0] if only_dropped else full_view
columns = [pre_id] + extra_cols + ["pre", "post", "gain"]
table = (table[columns]
         .rename(columns={pre_id: "Participant", "pre": "Pre-test",
                          "post": "Post-test", "gain": "Gain"})
         .sort_values("Gain", ascending=False, na_position="last")
         .round(1))
st.dataframe(table, use_container_width=True, height=430)

buffer = io.StringIO()
table.to_csv(buffer, index=False)
st.download_button("Download this table (CSV)", buffer.getvalue(),
                   file_name="pre_post_results.csv", mime="text/csv")

post_only = post.loc[~post["_key"].isin(pre["_key"]), [post_id, post_score_col]]
if not post_only.empty:
    with st.expander(f"Post-test submissions with no matching pre-test ({len(post_only)})"):
        st.dataframe(post_only, use_container_width=True)
