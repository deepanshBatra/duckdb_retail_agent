# 🍁 Canadian Tire Retail AI Copilot

A proof of concept that lets leaders ask sales and margin questions in plain English. A tool-calling LLM answers by writing SQL against an in-memory **DuckDB** database, so every number comes from a query you can open and check.

**Status:** proof of concept, built on the case-study extract (7,310 rows, 10 stores, 1 Jan 2023 – 31 Dec 2024).

---

## What it does

- **Answers questions with real queries.** The model never does the math itself. It calls tools, and every answer shows each SQL query, its result table, how long it took, and a CSV download.
- **Knows the business definitions.** Sales, COGS, margin and margin rate are pre-computed per row, and helper views give year-over-year results by division, category, brand, region, store, store format and product. Margin rates are always weighted (`SUM(margin) / SUM(sales)`), never averaged.
- **Explains *why* margin moved.** A `margin_bridge` tool splits the 2023 → 2024 margin change into price, cost, volume and mix effects that add up exactly. It also splits the margin-rate change into mix vs. price-vs-cost.
- **Knows the limits of the data.** The prompt tells the model that the extract has one row per store per day, so store-size and seasonality comparisons aren't reliable, and it says so instead of making recommendations from them.
- **Gives instant answers to four quick questions** without an API key. These use fixed, tested queries, and the text is built from the live results.
- **Is locked down.** The database is read-only, file and network access are switched off inside DuckDB, and only a single `SELECT` is accepted per call. API keys are read on the server only.

## Accuracy

| Check | Result |
| :--- | :--- |
| `python test_agent.py` (offline) | Checks every core number against the validated case-study results, plus the security guardrails. |
| `python eval_agent.py` (live, 14 questions with known answers) | `openai/gpt-oss-120b`: 14/14 in two runs · `qwen/qwen3.8-27b`: 14/14 (Groq, Nov 2026) |

The SQL results are deterministic. The written answer comes from an LLM and can vary between runs. An early version scored 12–13/14 because the model sometimes did arithmetic in its head; the prompt now requires every number to come from SQL. Re-run the eval before a demo, and keep the query drawer open so people can check.

## Project layout

| File | What it holds |
| :--- | :--- |
| `app.py` | Streamlit UI: sidebar settings, KPI header, quick questions, chat and the query drawer. |
| `agent_core.py` | Data loading, DuckDB views and lockdown, SQL guardrail, margin-bridge tool, system prompt, agent loop, quick answers. No Streamlit, so it can be tested. |
| `test_agent.py` | Offline tests (no API key). |
| `eval_agent.py` | Live accuracy check against a real model. |
| `retail_data.csv` | The case-study data. |

## Tables the agent can query

| Name | One row per | Highlights |
| :--- | :--- | :--- |
| `retail_sales` | store per day | Raw columns plus `SALES`, `COGS`, `MARGIN`, `STORE_FORMAT` |
| `yearly_kpis` | year | sales, cogs, margin, margin rate, units |
| `division_yoy`, `category_yoy`, `brand_yoy`, `region_yoy`, `store_yoy`, `product_yoy` | group | 2023 vs 2024 sales, margin, rate (bps), units; brands and products add price/cost % change; stores add $/sq ft |
| `format_yoy` | store format | store count, sq ft, margin per store, $/sq ft |

## Run it locally

```bash
pip install -r requirements.txt
cp .env.example .env        # then add your GROQ_API_KEY
streamlit run app.py
```

Open http://localhost:8501.

## Deploy on Streamlit Community Cloud

1. Push this repository to GitHub.
2. At [share.streamlit.io](https://share.streamlit.io), create an app from the repo with main file `app.py`.
3. Under **Advanced settings → Secrets**, add:
   ```toml
   GROQ_API_KEY = "gsk_..."
   ```
4. Deploy. Pushes to `main` redeploy automatically.

## Where this could go next

| Step | What it adds |
| :--- | :--- |
| Connect to the data warehouse | Point DuckDB at Snowflake / Databricks / Parquet so the same agent works on full POS, loyalty and inventory data. |
| Margin alerts | Run the brand price-vs-cost check on a schedule and flag brands like Fynix (cost +10% vs price +6%) before the next buy. |
| What-if questions | "If we raise Fynix prices 4%, what happens to margin?", using the same bridge logic. |
| Teams / Slack | Ask questions where people already work. |
