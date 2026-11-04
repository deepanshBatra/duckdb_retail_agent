# Canadian Tire Retail AI Copilot: POC Summary

## The problem
LLMs are good with language and bad with arithmetic over thousands of rows. Ask a chat model "which brands lost margin rate in 2024?" and it may make up the numbers.

## The approach
The model never calculates. It writes SQL (or calls a fixed tool), DuckDB runs it in memory in a few milliseconds, and the model explains the result in plain English. Every answer shows the queries behind it.

## What the POC does today
- **Plain-English questions** about sales, cost, margin, units, brands, divisions, regions, stores and products.
- **Business definitions built in.** Sales, COGS, margin and weighted margin rate are pre-computed, with year-over-year views for every level.
- **A margin bridge tool** that splits the 2023 → 2024 margin change into price (+$22,341), cost (−$9,630), volume (+$9,078) and mix (+$2,678), adding up exactly to +$24,468.
- **Honest about the data.** It warns that store-size and seasonality comparisons aren't reliable in this extract (one row per store per day).
- **Checked accuracy.** 14 of 14 test questions answered correctly on two Groq models, plus offline tests that tie every core number to the validated case-study results and the Power BI dashboard.
- **Safe by design.** Read-only database, no file or network access from SQL, a single SELECT per call, and API keys kept on the server.

## What a full version could do
1. **Run on the warehouse.** Point the same agent at full POS, Triangle Rewards and inventory data.
2. **Flag margin squeezes early.** Check supplier cost changes against retail prices every week and alert category managers. In this data, that would have caught Fynix (cost +10%, price +6%, margin rate −265 bps).
3. **Answer what-if questions.** "If we raise Fynix prices 4%, what happens to margin?"
4. **Meet people where they work** in Teams or Slack, with a morning summary for store and category leaders.

## Takeaway
The POC shows that a leader can get a correct, checkable answer to a margin question in seconds without opening a dashboard. The next step is real enterprise data.
