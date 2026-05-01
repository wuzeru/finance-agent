#!/usr/bin/env bash
# feishu-listener.sh — 飞书消息监听守护进程
# 由 launchd 守护全天候运行，监听飞书 IM 消息事件
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── 前置检查 ──

# 1. 检查 lark-cli 是否安装
if ! command -v lark-cli &> /dev/null; then
    echo "[FATAL] lark-cli 未安装" >&2
    exit 1
fi

# 2. 检查 finance-agent profile 是否已登录
if ! lark-cli --profile finance-agent contact +get-user &> /dev/null; then
    echo "[FATAL] finance-agent profile 未登录，请先执行: lark-cli --profile finance-agent auth login" >&2
    exit 1
fi

# 载入环境变量
set -a
# shellcheck disable=SC1091
source "$PROJECT_ROOT/.env"
set +a

# 确保必需的环境变量已设置
: "${ALLOWED_OPEN_ID:?ALLOWED_OPEN_ID must be set in .env}"
: "${FEISHU_APP_ID:?FEISHU_APP_ID must be set in .env}"
: "${FEISHU_APP_SECRET:?FEISHU_APP_SECRET must be set in .env}"

cleanup() {
    # Kill all child processes (background intent_router.sh instances).
    # Since while loop runs in main shell via process substitution,
    # & children are direct children of $$.
    jobs -p | xargs kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[feishu-listener] Starting WebSocket listener..."
echo "[feishu-listener] Whitelisted user: $ALLOWED_OPEN_ID"
echo "[feishu-listener] 等待飞书事件... (日志输出到 stderr)" >&2

event_count=0

# lark-event 通过 WebSocket 长连接监听消息事件
# 输出为 NDJSON，每行一条事件，通过管道交给 intent_router 处理
# Process substitution (< <(...)) keeps the while loop in the main shell
# so that & background children are visible to cleanup's jobs -p.
while IFS= read -r line; do
    event_count=$((event_count + 1))

    # Always print the raw event to stderr for visibility
    echo "[event #$event_count] $line" >&2

    # Extract message content and sender info (NDJSON compact format)
    content=$(echo "$line" | jq -r '.message.content // empty')
    sender_id=$(echo "$line" | jq -r '.sender.open_id // empty')
    msg_id=$(echo "$line" | jq -r '.message.message_id // empty')

    echo "[parsed] sender=$sender_id msg=$msg_id content=$content" >&2

    # Whitelist check — only respond to ALLOWED_OPEN_ID
    if [[ "$sender_id" != "$ALLOWED_OPEN_ID" ]]; then
        echo "[skip] 发送者 $sender_id 不在白名单中" >&2
        continue
    fi

    if [[ -z "$content" ]]; then
        echo "[skip] 消息内容为空" >&2
        continue
    fi

    # Strip leading @ mention (e.g. "@BotName " or "@_user_1 ")
    # shellcheck disable=SC2001  # regex needed; bash param expansion can't express [^[:space:]]
    content=$(echo "$content" | sed 's/^@[^[:space:]]*[[:space:]]*//')

    echo "[dispatch] 内容=$content → intent_router" >&2

    # Log received message to agent.log
    echo "{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"sender_id\":\"$sender_id\",\"msg_id\":\"$msg_id\",\"event\":\"message_received\"}" >> "$PROJECT_ROOT/agent.log"

    # Async dispatch — do NOT block the event loop
    "$PROJECT_ROOT/scripts/intent_router.sh" "$content" "$sender_id" "$msg_id" &
done < <(lark-event --profile finance-agent \
    --event-type im.message.receive_v1 \
    --compact \
    --output-ndjson)
