# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**语言要求：所有输出、报告和交互均使用中文。**

## Project Identity

This is **not** a traditional software project — there is no `main.py`, no application server, no deployable binary. Claude Code **is the runtime**. The repo is a set of data files, Python scripts, and configuration that Claude Code reads and executes to function as an automated personal investment analysis agent.

When you (Claude Code) are invoked in this directory, you are expected to drive the full analysis lifecycle: read portfolio data, fetch market data via OpenBB, reason about holdings, generate a structured report, and optionally push results to Feishu.

## Architecture: Dual-Path

```
Path A (Scheduled Push)              Path B (On-demand Pull)

launchd → run-analysis.py            launchd → feishu-listener.py → lark-event WebSocket
    ↓                                      ↓ (always-on)
Claude Code full 7-step workflow     Claude Code 自然语言理解 + 回复
    ↓                                      ↓
report → Feishu push → archive       listener 捕获 stdout → 飞书回复
```

feishu-listener.py is a **pure bridge**: receives Feishu messages → strips @ mention → sends OK reaction → passes to Claude with `--session-id` → captures Claude's stdout → pushes reply to Feishu via `lark-cli +messages-send`. Claude outputs markdown content; the listener handles all Feishu I/O. No regex routing, no `intent_router.sh`.

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
- `FEISHU_APP_ID` — Feishu open platform app ID (for `lark-cli config init`)
- `FEISHU_APP_SECRET` — Feishu open platform app secret
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

When invoked via `scripts/run-analysis.py` (launchd trigger, weekdays 9:00 / 13:00):

### Step 1 — Acquire Lock

通过 `LOCK_FILE.touch(exist_ok=False)` 获取文件锁（touch 排他锁），与 feishu-listener.py 共享同一锁机制。锁超时 30 分钟后自动清理。

### Step 2 — Load State
Read `portfolio.csv` and `recommendations.csv` into context.

### Step 3 — Collect Data

