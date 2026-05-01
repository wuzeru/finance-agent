#!/bin/bash
# feishu-listener.sh — 飞书消息监听守护进程
# 由 launchd 守护全天候运行，监听飞书 IM 消息事件
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
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
# shellcheck disable=SC1091
source .env 2>/dev/null || true
set +a

# 检查必需的环境变量
if [[ -z "${ALLOWED_OPEN_ID:-}" ]]; then
  echo "[FATAL] ALLOWED_OPEN_ID 环境变量未设置，请在 .env 中配置。" >&2
  exit 1
fi

# 激活 Python 虚拟环境
# shellcheck disable=SC1091
source venv/bin/activate

echo "[$(date -Iseconds)] feishu-listener started, profile=finance-agent" >> agent.log

# lark-event 通过 WebSocket 长连接监听消息事件
# 输出为 NDJSON，每行一条事件，通过管道交给 intent_router 处理
lark-event --profile finance-agent \
  --event-type im.message.receive_v1 \
  --compact \
  --output-ndjson \
  | while IFS= read -r event_line; do
      # 跳过空行
      [[ -z "$event_line" ]] && continue

      # 提取消息内容与发送者信息
      MSG_TEXT=$(echo "$event_line" | jq -r '.message.content // empty')
      SENDER_ID=$(echo "$event_line" | jq -r '.sender.open_id // empty')
      MSG_ID=$(echo "$event_line" | jq -r '.message.message_id // empty')

      # 跳过无法解析的消息
      if [[ -z "$MSG_TEXT" || -z "$SENDER_ID" ]]; then
        echo "[$(date -Iseconds)] skipped unparseable event" >> agent.log
        continue
      fi

      # 身份校验：仅响应白名单用户
      if [[ "$SENDER_ID" != "$ALLOWED_OPEN_ID" ]]; then
        echo "[$(date -Iseconds)] rejected unknown sender: $SENDER_ID" >> agent.log
        continue
      fi

      # 解析意图并路由到 Claude Code 会话
      bash scripts/intent_router.sh "$MSG_TEXT" "$SENDER_ID" "$MSG_ID" &
    done
