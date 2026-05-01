# 个人 AI 投资分析代理 — 构建蓝图 v0.0.2

> v0.0.1 → v0.0.2 变更摘要：
> - **新增飞书交互通道**：通过飞书 Bot 实现与 agent 的双向交互（查询/订阅/推送）
> - 交互架构：`lark-event`（WebSocket 长连接监听）+ `lark-im`（消息发送）+ Claude Code（推理）
> - 新增交互意图路由：持仓诊断 / 个股快查 / 建议回溯 / 报告推送 / 市场异动提醒
> - 更新项目文件结构、成本估算、风险表、实施计划

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

构建一个运行在本地 Mac mini 上的 **AI 驱动投资分析代理**。代理负责：
1. 按计划自动采集市场数据，结合持仓生成结构化分析报告
2. 追踪历史建议并自我验证（反馈闭环）
3. **通过飞书 App 提供双向交互**：用户可随时向 agent 发送查询指令，agent 实时响应分析结果
4. 最终交易决策和执行 100% 由人工完成

### 非目标

- 不执行自动交易（无券商 API 接入）
- 不追求实时高频分析（日频/周频为主，按需查询除外）
- 不替代专业财务顾问的法律责任
- 不保证盈利
- 飞书 Bot 不对外开放（仅服务于所有者本人的 open_id）

---

## 1. 核心理念

- **自主可控**：核心逻辑、持仓数据与运行环境均部署在本地硬件上，保障隐私与控制权。
- **低成本运行**：充分利用本地算力与免费/低成本 API 数据源，模型成本集中在 Claude Code 调用。
- **原生与简洁**：不自写「主控脚本 + 工具注册」样板代码，由 **Claude Code** 负责会话、工具调用循环与任务拆解。
- **人机结合**：AI 负责自动化采集与分析并给出建议，人工保留最终决策与操作权。
- **反馈闭环**：每次建议都被记录并在后续运行中回溯验证，形成持续改进的正循环。
- **随身交互**：通过飞书 App 实现移动端随时查询、随时收到报告推送，不再需要 SSH 到 Mac mini 查看结果。

---

## 2. 技术栈（完整版）

| 类别 | 工具/技术 | 版本/约束 | 作用 |
|------|-----------|-----------|------|
| 硬件主机 | Mac mini | Apple Silicon, macOS 15+ | 7×24 本地运行环境 |
| 编排与推理 | **Claude Code** | 官方 CLI | 驱动分析、数据采集、报告生成、交互响应 |
| 大脑 (Brain) | Claude 模型 | 经 Claude Code 调用 | 深度分析与建议生成 |
| 数据引擎 | **OpenBB Platform v4** | `pip install openbb` | 统一数据接口 |
| 数据源 | FMP + FRED | 免费套餐 API Key | 股票价格/基本面 + 宏观经济 |
| 数据源（备选） | Yahoo Finance (yfinance) | `pip install yfinance` | FMP 超限/缺失时补位 |
| 交互通道 | **飞书 Bot** + lark-cli | 飞书开放平台应用 | 消息接收（WebSocket）+ 消息发送 |
| 事件监听 | **lark-event** | lark-cli 内置 Skill | WebSocket 长连接监听飞书消息事件 |
| 消息发送 | **lark-im** | lark-cli 内置 Skill | 主动推送报告、回复查询结果 |
| 身份校验 | **lark-contact** | lark-cli 内置 Skill | 验证消息发送者身份（open_id） |
| 持仓管理 | `portfolio.csv` | 严格 Schema | 持仓记录 |
| 建议追踪 | `recommendations.csv` | 严格 Schema | 反馈闭环核心 |
| 运行日志 | `agent.log` | NDJSON | 调试与审计 |
| 调度 | macOS `launchd` | 2 个 `.plist` | 定时分析 + 飞书监听守护 |
| 开发环境 | Python 3.12+ + venv | `requirements.txt` | 数据脚本执行环境 |

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

