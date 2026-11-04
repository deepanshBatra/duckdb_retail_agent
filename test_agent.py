"""
Offline tests for the retail agent (no API key needed).

Run either way:
    python test_agent.py
    pytest test_agent.py
The expected numbers are the validated figures from the case study results
(and the Power BI dashboard).
"""

import os

import agent_core as core

HERE = os.path.dirname(os.path.abspath(__file__))


def _cursor():
    with open(os.path.join(HERE, "retail_data.csv"), "rb") as f:
        df = core.load_dataframe(f.read(), "retail_data.csv")
    return core.build_database(df).cursor()


CUR = _cursor()


def close(a, b, tol=0.01):
    return abs(float(a) - float(b)) <= tol


# ---------------- Data + KPIs ----------------
def test_profile():
    p = core.profile_dataset(CUR)
    assert p["rows"] == 7310
    assert p["days"] == 731
    assert len(p["stores"]) == 10
    assert p["products"] == 165
    assert p["one_row_per_store_day"] is True
    assert p["one_price_per_product_year"] is True


def test_yearly_kpis():
    df = CUR.execute("SELECT * FROM yearly_kpis ORDER BY YEAR").df()
    a, b = df.iloc[0], df.iloc[1]
    assert close(a.sales, 1_023_950.39) and close(b.sales, 1_063_778.23)
    assert close(a.cogs, 517_989.13) and close(b.cogs, 533_349.22)
    assert close(a.margin, 505_961.26) and close(b.margin, 530_429.02)
    assert close(a.margin_rate_pct, 49.41) and close(b.margin_rate_pct, 49.86)
    assert int(a.units) == 10_868 and int(b.units) == 11_063


def test_division_yoy():
    df = CUR.execute("SELECT * FROM division_yoy").df().set_index("PRODUCT_DIVISION")
    assert close(df.loc["Sports", "margin_change"], 26_577.25)
    assert close(df.loc["Apparel", "margin_change"], -3_597.44)
    assert close(df.loc["Apparel", "sales_2024"], 270_445.46)
    assert close(df.loc["Sports", "margin_rate_2024"], 48.30)


def test_brand_yoy_is_weighted():
    df = CUR.execute("SELECT * FROM brand_yoy").df().set_index("BRAND")
    assert close(df.loc["Fynix", "price_change_pct"], 6.0) and close(df.loc["Fynix", "cost_change_pct"], 10.0)
    assert close(df.loc["Fynix", "margin_rate_change_bps"], -264.86)
    # Weighted Quanta change is +24 bps (the old SKU-average figure was -98).
    assert close(df.loc["Quanta", "margin_rate_change_bps"], 23.99)
    assert close(df.loc["Vetra", "units_change_pct"], 6.19)


def test_region_and_store_yoy():
    r = CUR.execute("SELECT * FROM region_yoy").df().set_index("REGION")
    assert int(r.loc["East", "store_count"]) == 4
    assert close(r.loc["East", "margin_2024"], 211_624.51)
    assert close(r.loc["West", "margin_change_pct"], 12.03)
    s = CUR.execute("SELECT * FROM store_yoy").df().set_index("STORE_NAME")
    assert close(s.loc["OBQH", "margin_change_pct"], -14.93)
    assert s.loc["MTMA", "STORE_FORMAT"] == "Mid-Size"
    assert s.loc["ABCX", "STORE_FORMAT"] == "Compact"
    f = CUR.execute("SELECT * FROM format_yoy").df().set_index("STORE_FORMAT")
    assert int(f.loc["Big-Box", "store_count"]) == 5
    # Two-year Compact $/sqft is 4.66 in results.md, so one year must be roughly half.
    assert close(f.loc["Compact", "margin_per_sqft_2023"] + f.loc["Compact", "margin_per_sqft_2024"], 4.66)


# ---------------- Margin bridge ----------------
def test_bridge_company():
    b = core.margin_bridge(CUR, "company")
    t = b["table"].iloc[0]
    assert close(t.price_effect, 22_340.88)
    assert close(t.cost_effect, -9_629.59)
    assert close(t.volume_effect, 9_078.25)
    assert close(t.mix_effect, 2_678.21)
    assert close(t.margin_change, 24_467.76)
    assert abs(t.check_difference) < 1e-6
    rs = b["rate_split"]
    assert close(rs["rate_change_bps"], 45.0, 0.1)
    assert close(rs["from_mix_bps"], 29.9, 0.1)
    assert close(rs["from_price_vs_cost_bps"], 15.1, 0.1)


