# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Identity

This is **not** a traditional software project — there is no `main.py`, no application server, no deployable binary. Claude Code **is the runtime**. The repo is a set of data files, shell scripts, and configuration that Claude Code reads and executes to function as an automated personal investment analysis agent.

When you (Claude Code) are invoked in this directory, you are expected to drive the full analysis lifecycle: read portfolio data, fetch market data via OpenBB, reason about holdings, generate a structured report, and optionally push results to Feishu.

## Architecture: Dual-Path

```
Path A (Scheduled Push)              Path B (On-demand Pull)

launchd → run-analysis.sh            launchd → feishu-listener.sh → lark-event WebSocket
    ↓                                      ↓ (always-on)
Claude Code full 7-step workflow     intent_router.sh → Claude Code targeted session
    ↓                                      ↓
report → Feishu push → archive       Feishu reply
```

Both paths share `portfolio.csv`, `recommendations.csv`, `.env` credentials, and `venv/`. Never run both simultaneously — `.analysis.lock` file lock prevents this.

## Environment Setup Commands

```bash
# First time only
python3 -m venv venv
source venv/bin/activate
pip install openbb openbb-fmp openbb-fred pandas yfinance
pip freeze > requirements.txt

# Subsequent use
source venv/bin/activate
```

API keys in `.env` (gitignored):
- `OPENBB_FMP_API_KEY` — Financial Modeling Prep free tier (~250 req/day)
- `OPENBB_FRED_API_KEY` — FRED macroeconomic data
- `ALLOWED_OPEN_ID` — whitelisted Feishu open_id

## Data File Schemas

### `portfolio.csv` (holds current portfolio state)

Columns: `symbol,name,type,quantity,avg_cost,currency,exchange,notes`

- `type`: `stock` | `etf` | `bond` | `cash`
- `quantity`: > 0 (float)
- `avg_cost`: cost basis including fees (float)
- `currency`: `USD` | `HKD` | `CNY`
- `exchange`: optional, helps OpenBB disambiguate symbols

### `recommendations.csv` (feedback loop memory)

Columns: `id,timestamp,symbol,action,target_price_low,target_price_high,confidence,rationale_summary,status,verified_at,price_at_verification,outcome,outcome_detail`

- `action`: `buy` | `sell` | `hold` | `reduce`
- `confidence`: 1–5
- `status`: `pending` | `verified` | `expired`
- `outcome`: `correct` | `incorrect` | `partial` | `expired`

Always read `recommendations.csv` at the start of every analysis run. It is the project's persistent memory across sessions.

### `agent.log` (NDJSON per line)

Every significant event gets a JSON line with `ts` and `event` fields. Track: session start/end, data fetch attempts (with source and status), Feishu queries, response sent.

## Path A: Scheduled Analysis Workflow (7 Steps)

When invoked via `run-analysis.sh` (launchd trigger, weekdays 9:00 / 13:00):

### Step 1 — Acquire Lock

```bash
exec 200>".analysis.lock" && flock 200
```

### Step 2 — Load State
Read `portfolio.csv` and `recommendations.csv` into context.

### Step 3 — Collect Data

For each holding in `portfolio.csv`, execute Python snippets via terminal (inside venv) using OpenBB Platform v4. Data priority chain: **FMP → yfinance (fallback) → mark "unavailable"**.

Fetch per symbol: current price, daily change, 20/50/200 MA, RSI(14), MACD, Bollinger Bands, PE/PB/market cap, last 5 news headlines.

Fetch once globally: S&P 500, VIX, 10Y yield, Fed funds rate (FRED).

**Rate limit**: max 5 API calls per symbol per run. Total ~10–20 calls/run. Space calls ≥1 second apart. If FMP returns rate-limit error, switch to yfinance for that call.

### Step 4 — Verify History + Analyze

First, verify pending recommendations:
- Skip recommendations less than 3 trading days old (too noisy)
- For `buy`: did price touch `target_price_low`? → `correct`
- For `sell/reduce`: did price touch `target_price_high`? → `correct`
- For `hold`: no >5% adverse move? → `correct`
- Older than 30 trading days without trigger → `expired` (neutral, excluded from stats)

Then analyze current holdings with latest data + historical accuracy context. If a symbol has ≥3 consecutive `incorrect` outcomes, flag it in the report as "reduced confidence".

### Step 5 — Generate Report

Write to `reports/analysis-YYYY-MM-DD-HHmm.md`. Must include:

1. **Market Overview**: key indices, VIX, rates
2. **History Review**: rolling 30-day accuracy by action type, by symbol, by confidence tier
3. **Holdings Diagnosis**: per-symbol analysis (fundamental, technical, risk flags)
4. **Recommendations Table**:

```
| Symbol | Action | Target Range | Confidence | Rationale |
|--------|--------|-------------|------------|-----------|
| AAPL   | hold   | —           | 4          | PE fair, uptrend intact |
| TSLA   | reduce 30% | 280-290 | 3       | RSI overbought, stretched valuation |
```

5. **Risk Alerts**: macro risks, earnings calendar, concentration warnings

### Step 6 — Write Recommendations

Append new recommendations to `recommendations.csv` with `status=pending`. Use the next sequential `id`.

### Step 7 — Push & Archive

Push the report summary to Feishu via `lark-cli im send`. Full report stays in `reports/`. Log end-of-run to `agent.log`. Release lock.

## Path B: Feishu On-Demand Interaction

When `feishu-listener.sh` receives a message from the whitelisted `ALLOWED_OPEN_ID`, it routes to `intent_router.sh` which spawns a Claude Code session.

### Intent Routing Rules