### 3.4 Free-Tier 限制与应对

| 数据源 | 免费额度 | 限制 |
|--------|---------|------|
| FMP Free | ~250 req/day | 个股基本面/价格/新闻，延迟 15min |
| FRED | ~120 req/min | 美国宏观经济指标 |
| yfinance（备选）| 无硬限制 | 非官方 API，稳定性稍差，覆盖全球 |

**应对策略**：
- 日常价格查询优先用 FMP（结构化、稳定）
- yfinance 作为 FMP 超限或数据缺失时的 fallback
- FRED 仅在需要宏观数据时使用，低频调用
- **按需查询也计入 FMP 日限额**，需在 `CLAUDE.md` 中明确配额分配（如 80% 给定时分析，20% 留给按需查询）

---

## 4. 交互架构总览

项目存在两条独立的执行路径：

```
┌──────────────────────────────────────────────────────────────────┐
│                    finance-agent 双路径架构                         │
├────────────────────────────┬─────────────────────────────────────┤
│  路径 A: 定时分析 (Push)     │  路径 B: 飞书交互 (Pull/On-demand)    │
│                            │                                     │
│  launchd 定时触发           │  launchd 守护飞书监听器 (始终在线)      │
│       ↓                    │       ↓                             │
│  run-analysis.sh           │  scripts/feishu-listener.sh         │
│       ↓                    │       ↓                             │
│  Claude Code               │  lark-event WebSocket               │
│  (完整 7-Step 工作流)       │  (监听消息事件)                       │
│       ↓                    │       ↓                             │
│  生成报告                   │  意图路由                             │
│       ↓                    │       ↓                             │
│  写入 recommendations.csv   │  Claude Code (按需会话)               │
│       ↓                    │       ↓                             │
│  推送飞书 (lark-im)         │  飞书回复 (lark-im)                   │
│       ↓                    │       ↓                             │
│  归档 reports/              │  追加 agent.log                      │
└────────────────────────────┴─────────────────────────────────────┘
```

### 关键约束

- 两路径共享 `portfolio.csv`、`recommendations.csv`、API Key（通过 `.env`）
- 两路径共享 `venv/` 下的 Python 环境，**但不会同时运行**（通过文件锁互斥，见 §4.3）
- 路径 B 的按需查询不写入 `recommendations.csv`（非正式建议），但会写入 `agent.log`

---

## 5. 路径 A：定时分析工作流（增强版）

路径 A 与 v0.0.1 一致，但 Step 7 的通知方式从"可选 Telegram/Bark"改为 **主推飞书**。

```
Trigger → Load → Collect → Think & Verify → Generate → Write Recs → Push Lark + Archive
```

### Step A1-A6

与 v0.0.1 §4 的 Step 1-6 完全一致。

### Step A7: 推送飞书 + 归档

- 报告写入 `reports/analysis-YYYY-MM-DD-HHmm.md`
- **通过 `lark-im` 将报告摘要推送到飞书**（因飞书单条消息长度限制，推送摘要 + 关键建议表；完整报告通过飞书文档或文件消息推送）
- 追加一行到 `agent.log`（运行摘要）
- 会话结束

### 飞书推送格式

推送消息为结构化卡片或 Markdown 文本：

```
📊 投资分析报告 | 2025-05-12 09:00

**市场总览**
标普500: 5,280 (+0.4%) | VIX: 16.2 | 10Y: 4.25%

**操作建议**
• AAPL → 持有 (置信度 4/5) — PE 合理，趋势向上
• TSLA → 减仓30% (置信度 3/5) — RSI 超买，估值偏高
• NVDA → 加仓10% (置信度 4/5) — 回调至支撑位

**历史准确率** 过去30天: 7/10 (70%)
---
完整报告: reports/analysis-2025-05-12-0900.md
```

---

## 6. 路径 B：飞书交互通道设计（核心新增）

