"""
Core logic for the Canadian Tire Retail AI Copilot.

Everything here is UI-free so it can be tested on its own:
  * loading and validating the dataset
  * building a locked-down, read-only DuckDB database with helper views
  * the SQL guardrail and query runner
  * the margin-bridge tool (price / cost / volume / mix)
  * the system prompt and the tool-calling agent loop
  * deterministic answers for the quick questions (used when no API key is set)
"""

from __future__ import annotations

import io
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import duckdb
import numpy as np
import pandas as pd

# ==============================================================================
# 1. DATA LOADING
# ==============================================================================
REQUIRED_COLUMNS = [
    "YEAR", "QUARTER", "MONTH", "DATE", "STORE_ID", "STORE_NAME", "STORE_SIZE",
    "REGION", "PRODUCT_ID", "BRAND", "PRODUCT_CATEGORY", "PRODUCT_DIVISION",
    "PRODUCT_NAME", "SELLING_PRICE_PER_UNIT", "UNITS_SOLD", "COST_PER_UNIT",
]
_RENAMES = {"SELLING_PRICE": "SELLING_PRICE_PER_UNIT", "COST": "COST_PER_UNIT"}


def load_dataframe(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """Read a CSV or Excel file and check it has the columns the agent needs."""
    buf = io.BytesIO(file_bytes)
    df = pd.read_csv(buf) if file_name.lower().endswith(".csv") else pd.read_excel(buf)
    df.columns = [str(c).strip().upper() for c in df.columns]
    df = df.rename(columns={k: v for k, v in _RENAMES.items() if k in df.columns and v not in df.columns})
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"The file is missing these columns: {', '.join(missing)}")
    return df[REQUIRED_COLUMNS].copy()


# ==============================================================================
# 2. DATABASE + HELPER VIEWS
# ==============================================================================
_PRODUCT_DIMS = ["PRODUCT_ID", "PRODUCT_NAME", "BRAND", "PRODUCT_CATEGORY", "PRODUCT_DIVISION"]


def _yoy_view_sql(name: str, dims: List[str], extra_select: str = "", extra_cte: str = "") -> str:
    """Build a one-row-per-group view comparing 2023 with 2024."""
    d = ", ".join(dims)

    def y(expr: str, yr: int) -> str:
        return f"SUM(CASE WHEN YEAR = {yr} THEN {expr} END)"

    return f"""
CREATE OR REPLACE VIEW {name} AS
WITH base AS (
    SELECT {d},
        {y('SALES', 2023)} AS sales_2023,  {y('SALES', 2024)} AS sales_2024,
        {y('COGS', 2023)} AS cogs_2023,    {y('COGS', 2024)} AS cogs_2024,
        {y('MARGIN', 2023)} AS margin_2023, {y('MARGIN', 2024)} AS margin_2024,
        {y('UNITS_SOLD', 2023)} AS units_2023, {y('UNITS_SOLD', 2024)} AS units_2024
    FROM retail_sales
    GROUP BY {d}
){extra_cte}
SELECT base.*,
    (sales_2024 / NULLIF(sales_2023, 0) - 1) * 100            AS sales_change_pct,
    margin_2024 - margin_2023                                  AS margin_change,
    (margin_2024 / NULLIF(margin_2023, 0) - 1) * 100          AS margin_change_pct,
    margin_2023 / NULLIF(sales_2023, 0) * 100                 AS margin_rate_2023,
    margin_2024 / NULLIF(sales_2024, 0) * 100                 AS margin_rate_2024,
    (margin_2024 / NULLIF(sales_2024, 0) - margin_2023 / NULLIF(sales_2023, 0)) * 10000 AS margin_rate_change_bps,
    (units_2024 / NULLIF(units_2023, 0) - 1) * 100            AS units_change_pct
    {extra_select}
FROM base
"""


def build_database(df: pd.DataFrame) -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB database, add helper views, then lock it down."""
    con = duckdb.connect(database=":memory:")
    con.register("_src", df)
    con.execute("CREATE TABLE retail_raw AS SELECT * FROM _src")
    con.unregister("_src")

    con.execute("""
    CREATE OR REPLACE VIEW retail_sales AS
    SELECT
        CAST(YEAR AS INTEGER)               AS YEAR,
        CAST(QUARTER AS INTEGER)            AS QUARTER,
        CAST(MONTH AS INTEGER)              AS MONTH,
        CAST(DATE AS DATE)                  AS DATE,
        CAST(STORE_ID AS INTEGER)           AS STORE_ID,
        CAST(STORE_NAME AS VARCHAR)         AS STORE_NAME,
        CAST(STORE_SIZE AS BIGINT)          AS STORE_SIZE,
        CASE WHEN STORE_SIZE <= 30000 THEN 'Compact'
             WHEN STORE_SIZE <= 75000 THEN 'Mid-Size'
             ELSE 'Big-Box' END             AS STORE_FORMAT,
        CAST(REGION AS VARCHAR)             AS REGION,
        CAST(PRODUCT_ID AS INTEGER)         AS PRODUCT_ID,
        CAST(PRODUCT_NAME AS VARCHAR)       AS PRODUCT_NAME,
        CAST(BRAND AS VARCHAR)              AS BRAND,
        CAST(PRODUCT_CATEGORY AS VARCHAR)   AS PRODUCT_CATEGORY,
        CAST(PRODUCT_DIVISION AS VARCHAR)   AS PRODUCT_DIVISION,
        CAST(SELLING_PRICE_PER_UNIT AS DOUBLE) AS SELLING_PRICE_PER_UNIT,
        CAST(COST_PER_UNIT AS DOUBLE)       AS COST_PER_UNIT,
        CAST(UNITS_SOLD AS INTEGER)         AS UNITS_SOLD,
        UNITS_SOLD * SELLING_PRICE_PER_UNIT AS SALES,
        UNITS_SOLD * COST_PER_UNIT          AS COGS,
        UNITS_SOLD * (SELLING_PRICE_PER_UNIT - COST_PER_UNIT) AS MARGIN
    FROM retail_raw
    """)

    con.execute("""
    CREATE OR REPLACE VIEW yearly_kpis AS
    SELECT YEAR,
        SUM(SALES) AS sales, SUM(COGS) AS cogs, SUM(MARGIN) AS margin,
        SUM(MARGIN) / NULLIF(SUM(SALES), 0) * 100 AS margin_rate_pct,
        SUM(UNITS_SOLD) AS units, COUNT(*) AS row_count
    FROM retail_sales GROUP BY YEAR ORDER BY YEAR
    """)

    product_extra_cte = """,
