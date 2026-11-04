"""
Canadian Tire Retail AI Copilot - Streamlit UI
==============================================
Ask questions in plain English; a tool-calling LLM answers them by querying a
locked-down, in-memory DuckDB database. All data, security and agent logic
lives in agent_core.py.
"""

import hashlib
import os
from typing import Any, Dict, List, Optional

import streamlit as st
from dotenv import load_dotenv

import agent_core as core

load_dotenv()

st.set_page_config(
    page_title="Canadian Tire Retail AI Copilot",
    page_icon="🍁",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = os.path.join(APP_DIR, "retail_data.csv")

PROVIDERS = {
    "Groq": {
        "key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"],
    },
    "OpenAI": {
        "key": "OPENAI_API_KEY",
        "base_url": None,
        "models": ["gpt-4o-mini", "gpt-4o"],
    },
    "Custom / Ollama (OpenAI compatible)": {
        "key": None,
        "base_url": "http://localhost:11434/v1",
        "models": [],
    },
}


# ------------------------------------------------------------------------------
# Data + database
# ------------------------------------------------------------------------------
@st.cache_resource(show_spinner="Loading data into DuckDB...")
def get_database(file_bytes: bytes, file_name: str):
    """One locked-down database per dataset, shared read-only across sessions."""
    df = core.load_dataframe(file_bytes, file_name)
    return core.build_database(df)


def get_session_cursor(file_bytes: bytes, file_name: str):
    """Each browser session gets its own cursor (DuckDB connections aren't shared across threads)."""
    data_key = hashlib.sha1(file_bytes).hexdigest()
    if st.session_state.get("data_key") != data_key:
        con = get_database(file_bytes, file_name)
        st.session_state.cursor = con.cursor()
        st.session_state.data_key = data_key
        st.session_state.profile = core.profile_dataset(st.session_state.cursor)
        st.session_state.chat_history = []
    return st.session_state.cursor, st.session_state.profile


def resolve_api_key(key_name: Optional[str]) -> str:
    """Read the key from the environment or Streamlit secrets. Never from the UI."""
    if not key_name:
        return "not-needed"
    val = os.getenv(key_name, "")
    if val:
        return val
    try:
        if key_name in st.secrets:
            return str(st.secrets[key_name])
    except Exception:
        pass
    return ""


# ------------------------------------------------------------------------------
# Rendering helpers
# ------------------------------------------------------------------------------
def render_steps(steps: List[core.Step], key_prefix: str, expanded: bool = False) -> None:
    if not steps:
        return
    ok = [s for s in steps if not s.error]
    label = f"🔍 How this was calculated ({len(ok)} quer{'y' if len(ok) == 1 else 'ies'}"
    label += f", {len(steps) - len(ok)} fixed error{'s' if len(steps) - len(ok) != 1 else ''})" if len(ok) != len(steps) else ")"
    with st.expander(label, expanded=expanded):
        for i, s in enumerate(steps, 1):
            title = "SQL query" if s.tool == "run_sql" else f"Margin bridge tool (level = {s.args.get('level', 'company')})"
            st.markdown(f"**Step {i}: {title}**")
            if s.sql:
                st.code(s.sql, language="sql")
            if s.error:
                st.warning(f"This step failed and the agent retried: {s.error}")
                continue
            if s.df is not None:
                st.dataframe(s.df, width="stretch", hide_index=True)
                st.caption(f"⚡ {s.ms} ms · {len(s.df):,} rows")
                st.download_button(
                    "📥 Download as CSV",
                    data=s.df.to_csv(index=False).encode("utf-8"),
                    file_name=f"result_step{i}.csv",
                    mime="text/csv",
                    key=f"dl_{key_prefix}_{i}",
                )


def render_kpis(cur) -> None:
    df = cur.execute("SELECT * FROM yearly_kpis ORDER BY YEAR").df()
    if len(df) < 2:
        return
    a, b = df.iloc[-2], df.iloc[-1]
    ya, yb = int(a.YEAR), int(b.YEAR)
    pct = lambda new, old: f"{(new / old - 1) * 100:+.2f}%"
    c = st.columns(5)
    c[0].metric(f"{yb} Sales", f"${b.sales:,.0f}", pct(b.sales, a.sales))
    c[1].metric(f"{yb} Margin", f"${b.margin:,.0f}", pct(b.margin, a.margin))
    c[2].metric(f"{yb} Margin rate", f"{b.margin_rate_pct:.2f}%", f"{(b.margin_rate_pct - a.margin_rate_pct) * 100:+.0f} bps")
    c[3].metric(f"{yb} Units", f"{int(b.units):,}", pct(b.units, a.units))
    c[4].metric(f"{yb} COGS", f"${b.cogs:,.0f}", pct(b.cogs, a.cogs), delta_color="inverse")
    st.caption(f"Change vs {ya}. Same numbers as the Power BI dashboard.")


