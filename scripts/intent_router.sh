#!/usr/bin/env bash
set -euo pipefail

MSG_TEXT="${1:-}"
SENDER_ID="${2:-}"
MSG_ID="${3:-}"

if [[ -z "$MSG_TEXT" ]] || [[ -z "$SENDER_ID" ]]; then
    echo "Usage: $0 <MSG_TEXT> <SENDER_ID> [MSG_ID]" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load environment variables
set -a; source "$PROJECT_ROOT/.env"; set +a

# === Helpers ===

log_event() {
    local route="$1"
    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    echo "{\"ts\":\"$ts\",\"sender_id\":\"$SENDER_ID\",\"route\":\"$route\",\"msg_id\":\"$MSG_ID\",\"event\":\"query\"}" >> "$PROJECT_ROOT/agent.log"
}

reply() {
    local msg="$1"
    lark-cli --profile finance-agent im +messages-send --user "$SENDER_ID" --msg "$msg"
}

# Acquire mkdir-based lock (non-blocking) to prevent concurrent Claude Code sessions.
# Uses mkdir atomicity: succeeds only if directory does not exist.
LOCK_DIR="$PROJECT_ROOT/.analysis.lock"

# Stale lock cleanup: if the lock dir is older than 30 minutes, the previous
# holder likely crashed — remove it so we don't get stuck permanently.
if [[ -d "$LOCK_DIR" ]]; then
    lock_age=$(($(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null)))
    if [[ $lock_age -gt 1800 ]]; then
        rmdir "$LOCK_DIR" 2>/dev/null || true
    fi
fi

LOCK_ACQUIRED=0
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    reply "Agent 正在执行分析任务，请稍后再试。"
    exit 0
fi
LOCK_ACQUIRED=1

# Release lock on exit (only if this process created it)
trap 'if [[ $LOCK_ACQUIRED -eq 1 ]]; then rmdir "$LOCK_DIR" 2>/dev/null || true; fi' EXIT

# Trim whitespace
MSG_TEXT="$(echo "$MSG_TEXT" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

# === Route 1: Help (no Claude call — static response) ===
if echo "$MSG_TEXT" | grep -qE '帮助|help|命令|怎么用'; then
    log_event "help"
    reply "$(cat <<'EOF'
可用命令:
• 分析 / 持仓 / 诊断 — 完整投资组合分析
• 查 AAPL 或直接输入代码 — 个股快速诊断
• 准确率 / 回溯 / 历史 — 查看历史建议准确率
• 报告 — 获取最新分析报告
• 开启/关闭异动提醒 — 控制异动提醒
• 帮助 / help — 显示本帮助
EOF
)"
    exit 0
fi

# === Route 2: Alert toggle (no Claude call — filesystem toggle) ===
if echo "$MSG_TEXT" | grep -qE '异动提醒|alert'; then
    log_event "alert_toggle"
    ALERT_FILE="$PROJECT_ROOT/.alert-enabled"
    if echo "$MSG_TEXT" | grep -qE '开启|启用|on|start'; then
        touch "$ALERT_FILE"
        reply "异动提醒已开启。"
    elif echo "$MSG_TEXT" | grep -qE '关闭|禁用|off|stop'; then
        rm -f "$ALERT_FILE"
        reply "异动提醒已关闭。"
    else
        if [[ -f "$ALERT_FILE" ]]; then
            reply "异动提醒当前状态: 已开启。发送「关闭异动提醒」可关闭。"
        else
            reply "异动提醒当前状态: 已关闭。发送「开启异动提醒」可开启。"
        fi
    fi
    exit 0
fi

# === Route 3: Accuracy / History / Verify ===
if echo "$MSG_TEXT" | grep -qE '准确率|回溯|历史|verify'; then
    log_event "accuracy"
    cd "$PROJECT_ROOT"
    reply=$(claude -p "读取 recommendations.csv，输出按 action/symbol/confidence 分组的准确率统计。用中文回复，不超过 2000 字。" < /dev/null 2>/dev/null)
    if [[ -n "$reply" ]]; then
        reply "$reply"
    else
        reply "未能获取准确率数据，请稍后重试。"
    fi
    exit 0
