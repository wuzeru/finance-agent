#!/usr/bin/env bash
# run-analysis.sh — 定时分析包装脚本
# 由 launchd 在工作日 9:00 / 13:00 触发
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
cd "$PROJECT_ROOT"

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

# Generate run ID early (used in subsequent log entries)
RUN_ID="scheduled-$(date +%Y%m%d-%H%M)"

# Prevent sleep during analysis (macOS)
CAFFEINATE_PID=""
if command -v caffeinate &>/dev/null; then
    caffeinate -i -w $$ &
    CAFFEINATE_PID=$!
fi

# shellcheck disable=SC2329  # cleanup is invoked via trap
cleanup() {
    if [[ -n "$CAFFEINATE_PID" ]]; then
        kill "$CAFFEINATE_PID" 2>/dev/null || true
    fi
    rmdir .analysis.lock 2>/dev/null || true
}
# trap registered after lock acquisition below

# Load environment variables
set -a; source .env; set +a

# 检查必需的环境变量
if [[ -z "${ALLOWED_OPEN_ID:-}" ]]; then
    echo "[FATAL] ALLOWED_OPEN_ID 环境变量未设置，请在 .env 中配置。" >&2
    rmdir .analysis.lock 2>/dev/null || true
    exit 1
fi

# Activate Python virtual environment (tolerate missing venv)
# shellcheck disable=SC1091  # venv may not exist yet
if [[ -f venv/bin/activate ]]; then
    source venv/bin/activate
else
    echo "{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"run_id\":\"$RUN_ID\",\"event\":\"warn\",\"detail\":\"venv not found\"}" >> agent.log
fi

# Ensure directories exist
mkdir -p reports logs

# Acquire mkdir-based lock (blocking — wait if another analysis is running).
# Uses mkdir atomicity: succeeds only if directory does not exist.
while true; do
    # Stale lock cleanup (>30 min means previous holder likely crashed)
    if [[ -d .analysis.lock ]]; then
        lock_age=$(($(date +%s) - $(stat -f %m .analysis.lock 2>/dev/null)))
        if [[ $lock_age -gt 1800 ]]; then
            rmdir .analysis.lock 2>/dev/null || true
        fi
    fi
    mkdir .analysis.lock 2>/dev/null && break
    sleep 2
done

# Register cleanup trap (now includes lock release)
trap cleanup EXIT

# Log start of run
echo "{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"run_id\":\"$RUN_ID\",\"event\":\"start\"}" >> agent.log

# Execute full Path A 7-Step workflow via Claude Code.
# Step 1 (lock) is handled above by the shell script.
# Claude executes Steps 2–7 as defined in CLAUDE.md.
claude -p "执行 Path A 完整 7-Step 定时分析工作流（Step 1 锁获取已由脚本完成）：
- Step 2: 读取 portfolio.csv 和 recommendations.csv 到上下文
- Step 3: 通过 OpenBB 获取所有持仓数据（优先 FMP，备选 yfinance）+ 全局宏观数据（S&P 500, VIX, 10Y收益率, 联邦基金利率）
- Step 4: 验证 pending 建议（3个交易日内的跳过，买入/卖出按目标价验证，hold 验证无>5%不利波动，>30交易日无触发标为expired）；分析当前持仓
- Step 5: 生成完整报告写入 reports/analysis-YYYY-MM-DD-HHmm.md（含市场概况、历史回顾、持仓诊断、建议表、风险提示）
- Step 6: 追加新建议到 recommendations.csv（status=pending，顺序 ID）
- Step 7: 通过 lark-cli --profile finance-agent im +messages-send --user \"$ALLOWED_OPEN_ID\" --msg \"...\" 推送报告摘要到飞书；日志写入 agent.log

用中文生成所有输出和报告。所有数据获取优先使用 FMP provider，失败时 fallback 到 yfinance。" < /dev/null 2>>logs/claude-error.log

CLAUDE_EXIT=$?

# Log end of run
echo "{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"run_id\":\"$RUN_ID\",\"event\":\"end\",\"exit_code\":$CLAUDE_EXIT}" >> agent.log

exit $CLAUDE_EXIT
