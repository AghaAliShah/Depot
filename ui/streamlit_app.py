"""
Optional web UI.

    streamlit run ui/streamlit_app.py

It calls exactly the same functions as the terminal app — app/storage_crud.py
and app/table_crud.py. Nothing about Supabase is re-implemented here, this file
is only buttons and boxes.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import altair as alt
import httpx
import pandas as pd
import streamlit as st

# Make "app" importable when Streamlit runs this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import storage_crud, table_crud  # noqa: E402
from app.config import ConfigError  # noqa: E402
from app.edge import EdgeFunctionError  # noqa: E402
from app.supabase_client import get_client, get_settings, ping  # noqa: E402

st.set_page_config(page_title="Depot", layout="wide")

# get_client() is cached process-wide (see supabase_client.py) so the CLI can
# reuse one connection pool across a batch of calls. In Streamlit that same
# cache lives for as long as the server process runs, so if the app sits idle
# for a while, the pooled HTTP/2 connection to Supabase goes stale and the
# next request dies with httpx.RemoteProtocolError("ConnectionTerminated...")
# — postgrest-py and storage3 both pin http2=True internally, and Supabase's
# edge closes idle HTTP/2 connections without warning. Since Streamlit reruns
# this whole script on every interaction anyway, clearing the cache here
# trades that one cross-rerun optimization for a connection that's always
# fresh, which is what actually avoids the crash.
get_client.cache_clear()

# ---------------------------------------------------------------------------
# Connection check — fail loudly and helpfully rather than with a stack trace.
# ---------------------------------------------------------------------------
try:
    settings = get_settings()
except ConfigError as exc:
    st.error("Configuration problem")
    st.code(str(exc))
    st.stop()

# ============================================
# Palette
# ============================================
# Every surface step below is a deliberate, larger jump in lightness than the
# first pass had (0F1115 -> 171A21 -> 1D2028 was too tight a range to read as
# distinct layers) so page / card / raised-card actually separate at a
# glance, and borders/badges use a shared *_RGB constant at higher alpha so
# they're visible instead of nearly blending into the surface behind them.
COLOR_PRIMARY = "#818CF8"        # indigo-400 — brighter, holds up on near-black
COLOR_PRIMARY_DEEP = "#6366F1"   # indigo-500 — gradient's dark stop
COLOR_PRIMARY_RGB = "129, 140, 248"
COLOR_ACCENT = "#2DD4BF"         # teal-400
COLOR_ACCENT_RGB = "45, 212, 191"
COLOR_BG = "#0A0C11"
COLOR_SURFACE = "#141824"
COLOR_SURFACE_RAISED = "#1D2333"
COLOR_SURFACE_HOVER = "#262E42"
COLOR_BORDER = "#333B52"
COLOR_BORDER_STRONG = "#4A5474"
COLOR_TEXT = "#F5F6FA"
COLOR_TEXT_SECONDARY = "#C7CBDB"
COLOR_MUTED = "#9098B3"
COLOR_DANGER = "#FB7185"
COLOR_SUCCESS = "#34D399"
COLOR_WARNING = "#F59E0B"

CATEGORY_STYLES = {
    "document": COLOR_PRIMARY,
    "image": COLOR_ACCENT,
    "data": COLOR_WARNING,
    "archive": COLOR_DANGER,
}
CATEGORY_DEFAULT = COLOR_MUTED

# ============================================
# Formatting helpers (display only — no Supabase logic here)
# ============================================
def _fmt_dt(value: str | None) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y · %H:%M")
    except ValueError:
        return str(value)


def _human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def _category_color(category: str | None) -> str:
    return CATEGORY_STYLES.get((category or "").lower(), CATEGORY_DEFAULT)


def badge(text: str, color: str) -> str:
    return (
        f'<span class="badge" style="color:{color};background:{color}29;'
        f'border:1px solid {color}70;">{text.upper()}</span>'
    )


def tag_pills(tags: list[str] | None) -> str:
    if not tags:
        return ""
    return "".join(f'<span class="tag-pill">{t}</span>' for t in tags)


def _resilient(fn, *args, **kwargs):
    """Run a Supabase read, retrying once on a dropped connection.

    postgrest-py and storage3 both pin http2=True on their internal httpx
    client (see app/supabase_client.py), and Supabase's edge occasionally
    tears down an HTTP/2 connection out from under an in-flight request —
    surfaced as httpx.RemoteProtocolError. It isn't tied to how long the
    connection has been open (a freshly built client can hit it too), so
    the only reliable fix is to retry once on a new connection.
    """
    try:
        return fn(*args, **kwargs)
    except httpx.RemoteProtocolError:
        get_client.cache_clear()
        return fn(*args, **kwargs)


# ============================================
# Cached connection status
# ============================================
@st.cache_data(ttl=45, show_spinner=False)
def _connection_status() -> tuple[bool, dict]:
    try:
        results = _resilient(ping)
    except Exception:  # noqa: BLE001
        return False, {}
    ok = all(v == "ok" for k, v in results.items() if k != "url")
    return ok, results


# ============================================
# Styling
# ============================================
st.markdown(
    f"""
    <style>
    html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif;
    }}

    ::-webkit-scrollbar {{ width: 9px; height: 9px; }}
    ::-webkit-scrollbar-track {{ background: transparent; }}
    ::-webkit-scrollbar-thumb {{ background: {COLOR_BORDER}; border-radius: 5px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: {COLOR_MUTED}; }}

    @keyframes depot-pulse {{
        0% {{ box-shadow: 0 0 0 0 rgba({COLOR_PRIMARY_RGB}, 0.5); }}
        70% {{ box-shadow: 0 0 0 9px rgba({COLOR_PRIMARY_RGB}, 0); }}
        100% {{ box-shadow: 0 0 0 0 rgba({COLOR_PRIMARY_RGB}, 0); }}
    }}
    @keyframes depot-fade-in {{
        from {{ opacity: 0; transform: translateY(5px); }}
        to {{ opacity: 1; transform: translateY(0); }}
    }}

    /* ---- App bar ---- */
    .depot-header {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        background: linear-gradient(180deg, {COLOR_SURFACE_RAISED}, {COLOR_SURFACE});
        border-radius: 1rem;
        padding: 1rem 1.4rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 8px 24px -14px rgba(0, 0, 0, 0.6);
    }}
    .depot-header-left {{ display: flex; align-items: center; gap: 0.9rem; }}
    .depot-logo {{
        width: 44px;
        height: 44px;
        flex-shrink: 0;
        border-radius: 0.8rem;
        background: linear-gradient(135deg, {COLOR_PRIMARY}, {COLOR_ACCENT});
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 4px 16px -3px rgba({COLOR_PRIMARY_RGB}, 0.65);
    }}
    .depot-title {{
        font-size: 1.4rem;
        font-weight: 800;
        letter-spacing: -0.02em;
        color: {COLOR_TEXT};
        line-height: 1.2;
    }}

    .status-pill {{
        display: inline-flex;
        align-items: center;
        gap: 0.45rem;
        padding: 0.4rem 0.85rem;
        border-radius: 999px;
        font-size: 0.8rem;
        font-weight: 600;
        white-space: nowrap;
    }}
    .status-pill.ok {{
        background: rgba(52, 211, 153, 0.12);
        color: {COLOR_SUCCESS};
        border: 1px solid rgba(52, 211, 153, 0.35);
    }}
    .status-pill.offline {{
        background: rgba(248, 113, 113, 0.12);
        color: {COLOR_DANGER};
        border: 1px solid rgba(248, 113, 113, 0.35);
    }}
    .status-dot {{ width: 7px; height: 7px; border-radius: 50%; background: currentColor; }}
    .status-pill.ok .status-dot {{ animation: depot-pulse 2s infinite; }}

    /* ---- Stat cards ---- */
    .stats-grid {{
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 0.75rem;
        margin-bottom: 1.4rem;
    }}
    .stat-card {{
        background: linear-gradient(160deg, {COLOR_SURFACE_RAISED}, {COLOR_SURFACE});
        border: 1px solid {COLOR_BORDER};
        border-top: 3px solid var(--stat-accent, {COLOR_PRIMARY});
        border-radius: 0.75rem;
        padding: 0.9rem 1rem;
        animation: depot-fade-in 0.3s ease-out;
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04), 0 6px 16px -12px rgba(0, 0, 0, 0.8);
        transition: border-color 0.15s ease, transform 0.15s ease, box-shadow 0.15s ease;
    }}
    .stat-card:hover {{
        border-color: {COLOR_BORDER_STRONG};
        transform: translateY(-2px);
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04), 0 10px 22px -12px rgba(0, 0, 0, 0.9);
    }}
    .stats-grid .stat-card:nth-child(1) {{ --stat-accent: {COLOR_PRIMARY}; }}
    .stats-grid .stat-card:nth-child(2) {{ --stat-accent: {COLOR_ACCENT}; }}
    .stats-grid .stat-card:nth-child(3) {{ --stat-accent: {COLOR_WARNING}; }}
    .stats-grid .stat-card:nth-child(4) {{ --stat-accent: {COLOR_DANGER}; }}
    .stat-label {{
        font-size: 0.65rem;
        letter-spacing: 0.07em;
        color: {COLOR_MUTED};
        text-transform: uppercase;
        font-weight: 700;
    }}
    .stat-value {{ font-size: 1.6rem; font-weight: 800; color: {COLOR_TEXT}; margin-top: 0.25rem; }}

    /* ---- Badges / pills ---- */
    .badge {{
        display: inline-block;
        font-size: 0.66rem;
        font-weight: 700;
        letter-spacing: 0.03em;
        padding: 0.15rem 0.5rem;
        border-radius: 0.35rem;
        margin-right: 0.35rem;
    }}
    .tag-pill {{
        display: inline-block;
        font-size: 0.72rem;
        font-weight: 600;
        padding: 0.14rem 0.65rem;
        border-radius: 999px;
        background: rgba({COLOR_PRIMARY_RGB}, 0.18);
        border: 1px solid rgba({COLOR_PRIMARY_RGB}, 0.45);
        color: {COLOR_TEXT};
        margin: 0 0.3rem 0.3rem 0;
    }}

    /* ---- Cards (Streamlit native bordered containers / expanders) ---- */
    [data-testid="stExpander"] {{
        border: 1px solid {COLOR_BORDER} !important;
        border-radius: 0.75rem !important;
        background: linear-gradient(160deg, {COLOR_SURFACE_RAISED}, {COLOR_SURFACE}) !important;
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
        margin-bottom: 0.6rem;
        overflow: hidden;
        transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }}
    [data-testid="stExpander"] summary {{
        font-weight: 600;
        padding: 0.7rem 0.9rem !important;
        transition: background 0.15s ease;
    }}
    [data-testid="stExpander"] summary:hover {{ background: rgba({COLOR_PRIMARY_RGB}, 0.10); }}
    [data-testid="stExpander"]:hover {{
        border-color: rgba({COLOR_PRIMARY_RGB}, 0.55) !important;
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04), 0 4px 18px -8px rgba({COLOR_PRIMARY_RGB}, 0.35);
    }}
    div[data-testid="stVerticalBlockBorderWrapper"] {{
        border-color: {COLOR_BORDER} !important;
        border-radius: 0.85rem !important;
        background: linear-gradient(160deg, {COLOR_SURFACE_RAISED}, {COLOR_SURFACE}) !important;
    }}

    /* ---- Inputs: Streamlit's default border color equals its own
       background (invisible by design), which is why every field looked
       borderless and blended into the page. Give them a real edge and a
       focus state. ---- */
    [data-testid="stTextInputRootElement"],
    [data-testid="stTextAreaRootElement"],
    [data-testid="stNumberInputRootElement"] {{
        background: {COLOR_SURFACE_RAISED} !important;
        border: 1px solid {COLOR_BORDER} !important;
        border-radius: 0.6rem !important;
        transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }}
    [data-testid="stTextInputRootElement"]:focus-within,
    [data-testid="stTextAreaRootElement"]:focus-within,
    [data-testid="stNumberInputRootElement"]:focus-within {{
        border-color: {COLOR_PRIMARY} !important;
        box-shadow: 0 0 0 3px rgba({COLOR_PRIMARY_RGB}, 0.25);
    }}
    [data-baseweb="select"] > div {{
        background: {COLOR_SURFACE_RAISED} !important;
        border-color: {COLOR_BORDER} !important;
        border-radius: 0.6rem !important;
    }}

    /* ---- Buttons: secondary buttons were nearly the same color as the
       page background with an all-but-invisible border, and primary
       buttons had no fill at all — nothing read as clickable. ---- */
    button[data-testid="stBaseButton-secondary"] {{
        background: {COLOR_SURFACE_RAISED} !important;
        border: 1px solid {COLOR_BORDER} !important;
        border-radius: 0.6rem !important;
        font-weight: 600 !important;
        transition: border-color 0.15s ease, background 0.15s ease, transform 0.1s ease;
    }}
    button[data-testid="stBaseButton-secondary"]:hover:not(:disabled) {{
        border-color: {COLOR_PRIMARY} !important;
        background: {COLOR_SURFACE_HOVER} !important;
        transform: translateY(-1px);
    }}
    /* Streamlit ships its own equal-specificity !important rule for these
       (an emotion-generated class keyed off the same "primary" kind), and
       whichever one lands later in the document wins ties — which was
       usually Streamlit's, leaving the button transparent. Doubling the
       attribute selector and adding the native `kind` attribute raises our
       specificity above that tie so ours reliably wins regardless of
       injection order. */
    button[data-testid="stBaseButton-primary"][data-testid="stBaseButton-primary"][kind="primary"],
    button[data-testid="stBaseButton-primaryFormSubmit"][data-testid="stBaseButton-primaryFormSubmit"][kind="primaryFormSubmit"] {{
        background: linear-gradient(135deg, {COLOR_PRIMARY_DEEP}, {COLOR_PRIMARY}) !important;
        border: none !important;
        border-radius: 0.6rem !important;
        color: white !important;
        font-weight: 600 !important;
        box-shadow: 0 4px 16px -3px rgba({COLOR_PRIMARY_RGB}, 0.65);
        transition: box-shadow 0.15s ease, transform 0.1s ease;
    }}
    button[data-testid="stBaseButton-primary"][data-testid="stBaseButton-primary"]:hover:not(:disabled),
    button[data-testid="stBaseButton-primaryFormSubmit"][data-testid="stBaseButton-primaryFormSubmit"]:hover:not(:disabled) {{
        box-shadow: 0 6px 22px -3px rgba({COLOR_PRIMARY_RGB}, 0.85);
        transform: translateY(-1px);
    }}
    button:disabled {{ opacity: 0.4 !important; box-shadow: none !important; transform: none !important; }}

    /* ---- File uploader ---- */
    [data-testid="stFileUploaderDropzone"] {{
        background: {COLOR_SURFACE_RAISED} !important;
        border: 1.5px dashed {COLOR_BORDER} !important;
        border-radius: 0.75rem !important;
        transition: border-color 0.15s ease;
    }}
    [data-testid="stFileUploaderDropzone"]:hover {{ border-color: {COLOR_PRIMARY} !important; }}

    /* ---- Tabs ---- */
    /* Streamlit colors the selected tab's label text with the theme's
       primaryColor by default, which read as an odd stray blue word next
       to otherwise-white text. The gradient underline below is enough of
       a selected-state indicator on its own — keep the label itself
       white/bold instead of tinted. */
    [data-testid="stTab"] {{ font-weight: 600; transition: color 0.15s ease; }}
    [data-testid="stTab"] p {{ color: {COLOR_MUTED} !important; transition: color 0.15s ease; }}
    [data-testid="stTab"][aria-selected="true"] p {{ color: {COLOR_TEXT} !important; font-weight: 700 !important; }}
    [data-baseweb="tab-highlight"] {{
        background: linear-gradient(90deg, {COLOR_PRIMARY}, {COLOR_ACCENT}) !important;
        height: 3px !important;
    }}

    /* ---- Activity rows ---- */
    .activity-row {{
        display: flex;
        align-items: center;
        gap: 0.75rem;
        padding: 0.6rem 0.2rem;
        border-bottom: 1px solid {COLOR_BORDER};
        font-size: 0.86rem;
    }}
    .activity-row:last-child {{ border-bottom: none; }}
    .activity-label {{ color: {COLOR_TEXT}; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .activity-ts {{ color: {COLOR_MUTED}; font-size: 0.76rem; white-space: nowrap; }}

    .meta-row {{ color: {COLOR_MUTED}; font-size: 0.82rem; margin: 0.5rem 0 0.7rem 0; }}
    </style>
    """,
    unsafe_allow_html=True,
)

# ============================================
# Header
# ============================================
conn_ok, _conn_report = _connection_status()
status_class = "ok" if conn_ok else "offline"
status_text = "Connected" if conn_ok else "Attention needed"

st.markdown(
    f"""
    <div class="depot-header">
        <div class="depot-header-left">
            <div class="depot-logo">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <ellipse cx="12" cy="5.2" rx="7" ry="2.4" stroke="white" stroke-width="1.5"/>
                    <path d="M5 5.2v6.8c0 1.33 3.13 2.4 7 2.4s7-1.07 7-2.4V5.2" stroke="white" stroke-width="1.5"/>
                    <path d="M5 9.4c0 1.33 3.13 2.4 7 2.4s7-1.07 7-2.4" stroke="white" stroke-width="1.5"/>
                    <circle cx="12" cy="5.2" r="1.5" fill="white">
                        <animate attributeName="cy" values="5.2;13.6;5.2" dur="2.6s" repeatCount="indefinite" />
                        <animate attributeName="opacity" values="1;0.25;1" dur="2.6s" repeatCount="indefinite" />
                    </circle>
                </svg>
            </div>
            <div>
                <div class="depot-title">Depot</div>
            </div>
        </div>
        <div class="status-pill {status_class}"><span class="status-dot"></span>{status_text}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

overview_tab, files_tab, notes_tab = st.tabs(["Overview", "Files", "Notes"])

# ===========================================================================
# OVERVIEW
# ===========================================================================
with overview_tab:
    try:
        overview_files = _resilient(storage_crud.list_files, limit=200)
    except Exception as exc:  # noqa: BLE001
        st.error(f"{type(exc).__name__}: {exc}")
        overview_files = []
    try:
        overview_notes = _resilient(table_crud.list_notes, limit=200)
    except Exception as exc:  # noqa: BLE001
        st.error(f"{type(exc).__name__}: {exc}")
        overview_notes = []

    total_bytes = sum(f.get("size_bytes") or 0 for f in overview_files)
    pinned_count = sum(1 for n in overview_notes if n.get("is_pinned"))

    st.markdown(
        f"""
        <div class="stats-grid">
            <div class="stat-card"><div class="stat-label">Files</div><div class="stat-value">{len(overview_files)}</div></div>
            <div class="stat-card"><div class="stat-label">Storage used</div><div class="stat-value">{_human_size(total_bytes)}</div></div>
            <div class="stat-card"><div class="stat-label">Notes</div><div class="stat-value">{len(overview_notes)}</div></div>
            <div class="stat-card"><div class="stat-label">Pinned notes</div><div class="stat-value">{pinned_count}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([3, 2])

    with left:
        st.subheader("Files by category")
        counts = Counter((f.get("category") or "uncategorized") for f in overview_files)
        if counts:
            df = pd.DataFrame(
                {"category": list(counts.keys()), "files": list(counts.values())}
            )
            # A plain st.bar_chart renders one flat, fully-saturated block —
            # bar colors here instead match each category's badge color
            # elsewhere in the app (Files tab), so the chart reads as part
            # of the same color language rather than an unrelated widget.
            domain = list(CATEGORY_STYLES.keys()) + ["uncategorized"]
            palette = list(CATEGORY_STYLES.values()) + [CATEGORY_DEFAULT]
            chart = (
                alt.Chart(df)
                .mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6, size=42)
                .encode(
                    x=alt.X("category:N", title=None, sort="-y", axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("files:Q", title=None, axis=alt.Axis(tickMinStep=1)),
                    color=alt.Color(
                        "category:N",
                        scale=alt.Scale(domain=domain, range=palette),
                        legend=None,
                    ),
                    tooltip=["category", "files"],
                )
                .properties(height=260)
                .configure_view(strokeWidth=0)
                .configure_axis(
                    grid=True,
                    gridColor=COLOR_BORDER,
                    gridOpacity=0.6,
                    domain=False,
                    tickColor=COLOR_BORDER,
                    labelColor=COLOR_MUTED,
                    labelFont="-apple-system, sans-serif",
                    labelFontSize=11,
                )
                .configure_mark(opacity=0.95)
            )
            st.altair_chart(chart, use_container_width=True)
        else:
            st.info("Upload a file to see the breakdown.")

    with right:
        st.subheader("Recent activity")
        activity = [
            {"type": "File", "color": COLOR_PRIMARY, "label": f["file_name"], "ts": f.get("created_at")}
            for f in overview_files[:15]
        ] + [
            {"type": "Note", "color": COLOR_ACCENT, "label": n["title"], "ts": n.get("created_at")}
            for n in overview_notes[:15]
        ]
        activity.sort(key=lambda item: item["ts"] or "", reverse=True)
        activity = activity[:8]

        if not activity:
            st.info("Nothing has happened yet.")
        else:
            rows = "".join(
                f'<div class="activity-row">{badge(item["type"], item["color"])}'
                f'<span class="activity-label">{item["label"]}</span>'
                f'<span class="activity-ts">{_fmt_dt(item["ts"])}</span></div>'
                for item in activity
            )
            st.markdown(f'<div>{rows}</div>', unsafe_allow_html=True)


# ===========================================================================
# FILES
# ===========================================================================
with files_tab:
    left, right = st.columns([1, 2])

    with left:
        with st.container(border=True):
            st.subheader("Upload")
            uploaded = st.file_uploader("Pick a file")
            tags_raw = st.text_input("Tags (comma separated)", key="file_tags")
            if st.button("Upload", type="primary", disabled=uploaded is None):
                # storage_crud works with a path on disk, so stage the upload first.
                staging = Path(st.session_state.get("_staging", ".streamlit_uploads"))
                staging.mkdir(exist_ok=True)
                temp_path = staging / uploaded.name
                temp_path.write_bytes(uploaded.getbuffer())
                try:
                    row = storage_crud.create_file(
                        temp_path,
                        tags=[t.strip() for t in tags_raw.split(",") if t.strip()],
                    )
                    st.success(f"Accepted: {row['file_name']} ({row['size_human']})")
                except EdgeFunctionError as exc:
                    st.error("The Edge Function rejected this file")
                    st.code(str(exc))
                    st.caption("The file was removed from Storage again — nothing was kept.")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"{type(exc).__name__}: {exc}")
                finally:
                    temp_path.unlink(missing_ok=True)

    with right:
        st.subheader("Files")
        search = st.text_input("Filter by name", key="file_search")
        try:
            rows = _resilient(storage_crud.list_files, search=search or None)
        except Exception as exc:  # noqa: BLE001
            st.error(f"{type(exc).__name__}: {exc}")
            rows = []

        if not rows:
            st.info("No files yet.")
        for row in rows:
            with st.expander(f"{row['file_name']}  ·  {row.get('size_human') or ''}"):
                category_color = _category_color(row.get("category"))
                validated_color = COLOR_SUCCESS if row.get("validated") else COLOR_MUTED
                st.markdown(
                    badge(row.get("category") or "uncategorized", category_color)
                    + badge("validated" if row.get("validated") else "unverified", validated_color)
                    + f'<div class="meta-row">{row.get("mime_type") or "unknown type"} '
                    f'&middot; owner {row.get("owner")} &middot; uploaded {_fmt_dt(row.get("created_at"))}</div>'
                    + tag_pills(row.get("tags")),
                    unsafe_allow_html=True,
                )

                with st.expander("Raw metadata", expanded=False):
                    st.json(row, expanded=False)

                c1, c2, c3 = st.columns(3)

                with c1:
                    if st.button("Download", key=f"dl-{row['id']}"):
                        path = storage_crud.download_file(row["id"])
                        st.success(f"Saved to {path}")
                    if st.button("Share link (1h)", key=f"link-{row['id']}"):
                        st.code(storage_crud.signed_url(row["id"], 3600))

                with c2:
                    new_tags = st.text_input(
                        "Tags", ", ".join(row.get("tags") or []), key=f"tags-{row['id']}"
                    )
                    if st.button("Save metadata", key=f"save-{row['id']}"):
                        storage_crud.update_metadata(
                            row["id"],
                            tags=[t.strip() for t in new_tags.split(",") if t.strip()],
                        )
                        st.success("Updated.")
                        st.rerun()

                with c3:
                    replacement = st.file_uploader(
                        "Replace file", key=f"rep-{row['id']}", label_visibility="collapsed"
                    )
                    if replacement is not None and st.button("Replace", key=f"repbtn-{row['id']}"):
                        staging = Path(".streamlit_uploads")
                        staging.mkdir(exist_ok=True)
                        temp_path = staging / replacement.name
                        temp_path.write_bytes(replacement.getbuffer())
                        try:
                            storage_crud.replace_file(row["id"], temp_path)
                            st.success("Replaced and re-validated.")
                            st.rerun()
                        except EdgeFunctionError as exc:
                            st.error(str(exc))
                        finally:
                            temp_path.unlink(missing_ok=True)

                    if st.button("Delete", key=f"del-{row['id']}", type="secondary"):
                        storage_crud.delete_file(row["id"])
                        st.warning("Deleted.")
                        st.rerun()


# ===========================================================================
# NOTES
# ===========================================================================
with notes_tab:
    left, right = st.columns([1, 2])

    with left:
        with st.container(border=True):
            st.subheader("New note")
            with st.form("new_note", clear_on_submit=True):
                title = st.text_input("Title")
                content = st.text_area("Content")
                note_tags = st.text_input("Tags (comma separated)")
                pinned = st.checkbox("Pin")
                if st.form_submit_button("Create", type="primary") and title:
                    table_crud.create_note(
                        title,
                        content,
                        tags=[t.strip() for t in note_tags.split(",") if t.strip()],
                        is_pinned=pinned,
                    )
                    st.success("Created.")
                    st.rerun()

    with right:
        st.subheader("Notes")
        note_search = st.text_input("Search title or content", key="note_search")
        try:
            notes = _resilient(table_crud.list_notes, search=note_search or None)
        except Exception as exc:  # noqa: BLE001
            st.error(f"{type(exc).__name__}: {exc}")
            notes = []

        if not notes:
            st.info("No notes yet.")
        for note in notes:
            label = ("[PINNED] " if note.get("is_pinned") else "") + note["title"]
            with st.expander(label):
                st.markdown(
                    f'<div class="meta-row">updated {_fmt_dt(note.get("updated_at"))}</div>'
                    + tag_pills(note.get("tags")),
                    unsafe_allow_html=True,
                )
                new_title = st.text_input("Title", note["title"], key=f"nt-{note['id']}")
                new_content = st.text_area(
                    "Content", note.get("content") or "", key=f"nc-{note['id']}"
                )
                new_pin = st.checkbox(
                    "Pinned", bool(note.get("is_pinned")), key=f"np-{note['id']}"
                )
                c1, c2 = st.columns(2)
                if c1.button("Save", key=f"ns-{note['id']}"):
                    table_crud.update_note(
                        note["id"], title=new_title, content=new_content, is_pinned=new_pin
                    )
                    st.success("Saved.")
                    st.rerun()
                if c2.button("Delete", key=f"nd-{note['id']}"):
                    table_crud.delete_note(note["id"])
                    st.warning("Deleted.")
                    st.rerun()
