"""
Canadian Tire Retail AI Copilot - DuckDB In-Memory Text-to-SQL Agent
=====================================================================
A lightweight, production-ready Streamlit app pairing an in-memory DuckDB SQL engine
with a tool-calling LLM (OpenAI / Groq) to deliver 100% deterministic retail analytics.

Author: Senior Agentic AI Engineer
Workspace: Canadian Tire DSA Case Study
"""

import os
import re
import time
import json
from typing import Dict, Any, List, Optional, Tuple
import duckdb
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# Load local environment variables from .env if present
load_dotenv()

# ==============================================================================
# 1. STREAMLIT APP CONFIGURATION & STYLING
# ==============================================================================
st.set_page_config(
    page_title="Canadian Tire Retail AI Copilot | DuckDB Agent",
    page_icon="🍁",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Executive Theme CSS
st.markdown("""
<style>
    /* Executive Retail Styling */
    .main {
        background-color: #0e1117;
    }
    .metric-card {
        background: linear-gradient(135deg, #1e2530 0%, #151922 100%);
        border: 1px solid #2d3748;
        border-radius: 10px;
        padding: 16px 20px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
    }
    .metric-title {
        color: #94a3b8;
        font-size: 0.85rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .metric-value {
        color: #f8fafc;
        font-size: 1.6rem;
        font-weight: 700;
        margin-top: 4px;
    }
    .badge-chip {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
        margin-right: 6px;
    }
    .badge-duckdb {
        background-color: #fff3bf;
        color: #d97706;
        border: 1px solid #fde68a;
    }
    .badge-sql {
        background-color: #e0e7ff;
        color: #4338ca;
        border: 1px solid #c7d2fe;
    }
    .badge-agent {
        background-color: #fee2e2;
        color: #b91c1c;
        border: 1px solid #fecaca;
    }
    .sql-container {
        background-color: #0b0f19;
        border-left: 4px solid #ef4444;
        padding: 12px;
        border-radius: 4px;
        font-family: 'Courier New', monospace;
        font-size: 0.88rem;
    }
    .executive-summary {
        background: #131b2e;
        border-left: 4px solid #3b82f6;
        padding: 14px 18px;
        border-radius: 6px;
        margin-bottom: 12px;
    }
</style>
""", unsafe_allow_html=True)


# ==============================================================================
# 2. DUCKDB IN-MEMORY DATABASE & DATA LOADER
# ==============================================================================
@st.cache_resource(show_spinner="Initializing DuckDB In-Memory Engine...")
def init_duckdb_engine(uploaded_file=None, file_path_override: Optional[str] = None) -> Tuple[duckdb.DuckDBPyConnection, pd.DataFrame]:
    """
    Connect to in-memory DuckDB and register standardized 'retail_sales' view.
    Searches for uploaded file, local CSV, or parent Excel file automatically.
    """
    con = duckdb.connect(database=":memory:")
    
    # 1. Determine data source
    df: Optional[pd.DataFrame] = None
    
    if uploaded_file is not None:
        try:
            if uploaded_file.name.endswith(".csv"):
                df = pd.read_csv(uploaded_file)
            else:
                df = pd.read_excel(uploaded_file)
        except Exception as e:
            st.error(f"Failed to read uploaded file: {e}")
            
    if df is None and file_path_override and os.path.exists(file_path_override):
        if file_path_override.endswith(".csv"):
            df = pd.read_csv(file_path_override)
        else:
            df = pd.read_excel(file_path_override)
            
    if df is None:
        candidates = [
            "retail_data.csv",
            os.path.join(os.path.dirname(__file__), "retail_data.csv"),
            os.path.join(os.path.dirname(__file__), "..", "retail_data.csv"),
            os.path.join(os.path.dirname(__file__), "..", "CaseStudy_DataExtractFromPowerBIFile.xlsx"),
            "CaseStudy_DataExtractFromPowerBIFile.xlsx"
        ]
        for p in candidates:
            if os.path.exists(p):
                try:
                    if p.endswith(".csv"):
                        df = pd.read_csv(p)
                    else:
                        df = pd.read_excel(p)
                    break
                except Exception:
                    continue

    if df is None:
        # Fallback empty dataframe with schema
        df = pd.DataFrame(columns=[
            "YEAR", "QUARTER", "MONTH", "DATE", "STORE_ID", "STORE_NAME", "STORE_SIZE",
            "REGION", "PRODUCT_ID", "BRAND", "PRODUCT_CATEGORY", "PRODUCT_DIVISION",
            "PRODUCT_NAME", "SELLING_PRICE_PER_UNIT", "UNITS_SOLD", "COST_PER_UNIT"
        ])

    # Standardize column naming if needed
    col_mapping = {}
    if "SELLING_PRICE" in df.columns and "SELLING_PRICE_PER_UNIT" not in df.columns:
        col_mapping["SELLING_PRICE"] = "SELLING_PRICE_PER_UNIT"
    if "COST" in df.columns and "COST_PER_UNIT" not in df.columns:
        col_mapping["COST"] = "COST_PER_UNIT"
    if col_mapping:
        df = df.rename(columns=col_mapping)

    # Register raw table in DuckDB
    con.register("_raw_retail_table", df)

    # Create standardized view with aliasing and store size categorization
    con.execute("""
    CREATE OR REPLACE VIEW retail_sales AS
    SELECT 
        CAST(YEAR AS INTEGER) AS YEAR,
        CAST(QUARTER AS INTEGER) AS QUARTER,
        CAST(MONTH AS INTEGER) AS MONTH,
        TRY_CAST(DATE AS DATE) AS DATE,
        CAST(STORE_ID AS INTEGER) AS STORE_ID,
        CAST(STORE_NAME AS VARCHAR) AS STORE_NAME,
        CAST(STORE_SIZE AS BIGINT) AS STORE_SIZE,
        CASE 
            WHEN STORE_SIZE < 30000 THEN 'Small (<30k sqft)'
            WHEN STORE_SIZE BETWEEN 30000 AND 80000 THEN 'Medium (30k-80k sqft)'
            ELSE 'Large (>80k sqft)'
        END AS STORE_SIZE_CATEGORY,
        CAST(REGION AS VARCHAR) AS REGION,
        CAST(PRODUCT_ID AS INTEGER) AS PRODUCT_ID,
        CAST(BRAND AS VARCHAR) AS BRAND,
        CAST(PRODUCT_CATEGORY AS VARCHAR) AS PRODUCT_CATEGORY,
        CAST(PRODUCT_DIVISION AS VARCHAR) AS PRODUCT_DIVISION,
        CAST(PRODUCT_NAME AS VARCHAR) AS PRODUCT_NAME,
        CAST(SELLING_PRICE_PER_UNIT AS DOUBLE) AS SELLING_PRICE_PER_UNIT,
        CAST(SELLING_PRICE_PER_UNIT AS DOUBLE) AS SELLING_PRICE,
        CAST(UNITS_SOLD AS INTEGER) AS UNITS_SOLD,
        CAST(COST_PER_UNIT AS DOUBLE) AS COST_PER_UNIT,
        CAST(COST_PER_UNIT AS DOUBLE) AS COST
    FROM _raw_retail_table;
    """)

    return con, df


# ==============================================================================
# 3. SAFE SQL EXECUTION TOOL
# ==============================================================================
def execute_sql(con: duckdb.DuckDBPyConnection, query: str) -> Dict[str, Any]:
    """
    Executes a SELECT query safely against DuckDB in-memory view.
    Includes guardrails against destructive statements and returns
    structured records or syntax error messages for self-correction.
    """
    clean_query = query.strip().rstrip(";")
    
    # Strip comments to inspect primary statement
    uncommented = re.sub(r"--.*?\n", "", clean_query, flags=re.MULTILINE)
    uncommented = re.sub(r"/\*.*?\*/", "", uncommented, flags=re.DOTALL).strip()
    
    # Guardrail: Only SELECT or WITH (CTE) queries allowed
    first_word = uncommented.split()[0].upper() if uncommented.split() else ""
    if first_word not in ("SELECT", "WITH", "DESCRIBE", "EXPLAIN"):
        return {
            "success": False,
            "error": f"Security restriction: Only SELECT or WITH queries are permitted. Attempted: {first_word}",
            "df": None,
            "row_count": 0
        }
        
    # Check for forbidden mutations
    forbidden_tokens = [r"\bDROP\b", r"\bDELETE\b", r"\bINSERT\b", r"\bUPDATE\b", r"\bALTER\b", r"\bTRUNCATE\b", r"\bEXEC\b"]
    for token in forbidden_tokens:
        if re.search(token, uncommented, re.IGNORECASE):
            return {
                "success": False,
                "error": f"Forbidden mutation statement detected: '{token}' is not allowed in read-only mode.",
                "df": None,
                "row_count": 0
            }

    try:
        start_time = time.time()
        result_df = con.execute(clean_query).df()
        elapsed_ms = round((time.time() - start_time) * 1000, 2)
        
        # Format datetimes/floats for JSON compatibility
        records = json.loads(result_df.head(100).to_json(orient="records", date_format="iso"))
        
        return {
            "success": True,
            "data": records,
            "row_count": len(result_df),
            "columns": list(result_df.columns),
            "elapsed_ms": elapsed_ms,
            "df": result_df,
            "query": clean_query
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"DuckDB SQL Execution Error: {str(e)}",
            "df": None,
            "row_count": 0,
            "query": clean_query
        }


# ==============================================================================
# 4. AGENT SYSTEM PROMPT & TOOL DEFINITIONS
# ==============================================================================
SYSTEM_PROMPT = """You are the Senior Retail Analytics AI Copilot for Canadian Tire's Executive Leadership.
Your mission is to translate business and merchandising questions into precise ANSI SQL, execute them via DuckDB,
and synthesize executive summaries with 100% mathematical accuracy.

### DATABASE SCHEMA (Table View: 'retail_sales'):
- DATE (Date: YYYY-MM-DD, 2023-01-01 to 2024-12-31, 730 days)
- YEAR (Integer: 2023, 2024)
- QUARTER (Integer: 1, 2, 3, 4)
- MONTH (Integer: 1 to 12)
- PRODUCT_ID (Integer: 1 to 165)
- PRODUCT_NAME (String: 'Item A', 'Item B', etc.)
- PRODUCT_CATEGORY (String: 12 categories: Beverages, Decor, Fitness, Garden Tools, Pantry, Plants, Power Tools, Safety Gear, Shirts, Shoes, Snacks, Team Sports)
- PRODUCT_DIVISION (String: 5 divisions: Apparel, Food, Gardening, Sports, Tools)
- BRAND (String: 10 brands: Dexon, Fynix, Kryta, Lumix, Novex, Quanta, Solvo, Trion, Vetra, Zentra)
- STORE_ID (Integer: 1 to 10)
- STORE_NAME (String: Store 1 through Store 10)
- STORE_SIZE (BigInt: Square footage, range ~16,400 to 91,800 sq ft)
- STORE_SIZE_CATEGORY (String: 'Small (<30k sqft)', 'Medium (30k-80k sqft)', 'Large (>80k sqft)')
- REGION (String: East, North, South, West)
- UNITS_SOLD (Integer: Quantity sold)
- SELLING_PRICE (Double: Unit retail price; also aliased as SELLING_PRICE_PER_UNIT)
- COST (Double: Unit cost to business; also aliased as COST_PER_UNIT)

### MANDATORY KPI DEFINITIONS:
1. Sales ($) = SUM(UNITS_SOLD * SELLING_PRICE)
2. COGS ($) = SUM(UNITS_SOLD * COST)
3. Margin ($) = SUM(UNITS_SOLD * SELLING_PRICE) - SUM(UNITS_SOLD * COST)
4. Margin Rate (%) = (SUM(UNITS_SOLD * SELLING_PRICE) - SUM(UNITS_SOLD * COST)) / NULLIF(SUM(UNITS_SOLD * SELLING_PRICE), 0)
5. Margin Growth (%) = (Margin_2024 - Margin_2023) / NULLIF(Margin_2023, 0) * 100

### CRITICAL CALCULATION RULES:
- NEVER average `SELLING_PRICE`, `COST`, or `Margin Rate` across rows. Always compute dollar totals first, then divide.
- If comparing 2024 vs 2023, use conditional aggregation `SUM(CASE WHEN YEAR = 2024 THEN ... END)` or CTEs.
- Always use `NULLIF(..., 0)` in denominators to prevent division by zero.
- Round metrics to 2 decimal places in final outputs.

### RESPONSE GUIDELINES:
1. Always call the `execute_sql` tool to fetch real data before answering.
2. Structure your executive answer:
   - **Executive Summary**: 2-3 concise bullet points answering the core question with dollar amounts ($) and percentages (%).
   - **Key Metrics Table**: Markdown table summarizing the findings.
   - **Strategic Merchandising Insights**: Brief actionable recommendation (e.g., pricing adjustment, margin dilution warning).
3. Be deterministic: Never guess or extrapolate numbers. Base all statements strictly on the SQL result.
"""

TOOL_SPECIFICATION = [
    {
        "type": "function",
        "function": {
            "name": "execute_sql",
            "description": "Execute a standard ANSI SQL SELECT query against DuckDB in-memory view 'retail_sales'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Valid ANSI SQL SELECT statement to query table 'retail_sales'."
                    }
                },
                "required": ["query"]
            }
        }
    }
]


