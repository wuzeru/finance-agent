# 个人 AI 投资分析代理 — 构建蓝图 v0.0.1

> v0.0.0 → v0.0.1 变更摘要：
> - 明确 OpenBB 版本与数据源接入方式
> - 新增「反馈闭环」设计（`recommendations.csv` + 回溯验证）
> - 新增成本估算模型
> - 定义 `portfolio.csv` 严格 Schema
> - 新增错误处理与降级策略
> - 明确 macOS 7×24 运行方案（防休眠 + launchd）
> - 新增运行间状态传递机制

---

## 0. 项目目标与非目标

### 目标

构建一个运行在本地 Mac mini 上的 **AI 驱动投资分析代理**。代理负责：（1）自动采集市场数据；（2）结合持仓给出结构化分析报告；（3）追踪历史建议并自我验证；（4）最终交易决策和执行 100% 由人工完成。

### 非目标

- 不执行自动交易（无券商 API 接入）
- 不追求实时高频分析（日频/周频为主）
- 不替代专业财务顾问的法律责任
- 不保证盈利

---

## 1. 核心理念

- **自主可控**：核心逻辑、持仓数据与运行环境均部署在本地硬件上，保障隐私与控制权。
- **低成本运行**：充分利用本地算力与免费/低成本 API 数据源，模型成本集中在 Claude Code 调用。
- **原生与简洁**：不自写「主控脚本 + 工具注册」样板代码，由 **Claude Code** 负责会话、工具调用循环与任务拆解。
- **人机结合**：AI 负责自动化采集与分析并给出建议，人工保留最终决策与操作权。
- **反馈闭环**：每次建议都被记录并在后续运行中回溯验证，形成持续改进的正循环。这是本方案与一次性数据解读器的核心区别。

---

## 2. 技术栈（明确版）

| 类别 | 工具/技术 | 版本/约束 | 作用 |
|------|-----------|-----------|------|
| 硬件主机 | Mac mini | Apple Silicon, macOS 15+ | 7×24 本地运行环境 |
| 编排与推理 | Claude Code | 官方 CLI，项目目录下运行 | 读持仓、拉数据、多轮推理、出报告 |
| 大脑 (Brain) | Claude 模型 | 经 Claude Code 调用 | 深度分析与建议生成 |
| 数据引擎 | **OpenBB Platform v4** | `pip install openbb`（非旧版 SDK v3） | 统一数据接口 |
| 数据源 | FMP + FRED | 免费套餐 API Key | 股票价格/基本面 + 宏观经济 |
| 数据源（备选） | Yahoo Finance (yfinance) | `pip install yfinance` | FMP 免费层无法覆盖时的补充 |
| 持仓管理 | `portfolio.csv` | 严格 Schema（见 §6.1） | 持仓记录 |
| 建议追踪 | `recommendations.csv` | 严格 Schema（见 §6.2） | 反馈闭环核心 |
| 运行日志 | `agent.log` | 追加写入，结构化 | 调试与审计 |
| 调度 | macOS `launchd` | `.plist` 配置文件 | 防休眠 + 崩溃自动重启 |
| 开发环境 | Python 3.12+ + venv | `requirements.txt` 锁定版本 | 数据脚本执行环境 |

---

## 3. OpenBB 版本说明（关键决策）

### 3.1 为什么明确版本

OpenBB 在 2023-2024 经历了重大架构变更：
- **OpenBB SDK v3.x（已弃用）**：旧的单体外挂模式，`from openbb import obb`
- **OpenBB Platform v4.x（当前）**：模块化架构，Provider → Extension 模型

本项目使用 **OpenBB Platform v4**。

### 3.2 安装与配置

```bash
python3 -m venv venv
source venv/bin/activate
pip install openbb           # OpenBB Platform v4
pip install openbb-fmp        # FMP data provider extension
pip install openbb-fred       # FRED data provider extension
pip install pandas
```

