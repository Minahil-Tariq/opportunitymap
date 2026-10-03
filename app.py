"""OpportunityMap - turn a client discovery call into a ranked automation opportunity map."""
import os
from pathlib import Path

import pandas as pd
import streamlit as st

import engine

st.set_page_config(page_title="OpportunityMap", page_icon="🗺️", layout="wide")

DEMO_USER, DEMO_PASS = "demo", "demo123"      # dummy credentials (shown on the login page on purpose)
SAMPLES = sorted(Path(__file__).parent.joinpath("sample_transcripts").glob("*.txt"))
EDIT_COLS = ["Task", "Service", "Owner", "Hours/week", "Automatable %", "Assumed"]


def get_api_key():
    try:
        key = st.secrets.get("GROQ_API_KEY")
    except Exception:
        key = None
    return key or os.environ.get("GROQ_API_KEY")


def sample_label(p: Path) -> str:
    return p.stem.split("_", 1)[1].replace("_", " ").title()


# ------------------------------------------------------------------ login
if "auth" not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    st.title("🗺️ OpportunityMap")
    st.caption("Turn a client discovery call into a ranked, evidence-backed automation plan.")
    with st.container(border=True):
        st.subheader("Sign in")
        st.info(f"Demo credentials  →  username: **{DEMO_USER}**   password: **{DEMO_PASS}**")
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.button("Sign in", type="primary"):
            if u == DEMO_USER and p == DEMO_PASS:
                st.session_state.auth = True
                st.rerun()
            else:
                st.error("Wrong username or password.")
    st.stop()

# ------------------------------------------------------------------ sidebar
api_key = get_api_key()
with st.sidebar:
    st.header("Settings")
    hourly = st.number_input("Staff cost per hour ($)", min_value=1.0, value=25.0, step=1.0)
    use_ai = False
    if api_key:
        use_ai = st.toggle("Use AI extraction (Llama 3 via Groq)", value=True)
    else:
        st.info("Running in **demo mode** (rule-based extraction). Add a `GROQ_API_KEY` secret to switch on AI mode.")
    st.divider()
    if st.button("Sign out"):
        st.session_state.clear()
        st.rerun()

# ------------------------------------------------------------------ main
st.title("🗺️ OpportunityMap")
st.caption("Paste or upload a discovery-call transcript. The AI extracts the facts; plain code calculates the ROI; "
           "you can edit every assumption.")

source = st.radio("Transcript source", ["Sample call", "Paste text", "Upload .txt"], horizontal=True)
transcript = ""
if source == "Sample call":
    choice = st.selectbox("Choose a sample discovery call", SAMPLES, format_func=sample_label)
    transcript = choice.read_text(encoding="utf-8")
    st.text_area("Transcript", transcript, height=220, disabled=True)
elif source == "Paste text":
    transcript = st.text_area("Transcript (use 'Engineer:' and 'Client:' labels)", height=260)
else:
    up = st.file_uploader("Upload a .txt transcript", type=["txt"])
    if up:
        transcript = up.read().decode("utf-8", errors="ignore")
        st.text_area("Transcript", transcript, height=220, disabled=True)

if st.button("Generate opportunity map", type="primary", disabled=not transcript.strip()):
    with st.spinner("Analyzing the call..."):
        st.session_state.analysis = engine.analyze(transcript, use_ai=use_ai, api_key=api_key)
        st.session_state.run_id = st.session_state.get("run_id", 0) + 1

analysis = st.session_state.get("analysis")
if analysis:
    for note in analysis.notes:
        st.warning(note)
    if not analysis.opportunities:
        st.warning("No manual, repetitive tasks were found in this transcript. Try a longer or more detailed call.")
        st.stop()

    base = engine.to_dataframe(analysis)
    tab_map, tab_edit, tab_evidence = st.tabs(["📊 Opportunity map", "✏️ Edit assumptions", "💬 Evidence"])

    with tab_edit:
        st.write("Change the hours or the automatable share and the map updates instantly. "
                 "Rows marked **Assumed** had no time stated on the call, so confirm them with the client.")
        edited = st.data_editor(
            base[EDIT_COLS], hide_index=True,
            key=f"editor_{st.session_state.get('run_id', 0)}",
            disabled=["Task", "Service", "Owner", "Assumed"],
            column_config={
                "Hours/week": st.column_config.NumberColumn(min_value=0.0, max_value=168.0, step=0.5),
                "Automatable %": st.column_config.NumberColumn(min_value=0, max_value=100, step=5),
            })

    full = base.copy()
    full["Hours/week"] = edited["Hours/week"]
    full["Automatable %"] = edited["Automatable %"]
    results = engine.compute(full, hourly)

    with tab_map:
        st.markdown(f"**Client summary:** {analysis.client_summary}")
        st.caption(f"Extraction: {analysis.mode}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Estimated savings / year", f"${int(results['Annual savings ($)'].sum()):,}")
        c2.metric("Hours saved / year", f"{int(results['Hours saved/yr'].sum()):,}")
        c3.metric("Top priority", results.loc[0, "Service"])
        show = results[["Priority", "Task", "Service", "Dafinitiq offering", "Hours/week",
                        "Hours saved/yr", "Annual savings ($)", "Effort", "Assumed"]]
        st.dataframe(show, hide_index=True)
        st.bar_chart(results.set_index("Service")["Annual savings ($)"])

        md = engine.report_markdown(analysis.client_summary, results, hourly, analysis.mode)
        d1, d2 = st.columns(2)
        d1.download_button("⬇️ Download report (Markdown)", md, "opportunity_map.md", "text/markdown")
        d2.download_button("⬇️ Download table (CSV)", results.drop(columns=["Evidence"]).to_csv(index=False),
                           "opportunity_map.csv", "text/csv")

    with tab_evidence:
        st.write("Every opportunity is backed by the client's own words.")
        for _, r in results.iterrows():
            with st.expander(f"{r['Priority']}. {r['Task']}  ·  {r['Service']}"):
                st.markdown(f"> {r['Evidence']}")
                st.caption(f"Owner: {r['Owner']}  ·  Time cost: "
                           f"{'assumed default' if r['Assumed'] else 'stated by client'}  ·  "
                           f"Quote check: {'✅ found in transcript' if r['Quote verified'] else '⚠️ not found verbatim'}")