# ==============================================================================
# 5. AGENT EXECUTION LOOP (TOOL-CALLING WITH SELF-CORRECTION)
# ==============================================================================
def run_agent_turn(
    messages: List[Dict[str, Any]],
    con: duckdb.DuckDBPyConnection,
    api_key: str,
    model_name: str,
    base_url: Optional[str] = None
) -> Tuple[str, Optional[str], Optional[pd.DataFrame], Optional[float]]:
    """
    Executes an LLM agent turn using official OpenAI client.
    Supports function/tool calling and feeds syntax errors back to the model
    for automatic self-correction (up to 3 iterations).
    """
    from openai import OpenAI
    
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)
    
    last_sql_query: Optional[str] = None
    last_df: Optional[pd.DataFrame] = None
    last_elapsed_ms: Optional[float] = None
    
    # Copy conversation history to preserve state
    conversation = [m for m in messages]
    
    # Self-correction loop: up to 3 tool calls
    for iteration in range(3):
        response = client.chat.completions.create(
            model=model_name,
            messages=conversation,
            tools=TOOL_SPECIFICATION,
            tool_choice="auto",
            temperature=0.1
        )
        
        response_msg = response.choices[0].message
        
        # Check if the LLM requested a tool execution
        if response_msg.tool_calls:
            # Append assistant message with tool calls
            conversation.append(response_msg)
            
            for tool_call in response_msg.tool_calls:
                if tool_call.function.name == "execute_sql":
                    call_id = tool_call.id
                    try:
                        args = json.loads(tool_call.function.arguments)
                        raw_sql = args.get("query", "")
                    except Exception as json_err:
                        raw_sql = ""
                        exec_res = {"success": False, "error": f"Invalid JSON arguments: {str(json_err)}"}
                    
                    if raw_sql:
                        exec_res = execute_sql(con, raw_sql)
                        last_sql_query = exec_res.get("query", raw_sql)
                        if exec_res.get("success"):
                            last_df = exec_res.get("df")
                            last_elapsed_ms = exec_res.get("elapsed_ms")
                    
                    # Tool response payload
                    if exec_res.get("success"):
                        tool_content = json.dumps({
                            "status": "success",
                            "row_count": exec_res.get("row_count"),
                            "data": exec_res.get("data")
                        })
                    else:
                        tool_content = json.dumps({
                            "status": "error",
                            "error_message": exec_res.get("error"),
                            "instruction": "Fix the SQL syntax or column name based on the schema and try again."
                        })
                        
                    # Feed result back to the model
                    conversation.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": "execute_sql",
                        "content": tool_content
                    })
        else:
            # Final synthesis message received
            final_text = response_msg.content or ""
            return final_text, last_sql_query, last_df, last_elapsed_ms
            
    # If exhausted iterations, do one final completion request
    final_res = client.chat.completions.create(
        model=model_name,
        messages=conversation,
        temperature=0.1
    )
    final_text = final_res.choices[0].message.content or "Completed analysis."
    return final_text, last_sql_query, last_df, last_elapsed_ms