| User Input Pattern | Action | Scope |
|---|---|---|
| Contains "分析" / "持仓" / "诊断" / "portfolio" | Full analysis (Steps 2–5), condensed output ≤3000 chars | Do NOT write `recommendations.csv`, do NOT archive to `reports/` |
| Single ticker (1-5 uppercase letters) or "查 XXXX" | Quick single-stock diagnosis: price + RSI/MACD/MA + one-line recommendation ≤1500 chars | Read-only, no writes |
| Contains "准确率" / "回溯" / "历史" / "verify" | Read `recommendations.csv`, output accuracy stats ≤2000 chars | Read-only |
| Contains "报告" | Read latest file from `reports/`, push to Feishu | Read-only |
| Contains "help" / "帮助" / "命令" | Return command list directly, do NOT invoke Claude Code | Zero cost |
| Anything else | General investment Q&A, Claude Code answers freely ≤2000 chars | Read-only |

**Critical**: on-demand queries NEVER write to `recommendations.csv`. The feedback loop is only fed by scheduled analyses.

### How to Send Feishu Messages

```bash
# Send to the whitelisted user
lark-cli im send --user "$SENDER_ID" --msg "message text"

# For long messages, write to temp file then send
echo "$LONG_MSG" > /tmp/feishu_response.txt
lark-cli im send --user "$SENDER_ID" --msg "$(cat /tmp/feishu_response.txt)"
```

## OpenBB Usage Pattern

```python
from openbb import obb
import os

# Configure credentials (or rely on env vars OPENBB_FMP_API_KEY)
obb.user.credentials.fmp_api_key = os.environ.get("OPENBB_FMP_API_KEY")

# Price snapshot
quote = obb.equity.price.quote("AAPL", provider="fmp")

# Historical prices (1 year daily)
hist = obb.equity.price.historical("AAPL", provider="fmp", start_date="2024-01-01")

# Fundamentals
profile = obb.equity.fundamental.profile("AAPL", provider="fmp")

# If FMP fails, retry with yfinance:
# quote = obb.equity.price.quote("AAPL", provider="yfinance")
```

Always prefer FMP provider first, fallback to yfinance. Log each fetch attempt in `agent.log`.

## A-Share / HK Stock Data Sources

For Chinese market data (A-shares and Hong Kong stocks), use **akshare** — the only free Python library covering both markets with no registration required. akshare coexists with OpenBB in the same `venv/` without conflicts.

### Installation

Already included in `requirements.txt`. Installed alongside OpenBB:

```bash
pip install akshare
```

### Data Priority Chain (updated for Chinese markets)

```
FMP → yfinance → akshare → mark "unavailable"
```

akshare serves as the data source for Chinese-market symbols where FMP/yfinance have no coverage.

### Key Functions

```python
import akshare as ak

# A-share historical daily (symbol: 6-digit code, no prefix for stock_zh_a_hist)
# adjust: "qfq" (forward-adjusted, recommended) | "hfq" | "" (nominal)
df = ak.stock_zh_a_hist(symbol="600519", period="daily",
                        start_date="20250101", end_date="20260501",
                        adjust="qfq")

# A-share real-time snapshot (single request, all stocks)
df = ak.stock_zh_a_spot_em()

# HK stock historical daily (symbol: 5-digit code, left-padded with zeros)
df = ak.stock_hk_hist(symbol="00700", period="daily",
                      start_date="20250101", end_date="20260501",
                      adjust="qfq")

# HK stock real-time snapshot
df = ak.stock_hk_spot_em()

# A-share individual stock fundamentals (PE/PB/market cap etc.)
df = ak.stock_individual_info_em(symbol="600519")
```

### Symbol Format Rules

| Market | Format | Example |
|--------|--------|---------|
| Shanghai A-share (`stock_zh_a_hist`) | 6-digit numeric string | `"600519"` (贵州茅台) |
| Shenzhen A-share (`stock_zh_a_hist`) | 6-digit numeric string | `"000001"` (平安银行) |
| HK Stock (`stock_hk_hist`) | 5-digit string, zero-left-padded | `"00700"` (腾讯), `"09988"` (阿里) |

Note: `stock_zh_a_spot_em()` uses a different format: `"sh600519"`, `"sz000001"`.

### Rate Limiting Recommendations

- No official rate limit, but scrape-based. Rapid-fire requests can trigger temporary IP blocks from upstream sources (Sina/East Money/Tencent).
- Add `time.sleep(1)` between individual symbol requests.
- Batch snapshots (`stock_zh_a_spot_em()`, `stock_hk_spot_em()`) are single requests — no throttling needed.

### Known Limitations

- **End-of-day only**: No reliable real-time/intraday data for trading decisions
- **Scrape-based**: No API SLA. Upstream website changes (Sina, East Money, Tencent) may cause transient 1-2 week breakages
- **No Level 2 data**: Only Level 1 (OHLCV + basic fundamentals)
- **HK GEM limited**: Price/volume available, but fundamentals coverage is thin for GEM stocks

### Integration Verification

Run the integration test to confirm akshare is functional:

```bash
source venv/bin/activate
python scripts/test_akshare.py
```

## Model Selection

Default model is DeepSeek V4 via cc-switch (cost ~¥0.10 per scheduled run). For quarterly deep reviews, manually switch to Claude Sonnet for higher-quality analysis.

## Error Handling Tiers

- **WARN**: single symbol data fetch fails → skip symbol, label "data unavailable", continue
- **WARN**: API returns empty → retry once with fallback provider
- **ERROR**: ALL symbol fetches fail → generate "data source unavailable" report, notify user, terminate
- **FATAL**: `portfolio.csv` missing or malformed → log error, notify, exit

## CI

CI runs shellcheck on `scripts/` and validates YAML syntax. Triggered on push/PR to `main`.

```bash
# Run locally
shellcheck scripts/*.sh
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```