### 6.1 设计目标

用户通过飞书 App 随时随地与 agent 交互，无需 SSH 登录 Mac mini。支持：
- **查询类**：持仓诊断、个股快查、建议回溯、准确率统计
- **指令类**：触发一次即时分析
- **推送类**：定时报告自动推送、市场异动提醒

### 6.2 整体数据流

```
┌──────────┐    消息事件      ┌──────────┐   WebSocket    ┌───────────────┐
│ 用户飞书  │ ──────────────→ │ 飞书服务器 │ ─────────────→ │ Mac mini       │
│  App     │ ←────────────── │          │ ←───────────── │ feishu-listener│
└──────────┘   回复消息       └──────────┘   HTTP API      │  ↓             │
                                                     │ intent_router  │
                                                     │  ↓             │
                                                     │ Claude Code    │
                                                     │  ↓             │
                                                     │ lark-im send   │
                                                     └───────────────┘
```

### 6.3 飞书 Bot 准备（一次性配置）

在 [飞书开放平台](https://open.feishu.cn/) 创建企业自建应用：

| 配置项 | 值 | 说明 |
|--------|-----|------|
| 应用类型 | 企业自建应用 | 仅组织内可用 |
| 可用范围 | 仅创建者本人 | 安全策略：不对外开放 |
| 权限 (scope) | `im:message:send_as_bot` | Bot 发送消息 |
|  | `im:message:read` | 读取用户消息 |
|  | `im:message:event` | 接收消息事件推送 |
| 事件订阅 | `im.message.receive_v1` | 接收用户发给 Bot 的单聊消息 |
| 订阅方式 | WebSocket 长连接 | 无需公网 URL，本地 Mac mini 直连 |

```bash
# 使用 lark-cli 以独立 profile 初始化配置（在 Mac mini 上执行一次）
# --profile finance-agent 确保与用户本机其他 lark-cli 配置隔离
lark-cli --profile finance-agent auth login

# 配置完成后验证
lark-cli --profile finance-agent im send --user <your_open_id> --msg "Agent 上线"
```

### 6.4 消息监听：`scripts/feishu-listener.sh`

核心组件：一个由 `launchd` 守护的长驻脚本，通过 `lark-event` 监听飞书 WebSocket 事件。

```bash
#!/bin/bash
# feishu-listener.sh — 飞书消息监听守护进程
set -euo pipefail

PROJECT_DIR="/Users/yourname/finance-agent"
cd "$PROJECT_DIR"

# ── 前置检查 ──
# 1. 检查 lark-cli 是否安装
if ! command -v lark-cli &> /dev/null; then
  echo "[FATAL] lark-cli 未安装，请先安装 lark-cli 后重新运行。" >&2
  exit 1
fi

# 2. 检查 finance-agent profile 是否已登录
if ! lark-cli --profile finance-agent contact +get-user &> /dev/null; then
  echo "[FATAL] finance-agent profile 未登录，请先执行: lark-cli --profile finance-agent auth login" >&2
  exit 1
fi

# 载入环境变量
set -a
source .env 2>/dev/null || true
set +a

source venv/bin/activate

# lark-event 通过 WebSocket 长连接监听消息事件
# 输出为 NDJSON，每行一条事件，通过管道交给 intent_router 处理
lark-event --profile finance-agent \
  --event-type im.message.receive_v1 \
  --compact \
  --output-ndjson \
  | while IFS= read -r event_line; do
      # 提取消息内容与发送者信息
      MSG_TEXT=$(echo "$event_line" | jq -r '.message.content // empty')
      SENDER_ID=$(echo "$event_line" | jq -r '.sender.open_id // empty')
      MSG_ID=$(echo "$event_line" | jq -r '.message.message_id // empty')

      # 身份校验：仅响应白名单用户
      if [[ "$SENDER_ID" != "$ALLOWED_OPEN_ID" ]]; then
        echo "[$(date -Iseconds)] rejected unknown sender: $SENDER_ID" >> agent.log
        continue
      fi

      # 解析意图并路由到 Claude Code 会话
      bash scripts/intent_router.sh "$MSG_TEXT" "$SENDER_ID" "$MSG_ID" &
    done
```

### 6.5 意图路由：`scripts/intent_router.sh`

```bash
#!/bin/bash
# intent_router.sh — 解析用户意图并路由到对应的 Claude Code 会话
MSG_TEXT="$1"
SENDER_ID="$2"
MSG_ID="$3"
PROJECT_DIR="/Users/yourname/finance-agent"
LOCK_FILE="$PROJECT_DIR/.analysis.lock"

cd "$PROJECT_DIR"

# 互斥锁：避免与定时分析同时运行
exec 200>"$LOCK_FILE"
flock -n 200 || {
  lark-cli --profile finance-agent im send --user "$SENDER_ID" --msg "Agent 正在执行分析任务，请稍后再试。"
  exit 0
}

log_event() {
  jq -n --arg ts "$(date -Iseconds)" --arg event "feishu_query" --arg sender "$SENDER_ID" --arg mid "$MSG_ID" --arg msg "$1" \
    '{ts: $ts, event: $event, sender: $sender, message_id: $mid, msg: $msg}' >> agent.log
}

log_event "$MSG_TEXT"

INTENT=$(echo "$MSG_TEXT" | tr '[:upper:]' '[:lower:]')

# 对消息文本做 shell 转义，防止 $MSG_TEXT 包含的特殊字符被 bash 解释
MSG_TEXT_SAFE=$(printf '%s' "$MSG_TEXT" | sed 's/[\\"$`]/\\&/g')