# ==============================================================================
# 6. PRE-COMPUTED DEMO QUERY RUNNER (OFFLINE / ZERO-API-KEY MODE)
# ==============================================================================
DEMO_QUERIES = {
    "Which Product Divisions drove the highest margin in 2024 vs 2023?": {
        "sql": """
SELECT 
    PRODUCT_DIVISION,
    ROUND(SUM(CASE WHEN YEAR = 2023 THEN UNITS_SOLD * SELLING_PRICE - UNITS_SOLD * COST ELSE 0 END), 2) AS Margin_2023,
    ROUND(SUM(CASE WHEN YEAR = 2024 THEN UNITS_SOLD * SELLING_PRICE - UNITS_SOLD * COST ELSE 0 END), 2) AS Margin_2024,
    ROUND(SUM(CASE WHEN YEAR = 2024 THEN UNITS_SOLD * SELLING_PRICE - UNITS_SOLD * COST ELSE 0 END) - 
          SUM(CASE WHEN YEAR = 2023 THEN UNITS_SOLD * SELLING_PRICE - UNITS_SOLD * COST ELSE 0 END), 2) AS Margin_Diff,
    ROUND((SUM(CASE WHEN YEAR = 2024 THEN UNITS_SOLD * SELLING_PRICE - UNITS_SOLD * COST ELSE 0 END) - 
           SUM(CASE WHEN YEAR = 2023 THEN UNITS_SOLD * SELLING_PRICE - UNITS_SOLD * COST ELSE 0 END)) / 
           NULLIF(SUM(CASE WHEN YEAR = 2023 THEN UNITS_SOLD * SELLING_PRICE - UNITS_SOLD * COST ELSE 0 END), 0) * 100, 2) AS Margin_Growth_Pct
FROM retail_sales
GROUP BY PRODUCT_DIVISION
ORDER BY Margin_2024 DESC;
""",
        "summary": """
### Executive Summary: Product Division Margin Performance (2024 vs 2023)
- **Top Margin Driver**: **Sports** took the #1 spot in 2024, delivering **$157,191.89** in total margin—an impressive **+20.35% YoY expansion** (+\\$26,577.25).
- **Apparel Contraction**: Apparel fell to second place with **$135,638.10**, declining by **-2.58% YoY** (-\\$3,597.44).
- **Gardening & Tools Stability**: Gardening remained virtually flat at **$128,673.45** (+0.11%), while Tools dipped slightly by **-0.45%** to **$86,461.35**.
- **Food High-Growth**: Although smaller in base volume, **Food** expanded **+8.36% YoY** to **$22,464.23**.
"""
    },
    "Which 5 brands had the lowest margin rates in 2024?": {
        "sql": """
SELECT 
    BRAND,
    ROUND(SUM(UNITS_SOLD * SELLING_PRICE), 2) AS Total_Sales_2024,
    ROUND(SUM(UNITS_SOLD * SELLING_PRICE - UNITS_SOLD * COST), 2) AS Total_Margin_2024,
    ROUND((SUM(UNITS_SOLD * SELLING_PRICE - UNITS_SOLD * COST) / NULLIF(SUM(UNITS_SOLD * SELLING_PRICE), 0)) * 100, 2) AS Margin_Rate_Pct
FROM retail_sales
WHERE YEAR = 2024
GROUP BY BRAND
ORDER BY Margin_Rate_Pct ASC
LIMIT 5;
""",
        "summary": """
### Executive Summary: 5 Lowest Margin Rate Brands in 2024
- **Lowest Margin Rate**: **Fynix** posted the lowest margin rate across the business at **42.92%** on **$94,839.95** in sales ($40,701.49 margin).
- **Bottom 5 Cohort**:
  1. **Fynix**: 42.92% Margin Rate
  2. **Kryta**: 45.60% Margin Rate ($100,602.54 Sales, $45,876.91 Margin)
  3. **Novex**: 45.93% Margin Rate ($130,129.21 Sales, $59,771.29 Margin)
  4. **Dexon**: 47.26% Margin Rate ($63,848.39 Sales, $30,172.43 Margin)
  5. **Quanta**: 48.11% Margin Rate ($113,213.53 Sales, $54,468.49 Margin)
- **Merchandising Note**: Novex and Quanta represent significant sales volume (>\\$110k each); even a 150 bps improvement in supplier terms or promotional mix could recover over $3,500 in bottom-line margin.
"""
    },
    "List the products where unit cost increased faster than selling price in 2024.": {
        "sql": """
WITH yearly_pricing AS (
    SELECT 
        PRODUCT_ID,
        PRODUCT_NAME,
        PRODUCT_CATEGORY,
        SUM(CASE WHEN YEAR = 2023 THEN UNITS_SOLD * SELLING_PRICE ELSE 0 END) / NULLIF(SUM(CASE WHEN YEAR = 2023 THEN UNITS_SOLD ELSE 0 END), 0) AS Avg_Price_2023,
        SUM(CASE WHEN YEAR = 2024 THEN UNITS_SOLD * SELLING_PRICE ELSE 0 END) / NULLIF(SUM(CASE WHEN YEAR = 2024 THEN UNITS_SOLD ELSE 0 END), 0) AS Avg_Price_2024,
        SUM(CASE WHEN YEAR = 2023 THEN UNITS_SOLD * COST ELSE 0 END) / NULLIF(SUM(CASE WHEN YEAR = 2023 THEN UNITS_SOLD ELSE 0 END), 0) AS Avg_Cost_2023,
        SUM(CASE WHEN YEAR = 2024 THEN UNITS_SOLD * COST ELSE 0 END) / NULLIF(SUM(CASE WHEN YEAR = 2024 THEN UNITS_SOLD ELSE 0 END), 0) AS Avg_Cost_2024
    FROM retail_sales
    GROUP BY PRODUCT_ID, PRODUCT_NAME, PRODUCT_CATEGORY
),
pct_changes AS (
    SELECT 
        PRODUCT_ID,
        PRODUCT_NAME,
        PRODUCT_CATEGORY,
        ROUND(Avg_Price_2023, 2) AS Price_2023,
        ROUND(Avg_Price_2024, 2) AS Price_2024,
        ROUND(Avg_Cost_2023, 2) AS Cost_2023,
        ROUND(Avg_Cost_2024, 2) AS Cost_2024,
        ROUND((Avg_Price_2024 - Avg_Price_2023) / NULLIF(Avg_Price_2023, 0) * 100, 2) AS Price_Change_Pct,
        ROUND((Avg_Cost_2024 - Avg_Cost_2023) / NULLIF(Avg_Cost_2023, 0) * 100, 2) AS Cost_Change_Pct
    FROM yearly_pricing
    WHERE Avg_Price_2023 IS NOT NULL AND Avg_Price_2024 IS NOT NULL
)
SELECT *
FROM pct_changes
WHERE Cost_Change_Pct > Price_Change_Pct
ORDER BY (Cost_Change_Pct - Price_Change_Pct) DESC
LIMIT 10;
""",
        "summary": """
### Executive Summary: Cost-Price Spread Squeeze Audit (2024 vs 2023)
- **Inflation Spread Finding**: Multiple high-volume SKUs experienced a **+10.00% supplier cost surge** while retail prices were only adjusted by **+6.00%**.
- **Affected SKUs**:
  - `Item D_2` (Product 41, Decor)
  - `Item F` (Product 7, Power Tools)
  - `Item B_5` (Product 134, Fitness)
  - `Item T_6` (Product 96, Snacks)
  - `Item J_10` (Product 103, Safety Gear)
- **Strategic Impact**: This 4.0% unhedged cost spread caused margin rate compression of approximately 180 to 220 basis points across these categories.
"""
    },
    "Break down total sales and margin rate by region and store size.": {
        "sql": """
SELECT 
    REGION,
    STORE_SIZE_CATEGORY,
    COUNT(DISTINCT STORE_ID) AS Store_Count,
    ROUND(SUM(UNITS_SOLD * SELLING_PRICE), 2) AS Total_Sales,
    ROUND(SUM(UNITS_SOLD * SELLING_PRICE - UNITS_SOLD * COST), 2) AS Total_Margin,
    ROUND((SUM(UNITS_SOLD * SELLING_PRICE - UNITS_SOLD * COST) / NULLIF(SUM(UNITS_SOLD * SELLING_PRICE), 0)) * 100, 2) AS Margin_Rate_Pct
FROM retail_sales
GROUP BY REGION, STORE_SIZE_CATEGORY
ORDER BY REGION, Total_Sales DESC;
""",
        "summary": """
### Executive Summary: Regional & Store Size Efficiency Matrix
- **Largest Sales Contributor**: **East Region Large Stores (>80k sqft)** generated the highest sales volume (**$627,046.88**) and total margin (**$311,921.28**) with a healthy **49.75%** margin rate.
- **Top Margin Rate Performer**: **East Region Small Stores (<30k sqft)** captured the highest margin efficiency at **50.60%** ($207,639.12 Sales, $105,059.44 Margin).
- **North Region**: Small stores generated **$428,591.31** at **49.83%**, while medium stores reached **$203,346.73** at **49.35%**.
- **West & South**: West Large stores captured **$417,543.68** at **49.28%**, and South Medium stores generated **$203,668.62** at **48.96%**.
"""
    }
}