### 3.3 数据 Provider 配置

API Key 通过环境变量注入，在 `CLAUDE.md` 中引用：

```bash
export OPENBB_FMP_API_KEY="your-fmp-key"
export OPENBB_FRED_API_KEY="your-fred-key"
```

OpenBB Platform v4 在首次 `obb.equity.price.quote("AAPL")` 调用时会自动识别已安装的 provider extension 并使用。也支持在调用时显式指定：

```python
from openbb import obb
obb.user.credentials.fmp_api_key = "xxx"       # 或从 os.environ 读取
```

### 3.4 Free-Tier 限制与应对

| 数据源 | 免费额度 | 限制 |
|--------|---------|------|
| FMP Free | ~250 req/day | 个股基本面/价格/新闻，延迟 15min |
| FRED | ~120 req/min | 美国宏观经济指标 |
| yfinance（备选）| 无硬限制 | 非官方 API，稳定性稍差，覆盖全球 |

**应对策略**：
- 日常价格查询优先用 FMP（结构化、稳定）
- yfinance 作为 FMP 超限或数据缺失时的 fallback
- FRED 仅在需要宏观数据时使用（利率、CPI、失业率等），低频调用
- 在 `CLAUDE.md` 中明确每个数据源的调用优先级和频率限制

---

## 4. 工作流程（增强版）

```
┌──────────────────────────────────────────────────────────────┐
│                    每次运行（~10-15min）                        │
├──────────┬──────────┬──────────┬──────────┬──────────┬───────┤
│ Trigger  │ Load     │ Collect  │ Think &  │ Generate │ Log & │
│ (launchd)│ State    │ Data     │ Analyze  │ Report   │ Close │
│          │          │          │ + Verify │          │       │
│          │          │          │ History  │          │       │
└──────────┴──────────┴──────────┴──────────┴──────────┴───────┘
```

### Step 1: 触发 (Trigger)

`launchd` 在预定时间（如每个交易日 9:00 / 13:00）启动包装脚本 `run-analysis.sh`，后者在 `caffeinate` 保护下维持唤醒状态并调用 Claude Code。

关键设计决策：使用 `launchd` 而非 `cron`，原因见 §10。

### Step 2: 加载状态 (Load State)

Claude Code 读取两个文件：
- `portfolio.csv` → 当前持仓标的、数量、成本基准
- `recommendations.csv` → 上一期的所有建议记录，特别是"待验证"状态的建议

### Step 3: 采集数据 (Data Collection)

对 `portfolio.csv` 中的每个持仓标的，通过终端执行 Python（已激活 venv + 已配置 API key），调用 OpenBB Platform v4 获取：

- **价格数据**: 当前价、日内涨跌幅、近 20/50/200 日均线
- **技术指标**: RSI(14), MACD, 布林带
- **基本面**（股票，FMP 免费层可获取的字段）: PE, PB, 市值
- **近期新闻**: 影响该标的的最近 N 条新闻标题（FMP 或 WebFetch 补充）
- **宏观背景**（仅运行开始时全局拉取一次）: 联邦基金利率、CPI、VIX 等

对 `recommendations.csv` 中"待验证"的建议，获取这些建议对应的标的当前价格，用于后续验证。

**频率控制**：一次运行内，每个标的的总 API 调用数控制在 3-5 次以内，避免触发 FMP 日限额。

### Step 4: 思考与分析 + 历史验证 (Think, Analyze & Verify History)

新增步骤：**在分析当前持仓前，先验证历史建议**。

对 `recommendations.csv` 中最近 5-10 条状态为 "pending" 的记录，比较目标价与当前实际价：
- 标记 `verified` 字段为实际结果（如 "价格在 30 日内达到目标价 150"，"未达到"等）
- 更新 `outcome` 为 `correct` / `incorrect` / `partial`
- 对连续出错的建议类型和标的，标记为 `needs_review`