For each holding in `portfolio.csv`, execute Python snippets via terminal (inside venv) using OpenBB Platform v4. Data priority chain (US/global symbols): **FMP → yfinance → mark "unavailable"**. For Chinese-market symbols (A-shares/HK), see [A-Share / HK Stock Data Sources](#a-share--hk-stock-data-sources) below.

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

Push the report summary to Feishu via `lark-cli --profile finance-agent im send`. Full report stays in `reports/`. Log end-of-run to `agent.log`. Release lock.

## Path B: Feishu On-Demand Interaction

When `scripts/feishu-listener.py` receives a message from the whitelisted `ALLOWED_OPEN_ID`, it: strips @ mention → sends OK reaction → acquires lock → calls `claude -p` with `--session-id`/`--resume` → captures stdout → pushes reply to Feishu via `lark-cli +messages-send`.

### How Claude Outputs Replies

When invoked via feishu-listener, the prompt instructs: "直接输出你的回复内容（markdown 格式，中文），不要说你已发送消息。listener 会负责把回复推到飞书。"

Claude simply outputs markdown content to stdout. The listener captures it and handles Feishu delivery. Do NOT call lark-cli yourself — the listener owns all Feishu I/O.

### Behavior Guidelines for On-Demand Queries

1. **自然语言理解**: Understand the user's intent naturally — no fixed keyword matching
2. **只读优先**: On-demand queries are read-only by default. Do NOT write to `recommendations.csv` or `reports/`
3. **长度控制**: Keep responses concise — typically ≤2000 chars for Q&A, ≤3000 chars for full portfolio analysis
4. **上下文延续**: `--session-id` persists conversation context. Users can ask follow-up questions naturally
5. **命令可用性**: Users may ask for help — tell them about: 分析/持仓/诊断, 查个股, 准确率/回溯, 报告, 异动提醒
6. **异动提醒开关**: Toggle via `.alert_enabled` file: `touch` to enable, `rm` to disable

**Critical**: on-demand queries NEVER write to `recommendations.csv`. The feedback loop is only fed by scheduled analyses.

## 价格异动提醒

### 开关操作

用户可通过自然语言控制异动提醒功能的开关：

- **开启**（飞书消息：如 "异动提醒开" / "开启异动通知"）→ 执行 `touch .alert_enabled`
- **关闭**（飞书消息：如 "异动提醒关" / "关闭异动提醒"）→ 执行 `rm -f .alert_enabled`
- **状态查询**（飞书消息：如 "异动提醒状态"）→ 执行 `test -f .alert_enabled && echo "已开启" || echo "已关闭"`

### 配置文件

阈值配置在 `scripts/alert_config.sh`：
```bash
ANOMALY_THRESHOLD=5  # 涨跌幅绝对值 ≥ 此百分比即触发异动提醒
```

读取方式：`source scripts/alert_config.sh 2>/dev/null; echo ${ANOMALY_THRESHOLD:-5}`

### 提醒消息格式

当 Path A 定时分析检测到价格异动时，会通过飞书发送一条独立消息：

```
🚨 价格异动提醒
当前阈值: ±5%

• AAPL 现价 $185.32 (↑7.2%) 触发阈值 ±5%
---
• TSLA 现价 $210.50 (↓6.1%) 触发阈值 ±5%
```

- 涨用 `↑` + 涨幅，跌用 `↓` + 跌幅
- 多只标的以 `---` 分隔
- 若所有标的均未触发，不发送此消息，报告内注明 "今日无价格异动"

## OpenBB Usage Pattern

```python
from openbb import obb
import os
import pandas as pd

# Configure credentials (or rely on env vars OPENBB_FMP_API_KEY)
obb.user.credentials.fmp_api_key = os.environ.get("OPENBB_FMP_API_KEY")

# OBBject 需调用 .to_dataframe() 才能拿到 DataFrame
# FMP quote fields: last_price, change_percent, ma50, ma200, market_cap
quote_df = obb.equity.price.quote("AAPL", provider="fmp").to_dataframe()

# FMP historical: OHLCV DataFrame
hist_df = obb.equity.price.historical("AAPL", provider="fmp",
    start_date="2025-01-01", end_date="2026-05-01").to_dataframe()

# Fundamentals: FMP 没有 profile 接口, 用 metrics 或 ratios
metrics = obb.equity.fundamental.metrics("AAPL", provider="fmp", limit=1)
ratios = obb.equity.fundamental.ratios("AAPL", provider="fmp", limit=1)
# PE/PB 也可以用 yfinance 补充:
import yfinance as yf
info = yf.Ticker("AAPL").info
pe, pb = info.get("trailingPE"), info.get("priceToBook")

# FMP 免费层不支持 ETF(如 VOO) → fallback 到 yfinance
# If FMP fails, retry with yfinance:
# quote_df = obb.equity.price.quote("AAPL", provider="yfinance").to_dataframe()
```

**FMP 关键注意事项:**
- OBBject 不支持 `len()` / `iloc[]`，必须先 `.to_dataframe()`
- 字段名是 `last_price` 不是 `price`，`change_percent` 不是 `changes_percentage`
- 免费层不支持 ETF 标的（VOO 等会报 402）→ 个股走 FMP，ETF 走 yfinance
- `obb.equity.fundamental.profile` 不存在于 FMP provider

Always prefer FMP provider first, fallback to yfinance. Log each fetch attempt in `agent.log`.

## A-Share / HK Stock Data Sources

For Chinese market data (A-shares and Hong Kong stocks), use **akshare** — the only free Python library covering both markets with no registration required. akshare coexists with OpenBB in the same `venv/` without conflicts.

### Installation

Already included in `requirements.txt`. Install with:

```bash
pip install -r requirements.txt
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

### Dual-Source Fallback (East Money → Sina)

East Money (`em` suffix functions) is the primary data source, but its API servers may reject connections from non-mainland-China IPs. Always use a fallback pattern:

```python
# A-share: try East Money first, fall back to Sina
try:
    df = ak.stock_zh_a_hist(symbol="600519", period="daily",
                            start_date=start, end_date=end, adjust="qfq")
except Exception as e:
    if "connection" in str(e).lower() or "timeout" in str(e).lower():
        df = ak.stock_zh_a_daily(symbol="sh600519",
                                 start_date=start, end_date=end, adjust="qfq")
        # Sina uses English column names; rename for consistency
        df = df.rename(columns={"date": "日期", "open": "开盘", "high": "最高",
                                "low": "最低", "close": "收盘", "volume": "成交量"})

# HK: same pattern
try:
    df = ak.stock_hk_hist(symbol="00700", period="daily",
                          start_date=start, end_date=end, adjust="qfq")
except Exception as e:
    if "connection" in str(e).lower() or "timeout" in str(e).lower():
        df = ak.stock_hk_daily(symbol="00700", adjust="qfq")
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        df = df.rename(columns={"date": "日期", "open": "开盘", "high": "最高",
                                "low": "最低", "close": "收盘", "volume": "成交量"})
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

CI runs Python syntax validation on `scripts/` and validates YAML syntax. Triggered on push/PR to `main`.

```bash
# Run locally
python3 -m py_compile scripts/*.py
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```