prices AS (
    SELECT PRODUCT_ID,
        SUM(CASE WHEN YEAR = 2023 THEN SALES END) / NULLIF(SUM(CASE WHEN YEAR = 2023 THEN UNITS_SOLD END), 0) AS unit_price_2023,
        SUM(CASE WHEN YEAR = 2024 THEN SALES END) / NULLIF(SUM(CASE WHEN YEAR = 2024 THEN UNITS_SOLD END), 0) AS unit_price_2024,
        SUM(CASE WHEN YEAR = 2023 THEN COGS END)  / NULLIF(SUM(CASE WHEN YEAR = 2023 THEN UNITS_SOLD END), 0) AS unit_cost_2023,
        SUM(CASE WHEN YEAR = 2024 THEN COGS END)  / NULLIF(SUM(CASE WHEN YEAR = 2024 THEN UNITS_SOLD END), 0) AS unit_cost_2024
    FROM retail_sales GROUP BY PRODUCT_ID
)"""
    product_sql = _yoy_view_sql(
        "product_yoy", _PRODUCT_DIMS,
        extra_cte=product_extra_cte,
        extra_select=""",
    p.unit_price_2023, p.unit_price_2024, p.unit_cost_2023, p.unit_cost_2024,
    (p.unit_price_2024 / NULLIF(p.unit_price_2023, 0) - 1) * 100 AS price_change_pct,
    (p.unit_cost_2024  / NULLIF(p.unit_cost_2023, 0) - 1) * 100  AS cost_change_pct""",
    ).replace("FROM base\n", "FROM base JOIN prices p USING (PRODUCT_ID)\n")
    con.execute(product_sql)

    con.execute(_yoy_view_sql("division_yoy", ["PRODUCT_DIVISION"]))
    con.execute(_yoy_view_sql("category_yoy", ["PRODUCT_DIVISION", "PRODUCT_CATEGORY"]))
    con.execute(_yoy_view_sql("region_yoy", ["REGION"], extra_select=",\n    s.store_count",
                              extra_cte=",\nstores AS (SELECT REGION, COUNT(DISTINCT STORE_ID) AS store_count FROM retail_sales GROUP BY REGION)")
                .replace("FROM base\n", "FROM base JOIN stores s USING (REGION)\n"))
    con.execute(_yoy_view_sql("store_yoy", ["STORE_ID", "STORE_NAME", "REGION", "STORE_SIZE", "STORE_FORMAT"],
                              extra_select=""",
    sales_2023  / NULLIF(STORE_SIZE, 0) AS sales_per_sqft_2023,
    sales_2024  / NULLIF(STORE_SIZE, 0) AS sales_per_sqft_2024,
    margin_2023 / NULLIF(STORE_SIZE, 0) AS margin_per_sqft_2023,
    margin_2024 / NULLIF(STORE_SIZE, 0) AS margin_per_sqft_2024"""))
    con.execute("""
    CREATE OR REPLACE VIEW format_yoy AS
    SELECT STORE_FORMAT,
        COUNT(*) AS store_count, SUM(STORE_SIZE) AS total_sqft,
        SUM(sales_2023) AS sales_2023, SUM(sales_2024) AS sales_2024,
        SUM(margin_2023) AS margin_2023, SUM(margin_2024) AS margin_2024,
        SUM(margin_2024) / COUNT(*) AS margin_per_store_2024,
        SUM(margin_2024) / NULLIF(SUM(sales_2024), 0) * 100 AS margin_rate_2024,
        SUM(sales_2024)  / NULLIF(SUM(STORE_SIZE), 0) AS sales_per_sqft_2024,
        SUM(margin_2023) / NULLIF(SUM(STORE_SIZE), 0) AS margin_per_sqft_2023,
        SUM(margin_2024) / NULLIF(SUM(STORE_SIZE), 0) AS margin_per_sqft_2024
    FROM store_yoy GROUP BY STORE_FORMAT
    """)
    con.execute(_yoy_view_sql("brand_yoy", ["BRAND"],
                              extra_cte=""",