def resolve_api_key(provider: str) -> str:
    """
    Safely resolves API key from backend environment variables or Streamlit Secrets.
    Prevents leaking credentials or requiring users to enter keys on the frontend.
    """
    key_name = "GROQ_API_KEY" if provider == "Groq" else "OPENAI_API_KEY"
    val = os.getenv(key_name, "")
    if val:
        return val
    try:
        if hasattr(st, "secrets") and key_name in st.secrets:
            return str(st.secrets[key_name])
    except Exception:
        pass
    if provider == "Custom / Ollama (OpenAI Compatible)":
        return "ollama"
    return ""


# ==============================================================================
# 7. MAIN STREAMLIT APPLICATION
# ==============================================================================
def main():
    # Header Banner
    col_logo, col_desc = st.columns([1, 6])
    with col_logo:
        st.markdown("<h1 style='font-size: 3.5rem; margin: 0;'>🍁</h1>", unsafe_allow_html=True)
    with col_desc:
        st.markdown("""
        <h2 style='margin-bottom: 2px; color: #f8fafc;'>Canadian Tire Retail AI Copilot</h2>
        <p style='color: #94a3b8; font-size: 0.95rem; margin: 0;'>
            Deterministic In-Memory SQL Agent powered by <span class='badge-chip badge-duckdb'>DuckDB</span>
            <span class='badge-chip badge-sql'>ANSI SQL</span>
            <span class='badge-chip badge-agent'>Tool-Calling LLM</span>
        </p>
        """, unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # SIDEBAR: CONTROLS & DATA CONFIGURATION
    # --------------------------------------------------------------------------
    with st.sidebar:
        st.header("⚙️ Configuration")
        
        has_groq = bool(resolve_api_key("Groq"))
        has_openai = bool(resolve_api_key("OpenAI"))
        default_provider_idx = 0 if has_groq else (1 if has_openai else 0)

        provider = st.selectbox(
            "LLM Provider",
            ["Groq", "OpenAI", "Custom / Ollama (OpenAI Compatible)"],
            index=default_provider_idx
        )
        
        if provider == "Groq":
            default_model = "qwen/qwen3.8-27b"
            model_options = ["qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "openai/gpt-oss-120b", "llama-3.3-70b-versatile"]
            base_url = "https://api.groq.com/openai/v1"
        elif provider == "OpenAI":
            default_model = "gpt-4o-mini"
            model_options = ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"]
            base_url = None
        else:
            default_model = "qwen2.5:latest"
            model_options = ["qwen2.5:latest", "llama3.1:latest", "deepseek-r1:latest", "custom"]
            base_url = st.text_input("Base URL", value="http://localhost:11434/v1")
            
        api_key = resolve_api_key(provider)
        
        # Backend Security Indicator
        if api_key:
            st.success(f"🔒 **{provider} Connected** (via Secure Backend)", icon="✅")
        else:
            env_var_name = "GROQ_API_KEY" if provider == "Groq" else "OPENAI_API_KEY"
            st.warning(f"⚠️ **{provider} Key Not Detected**\nSet `{env_var_name}` in environment or Streamlit Secrets.", icon="⚠️")
        
        model_name = st.selectbox("Model Name", model_options, index=0)
        
        st.markdown("---")
        st.subheader("📂 Dataset Source")
        uploaded_file = st.file_uploader(
            "Upload Retail CSV or Excel",
            type=["csv", "xlsx"],
            help="Upload custom retail sales data or use default workspace dataset."
        )
        
        # Initialize DuckDB Engine
        con, raw_df = init_duckdb_engine(uploaded_file)
        
        # Dataset Summary Metrics
        if not raw_df.empty:
            total_rows = len(raw_df)
            min_date = raw_df["DATE"].min() if "DATE" in raw_df.columns else "N/A"
            max_date = raw_df["DATE"].max() if "DATE" in raw_df.columns else "N/A"
            
            st.markdown(f"**Loaded Rows:** `{total_rows:,}`")
            st.markdown(f"**Date Range:** `{str(min_date)[:10]}` to `{str(max_date)[:10]}`")
            
            with st.expander("🔍 Schema & Top 5 Preview", expanded=False):
                st.caption("Active DuckDB View: `retail_sales`")
                st.dataframe(raw_df.head(5), use_container_width=True)
                st.caption(f"Columns: {', '.join(list(raw_df.columns)[:8])}...")
        else:
            st.warning("No retail dataset loaded. Please upload a CSV.")

        st.markdown("---")
        if st.button("🗑️ Reset Chat History", use_container_width=True):
            st.session_state.chat_history = []
            st.rerun()

    # --------------------------------------------------------------------------
    # EXECUTIVE KPI HEADER CARDS
    # --------------------------------------------------------------------------
    if not raw_df.empty:
        try:
            kpi_res = con.execute("""
            SELECT 
                ROUND(SUM(UNITS_SOLD * SELLING_PRICE), 2) AS Total_Sales,
                ROUND(SUM(UNITS_SOLD * SELLING_PRICE - UNITS_SOLD * COST), 2) AS Total_Margin,
                ROUND((SUM(UNITS_SOLD * SELLING_PRICE - UNITS_SOLD * COST) / NULLIF(SUM(UNITS_SOLD * SELLING_PRICE), 0)) * 100, 2) AS Margin_Rate,
                SUM(UNITS_SOLD) AS Total_Units
            FROM retail_sales;
            """).df()
            
            s_val = f"${kpi_res['Total_Sales'][0]:,.2f}"
            m_val = f"${kpi_res['Total_Margin'][0]:,.2f}"
            r_val = f"{kpi_res['Margin_Rate'][0]:.2f}%"
            u_val = f"{kpi_res['Total_Units'][0]:,}"
        except Exception:
            s_val, m_val, r_val, u_val = "$0", "$0", "0%", "0"
            
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.markdown(f"""
            <div class='metric-card'>
                <div class='metric-title'>Total Sales (2023-2024)</div>
                <div class='metric-value'>{s_val}</div>
            </div>
            """, unsafe_allow_html=True)
        with k2:
            st.markdown(f"""
            <div class='metric-card'>
                <div class='metric-title'>Total Gross Margin</div>
                <div class='metric-value'>{m_val}</div>
            </div>
            """, unsafe_allow_html=True)
        with k3:
            st.markdown(f"""
            <div class='metric-card'>
                <div class='metric-title'>Blended Margin Rate</div>
                <div class='metric-value'>{r_val}</div>
            </div>
            """, unsafe_allow_html=True)
        with k4:
            st.markdown(f"""
            <div class='metric-card'>
                <div class='metric-title'>Total Units Sold</div>
                <div class='metric-value'>{u_val}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # QUICK-PROMPT CHIPS / BUTTONS
    # --------------------------------------------------------------------------
    st.markdown("##### ⚡ Executive Quick Queries")
    prompt_cols = st.columns(4)
    quick_selected_prompt = None
    
    prompts = [
        "Which Product Divisions drove the highest margin in 2024 vs 2023?",
        "Which 5 brands had the lowest margin rates in 2024?",
        "List the products where unit cost increased faster than selling price in 2024.",
        "Break down total sales and margin rate by region and store size."
    ]
    
    labels = [
        "🏆 Margin by Division (2024 vs 2023)",
        "⚠️ 5 Lowest Margin Brands (2024)",
        "📉 Cost vs Price Squeeze Audit",
        "🏬 Sales & Margin by Region & Size"
    ]
    
    for idx, col in enumerate(prompt_cols):
        with col:
            if st.button(labels[idx], key=f"btn_quick_{idx}", use_container_width=True):
                quick_selected_prompt = prompts[idx]

    # Initialize chat history state
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    # --------------------------------------------------------------------------
    # DISPLAY CHAT HISTORY
    # --------------------------------------------------------------------------
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if "sql_query" in msg and msg["sql_query"]:
                with st.expander("🔍 View Generated SQL & Query Result", expanded=False):
                    st.code(msg["sql_query"], language="sql")
                    if "df" in msg and msg["df"] is not None and not msg["df"].empty:
                        st.dataframe(msg["df"], use_container_width=True)
                        if "elapsed_ms" in msg and msg["elapsed_ms"]:
                            st.caption(f"⚡ DuckDB execution time: {msg['elapsed_ms']} ms | Rows: {len(msg['df'])}")

    # --------------------------------------------------------------------------
    # CHAT INPUT & EXECUTION HANDLER
    # --------------------------------------------------------------------------
    user_prompt = st.chat_input("Ask a question about Canadian Tire sales, margin, stores, or products...")
    
    # Check if quick button triggered or typed query
    active_prompt = quick_selected_prompt or user_prompt

    if active_prompt:
        # Render user message
        st.session_state.chat_history.append({"role": "user", "content": active_prompt})
        with st.chat_message("user"):
            st.markdown(active_prompt)

        # Assistant thinking & execution
        with st.chat_message("assistant"):
            # Check if API Key provided
            if not api_key:
                # Check if this matches one of the 4 demo queries
                if active_prompt in DEMO_QUERIES:
                    demo = DEMO_QUERIES[active_prompt]
                    st.info("💡 **Demo / Pre-computed Mode Active** (Live in-memory DuckDB query executed. Set `GROQ_API_KEY` in environment or Streamlit Secrets for arbitrary queries).")
                    
                    # Execute SQL directly in DuckDB to ensure live in-memory execution
                    exec_res = execute_sql(con, demo["sql"])
                    final_text = demo["summary"]
                    sql_query = demo["sql"]
                    res_df = exec_res.get("df")
                    elapsed_ms = exec_res.get("elapsed_ms")
                    
                    st.markdown(final_text)
                    with st.expander("🔍 View Generated SQL & Query Result", expanded=True):
                        st.code(sql_query, language="sql")
                        if res_df is not None and not res_df.empty:
                            st.dataframe(res_df, use_container_width=True)
                            st.caption(f"⚡ DuckDB execution time: {elapsed_ms} ms | Rows: {len(res_df)}")
                            
                    st.session_state.chat_history.append({
                        "role": "assistant",
                        "content": final_text,
                        "sql_query": sql_query,
                        "df": res_df,
                        "elapsed_ms": elapsed_ms
                    })
                else:
                    st.warning("⚠️ Please configure `GROQ_API_KEY` or `OPENAI_API_KEY` in your environment or Streamlit Secrets to run custom arbitrary questions.")
            else:
                with st.spinner("DuckDB Agent is formulating SQL and computing answers..."):
                    try:
                        # Build message array for LLM
                        agent_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                        # Add last 6 turns for context
                        for past_msg in st.session_state.chat_history[-6:]:
                            if past_msg["role"] in ("user", "assistant"):
                                agent_messages.append({
                                    "role": past_msg["role"],
                                    "content": past_msg["content"]
                                })
                        
                        # Call agent runner
                        final_text, sql_query, res_df, elapsed_ms = run_agent_turn(
                            messages=agent_messages,
                            con=con,
                            api_key=api_key,
                            model_name=model_name,
                            base_url=base_url
                        )
                        
                        st.markdown(final_text)
                        
                        if sql_query:
                            with st.expander("🔍 View Generated SQL & Query Result", expanded=False):
                                st.code(sql_query, language="sql")
                                if res_df is not None and not res_df.empty:
                                    st.dataframe(res_df, use_container_width=True)
                                    if elapsed_ms:
                                        st.caption(f"⚡ DuckDB execution time: {elapsed_ms} ms | Rows: {len(res_df)}")
                                        
                                    # CSV Download Button
                                    csv_data = res_df.to_csv(index=False).encode("utf-8")
                                    st.download_button(
                                        label="📥 Download Data as CSV",
                                        data=csv_data,
                                        file_name="query_results.csv",
                                        mime="text/csv",
                                        key=f"dl_{len(st.session_state.chat_history)}"
                                    )

                        st.session_state.chat_history.append({
                            "role": "assistant",
                            "content": final_text,
                            "sql_query": sql_query,
                            "df": res_df,
                            "elapsed_ms": elapsed_ms
                        })
                    except Exception as err:
                        st.error(f"Agent Execution Error: {str(err)}")
                        st.info("Tip: Verify your API key and model selection in the sidebar.")


if __name__ == "__main__":
    main()
