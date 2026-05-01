#!/bin/bash
# intent_router.sh — 解析用户意图并路由到对应的 Claude Code 会话
set -euo pipefail

MSG_TEXT="$1"
SENDER_ID="$2"
MSG_ID="$3"

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOCK_FILE="$PROJECT_DIR/.analysis.lock"

cd "$PROJECT_DIR"

# 互斥锁：避免与定时分析同时运行
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  lark-cli --profile finance-agent im send --user "$SENDER_ID" --msg "Agent 正在执行分析任务，请稍后再试。"
  exit 0
fi

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