pp AS (SELECT BRAND, COUNT(*) AS sku_count,
              AVG(price_change_pct) AS price_change_pct,
              AVG(cost_change_pct)  AS cost_change_pct
       FROM product_yoy GROUP BY BRAND)""",
                              extra_select=",\n    pp.sku_count, pp.price_change_pct, pp.cost_change_pct")
                .replace("FROM base\n", "FROM base JOIN pp USING (BRAND)\n"))

    # Lock it down: no file / network access and the setting cannot be turned back on.
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def profile_dataset(cur: duckdb.DuckDBPyConnection) -> Dict[str, Any]:
    """Facts about the loaded data, used in the prompt and the sidebar."""
    q = lambda s: cur.execute(s).fetchall()
    rows, d_min, d_max, days = q("SELECT COUNT(*), MIN(DATE), MAX(DATE), COUNT(DISTINCT DATE) FROM retail_sales")[0]
    stores = q("SELECT DISTINCT STORE_ID, STORE_NAME, REGION, STORE_SIZE, STORE_FORMAT FROM retail_sales ORDER BY STORE_ID")
    max_rows_store_day = q("SELECT MAX(n) FROM (SELECT COUNT(*) n FROM retail_sales GROUP BY DATE, STORE_ID)")[0][0]
    max_prices = q("SELECT MAX(n) FROM (SELECT COUNT(DISTINCT SELLING_PRICE_PER_UNIT) n FROM retail_sales GROUP BY PRODUCT_ID, YEAR)")[0][0]
    return {
        "rows": rows,
        "date_min": str(d_min), "date_max": str(d_max), "days": days,
        "years": [r[0] for r in q("SELECT DISTINCT YEAR FROM retail_sales ORDER BY 1")],
        "stores": stores,
        "regions": [r[0] for r in q("SELECT DISTINCT REGION FROM retail_sales ORDER BY 1")],
        "brands": [r[0] for r in q("SELECT DISTINCT BRAND FROM retail_sales ORDER BY 1")],
        "divisions": q("SELECT PRODUCT_DIVISION, STRING_AGG(DISTINCT PRODUCT_CATEGORY, ', ' ORDER BY PRODUCT_CATEGORY) FROM retail_sales GROUP BY 1 ORDER BY 1"),
        "products": q("SELECT COUNT(DISTINCT PRODUCT_ID) FROM retail_sales")[0][0],
        "one_row_per_store_day": max_rows_store_day == 1,
        "one_price_per_product_year": max_prices == 1,
    }


# ==============================================================================
# 3. SQL GUARDRAIL + QUERY RUNNER
# ==============================================================================
MAX_ROWS_TO_MODEL = 100


def validate_sql(query: str) -> str:
    """Return the cleaned query, or raise ValueError if it is not a single SELECT."""
    clean = (query or "").strip().rstrip(";").strip()
    if not clean:
        raise ValueError("The query is empty.")
    try:
        statements = duckdb.extract_statements(clean)
    except Exception as e:  # parser error: hand it back to the model to fix
        raise ValueError(f"SQL syntax error: {e}") from None
    if len(statements) != 1:
        raise ValueError("Only one SQL statement is allowed per call.")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise ValueError("Only read-only SELECT queries (including WITH ... SELECT) are allowed.")
    return clean


def run_query(cur: duckdb.DuckDBPyConnection, query: str) -> Dict[str, Any]:
    """Validate and run a query. Never raises: errors come back in the result."""
    try:
        clean = validate_sql(query)
    except ValueError as e:
        return {"ok": False, "sql": query, "error": str(e)}
    try:
        t0 = time.perf_counter()
        df = cur.execute(clean).df()
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return {"ok": True, "sql": clean, "df": df, "ms": ms}
    except Exception as e:
        return {"ok": False, "sql": clean, "error": f"DuckDB error: {e}"}


def df_to_payload(df: pd.DataFrame, max_rows: int = MAX_ROWS_TO_MODEL) -> Dict[str, Any]:
    """Compact JSON-safe version of a result for the model, with a truncation flag."""
    head = df.head(max_rows).copy()
    for c in head.select_dtypes(include="float").columns:
        head[c] = head[c].round(4)
    records = json.loads(head.to_json(orient="records", date_format="iso"))
    return {
        "row_count": int(len(df)),
        "rows_returned": int(len(head)),
        "truncated": bool(len(df) > max_rows),
        "columns": [str(c) for c in df.columns],
        "rows": records,
    }


# ==============================================================================
# 4. MARGIN BRIDGE TOOL (price / cost / volume / mix)
# ==============================================================================
BRIDGE_LEVELS = {
    "company": None,
    "division": "PRODUCT_DIVISION",
    "category": "PRODUCT_CATEGORY",
    "brand": "BRAND",
}


def margin_bridge(cur: duckdb.DuckDBPyConnection, level: str = "company") -> Dict[str, Any]:
    """
    Split the 2023 -> 2024 margin change into four parts, product by product:
      price  = (price_2024 - price_2023) * units_2024
      cost   = -(cost_2024 - cost_2023)  * units_2024
      volume = units_2023 * group_growth * unit_margin_2023
      mix    = (units_2024 - units_2023) * unit_margin_2023 - volume
    group_growth is the unit growth of the whole company, or of each group when a level is given.
    The four parts add up exactly to the margin change.
    """
    level = (level or "company").lower()
    if level not in BRIDGE_LEVELS:
        raise ValueError(f"level must be one of {list(BRIDGE_LEVELS)}")
    col = BRIDGE_LEVELS[level]

    p = cur.execute("SELECT * FROM product_yoy").df()
    p = p.dropna(subset=["units_2023", "units_2024"])
    m23 = p.unit_price_2023 - p.unit_cost_2023
    p["price_effect"] = (p.unit_price_2024 - p.unit_price_2023) * p.units_2024
    p["cost_effect"] = -(p.unit_cost_2024 - p.unit_cost_2023) * p.units_2024
    if col:
        g = p.groupby(col)[["units_2023", "units_2024"]].transform("sum")
        growth = g.units_2024 / g.units_2023 - 1
    else:
        growth = p.units_2024.sum() / p.units_2023.sum() - 1
    p["volume_effect"] = p.units_2023 * growth * m23
    p["mix_effect"] = (p.units_2024 - p.units_2023) * m23 - p["volume_effect"]

    cols = ["margin_2023", "price_effect", "cost_effect", "volume_effect", "mix_effect", "margin_2024"]
    sums = cols + ["sales_2023", "sales_2024"]
    if col:
        out = p.groupby(col)[sums].sum().reset_index()
        total = out[sums].sum().to_frame().T
        total.insert(0, col, "TOTAL")
        out = pd.concat([out, total], ignore_index=True)
    else:
        out = p[sums].sum().to_frame().T
        out.insert(0, "level", "Company")
    out["margin_change"] = out.margin_2024 - out.margin_2023
    # Each row's own margin rate, so a group's rate change is never confused with the company's.
    out["margin_rate_2023"] = out.margin_2023 / out.sales_2023 * 100
    out["margin_rate_2024"] = out.margin_2024 / out.sales_2024 * 100
    out["rate_change_bps"] = (out.margin_rate_2024 - out.margin_rate_2023) * 100
    out = out.drop(columns=["sales_2023", "sales_2024"])
    out["check_difference"] = (out.price_effect + out.cost_effect + out.volume_effect + out.mix_effect - out.margin_change).round(6)

    # Margin-rate split: how much of the rate change is mix vs price/cost.
    r23 = p.margin_2023.sum() / p.sales_2023.sum()
    r24 = p.margin_2024.sum() / p.sales_2024.sum()
    r_mix = (p.units_2024 * m23).sum() / (p.units_2024 * p.unit_price_2023).sum()
    rate_split = {
        "margin_rate_2023_pct": r23 * 100,
        "margin_rate_2024_pct": r24 * 100,
        "rate_change_bps": (r24 - r23) * 1e4,
        "from_mix_bps": (r_mix - r23) * 1e4,
        "from_price_vs_cost_bps": (r24 - r_mix) * 1e4,
    }
    note = ("Volume uses one company-wide unit growth rate." if not col else
            f"Volume uses each {level}'s own unit growth rate, so mix only shows shifts inside a {level}. "
            "Price and cost totals are the same at every level; the volume/mix split is not.")
    return {"table": out, "rate_split": rate_split, "note": note, "level": level}


# ==============================================================================
# 5. SYSTEM PROMPT
# ==============================================================================
def build_system_prompt(profile: Dict[str, Any]) -> str:
    stores = "\n".join(f"  - {sid}: {name} ({region}, {size:,} sq ft, {fmt})" for sid, name, region, size, fmt in profile["stores"])
    divisions = "\n".join(f"  - {d}: {cats}" for d, cats in profile["divisions"])
    facts = []
    if profile["one_row_per_store_day"]:
        facts.append(
            f"- The data has exactly ONE row per store per day ({profile['rows']:,} rows = stores x days). "
            "A row is one product sold in one store on one day (1-5 units). Do NOT call rows 'transactions' or 'baskets'.")
        facts.append(
            "- Because every store has the same number of rows, every store sells about the same amount whatever its size. "
            "Sales or margin per square foot mostly reflects store size, not performance. Do not recommend store-format "
            "or real-estate changes from this data; say this caveat if asked about store size or $/sq ft.")
        facts.append(
            "- Monthly patterns by category do not repeat between 2023 and 2024, so treat monthly 'seasonality' as noise "
            "and say so if asked.")
    if profile["one_price_per_product_year"]:
        facts.append(
            "- Each product has one selling price and one unit cost per year. Price and cost changes are set per brand "
            "(every product in a brand has the same % change). Use product_yoy / brand_yoy for them.")
    facts.append(
        "- Regions have different store counts. When comparing regions, also give margin per store "
        "(margin / store_count) so a region doesn't look better just because it has more stores.")
    facts_txt = "\n".join(facts)

    return f"""You are the retail analytics copilot for Canadian Tire leaders. You answer questions about sales, cost and margin