# ── 意图识别与路由 ──

if echo "$INTENT" | grep -qE "^报告$|最新报告"; then
  # 报告查询：读取最新报告直接推送，不经过 Claude Code
  LATEST=$(find reports/ -maxdepth 1 -name 'analysis-*.md' 2>/dev/null | sort -r | head -1)
  if [[ -n "$LATEST" ]]; then
    # 飞书单条消息约 3000 字符限制，预留 200 字符给前后缀，取报告前 2800 字符作为摘要
    SUMMARY=$(head -c 2800 "$LATEST")
    lark-cli --profile finance-agent im send --user "$SENDER_ID" --msg "$(printf '📊 最新分析报告\n\n%s\n\n──\n完整报告: %s' "$SUMMARY" "$LATEST")"
  else
    lark-cli --profile finance-agent im send --user "$SENDER_ID" --msg "暂无分析报告，请先触发一次定时分析或使用「分析」指令。"
  fi

elif echo "$INTENT" | grep -qE "分析|持仓|诊断|portfolio"; then
  # 完整持仓分析
  claude "用户通过飞书发起了一次持仓分析请求。请按照 CLAUDE.md 中的完整工作流执行分析，但注意：
  1. 报告需要精简（控制在飞书单条消息长度内，约 3000 字符），核心输出「操作建议表」+「历史准确率」
  2. 不要归档到 reports/（这是按需查询，非定时分析）
  3. 不要写入 recommendations.csv（非正式建议）
  4. 分析结束后，将结果通过飞书发送给用户 open_id=$SENDER_ID
  4.1 飞书发送消息请使用: lark-cli --profile finance-agent im send --user \"$SENDER_ID\" --msg \"...\"
  触发时间: $(date '+%Y-%m-%d %H:%M') | 触发方式: 飞书按需"