然后进入当前持仓分析：
- 整合持仓 + 最新数据 + 历史验证结果，多轮推理
- 如果发现之前对某标的的判断连续出错，需要在本次报告中标注"该标的分析置信度下降"
- 若需补充宏观或另类数据，继续在会话内调用终端

### Step 5: 生成报告 (Generate Report)

输出结构化报告（Markdown 格式），固定包含以下段落：

1. **市场总览**：今日宏观背景、关键指数表现
2. **历史建议回溯**：上一期建议的验证结果（正确/错误/部分正确），含简短归因
3. **持仓诊断**：逐个持仓分析（基本面、技术面、风险提示）
4. **操作建议表**：

| 标的 | 当前操作 | 目标价区间 | 置信度(1-5) | 理由 |
|------|---------|-----------|------------|------|
| AAPL | 持有 | — | 4 | PE 合理，趋势向上 |
| TSLA | 减仓30% | 280-290 | 3 | RSI 超买，估值偏高 |

5. **风险提示**

### Step 6: 写入建议日志 (Write Recommendations)

**新增关键步骤**：将本次报告中的每一条操作建议以结构化形式追加写入 `recommendations.csv`，状态初始为 `pending`。

### Step 7: 通知 + 归档 (Notify & Archive)

- 报告写入 `reports/analysis-YYYY-MM-DD-HHmm.md`
- 追加一行到 `agent.log`（运行摘要：耗时、API 调用数、token 消耗）
- 可选：通过 `scripts/notify.py` 发送 Telegram/Bark 通知
- 会话结束，等待下次触发

---

## 5. 反馈闭环设计（核心新增）

### 5.1 设计理念

一次性分析和持续进化的分析系统之间的区别在于：前者每次从零开始解读数据，后者知道自己以前说过什么、结果是对是错，并根据历史准确率调整当前判断。

### 5.2 数据结构：`recommendations.csv`

```csv
id,timestamp,symbol,action,target_price_low,target_price_high,confidence,rationale_summary,status,verified_at,price_at_verification,outcome,outcome_detail
1,2025-05-12T09:30,AAPL,hold,,,4,PE合理趋势向上 无明确买点,pending,,,,
2,2025-05-12T09:30,TSLA,reduce,280,290,3,RSI超买估值偏高,pending,,,,
3,2025-04-28T13:00,NVDA,buy,880,900,4,回调至支撑位 AI需求确定性强,verified,2025-05-01T09:00,920,correct,价格在3日内突破900
4,2025-04-28T13:00,BABA,buy,85,90,3,政策底出现 估值历史低位,verified,2025-05-01T09:00,78,incorrect,股价继续下跌至78 政策利好未兑现
```

**字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 自增，唯一标识 |
| `timestamp` | ISO datetime | 建议生成时间 |
| `symbol` | string | 标的代码 |
| `action` | enum | `buy` / `sell` / `hold` / `reduce` |
| `target_price_low` | float | 目标区间下限（buy/sell时有值，hold为空） |
| `target_price_high` | float | 目标区间上限 |
| `confidence` | int 1-5 | 置信度（1=低，5=高） |
| `rationale_summary` | string | 一句话理由摘要 |
| `status` | enum | `pending` / `verified` / `expired` |
| `verified_at` | datetime | 验证时间 |
| `price_at_verification` | float | 验证时该标的实际价格 |
| `outcome` | enum | `correct` / `incorrect` / `partial` / `expired` |
| `outcome_detail` | string | 验证结果描述 |

### 5.3 验证逻辑

在 Step 4 中，对 `status=pending` 的记录执行验证：

1. **时间窗口**：建议产生超过 3 个交易日后才进入验证范围（避免噪音）
2. **验证规则**：
   - `buy` 建议：验证期内价格是否触及 target_price_low → 触及则 `correct/partial`
   - `sell/reduce` 建议：验证期内价格是否触及 target_price_high → 触及则 `correct/partial`
   - `hold` 建议：验证期内未出现大幅反向波动（>5%）→ `correct`