fi

# === Route 4: Single Ticker Quick Diagnosis ===
# Match: 1-10 uppercase letters/colons (e.g. AAPL, BRK.B, 0700.HK) OR "查 XXXX"
TICKER=""
if echo "$MSG_TEXT" | grep -qE '^查[[:space:]]+'; then
    TICKER=$(echo "$MSG_TEXT" | sed 's/^查[[:space:]]*//' | tr -d '[:space:]')
elif echo "$MSG_TEXT" | grep -qE '^[A-Z:.0-9]{1,10}$'; then
    TICKER="$MSG_TEXT"
fi

if [[ -n "$TICKER" ]]; then
    log_event "quick_diagnosis"
    cd "$PROJECT_ROOT"
    reply=$(claude -p "对 $TICKER 进行快速技术诊断。通过 OpenBB 获取当前价格、RSI(14)、MACD、20/50/200 MA，给出一句话投资建议。用中文回复，不超过 1500 字。只读操作，不写任何文件。" < /dev/null 2>/dev/null)
    if [[ -n "$reply" ]]; then
        reply "$reply"
    else
        reply "未能获取 $TICKER 的诊断数据，请检查代码是否正确。"
    fi
    exit 0
fi

# === Route 5: Report ===
if echo "$MSG_TEXT" | grep -q '报告'; then
    log_event "report"
    cd "$PROJECT_ROOT"
    if [[ -d reports ]] && [[ -n "$(find reports -maxdepth 1 -type f -name '*.md' 2>/dev/null | head -1)" ]]; then
        latest_report=$(find reports -maxdepth 1 -type f -name '*.md' | sort -r | head -1)
        reply=$(claude -p "读取 $latest_report 文件，将其内容作为飞书消息推送。用中文回复，只读操作。" < /dev/null 2>/dev/null)
        if [[ -n "$reply" ]]; then
            reply "$reply"
        else
            reply "未能推送报告内容，请稍后重试。"
        fi
    else
        reply "暂无分析报告。报告会在每日定时分析后生成。"
    fi
    exit 0
fi

# === Route 6: Full Path B Analysis (no writes to recs/reports) ===
if echo "$MSG_TEXT" | grep -qE '分析|持仓|诊断|portfolio'; then
    log_event "full_analysis"
    cd "$PROJECT_ROOT"
    reply=$(claude -p "执行 Path B 完整投资组合分析工作流，按 CLAUDE.md 定义的 Steps 2-5 执行：
1. 读取 portfolio.csv 和 recommendations.csv
2. 通过 OpenBB 获取所有持仓的市场数据（优先 FMP，备选 yfinance）
3. 验证 pending 建议 + 分析持仓
4. 生成分析报告（含市场概况、历史回顾、持仓诊断、建议表、风险提示）
注意：这是按需查询，不写 recommendations.csv，不归档到 reports/。分析结束后通过飞书发送给用户。用中文回复，不超过 3000 字。" < /dev/null 2>/dev/null)
    if [[ -n "$reply" ]]; then
        reply "$reply"
    else
        reply "分析执行失败，请稍后重试。"
    fi
    exit 0
fi

# === Route 7: General Investment Q&A (catch-all) ===
log_event "general_qa"
cd "$PROJECT_ROOT"

# Escape double-quotes in user message for safe prompt interpolation
MSG_TEXT_SAFE="${MSG_TEXT//\"/\\\"}"

reply=$(claude -p "简短回答以下投资问题，用中文回复，不超过 2000 字。只读操作，不写任何文件: $MSG_TEXT_SAFE" < /dev/null 2>/dev/null)
if [[ -n "$reply" ]]; then
    reply "$reply"
else
    reply "抱歉，我无法处理此请求。请尝试「帮助」查看可用命令。"
fi