elif echo "$INTENT" | grep -qE "^[a-z]{1,5}$|查.*价格|看.*[a-z]{1,5}|快查"; then
  # 个股快查：从消息中提取 ticker
  TICKER=$(echo "$MSG_TEXT" | grep -oE '[A-Za-z]{1,5}' | head -1)
  if [[ -z "$TICKER" ]]; then
    lark-cli --profile finance-agent im send --user "$SENDER_ID" --msg "未识别到股票代码，请使用如「查 AAPL」或「TSLA」的格式。"
    flock -u 200
    exit 0
  fi
  claude "用户通过飞书查询 $TICKER 的当前状态。请执行快速诊断（仅该标的）：
  1. 拉取价格、技术指标（RSI/MACD/MA）、基本面
  2. 输出：当前价、涨跌幅、技术面判断、是否处于目标区间、一句话操作建议
  3. 结果控制在 1500 字符内
  4. 通过飞书发送给用户 open_id=$SENDER_ID
  4.1 飞书发送消息请使用: lark-cli --profile finance-agent im send --user \"$SENDER_ID\" --msg \"...\""

elif echo "$INTENT" | grep -qE "准确率|回溯|建议.*记录|历史|verify"; then
  # 建议回溯查询
  claude "用户通过飞书查询历史建议准确率。请读取 recommendations.csv，输出：
  1. 最近 30 天准确率统计（按 action 类型 + 按标的分）
  2. 连续失误标记
  3. 结果控制在 2000 字符内
  4. 通过飞书发送给用户 open_id=$SENDER_ID
  4.1 飞书发送消息请使用: lark-cli --profile finance-agent im send --user \"$SENDER_ID\" --msg \"...\""

elif echo "$INTENT" | grep -qE "help|帮助|命令|怎么用"; then
  lark-cli --profile finance-agent im send --user "$SENDER_ID" --msg "$(cat <<'HELP'
📋 Finance Agent 可用指令：

• 「分析」/「持仓诊断」 — 完整持仓分析与操作建议
• 「AAPL」/「查 TSLA」 — 个股快速诊断（价格+技术指标+建议）
• 「准确率」/「历史回溯」 — 查看历史建议准确率统计
• 「报告」 — 推送最近一次定时分析报告
• 「异动提醒开/关」 — 开启/关闭价格异动提醒
HELP
)"

else
  # 通用问答：交由 Claude Code 自由理解
  claude "用户通过飞书发送了以下消息: ${MSG_TEXT_SAFE}
这是来自投资分析 agent 用户的按需查询。请根据 CLAUDE.md 上下文和当前数据文件（portfolio.csv, recommendations.csv），用投资分析的视角回答用户的问题。回答控制在 2000 字符内。通过飞书发送给用户 open_id=$SENDER_ID。飞书发送消息请使用: lark-cli --profile finance-agent im send --user \"$SENDER_ID\" --msg \"...\""
fi