3. **过期处理**：超过 30 个交易日仍未触发验证条件的，标记为 `expired`（视为中性，不计入准确率统计）
4. **连续失误标记**：对连续 3 次以上 `incorrect` 的同标的/同类建议，在报告中显式降级置信度

### 5.4 准确率追踪

每次报告中的"历史建议回溯"部分输出滚动准确率统计：

```
过去 30 天建议准确率: 7 / 10 = 70%
  - buy 建议: 3/4 (75%)
  - sell/reduce 建议: 2/3 (67%)
  - hold 建议: 2/3 (67%)
按标的: AAPL 2/2, TSLA 1/2, NVDA 1/1, ...
低置信度(≤2)建议准确率: 60%
高置信度(≥4)建议准确率: 80%
```

---

## 6. 数据文件 Schema

### 6.1 `portfolio.csv` 严格 Schema

```csv
symbol,name,type,quantity,avg_cost,currency,exchange,notes
AAPL,Apple Inc.,stock,50,185.50,USD,NASDAQ,长期持有
GOOGL,Alphabet Inc.,stock,20,140.00,USD,NASDAQ,
VTI,Vanguard Total Stock Market ETF,etf,100,260.00,USD,NYSE,核心仓位
SHY,iShares 1-3 Year Treasury Bond ETF,etf,50,82.00,USD,NYSE,现金替代
```

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| `symbol` | string | Y | OpenBB 可识别的 ticker；股票用标准代码，ETF 用基金代码 |
| `name` | string | Y | 标的名称（便于报告可读性） |
| `type` | enum | Y | `stock` / `etf` / `bond` / `cash` |
| `quantity` | float | Y | > 0 |
| `avg_cost` | float | Y | > 0，持仓平均成本（含手续费） |
| `currency` | enum | Y | `USD` / `HKD` / `CNY` |
| `exchange` | string | N | 交易所代码，助于 OpenBB 定位 |
| `notes` | string | N | 自由备注 |

### 6.2 `recommendations.csv` Schema

见 §5.2。

### 6.3 `agent.log` 格式

每行一条 JSON，便于结构化分析：

```json
{"ts":"2025-05-12T09:00:00","run_id":"run-20250512-0900","event":"start"}
{"ts":"2025-05-12T09:00:05","run_id":"run-20250512-0900","event":"data_fetch","symbol":"AAPL","api":"fmp","status":"ok"}
{"ts":"2025-05-12T09:00:12","run_id":"run-20250512-0900","event":"data_fetch","symbol":"TSLA","api":"fmp","status":"error","detail":"rate_limit"}
{"ts":"2025-05-12T09:00:13","run_id":"run-20250512-0900","event":"fallback","symbol":"TSLA","api":"yfinance","status":"ok"}
{"ts":"2025-05-12T09:10:00","run_id":"run-20250512-0900","event":"report_generated","holdings_count":8,"recommendations_count":5}
{"ts":"2025-05-12T09:10:00","run_id":"run-20250512-0900","event":"end","duration_s":600,"api_calls":12,"tokens_est":25000}
```

---

## 7. 错误处理与降级策略

### 7.1 分级处理

| 级别 | 场景 | 处理方式 |
|------|------|---------|
| WARN | 单个标的数据获取失败 | 跳过该标的，报告中标注"数据不可用"，继续处理其他标的 |
| WARN | API 返回空数据 | 切换 fallback 数据源后重试一次 |
| ERROR | 所有标的数据获取均失败 | 生成"数据源不可用"报告，通知用户，不生成投资建议 |
| FATAL | `portfolio.csv` 不存在或格式错误 | 记录错误日志，通知用户，终止运行 |

### 7.2 Fallback 数据源链

