"""
Test script to verify DuckDB view creation and SQL queries for the 4 prompt questions.
"""
import duckdb
import pandas as pd

def run_tests():
    # 1. Connect and initialize view
    con = duckdb.connect(database=':memory:')
    
    # Load from retail_data.csv or Excel
    df = pd.read_csv('duckdb_retail_agent/retail_data.csv')
    con.register('raw_table', df)
    
    # Create standardized view with aliasing
    con.execute("""
    CREATE OR REPLACE VIEW retail_sales AS
    SELECT 
        YEAR,
        QUARTER,
        MONTH,
        CAST(DATE AS DATE) AS DATE,
        STORE_ID,
        STORE_NAME,
        STORE_SIZE,
        CASE 
            WHEN STORE_SIZE < 30000 THEN 'Small (<30k sqft)'
            WHEN STORE_SIZE BETWEEN 30000 AND 80000 THEN 'Medium (30k-80k sqft)'
            ELSE 'Large (>80k sqft)'
        END AS STORE_SIZE_CATEGORY,
        REGION,
        PRODUCT_ID,
        BRAND,
        PRODUCT_CATEGORY,
        PRODUCT_DIVISION,
        PRODUCT_NAME,
        SELLING_PRICE_PER_UNIT,
        SELLING_PRICE_PER_UNIT AS SELLING_PRICE,
        UNITS_SOLD,
        COST_PER_UNIT,
        COST_PER_UNIT AS COST
    FROM raw_table;
    """)
    
    print("Schema initialized successfully!")
    
    # Test Query 1: Which Product Divisions drove the highest margin in 2024 vs 2023?
    q1 = """
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
    """
    res1 = con.execute(q1).df()
    print("\n--- Question 1 Result ---")
    print(res1)
    
    # Test Query 2: Which 5 brands had the lowest margin rates in 2024?
    q2 = """
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
    """
    res2 = con.execute(q2).df()
    print("\n--- Question 2 Result ---")
    print(res2)
    
    # Test Query 3: List the products where unit cost increased faster than selling price in 2024 vs 2023
    q3 = """
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
    """
    res3 = con.execute(q3).df()
    print("\n--- Question 3 Result (Top 10) ---")
    print(res3)
    
    # Test Query 4: Break down total sales and margin rate by region and store size
    q4 = """
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
    """
    res4 = con.execute(q4).df()
    print("\n--- Question 4 Result ---")
    print(res4)

if __name__ == "__main__":
    run_tests()