flock -u 200
```

### 6.6 支持的交互模式

| 用户输入（示例） | 意图 | 处理方式 | 响应时间 |
|-----------------|------|---------|---------|
| "分析"/"持仓诊断"/"看看组合" | 完整持仓分析 | 路径A完整流程（精简输出） | ~5-10min |
| "AAPL"/"查一下NVDA"/"TSLA价格" | 个股快查 | 仅取该标的数据 + 快速诊断 | ~1-2min |
| "准确率"/"历史建议"/"回溯" | 建议准确率统计 | 读 recommendations.csv 直接统计 | ~30s |
| "报告"/"最新报告" | 推送最近报告 | 读 reports/ 目录最新文件推送 | ~10s |
| "异动提醒开"/"异动提醒关" | 开关异动提醒 | 写配置标记文件 | ~5s |
| "帮助"/"help" | 帮助 | 脚本直接返回，不经过 Claude Code | <1s |
| 其他自然语言 | 通用投资问答 | Claude Code 自由回答 | ~1-3min |

### 6.7 价格异动提醒（可选，v0.3.0）

当某持仓标的价格单日波动超过阈值（如 ±5%），agent 主动推送飞书提醒：

```
🚨 价格异动提醒
TSLA 当前 $172.30 (-7.2%)
触发阈值: -5%
建议: 查看 news 是否有利空事件
```

实现方式：在定时分析（路径A）的 Step 3 数据采集阶段，若检测到异动，在完成报告推送后额外发送异动提醒。此处预先设计架构，实际实现在 v0.3.0。

---

## 7. 反馈闭环设计

与 v0.0.1 §5 完全一致。`recommendations.csv` 是跨路径共享的持久记忆体。

---

## 8. 数据文件 Schema

### 8.1 `portfolio.csv` 严格 Schema

与 v0.0.1 §6.1 完全一致。

### 8.2 `recommendations.csv` Schema

与 v0.0.1 §5.2 完全一致。

### 8.3 `agent.log` 格式（扩展）

新增飞书交互事件类型：

```json
{"ts":"2025-05-12T09:00:00","event":"start","trigger":"launchd","run_id":"scheduled-20250512-0900"}
{"ts":"2025-05-12T10:23:15","event":"feishu_query","sender":"ou_xxx","msg":"查一下 NVDA"}
{"ts":"2025-05-12T10:23:20","event":"feishu_query","sender":"ou_xxx","msg":"持仓诊断"}
{"ts":"2025-05-12T10:25:00","event":"response_sent","target":"ou_xxx","tokens_est":8000,"duration_s":105}
{"ts":"2025-05-12T09:10:00","event":"end","run_id":"scheduled-20250512-0900","duration_s":600,"api_calls":12,"tokens_est":25000}
```

---

## 9. 错误处理与降级策略（扩展）

在 v0.0.1 §7 基础上，新增飞书相关：

| 级别 | 场景 | 处理方式 |
|------|------|---------|
| WARN | 飞书 WebSocket 断开 | `lark-event` 自动重连；断连期间消息不丢失（飞书服务器重试） |
| WARN | 飞书发消息失败（限流/网络） | 重试 3 次，指数退避；仍失败则写入 `agent.log` 并跳过 |
| ERROR | 按需查询与定时分析冲突 | 文件锁互斥，按需查询排队等待或返回"忙碌"提示 |
| FATAL | `feishu-listener.sh` 进程崩溃 | `launchd KeepAlive=true` 自动重启 |

### 互斥锁机制

```bash
# 定时分析和按需查询共享 .analysis.lock
# run-analysis.sh 开头：
exec 200>".analysis.lock"
flock 200

# intent_router.sh 中：
exec 200>".analysis.lock"
flock -n 200 || { /* 返回忙碌 */ }
```

---

## 10. 成本估算模型（飞书交互更新）

### 10.1 定时分析成本（不变）

| 频率 | 推荐模型 | 月运行次数 | 月成本 |
|------|---------|-----------|--------|
| 每日 2 次（交易日） | Haiku | ~42 | ~$2 |
| 每日 2 次（交易日） | Sonnet | ~42 | ~$6 |

### 10.2 飞书按需查询成本

| 查询类型 | 预估 Token/次 | 预估成本/次 (Haiku) | 月频次估算 |
|---------|-------------|-------------------|-----------|
| 个股快查 | ~8,000 | ~$0.008 | ~20 |
| 完整持仓分析 | ~50,000 | ~$0.05 | ~10 |
| 准确率回溯 | ~3,000 | ~$0.003 | ~5 |
| 通用问答 | ~5,000 | ~$0.005 | ~10 |
| **按需总计** | — | — | **~$0.70/月** |

### 10.3 综合月成本

| 方案 | 定时分析 | 按需查询 | 合计 |
|------|---------|---------|------|
| 经济型（Haiku） | ~$2 | ~$0.70 | **~$2.70/月** |
| 质量型（Sonnet定时 + Haiku按需） | ~$6 | ~$0.70 | **~$6.70/月** |

---

## 11. macOS 7×24 运行方案（双 launchd 作业）

### 11.1 作业一：定时分析（`com.finance-agent.analysis.plist`）

与 v0.0.1 §10.2 一致，每个交易日 9:00 / 13:00 触发 `run-analysis.sh`。

### 11.2 作业二：飞书监听守护（`com.finance-agent.feishu.plist`）

全天候运行，崩溃自动重启。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.finance-agent.feishu</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/yourname/finance-agent/scripts/feishu-listener.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/yourname/finance-agent</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ALLOWED_OPEN_ID</key>
        <string>ou_your_open_id_here</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key>
        <string>/Users/yourname</string>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/yourname/finance-agent/logs/feishu-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/yourname/finance-agent/logs/feishu-stderr.log</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
```