by querying a DuckDB database with tools. Every number in your answer must come from a tool result.
Never do arithmetic yourself: if you need a share, ratio, difference, sum or percentage, compute it in SQL and quote it.

## Data loaded
- {profile['rows']:,} rows, {profile['date_min']} to {profile['date_max']} ({profile['days']} days), years {profile['years']}.
- {len(profile['stores'])} stores:
{stores}
- Regions: {', '.join(profile['regions'])}
- Brands: {', '.join(profile['brands'])}
- {profile['products']} products. Divisions and their categories:
{divisions}

## Tables and views (all read-only)
retail_sales: one row per store per day.
  YEAR, QUARTER, MONTH, DATE, STORE_ID, STORE_NAME, STORE_SIZE (sq ft), STORE_FORMAT ('Compact' <=30k, 'Mid-Size' <=75k, 'Big-Box'),
  REGION, PRODUCT_ID, PRODUCT_NAME, BRAND, PRODUCT_CATEGORY, PRODUCT_DIVISION,
  SELLING_PRICE_PER_UNIT, COST_PER_UNIT, UNITS_SOLD,
  SALES (= units x price), COGS (= units x cost), MARGIN (= SALES - COGS)  <- already computed per row
yearly_kpis: YEAR, sales, cogs, margin, margin_rate_pct, units, row_count
Year-over-year views, one row per group, all with these columns:
  sales_2023, sales_2024, sales_change_pct, cogs_2023, cogs_2024, margin_2023, margin_2024, margin_change, margin_change_pct,
  margin_rate_2023, margin_rate_2024 (percent), margin_rate_change_bps, units_2023, units_2024, units_change_pct
  - division_yoy  (PRODUCT_DIVISION)
  - category_yoy  (PRODUCT_DIVISION, PRODUCT_CATEGORY)
  - brand_yoy     (BRAND) + sku_count, price_change_pct, cost_change_pct
  - region_yoy    (REGION) + store_count
  - store_yoy     (STORE_ID, STORE_NAME, REGION, STORE_SIZE, STORE_FORMAT)
                  + sales_per_sqft_2023/2024, margin_per_sqft_2023/2024 (one year each)
