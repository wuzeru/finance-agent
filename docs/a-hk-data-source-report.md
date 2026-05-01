# A-Share / HK Stock Data Source Evaluation

**Date**: 2026-05-01 | **Issue**: #13 | **Status**: Evaluated

## Context

The finance-agent currently relies on OpenBB (FMP/yfinance), which primarily serves US markets. To support A-share (Shanghai/Shenzhen) and Hong Kong stock analysis, a Chinese-market data source is needed. The requirement: **free tier, Python SDK, covers both A-shares and HK stocks**.

## Candidate Evaluation

### 1. akshare (Selected)

| Criterion | Assessment |
|-----------|-----------|
| **A-share coverage** | Full: Shanghai + Shenzhen, all main board + ChiNext + STAR |
| **HK stock coverage** | Full: Main Board + GEM |
| **Installation** | `pip install akshare` (pure Python, ~120 MB) |
| **Registration** | None required |
| **Rate limits** | No formal limit; recommended throttle: 1 req/sec to avoid IP-based blocks |
| **Data latency** | End-of-day (T+0 ~6-8pm CST). Intraday available for some sources but unreliable |
| **Data sources** | Sina Finance, East Money, Tencent Finance, Shanghai/Shenzhen exchanges (scrape-based) |
| **Cost** | Free |
| **Python API** | Native, pandas DataFrames throughout |
| **Maintenance** | Active (weekly releases, community-maintained) |

**Verdict**: Only source meeting all requirements at zero cost.

### 2. tushare (Rejected)

| Criterion | Assessment |
|-----------|-----------|
| **A-share coverage** | Yes (free tier: limited API calls/day) |
| **HK stock coverage** | Paid tier only (¥1000+/year) |
| **Registration** | Required (token-based auth) |
| **Free tier limits** | ~200 calls/day, restricted field access |

**Verdict**: HK stocks require paid tier. Rejected on cost.

### 3. baostock (Rejected)

| Criterion | Assessment |
|-----------|-----------|
| **A-share coverage** | Yes (full, daily/weekly/monthly) |
| **HK stock coverage** | No |
| **Installation** | `pip install baostock` |
| **Latency** | T+1 only |

**Verdict**: No HK stock support. Rejected on coverage.

### 4. Wind Financial Terminal (Rejected)

| Criterion | Assessment |
|-----------|-----------|
| **Coverage** | Comprehensive (A-shares, HK, global) |
| **Cost** | ~¥10,000–50,000/year (terminal license) |
| **Python SDK** | WindPy, requires terminal installation |

**Verdict**: Enterprise-grade but far exceeds budget. Rejected on cost.

## Coverage Matrix (akshare)

| Market | Daily OHLCV | Fundamentals | Real-time | Index | Sector |
|--------|-------------|--------------|-----------|-------|--------|
| A-share (Shanghai) | Yes | Yes (PE/PB/MC) | Limited | Yes (SSE Composite) | Yes |
| A-share (Shenzhen) | Yes | Yes (PE/PB/MC) | Limited | Yes (SZSE Component) | Yes |
| HK Main Board | Yes | Yes (PE/PB/MC) | Limited | Yes (HSI) | Yes |
| HK GEM | Yes | Limited | No | No | No |

## Key Functions (akshare)

```python
import akshare as ak

# A-share daily data (symbol format: 6-digit code, no prefix)
df = ak.stock_zh_a_hist(symbol="600519", period="daily", start_date="20250101", end_date="20260501", adjust="qfq")

# A-share real-time snapshot
df = ak.stock_zh_a_spot_em()

# HK stock daily data (symbol format: 00700, 09988)
df = ak.stock_hk_hist(symbol="00700", period="daily", start_date="20250101", end_date="20260501", adjust="qfq")

# A-share individual stock fundamentals
df = ak.stock_individual_info_em(symbol="600519")

# HK stock spot price
df = ak.stock_hk_spot_em()
```

## Symbol Format Rules

| Market | Format | Example |
|--------|--------|---------|
| Shanghai A-share (`stock_zh_a_hist`) | 6-digit numeric string | `"600519"` (贵州茅台) |
| Shenzhen A-share (`stock_zh_a_hist`) | 6-digit numeric string | `"000001"` (平安银行) |
| Shanghai A-share (`stock_zh_a_spot_em`) | `sh` + 6-digit code | `"sh600519"` |
| Shenzhen A-share (`stock_zh_a_spot_em`) | `sz` + 6-digit code | `"sz000001"` |
| HK Stock | 5-digit code, zero-left-padded | `"00700"` (腾讯), `"09988"` (阿里) |

## Technical Notes

### Data Source Stability
akshare scrapes public financial websites (Sina, East Money, Tencent). Data availability depends on these upstream sources. During exchange maintenance windows or upstream website changes, functions may break temporarily. The community typically patches breakages within 1-2 weeks.

### Rate Limiting Recommendations
- No official rate limit, but rapid-fire requests can trigger temporary IP blocks from upstream sources
- Recommended: 1-2 second delay between requests (`time.sleep(1)`)
- Batch operations (e.g., `stock_zh_a_spot_em()` for snapshot) are single-request and don't need throttling

### Price Adjustments
- `adjust="qfq"` — forward-adjusted (recommended for most analysis)
- `adjust="hfq"` — backward-adjusted
- `adjust=""` — unadjusted (nominal prices)

### Known Limitations
- **No real-time trading data**: Data is end-of-day. Intraday snapshots exist but are unreliable for trading decisions
- **No Level 2 data**: Only Level 1 market data available
- **No Hong Kong GEM fundamentals**: Only price/volume data for HK GEM stocks
- **Scrape-based**: No official API SLA. Upstream changes may cause transient failures

## Integration with OpenBB

akshare and OpenBB coexist in the same venv without conflicts (tested). They have zero dependency overlap issues:
- **OpenBB**: Uses its own provider plugins (FMP, yfinance, FRED) via `openbb-*` packages
- **akshare**: Self-contained with standard data stack (pandas, requests, lxml)

For workflow integration, the data priority chain becomes:

```
FMP → yfinance → akshare → mark "unavailable"
```

akshare serves as the fallback for Chinese-market symbols where FMP/yfinance have no coverage.

## Recommendation

**Adopt akshare** as the primary data source for A-share and Hong Kong stock market data. It is the only free source that covers both markets comprehensively with a clean pandas-based Python API.