加载：
```bash
launchctl load ~/Library/LaunchAgents/com.finance-agent.feishu.plist
```

### 11.3 两个作业的互斥

定时分析作业 (`run-analysis.sh`) 和飞书按需查询 (`intent_router.sh`) 通过 `.analysis.lock` 文件锁互斥，确保不会同时运行两个 Claude Code 会话消耗双倍 token。

---

## 12. 市场覆盖与数据能力

与 v0.0.1 §11 完全一致。飞书按需查询共享同一套数据源和频率限制。

---

## 13. 项目文件结构（最终版）

```
finance-agent/
├── CLAUDE.md                      # Claude Code 项目指令
├── portfolio.csv                  # 持仓数据（严格 Schema）
├── recommendations.csv            # 历史建议与验证记录（反馈闭环）
├── .env                           # API Key + ALLOWED_OPEN_ID（不入版本管理）
├── .gitignore                     # 忽略 venv/ .env agent.log reports/ logs/ .analysis.lock
├── .analysis.lock                 # 运行互斥锁（runtime 自动创建）
├── requirements.txt               # openbb, openbb-fmp, openbb-fred, pandas, yfinance
├── run-analysis.sh                # 定时分析包装脚本
├── venv/                          # Python 虚拟环境
├── scripts/
│   ├── feishu-listener.sh         # 飞书 WebSocket 长连接监听守护
│   ├── intent_router.sh           # 用户消息意图路由
│   └── notify.py                  # 辅助通知脚本（备用，主通道为飞书）
├── reports/                       # 定时分析报告存档
│   └── analysis-2025-05-12-0900.md
├── logs/                          # launchd/feishu 输出日志
│   ├── launchd-stdout.log
│   ├── launchd-stderr.log
│   ├── feishu-stdout.log
│   └── feishu-stderr.log
└── agent.log                      # 结构化运行日志（NDJSON）
```

---

## 14. 安全设计（飞书 Bot 专项）

| 措施 | 说明 |
|------|------|
| **仅响应白名单用户** | `ALLOWED_OPEN_ID` 环境变量限定唯一 open_id，拒绝所有他人消息 |
| **Bot 不加入群聊** | 仅支持单聊，避免群内信息泄露 |
| **不存储飞书消息内容于云端** | 所有消息上下文仅在本地 Claude Code 会话中处理，不写入飞书文档 |
| **API Key 不入飞书** | `.env` 仅在本地，飞书消息中不会出现 API Key |
| **指令不回显敏感数据** | 持仓数量、成本等敏感数据仅在报告中显示，不在意图路由脚本中回显 |
| **飞书 App 可用范围** | 仅创建者本人可见，组织内其他成员无法搜索和发现该 Bot |

---

## 15. 实施计划（更新版）

### Phase 1: 环境搭建
- [ ] 创建项目目录结构与 `.gitignore`
- [ ] 创建 Python venv，安装 `openbb` / `openbb-fmp` / `openbb-fred` / `pandas` / `yfinance`
- [ ] 注册 FMP 免费 API Key
- [ ] 注册 FRED API Key
- [ ] 创建 `.env` 文件并写入 API Key
- [ ] 写 `requirements.txt`