def test_bridge_division():
    df = core.margin_bridge(CUR, "division")["table"].set_index("PRODUCT_DIVISION")
    assert close(df.loc["Sports", "price_effect"], 7_847.82)
    assert close(df.loc["Apparel", "cost_effect"], -552.13)
    assert close(df.loc["TOTAL", "volume_effect"], 2_273.41)
    assert (df.check_difference.abs() < 1e-6).all()


# ---------------- Guardrails ----------------
def test_blocks_file_reads():
    r = core.run_query(CUR, "SELECT * FROM read_text('.env')")
    assert not r["ok"]


def test_blocks_multiple_statements():
    r = core.run_query(CUR, "SELECT 1; CREATE TABLE pwned AS SELECT 1")
    assert not r["ok"]
    tables = [t[0] for t in CUR.execute("SELECT table_name FROM information_schema.tables").fetchall()]
    assert "pwned" not in tables


def test_blocks_writes_and_settings():
    for q in ["DROP VIEW retail_sales", "CREATE TABLE x AS SELECT 1", "SET enable_external_access = true",
              "ATTACH 'x.db'", "COPY retail_sales TO 'out.csv'", "INSTALL httpfs"]:
        assert not core.run_query(CUR, q)["ok"], q


def test_allows_normal_selects():
    assert core.run_query(CUR, "SELECT COUNT(*) AS n FROM retail_sales -- comment")["ok"]
    assert core.run_query(CUR, "WITH a AS (SELECT * FROM brand_yoy) SELECT BRAND FROM a;")["ok"]


def test_syntax_error_is_returned_not_raised():
    r = core.run_query(CUR, "SELEC * FROM retail_sales")
    assert not r["ok"] and "error" in r


def test_payload_truncation_flag():
    df = CUR.execute("SELECT * FROM retail_sales").df()
    p = core.df_to_payload(df)
    assert p["truncated"] is True and p["rows_returned"] == core.MAX_ROWS_TO_MODEL and p["row_count"] == 7310


# ---------------- Quick answers ----------------
def test_quick_answers_use_live_numbers():
    texts = {label: fn(CUR).text for label, _q, fn in core.QUICK_QUESTIONS}
    joined = "\n".join(texts.values())
    for s in ["$1,063,778.23", "+45 bps", "$26,577.25", "+$22,340.88", "-$9,629.59", "Fynix", "Quanta"]:
        assert s in joined, s


def test_clean_model_text():
    assert core.clean_model_text("<think>secret</think>Answer") == "Answer"
    assert core.clean_model_text("partial reasoning</think>\nAnswer") == "Answer"


def test_system_prompt_has_real_stores():
    prompt = core.build_system_prompt(core.profile_dataset(CUR))
    assert "OBQH" in prompt and "731 days" in prompt and "ONE row per store per day" in prompt


def test_display_markdown():
    raw = ("**Answer**  \n"
           "Sales were $1,063,778.23 and margin $530,429.02 (+4.84 %), cost ‑$9.6K.\n"
           "| A | B |\n|--|--|\n| 1 | 2 |")
    out = core.to_display_markdown(raw)
    assert "Answer" not in out
    assert r"\$1,063,778.23" in out and r"\$530,429.02" in out      # $ escaped, so no LaTeX
    assert " " not in out and "‑" not in out and r"-\$9.6K" in out
    assert "\n\n| A | B |" in out                                     # blank line before table
    assert core.to_display_markdown("Use `SELECT $1` here") == "Use `SELECT $1` here"   # code untouched
    assert core.to_display_markdown(r"already \$5") == r"already \$5"                   # no double escape


def test_bridge_rates_are_per_group():
    df = core.margin_bridge(CUR, "division")["table"].set_index("PRODUCT_DIVISION")
    assert close(df.loc["Apparel", "rate_change_bps"], 111.8, 0.2)
    assert close(df.loc["Tools", "rate_change_bps"], -189.9, 0.2)


def test_prompt_has_format_rules():
    prompt = core.build_system_prompt(core.profile_dataset(CUR))
    assert "Answer format" in prompt and "NEGATIVE cost effect" in prompt and "percentage points" in prompt


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