```
FMP (首选) → 失败/超限 → yfinance (备选) → 失败 → 标注"数据不可用"
FRED (宏观) → 失败 → 跳过宏观分析段落，不阻塞主流程
```

### 7.3 API 调用频率控制

- 每标的每次运行最多 5 次 API 调用
- 全局调用间隔至少 1 秒（FMP free tier 有 rate limit）
- 预计每次运行约 10-20 次 API 调用（对应 8-10 个持仓标的），处于 FMP 免费套餐的 250 req/day 安全线内

### 7.4 调度层级容错

- `launchd` 保证进程崩溃后自动重启
- 但单次运行失败不自动重试（避免重复消费 API 额度），等待下一次调度自然触发

---

## 8. 运行间状态传递

### 8.1 问题定义

运行 #2 需要知道运行 #1 的结论是什么，否则每次都是孤立的数据解读。

### 8.2 解决方案

**不引入额外状态文件**。利用已有的文件作为状态载体：

- `recommendations.csv` 是跨运行的记忆体：包含所有历史建议及验证结果
- `portfolio.csv` 可能被人工更新（调仓后）
- `agent.log` 记录每轮运行的技术指标

每次运行开始时，Claude Code 读取 `recommendations.csv` 的全部内容（建议记录数预计在 50-200 条范围内，远在上下文窗口限制内），即可获取：
- 上期建议了什么
- 哪些已验证/未验证
- 历史准确率分布

### 8.3 激进方案（v0.2.0 考虑）

连续多期分析报告中，可以加入"趋势判断一致性检查"：
- 如果 agent 在连续 3 次报告中对同一标的的结论方向不同（买→卖→买），检查是否存在过度交易倾向

---

## 9. 成本估算模型

### 9.1 单次运行成本（中等复杂度，8 个持仓）

| 步骤 | 预估 Token | 成本（Haiku） | 成本（Sonnet） |
|------|-----------|-------------|---------------|
| 读取 portfolio.csv + recommendations.csv | ~2,000 | ~$0.002 | ~$0.006 |
| 数据采集（per symbol × 8，含 bash 输出） | ~10,000 | ~$0.01 | ~$0.03 |
| 历史验证 + 多轮推理分析 | ~30,000 | ~$0.03 | ~$0.09 |
| 报告生成 | ~5,000 | ~$0.005 | ~$0.015 |
| 杂项（工具调用 overhead） | ~3,000 | ~$0.003 | ~$0.009 |
| **单次合计** | **~50,000** | **~$0.05** | **~$0.15** |

### 9.2 月度成本估算

| 频率 | 推荐模型 | 月运行次数 | 月成本（估算） |
|------|---------|-----------|--------------|
| 每日 2 次（交易日） | Haiku | ~42 | ~$2 |
| 每日 2 次（交易日） | Sonnet | ~42 | ~$6 |
| 每日 1 次（交易日） | Haiku | ~22 | ~$1 |
| 每周 1 次 | Sonnet | ~4 | ~$0.60 |

> 实际成本随持仓数量、API 返回数据量、推理深度浮动。上述为保守上界估算。

### 9.3 数据源成本

FMP Free 和 FRED 均为免费套餐，在当前用量下数据源成本为 $0。

---

## 10. macOS 7×24 运行方案

### 10.1 防休眠

```bash
# 在 run-analysis.sh 开头加入，整个分析期间保持唤醒
caffeinate -i -w $$ &
CAFFEINATE_PID=$!

# ... Claude Code 分析流程 ...

kill $CAFFEINATE_PID 2>/dev/null  # 分析结束后释放
```

同时配置系统设置：
- 系统设置 → 电池/电源适配器 → 防止自动休眠 → 勾选
- 系统设置 → 锁定屏幕 → 设为"永不"（安全场景下的权衡）

### 10.2 为什么用 launchd 而不是 cron