# ------------------------------------------------------------------------------
# Main app
# ------------------------------------------------------------------------------
def main() -> None:
    st.markdown("## 🍁 Canadian Tire Retail AI Copilot")
    st.caption("Ask about sales, cost and margin in plain English. Every number comes from a SQL query you can inspect.")

    # ---------------- Sidebar ----------------
    with st.sidebar:
        st.header("⚙️ Settings")
        provider_names = list(PROVIDERS)
        default_idx = next((i for i, p in enumerate(provider_names) if resolve_api_key(PROVIDERS[p]["key"]) and PROVIDERS[p]["key"]), 0)
        provider = st.selectbox("LLM provider", provider_names, index=default_idx)
        cfg = PROVIDERS[provider]
        base_url = cfg["base_url"]
        if cfg["models"]:
            model = st.selectbox("Model", cfg["models"], index=0)
        else:
            base_url = st.text_input("Base URL", value=cfg["base_url"])
            model = st.text_input("Model name", value="qwen2.5:latest")
        api_key = resolve_api_key(cfg["key"])
        if api_key:
            st.success(f"{provider} key found on the server.", icon="🔒")
        else:
            st.warning(f"No `{cfg['key']}` set. Quick questions still work; typed questions need a key.", icon="⚠️")
        use_agent_for_quick = st.checkbox(
            "Answer quick questions with the AI agent", value=False,
            help="Off: quick questions use fixed, tested queries (instant and always the same). On: the LLM writes its own SQL.")

        st.divider()
        st.subheader("📂 Data")
        uploaded = st.file_uploader("Upload a CSV or Excel file with the same columns", type=["csv", "xlsx"])
        if uploaded is not None:
            file_bytes, file_name = uploaded.getvalue(), uploaded.name
        else:
            with open(DEFAULT_DATA, "rb") as f:
                file_bytes, file_name = f.read(), "retail_data.csv"

        try:
            cur, profile = get_session_cursor(file_bytes, file_name)
        except Exception as e:
            st.error(f"Couldn't load that file: {e}")
            st.stop()

        st.markdown(
            f"**{profile['rows']:,} rows** · {profile['date_min']} to {profile['date_max']} ({profile['days']} days)  \n"
            f"{len(profile['stores'])} stores · {len(profile['regions'])} regions · {len(profile['brands'])} brands · {profile['products']} products")
        if profile["one_row_per_store_day"]:
            st.info("Each row is one product sold in one store on one day, so every store has the same number of rows. "
                    "Store size and monthly seasonality comparisons are not reliable with this data.", icon="ℹ️")
        with st.expander("Tables the agent can query"):
            st.markdown("`retail_sales` (row level) · `yearly_kpis` · `division_yoy` · `category_yoy` · `brand_yoy` · "
                        "`region_yoy` · `store_yoy` · `product_yoy`, plus a `margin_bridge` tool.")

        st.divider()
        if st.button("🗑️ Clear chat", width="stretch"):
            st.session_state.chat_history = []
            st.rerun()

    # ---------------- KPI header ----------------
    render_kpis(cur)

    # ---------------- Quick questions ----------------
    st.markdown("##### ⚡ Quick questions")
    cols = st.columns(len(core.QUICK_QUESTIONS))
    clicked: Optional[int] = None
    for i, (label, _q, _fn) in enumerate(core.QUICK_QUESTIONS):
        if cols[i].button(label, key=f"quick_{i}", width="stretch"):
            clicked = i

    history: List[Dict[str, Any]] = st.session_state.setdefault("chat_history", [])

    # ---------------- Chat history ----------------
    for n, msg in enumerate(history):
        with st.chat_message(msg["role"]):
            st.markdown(core.to_display_markdown(msg["content"]))
            if msg["role"] == "assistant":
                if msg.get("source") == "instant":
                    st.caption("Instant answer from a fixed, tested query.")
                render_steps(msg.get("steps", []), key_prefix=f"h{n}")

    # ---------------- New question ----------------
    typed = st.chat_input("Ask a question, e.g. 'Which stores lost margin in 2024 and by how much?'")
    if clicked is not None:
        prompt = core.QUICK_QUESTIONS[clicked][1]
    else:
        prompt = typed
    if not prompt:
        return

    history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(core.to_display_markdown(prompt))

    with st.chat_message("assistant"):
        use_instant = clicked is not None and (not use_agent_for_quick or not api_key)
        if use_instant:
            result = core.QUICK_QUESTIONS[clicked][2](cur)
            source = "instant"
        elif not api_key:
            st.warning(f"Set `{cfg['key']}` in the environment or Streamlit secrets to ask your own questions. "
                       "The quick questions above work without a key.")
            history.pop()
            return
        else:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))
            with st.spinner("Writing SQL and checking the numbers..."):
                try:
                    result = core.run_agent(
                        client=client, model=model,
                        system_prompt=core.build_system_prompt(profile),
                        history=core.history_to_messages(history),
                        cur=cur,
                    )
                except Exception as e:
                    st.error(f"The model call failed: {e}")
                    st.caption("Check the API key and model in the sidebar, then try again.")
                    history.pop()
                    return
            source = "agent"

        text = result.text or "_The model returned an empty answer. Try rephrasing the question._"
        if result.hit_step_limit:
            text += "\n\n_Note: the agent hit its query limit, so this answer may be incomplete._"
        st.markdown(core.to_display_markdown(text))
        if source == "instant":
            st.caption("Instant answer from a fixed, tested query.")
        render_steps(result.steps, key_prefix=f"h{len(history)}", expanded=False)
        history.append({"role": "assistant", "content": text, "steps": result.steps, "source": source})


if __name__ == "__main__":
    main()