format_yoy: one row per STORE_FORMAT: store_count, total_sqft, sales_2023/2024, margin_2023/2024, margin_per_store_2024,
  margin_rate_2024, sales_per_sqft_2024, margin_per_sqft_2023/2024
  - product_yoy   (PRODUCT_ID, PRODUCT_NAME, BRAND, PRODUCT_CATEGORY, PRODUCT_DIVISION)
                  + unit_price_2023/2024, unit_cost_2023/2024, price_change_pct, cost_change_pct
Prefer these views over writing long CASE WHEN queries.

## Rules for numbers
- Margin rate = SUM(MARGIN) / SUM(SALES). Never average rates, prices or costs across rows or products.
- A brand's or division's margin-rate change must be weighted (use margin_rate_change_bps from the views), not an average of product changes.
- 1 bp = 0.01 percentage points. Rate changes are in bps; dollar and percent changes as $ and %.
- For "why did margin change" questions, call the margin_bridge tool. Do not build the price/volume/mix split yourself.
- Per-square-foot numbers: use store_yoy or format_yoy only. Never divide row-level values by STORE_SIZE and never
  average per-row ratios; a group's $/sq ft is SUM(margin) / SUM(STORE_SIZE) over distinct stores. Say which year it covers.
- Use NULLIF in denominators. ROUND outputs to 2 decimals.
- Give exact values to 2 decimals (e.g. $1,063,778.23, 49.86%). Do not round to K or M unless asked.

## Things to know about this data
{facts_txt}

## Reading the numbers correctly
- "% change" (e.g. margin +12.03%) is NOT "percentage points". Percentage points / bps are only for changes in a RATE.
- Bridge signs: a NEGATIVE cost effect means unit costs ROSE (it hurt margin); a positive one means costs fell.
  Negative volume = fewer units; negative mix = sales shifted toward lower-margin products.