| 特性 | launchd | cron |
|------|---------|------|
| 系统唤醒后自动恢复 | ✅ | ✅ |
| 崩溃后自动重启 | ✅ (KeepAlive) | ❌ |
| 环境变量支持 | ✅ | 需手动在 crontab 中 set |
| 执行频率控制 | ✅ (StartInterval / StartCalendarInterval) | ✅ |
| 日志自动记录 | ✅ (stdout/stderr → system log) | 需手动重定向 |
| macOS 原生集成 | ✅ | ⚠️ 功能子集 |

`~/Library/LaunchAgents/com.finance-agent.analysis.plist` 示例：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.finance-agent.analysis</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/yourname/finance-agent/run-analysis.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>9</integer>
        <key>Minute</key><integer>0</integer>
        <key>Weekday</key><integer>1</integer> <!-- 1-5 = 周一至周五 -->
    </dict>
    <key>WorkingDirectory</key>
    <string>/Users/yourname/finance-agent</string>
    <key>StandardOutPath</key>
    <string>/Users/yourname/finance-agent/logs/launchd-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/yourname/finance-agent/logs/launchd-stderr.log</string>
    <key>KeepAlive</key>
    <false/>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

加载：
```bash
launchctl load ~/Library/LaunchAgents/com.finance-agent.analysis.plist
```

### 10.3 `run-analysis.sh` 模板

```bash
#!/bin/bash
set -euo pipefail

PROJECT_DIR="/Users/yourname/finance-agent"
cd "$PROJECT_DIR"

# 保持系统唤醒
caffeinate -i -w $$ &
CAFFEINATE_PID=$!
trap "kill $CAFFEINATE_PID 2>/dev/null" EXIT

# 载入环境变量（API Keys）
set -a
source .env 2>/dev/null || true
set +a

# 激活 Python 虚拟环境
source venv/bin/activate

# 调用 Claude Code 执行分析
# 具体 CLI 参数以你安装的 Claude Code 官方用法为准
claude "请按照 CLAUDE.md 中定义的投资分析流程执行一次完整分析运行。触发时间: $(date '+%Y-%m-%d %H:%M')"

deactivate
echo "[$(date -Iseconds)] run-analysis completed" >> "$PROJECT_DIR/agent.log"
```

---

## 11. 市场覆盖与数据能力

### 11.1 目标市场（v0.0.1）

**优先**：美股 — FMP/FRED 数据最成熟，覆盖最全。

A 股/港股在 v0.2.0 之后考虑（FMP free tier 对 A 股和港股的支持有限，需要评估 akshare/tushare 等替代方案）。

### 11.2 数据类型覆盖

| 数据类型 | 来源 | 频率 |
|---------|------|------|
| 实时/延迟报价 | FMP (延迟15min) | 每次运行 |
| 历史价格（日线） | FMP / yfinance | 按需 |
| 技术指标 (RSI, MACD, MA, BB) | pandas-ta / 手动计算 | 每次运行 |
| 基本面 (PE, PB, MCap) | FMP | 每次运行 |
| 财报数据 | FMP | 按需（季报周期） |
| 新闻情绪 | FMP news endpoint + WebFetch | 每次运行 |
| 宏观经济（利率、CPI、就业、GDP） | FRED | 每次运行取一次 |
| 市场情绪（VIX） | FMP / yfinance | 每次运行 |

---

## 12. 项目文件结构（更新版）

```
finance-agent/
├── CLAUDE.md                 # Claude Code 项目指令：流程、路径、API Key 约定
├── portfolio.csv             # 持仓数据（严格 Schema）
├── recommendations.csv       # 历史建议与验证记录（反馈闭环）
├── .env                      # API Key（不纳入版本管理）
├── .gitignore                # 忽略 venv/ .env agent.log reports/
├── requirements.txt          # openbb, openbb-fmp, openbb-fred, pandas, yfinance
├── run-analysis.sh           # 包装脚本：caffeinate + 载入 env + 调 Claude Code
├── venv/                     # Python 虚拟环境
├── scripts/                  # 薄封装脚本（可选，仅封装高频重复命令）
│   └── notify.py             # 通知脚本（Telegram / Bark）
├── reports/                  # 生成的每次分析报告
│   └── analysis-2025-05-12-0900.md
├── logs/                     # launchd 输出日志
│   ├── launchd-stdout.log
│   └── launchd-stderr.log
└── agent.log                 # 结构化运行日志（NDJSON）
```

