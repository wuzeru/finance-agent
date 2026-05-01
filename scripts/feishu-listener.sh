#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load environment variables
set -a; source "$PROJECT_ROOT/.env"; set +a

# Ensure required env vars are set
: "${ALLOWED_OPEN_ID:?ALLOWED_OPEN_ID must be set in .env}"
: "${FEISHU_APP_ID:?FEISHU_APP_ID must be set in .env}"
: "${FEISHU_APP_SECRET:?FEISHU_APP_SECRET must be set in .env}"

# Dependency checks
check_deps() {
    local missing=()
    for cmd in jq lark-cli; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if ((${#missing[@]} > 0)); then
        echo "[FATAL] Missing dependencies: ${missing[*]}" >&2
        exit 1
    fi
}
check_deps

cleanup() {
    # Kill all child processes (background intent_router.sh instances).
    # Uses process-group kill since while loop runs in main shell via
    # process substitution, so & children are direct children of $$.
    jobs -p | xargs kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[feishu-listener] Starting WebSocket listener..."
echo "[feishu-listener] Whitelisted user: $ALLOWED_OPEN_ID"

# Subscribe to Feishu IM events via WebSocket (NDJSON output).
# Process substitution (< <(...)) keeps the while loop in the main shell
# so that & background children are visible to cleanup's jobs -p.
while IFS= read -r line; do
    sender_id=$(echo "$line" | jq -r '.sender_id // empty')
    content=$(echo "$line" | jq -r '.content // empty')
    msg_id=$(echo "$line" | jq -r '.message_id // empty')

    # Whitelist check — only respond to ALLOWED_OPEN_ID
    [[ "$sender_id" != "$ALLOWED_OPEN_ID" ]] && continue
    [[ -z "$content" ]] && continue

    # Strip leading @ mention (e.g. "@BotName " or "@_user_1 ")
    # shellcheck disable=SC2001  # regex needed; bash param expansion can't express [^[:space:]]
    content=$(echo "$content" | sed 's/^@[^[:space:]]*[[:space:]]*//')

    # Log received message
    echo "{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"sender_id\":\"$sender_id\",\"msg_id\":\"$msg_id\",\"event\":\"message_received\"}" >> "$PROJECT_ROOT/agent.log"

    echo "[feishu-listener] Dispatching: sender=$sender_id msg_id=$msg_id"

    # Async dispatch — do NOT block the event loop
    "$PROJECT_ROOT/scripts/intent_router.sh" "$content" "$sender_id" "$msg_id" &
done < <(lark-cli event +subscribe \
    --event-types im.message.receive_v1 \
    --compact --quiet)
