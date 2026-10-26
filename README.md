# 🍁 Canadian Tire Retail AI Copilot

> **Executive Status:** Proof of Concept (POC)  
> **Core Engine:** In-Memory DuckDB OLAP + Self-Correcting Tool-Calling LLM  
> **Deployment Target:** Streamlit Community Cloud / Enterprise Docker  

---

## 📌 Executive Summary: Proof of Concept (POC)

This application is an **Executive Proof of Concept (POC)** built to solve the fundamental flaw of traditional conversational AI in retail analytics: **mathematical hallucination**.

Traditional vector RAG systems fail when asked questions like *"Which brands had margin rate compression in 2024?"* because language models cannot reliably calculate aggregations across thousands of rows. 

The **Canadian Tire Retail AI Copilot** pairs a tool-calling LLM with **DuckDB**, an ultra-fast in-memory analytical SQL engine. Rather than guessing the numbers, the agent translates natural language questions into deterministic ANSI SQL queries, runs them against the raw transactional dataset in memory (typically in under **15 milliseconds**), and synthesizes executive-ready insights.

> 💡 **POC Note:** While this prototype operates over a localized two-year store dataset (21,931 records), it successfully proves the feasibility, speed, and 100% mathematical accuracy of agentic retail intelligence. When expanded to full enterprise scale, this architecture unlocks immense strategic and operational value across Canadian Tire's merchandising, supply chain, and store operations.

---

## ⚡ Key Functionalities (What the POC Delivers Today)

1. **Deterministic Math & Zero Hallucinations**:
   - Every metric (Gross Sales, Net Margin, Margin Rate %, Unit Velocity) is calculated by DuckDB via ANSI SQL—never estimated by the LLM.
   - Always respects retail accounting principles: calculates dollar totals before dividing margin rates (`SUM(Margin) / SUM(Sales)`).

2. **Autonomous Tool-Calling & Self-Correction**:
   - The LLM receives database schema specifications and invokes the `execute_sql` tool.
   - If an error occurs (syntax issue or missing column), the execution error is fed back to the model for **automatic self-correction** (up to 3 iterations) before answering.

3. **Sub-Second In-Memory OLAP Performance**:
   - DuckDB operates directly inside the application process with columnar vectorization, executing complex multi-year grouping and window functions in **5–20 ms**.

4. **Full Analytical Transparency**:
   - Every response includes an expandable audit drawer containing the exact generated SQL query, execution runtime, and interactive data table.
   - Executives and analysts can verify the logic or download query results as CSV with one click.

5. **Enterprise Credential Security (Zero Frontend Exposure)**:
   - Sensitive API keys (`GROQ_API_KEY`, `OPENAI_API_KEY`) are resolved strictly on the backend via environment variables or Streamlit Secrets. No keys are ever exposed or requested in the frontend UI.

6. **Executive Quick-Query Shortcuts**:
   - One-click access to critical retail diagnostic queries:
     - 🏆 *Margin Performance by Product Division (2024 vs 2023)*
     - ⚠️ *5 Lowest Margin Rate Brands in 2024*
     - 📉 *Cost vs. Price Squeeze Audit (Margin Dilution)*
     - 🏬 *Store Size & Regional Profitability Matrix*

---

## 🚀 The Enterprise Value Roadmap (When Fully Implemented)

While this POC validates the core analytical engine, expanding it into a full-scale enterprise solution will drive transformative value across Canadian Tire:

| Horizon | Capability | Business Impact |
| :--- | :--- | :--- |
| **Phase 1 (Current POC)** | In-memory text-to-SQL exploration, automated executive summaries, and schema-constrained queries. | Demonstrates feasibility, eliminates hallucination, and establishes trust with non-technical stakeholders. |
| **Phase 2 (Data Warehouse Integration)** | Connect DuckDB directly to **Snowflake, Databricks, or MotherDuck** via Iceberg/Parquet catalogs. | Expands query reach across billions of POS transactions, loyalty data (Triangle Rewards), and real-time inventory. |
| **Phase 3 (Proactive Merchandising Agents)** | Autonomous background agent that monitors daily sales and supplier price lists. | Flags margin dilution in real-time, alerts buyers when supplier cost increases outpace retail prices, and recommends price adjustments. |
| **Phase 4 (Scenario Modeling & What-If Simulations)** | Dynamic elasticity simulation (e.g., *"If we raise brand X prices by 3% in West Region, what is the margin impact?"*). | Replaces static PowerBI dashboards with actionable decision intelligence for Category Managers and VPs. |
| **Phase 5 (Enterprise Workflow Integration)** | Microsoft Teams / Slack bot & ERP triggers (SAP, Blue Yonder). | Delivers daily store performance briefings directly into field operations and automated purchase order adjustments. |

---

## 🛠️ Architecture & Technology Stack

```
   [ User Executive / Category Manager ]
                     |
           (Natural Language Query)
                     v
+-------------------------------------------------------------+
|               Streamlit Executive UI (Port 8501)            |
+-------------------------------------------------------------+
                     |
       (Prompt + In-Memory Schema Metadata)
                     v
+-------------------------------------------------------------+
|    Agent Runner (Groq / OpenAI API via Tool-Calling)        |
|    - Schema Guardrails                                      |
|    - Mathematical Rules (Non-additive ratios)               |
|    - Automated Error Self-Correction Loop                   |
+-------------------------------------------------------------+
                     |
          (Generated ANSI SQL Query)
                     v
+-------------------------------------------------------------+
|             DuckDB In-Memory OLAP Database                  |
|    - Standardized View: 'retail_sales' (21,931 rows)        |
|    - Columnar Storage & Vectorized Query Execution          |
+-------------------------------------------------------------+
                     |
          (Verified Dataframe + Execution Runtime)
                     v
+-------------------------------------------------------------+
|           LLM Synthesis & Executive Narrative               |
|    - Bulleted Takeaways & Merchandising Insights            |
|    - Downloadable CSV + Transparent SQL Drawer              |
+-------------------------------------------------------------+
```

---

## 📦 Local Quickstart

### 1. Clone & Navigate to Repository
```bash
git clone <your-repo-url>
cd duckdb_retail_agent
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure API Credentials
Create a `.env` file in the root directory:
```env
GROQ_API_KEY=your_groq_api_key_here
# Or if using OpenAI:
# OPENAI_API_KEY=your_openai_api_key_here
```

### 4. Run the Streamlit Application
```bash
streamlit run app.py
```
Open **`http://localhost:8501`** in your browser.

---

## ☁️ Deployment Guide (Streamlit Community Cloud)

1. **Push this repository to GitHub** (see instructions below).
2. Go to **[share.streamlit.io](https://share.streamlit.io)** and click **"New app"**.
3. Select your repository, branch (`main`), and set the main file path to **`app.py`**.
4. Click **"Advanced settings..."** -> **"Secrets"** and enter your API key:
   ```toml
   GROQ_API_KEY = "gsk_your_groq_key_here"
   ```
5. Click **"Deploy!"** Your agent will be live with secure backend credentials.