- Only quote numbers for the level asked about. Never present a company total (e.g. the company's +45 bps) as a
  division's, brand's or store's figure; query that group's own value.
- "Margin" means gross margin dollars; "margin rate" means margin / sales.

## How to answer
1. Run the query or tool first. If it errors, read the error and fix the query.
2. If the question is about store size / format productivity or monthly seasonality, put the data caveat in your FIRST
   sentence and do not recommend opening, closing or resizing stores, or seasonal stock moves, from this data.
3. Do not invent causes (weather, promotions, etc.) that are not in the data; say what the data shows and what it can't.

## Answer format (rendered as Markdown in a chat window)
- EXCEPTION, store size / format / per-sq-ft or seasonality questions: your first sentence must be the caveat that
  this data can't judge it (see the store-size example below), and only then the numbers.
- Open with the answer itself in 1-2 plain sentences. No heading, no "Answer:" or "**Answer**" label, no preamble
  like "Based on the data". Bold only the one or two key figures or names, never a whole sentence.
- Then, if there are 3+ numbers to compare, ONE markdown table:
  - a blank line before it; at most 6 columns and 10 rows (say "top 10 of N" if you cut rows);
  - short headers with units, e.g. `Margin 2024 ($)`, `Change (%)`, `Rate change (bps)`;
  - numbers formatted as $1,234.56 / 12.03% / +45 bps, with a sign on changes (+/-), no bold inside the table.
- Optionally finish with ONE line in italics: a caveat or a suggested next step. Don't repeat the table in prose.
- Keep it under about 150 words unless the user asks for detail. No emojis, no code blocks, no SQL (the app shows
  the SQL separately), no LaTeX.
- Use plain ASCII: normal spaces and the normal hyphen "-" (never non-breaking or narrow spaces/hyphens).

Example of a good answer:
West grew margin fastest in 2024, up **+12.03%** (+$11,671.92) to $108,719.61.

| Region | Stores | Margin 2024 ($) | Change ($) | Change (%) |
|:--|--:|--:|--:|--:|
| West | 2 | $108,719.61 | +$11,671.92 | +12.03% |
| South | 1 | $52,349.44 | +$4,974.47 | +10.50% |

*West and South have fewer stores, so compare margin per store before reallocating budget.*

Example for a store-size question (caveat first):
Store size can't be judged from this data: every store has one row per day, so each earns about the same
(**~$52K** of margin in 2024) whatever its size. Per square foot, Compact looks highest only because its stores are smaller.
"""


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "Run ONE read-only DuckDB SELECT query against retail_sales or the helper views. Returns up to 100 rows plus row_count and a truncated flag.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "A single SELECT (or WITH ... SELECT) statement."}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "margin_bridge",
            "description": ("Split the 2023 -> 2024 margin change into price, cost, volume and mix effects (they add up exactly). "
                            "Also returns how much of the margin-RATE change came from mix vs price-vs-cost. "
                            "level='company' for the overall bridge, or 'division' / 'category' / 'brand' for one row per group."),
            "parameters": {
                "type": "object",
                "properties": {"level": {"type": "string", "enum": list(BRIDGE_LEVELS)}},
                "required": ["level"],
            },
        },
    },
]


# ==============================================================================
# 6. AGENT LOOP
# ==============================================================================
@dataclass
class Step:
    tool: str
    sql: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    df: Optional[pd.DataFrame] = None
    ms: Optional[float] = None
    error: Optional[str] = None


@dataclass
class AgentResult:
    text: str
    steps: List[Step] = field(default_factory=list)
    hit_step_limit: bool = False


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def clean_model_text(text: Optional[str]) -> str:
    """Remove reasoning blocks that some models put in their answer."""
    text = _THINK_RE.sub("", text or "")
    text = re.sub(r"^.*?</think>", "", text, flags=re.DOTALL)  # unclosed opening tag at the top
    return text.strip()


_UNICODE_FIXES = {
    " ": " ", " ": " ", " ": " ", " ": " ",   # narrow / non-breaking / thin spaces
    "‑": "-", "‐": "-", "−": "-",                   # non-breaking hyphen, hyphen, minus sign
}
_CODE_SPLIT_RE = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)
_LABEL_RE = re.compile(r"^\s*(\*\*|#+\s*)?(answer|short answer|executive summary)\s*:?\s*(\*\*)?\s*:?\s*\n", re.IGNORECASE)


def to_display_markdown(text: str) -> str:
    """
    Make model (or quick-answer) text safe for st.markdown:
      * strip reasoning blocks and a leading "**Answer**" label
      * normalise unusual spaces / hyphens
      * escape $ so Streamlit doesn't render "$1 ... $2" as LaTeX math
      * make sure tables start after a blank line so they render as tables
    Code spans and blocks are left untouched.
    """
    text = clean_model_text(text)
    for bad, good in _UNICODE_FIXES.items():
        text = text.replace(bad, good)
    text = re.sub(r"[–—](?=\s?[\$\d])", "-", text)  # en/em dash used as a minus sign
    text = _LABEL_RE.sub("", text, count=1)

    parts = _CODE_SPLIT_RE.split(text)
    for i in range(0, len(parts), 2):  # even indexes are outside code
        parts[i] = re.sub(r"(?<!\\)\$", r"\\$", parts[i])
    text = "".join(parts)

    lines, out = text.split("\n"), []
    for ln in lines:
        if ln.lstrip().startswith("|") and out and out[-1].strip() and not out[-1].lstrip().startswith("|"):
            out.append("")
        out.append(ln)
    return "\n".join(out).strip()


def execute_tool(cur: duckdb.DuckDBPyConnection, name: str, args: Dict[str, Any]) -> (Step, str):
    """Run one tool call. Returns the step (for display) and the JSON string for the model."""
    if name == "run_sql":
        res = run_query(cur, args.get("query", ""))
        if res["ok"]:
            step = Step("run_sql", sql=res["sql"], df=res["df"], ms=res["ms"])
            return step, json.dumps({"status": "ok", **df_to_payload(res["df"])})
        step = Step("run_sql", sql=res["sql"], error=res["error"])
        return step, json.dumps({"status": "error", "error": res["error"],
                                 "hint": "Fix the query using the schema in the system prompt and try again."})
    if name == "margin_bridge":
        level = args.get("level", "company")
        try:
            t0 = time.perf_counter()
            b = margin_bridge(cur, level)
            ms = round((time.perf_counter() - t0) * 1000, 1)
        except Exception as e:
            return Step("margin_bridge", args=args, error=str(e)), json.dumps({"status": "error", "error": str(e)})
        step = Step("margin_bridge", args={"level": level}, df=b["table"], ms=ms)
        rate = {k: round(float(v), 2) for k, v in b["rate_split"].items()}
        return step, json.dumps({"status": "ok", "note": b["note"],
                                 "company_wide_rate_split": rate,
                                 "rate_split_note": ("company_wide_rate_split is for the WHOLE company only. For a group's own "
                                                     "rate change use rate_change_bps in that group's row."),
                                 **df_to_payload(b["table"])})
    return Step(name, args=args, error="Unknown tool"), json.dumps({"status": "error", "error": f"Unknown tool {name}"})


def history_to_messages(chat_history: List[Dict[str, Any]], max_turns: int = 6) -> List[Dict[str, str]]:
    """Turn saved chat history into model messages, including the SQL each answer used."""
    msgs = []
    for m in chat_history[-max_turns:]:
        content = m["content"]
        if m["role"] == "assistant" and m.get("steps"):
            sqls = [s.sql for s in m["steps"] if s.sql and not s.error]
            bridges = [s.args.get("level") for s in m["steps"] if s.tool == "margin_bridge" and not s.error]
            extra = []
            if sqls:
                extra.append("SQL used for this answer:\n" + "\n---\n".join(sqls)[:3000])
            if bridges:
                extra.append(f"margin_bridge called with level={bridges}")
            if extra:
                content = content + "\n\n[" + "\n".join(extra) + "]"
        msgs.append({"role": m["role"], "content": content})
    return msgs


def run_agent(client, model: str, system_prompt: str, history: List[Dict[str, str]],
              cur: duckdb.DuckDBPyConnection, max_steps: int = 6, temperature: float = 0.1) -> AgentResult:
    """Tool-calling loop. The model can call tools up to max_steps times, then must answer."""
    conversation: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}] + list(history)
    steps: List[Step] = []

    for _ in range(max_steps):
        resp = client.chat.completions.create(
            model=model, messages=conversation, tools=TOOLS, tool_choice="auto", temperature=temperature)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return AgentResult(clean_model_text(msg.content), steps)

        conversation.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                           for tc in msg.tool_calls],
        })
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as e:
                step, payload = Step(tc.function.name, error=f"Bad JSON arguments: {e}"), json.dumps(
                    {"status": "error", "error": f"Arguments were not valid JSON: {e}"})
            else:
                step, payload = execute_tool(cur, tc.function.name, args)
            steps.append(step)
            conversation.append({"role": "tool", "tool_call_id": tc.id, "content": payload})

    # Step limit reached: ask for an answer with what we have.
    conversation.append({"role": "user", "content":
                         "You have reached the tool-call limit. Answer now using only the results above. "
                         "If they are not enough, say what is missing."})
    resp = client.chat.completions.create(model=model, messages=conversation, temperature=temperature)
    return AgentResult(clean_model_text(resp.choices[0].message.content), steps, hit_step_limit=True)


# ==============================================================================
# 7. QUICK QUESTIONS (deterministic answers, no LLM needed)
# ==============================================================================
def _money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def _signed_money(x: float) -> str:
    return ("+" if x >= 0 else "-") + f"${abs(x):,.2f}"


def _pct(x: float, signed: bool = True) -> str:
    return f"{x:+.2f}%" if signed else f"{x:.2f}%"


def _bps(x: float) -> str:
    return f"{x:+.0f} bps"


def quick_yearly(cur) -> AgentResult:
    sql = "SELECT * FROM yearly_kpis ORDER BY YEAR"
    r = run_query(cur, sql)
    df = r["df"]
    a, b = df.iloc[0], df.iloc[1]
    text = (
        f"In {int(b.YEAR)}, sales were **{_money(b.sales)}** ({_pct((b.sales / a.sales - 1) * 100)} vs {int(a.YEAR)}) "
        f"and margin was **{_money(b.margin)}** ({_pct((b.margin / a.margin - 1) * 100)}). "
        f"Margin grew faster than sales, so the margin rate rose from {_pct(a.margin_rate_pct, False)} to "
        f"**{_pct(b.margin_rate_pct, False)}** ({_bps((b.margin_rate_pct - a.margin_rate_pct) * 100)}).\n\n"
        f"| | {int(a.YEAR)} | {int(b.YEAR)} | Change |\n|:--|--:|--:|--:|\n"
        f"| Sales | {_money(a.sales)} | {_money(b.sales)} | {_pct((b.sales / a.sales - 1) * 100)} |\n"
        f"| COGS | {_money(a.cogs)} | {_money(b.cogs)} | {_pct((b.cogs / a.cogs - 1) * 100)} |\n"
        f"| Margin | {_money(a.margin)} | {_money(b.margin)} | {_pct((b.margin / a.margin - 1) * 100)} |\n"
        f"| Margin rate | {_pct(a.margin_rate_pct, False)} | {_pct(b.margin_rate_pct, False)} | {_bps((b.margin_rate_pct - a.margin_rate_pct) * 100)} |\n"
        f"| Units | {int(a.units):,} | {int(b.units):,} | {_pct((b.units / a.units - 1) * 100)} |"
    )
    return AgentResult(text, [Step("run_sql", sql=r["sql"], df=df, ms=r["ms"])])


def quick_divisions(cur) -> AgentResult:
    sql = ("SELECT PRODUCT_DIVISION, ROUND(margin_2023, 2) AS margin_2023, ROUND(margin_2024, 2) AS margin_2024, "
           "ROUND(margin_change, 2) AS margin_change, ROUND(margin_change_pct, 2) AS margin_change_pct, "
           "ROUND(margin_rate_2024, 2) AS margin_rate_2024, ROUND(margin_rate_change_bps, 0) AS margin_rate_change_bps "
           "FROM division_yoy ORDER BY margin_change DESC")
    r = run_query(cur, sql)
    df = r["df"]
    total = df.margin_change.sum()
    top, worst = df.iloc[0], df.iloc[-1]
    rows = "\n".join(
        f"| {x.PRODUCT_DIVISION} | {_money(x.margin_2024)} | {_signed_money(x.margin_change)} | {_pct(x.margin_change_pct)} | {_bps(x.margin_rate_change_bps)} |"
        for x in df.itertuples())
    text = (
        f"**{top.PRODUCT_DIVISION}** drove margin growth: it added {_signed_money(top.margin_change)} "
        f"({_pct(top.margin_change_pct)}), which is {top.margin_change / total * 100:.1f}% of the company's net gain of "
        f"{_signed_money(total)}. **{worst.PRODUCT_DIVISION}** fell the most ({_signed_money(worst.margin_change)}).\n\n"
        f"| Division | Margin 2024 | Change | Change % | Rate change |\n|:--|--:|--:|--:|--:|\n{rows}"
    )
    return AgentResult(text, [Step("run_sql", sql=r["sql"], df=df, ms=r["ms"])])


def quick_bridge(cur) -> AgentResult:
    t0 = time.perf_counter()
    b = margin_bridge(cur, "company")
    ms = round((time.perf_counter() - t0) * 1000, 1)
    t = b["table"].iloc[0]
    rs = b["rate_split"]
    effects = {"price": t.price_effect, "cost": t.cost_effect, "volume": t.volume_effect, "mix": t.mix_effect}
    biggest = max(effects, key=lambda k: abs(effects[k]))
    direction = "grew" if t.margin_change >= 0 else "fell"
    cover = ""
    if t.price_effect > 0 and t.cost_effect < 0:
        cover = (" Price increases more than covered higher costs." if t.price_effect > -t.cost_effect
                 else " Price increases did not fully cover higher costs.")
    text = (
        f"Margin {direction} by **{_signed_money(t.margin_change)}**, from {_money(t.margin_2023)} to {_money(t.margin_2024)}. "
        f"The biggest single driver was the **{biggest} effect** ({_signed_money(effects[biggest])}).{cover}\n\n"
        f"| Step | Amount |\n|:--|--:|\n"
        f"| 2023 margin | {_money(t.margin_2023)} |\n"
        f"| Price effect | {_signed_money(t.price_effect)} |\n"
        f"| Cost effect | {_signed_money(t.cost_effect)} |\n"
        f"| Volume effect | {_signed_money(t.volume_effect)} |\n"
        f"| Mix effect | {_signed_money(t.mix_effect)} |\n"
        f"| **2024 margin** | **{_money(t.margin_2024)}** |\n\n"
        f"The margin rate moved {_bps(rs['rate_change_bps'])}: about {_bps(rs['from_mix_bps'])} from selling more "
        f"higher-margin products (mix) and {_bps(rs['from_price_vs_cost_bps'])} from prices rising faster than costs."
    )
    return AgentResult(text, [Step("margin_bridge", args={"level": "company"}, df=b["table"], ms=ms)])


def quick_squeeze(cur) -> AgentResult:
    sql = ("SELECT BRAND, sku_count, ROUND(price_change_pct, 2) AS price_change_pct, ROUND(cost_change_pct, 2) AS cost_change_pct, "
           "ROUND(margin_rate_2023, 2) AS margin_rate_2023, ROUND(margin_rate_2024, 2) AS margin_rate_2024, "
           "ROUND(margin_rate_change_bps, 0) AS margin_rate_change_bps, ROUND(units_change_pct, 2) AS units_change_pct "
           "FROM brand_yoy ORDER BY cost_change_pct - price_change_pct DESC")
    r = run_query(cur, sql)
    df = r["df"]
    sq = df[df.cost_change_pct > df.price_change_pct + 1e-9]
    if sq.empty:
        return AgentResult("No brand had unit costs rise faster than selling prices.", [Step("run_sql", sql=r["sql"], df=df, ms=r["ms"])])
    lines = "\n".join(
        f"| {x.BRAND} | {x.sku_count} | {_pct(x.price_change_pct)} | {_pct(x.cost_change_pct)} | "
        f"{_pct(x.margin_rate_2023, False)} → {_pct(x.margin_rate_2024, False)} ({_bps(x.margin_rate_change_bps)}) |"
        for x in sq.itertuples())
    names = " and ".join(sq.BRAND)
    first = sq.iloc[0]
    text = (
        f"{len(sq)} brand(s) had costs rise faster than prices: **{names}** "
        f"({int(sq.sku_count.sum())} products). The worst is **{first.BRAND}**: cost {_pct(first.cost_change_pct)} vs price "
        f"{_pct(first.price_change_pct)}, so its margin rate moved {_bps(first.margin_rate_change_bps)}.\n\n"
        f"| Brand | Products | Price change | Cost change | Margin rate 2023 → 2024 |\n|:--|--:|--:|--:|:--|\n{lines}\n\n"
        "The rate change is the brand's actual weighted rate, so it also includes shifts in which of its products sold more."
    )
    return AgentResult(text, [Step("run_sql", sql=r["sql"], df=df, ms=r["ms"])])


QUICK_QUESTIONS = [
    ("📊 2024 vs 2023", "How did 2024 compare with 2023?", quick_yearly),
    ("🏆 Divisions driving margin", "Which divisions drove margin growth in 2024?", quick_divisions),
    ("🌉 Why margin grew", "Why did margin grow in 2024? Split it into price, cost, volume and mix.", quick_bridge),
    ("📉 Brands squeezed by cost", "Which brands were squeezed by cost increases in 2024?", quick_squeeze),
]