---

## 13. 实施计划

### Phase 1: 环境搭建
- [ ] 创建项目目录结构与 `.gitignore`
- [ ] 创建 Python venv，安装 `openbb` / `openbb-fmp` / `openbb-fred` / `pandas` / `yfinance`
- [ ] 注册 FMP 免费 API Key（https://financialmodelingprep.com/）
- [ ] 注册 FRED API Key（https://fred.stlouisfed.org/）
- [ ] 创建 `.env` 文件并写入 API Key
- [ ] 写 `requirements.txt`（`pip freeze > requirements.txt`）

### Phase 2: 数据文件
- [ ] 创建 `portfolio.csv`（按 §6.1 Schema，填入你的实际持仓）
- [ ] 创建 `recommendations.csv` 空模板（含表头）

### Phase 3: 核心配置
- [ ] 编写 `CLAUDE.md`（完整项目指令，包含：工作流、文件 Schema、OpenBB 调用方式、报告格式、验证逻辑、降级策略）
- [ ] 验证：在终端中手动运行一次 `claude "按 CLAUDE.md 执行一次分析"`，确认 OpenBB 数据通路正常

### Phase 4: 调度上线
- [ ] 编写 `run-analysis.sh`
- [ ] 创建 `launchd` `.plist` 配置文件并加载
- [ ] 观察 2-3 次手动触发后，确认流程稳定

### Phase 5: 迭代（v0.2.0）
- [ ] 基于第一周的 `recommendations.csv` 验证结果，校准分析策略
- [ ] 考虑加入技术指标自动回测
- [ ] 评估 A 股/港股数据源方案

---

## 14. 已知风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| FMP Free 套餐收紧/取消 | 中 | 高 | yfinance 作为 fallback；准备评估 Alpha Vantage 等替代 |
| OpenBB Platform 版本 Breaking Change | 中 | 中 | `requirements.txt` 固定版本号；大版本升级时手动验证 |
| API 限流导致部分标的无数据 | 中 | 低 | 报告标注"数据不可用"，不生成该标的建议 |
| launchd 未按预期触发 | 低 | 中 | 手动验证期；`agent.log` 记录每次触发时间可反查 |
| Claude Code CLI 参数/接口变更 | 低 | 中 | 包装脚本隔离 CLI 细节；变更只改一个文件 |
| 持仓数量增长导致 Token 成本失控 | 低 | 低 | 初期 8-10 标的安全；未来可引入分批分析策略 |
| 连续错误建议未被及时捕捉 | 中 | 中 | `recommendations.csv` 的连续错误标记机制已是设计的一部分 |

---

## 15. 版本路线图

| 版本 | 内容 |
|------|------|
| **v0.0.1**（当前） | PRD 完善：反馈闭环、技术方案、成本估算、Schema 定义、错误处理 |
| v0.1.0 | Phase 1-2：环境搭建 + 数据文件就绪，手动运行首次分析 |
| v0.2.0 | Phase 3-4：CLAUDE.md 编写 + launchd 调度上线，稳定运行 1 周 |
| v0.3.0 | Phase 5：反馈闭环数据回看，分析策略校准，评估 A 股/港股扩展 |
| v1.0.0 | 连续运行 1 个月无致命故障，准确率统计稳定 |

---

*Finance Agent PRD v0.0.1 | 2025-05-01 | 基于 v0.0.0 增强：反馈闭环 + 具体技术方案*