### Phase 2: 飞书 Bot 配置
- [ ] 在飞书开放平台创建企业自建应用
- [ ] 配置 `im:message:send_as_bot` / `im:message:read` / `im:message:event` 权限
- [ ] 订阅 `im.message.receive_v1` 事件（WebSocket 方式）
- [ ] 获取并记录自己的 `open_id`（通过 `lark-cli --profile finance-agent auth login`）
- [ ] 验证：`lark-cli --profile finance-agent im send --user <open_id> --msg "hello"` 确认通路

### Phase 3: 数据文件
- [ ] 创建 `portfolio.csv`（按 Schema，填入你的实际持仓）
- [ ] 创建 `recommendations.csv` 空模板（含表头）

### Phase 4: 核心脚本
- [ ] 编写 `CLAUDE.md`（完整项目指令：路径A工作流 + 路径B响应格式 + 飞书发消息方式）
- [ ] 编写 `scripts/feishu-listener.sh`
- [ ] 编写 `scripts/intent_router.sh`
- [ ] 编写 `run-analysis.sh`

### Phase 5: 本地验证
- [ ] 手动运行 `run-analysis.sh`，确认 OpenBB 数据通路正常
- [ ] 手动运行 `scripts/feishu-listener.sh`，在飞书发送测试消息，确认意图路由正确
- [ ] 确认互斥锁逻辑正确（两个 Claude Code 会话不会同时跑）

### Phase 6: 调度上线
- [ ] 创建 `com.finance-agent.analysis.plist` 并加载（定时分析）
- [ ] 创建 `com.finance-agent.feishu.plist` 并加载（飞书监听）
- [ ] 观察 2-3 天，确认定时分析 + 按需查询均稳定

### Phase 7: 迭代（v0.3.0）
- [ ] 基于 `recommendations.csv` 验证结果校准策略
- [ ] 价格异动提醒功能
- [ ] 评估 A 股/港股数据源

---

## 16. 已知风险与缓解（更新版）

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| FMP Free 套餐收紧/取消 | 中 | 高 | yfinance fallback；评估 Alpha Vantage |
| OpenBB Platform Breaking Change | 中 | 中 | `requirements.txt` 固定版本号 |
| API 限流（定时+按需合计超限） | 中 | 中 | 配额策略：80%定时/20%按需；agent.log 反查 |
| 飞书 WebSocket 长连接断开 | 中 | 中 | `lark-event` 内置自动重连；`launchd KeepAlive` 兜底 |
| 飞书开放平台 API 变更 | 低 | 中 | lark-cli 封装层隔离；变更只影响 shell 脚本 |
| 两个 Claude Code 会话同时运行 | 低 | 高 | `.analysis.lock` 文件锁互斥 |
| 持仓增长导致 Token 成本失控 | 低 | 低 | 初期 8-10 标的安全；未来可分批分析 |
| 飞书 Bot 被他人发现/滥用 | 低 | 高 | 仅白名单 open_id；应用可用范围限制创建者本人 |
| Mac mini 断电/断网 | 低 | 高 | `launchd RunAtLoad` 开机自启；路由器/电源检查 |
| lark-cli 未登录/token 过期 | 中 | 中 | `feishu-listener.sh` 启动时检测登录状态，失败时写 log 告警 |

---

## 17. 版本路线图

| 版本 | 内容 |
|------|------|
| **v0.0.2**（当前） | PRD 完善：飞书交互通道 + 意图路由 + 双 launchd 架构 + 安全设计 |
| v0.1.0 | Phase 1-3：飞书 Bot 创建 + 环境搭建 + 数据文件就绪 |
| v0.2.0 | Phase 4-6：CLAUDE.md + 核心脚本 + launchd 上线，稳定运行 1 周 |
| v0.3.0 | Phase 7：策略校准 + 价格异动提醒 |
| v1.0.0 | 连续运行 1 个月无致命故障，准确率统计稳定 |

---

*Finance Agent PRD v0.0.2 | 2025-05-01 | 新增：飞书交互通道*
