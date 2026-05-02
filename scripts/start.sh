#!/usr/bin/env bash
# start.sh — 安装 @reboot cron 并即时启动 daemon
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(cd "$SCRIPT_DIR/.." && pwd)"
DAEMON="$SCRIPT_DIR/daemon.py"
PID_FILE="$PROJECT/.daemon.pid"
CRON_MARKER="# finance-agent daemon"

# ── 1. 安装 @reboot cron ──────────────────────────────────────────

if crontab -l 2>/dev/null | grep -qF "$DAEMON"; then
    echo "[start.sh] crontab 中已有 daemon 条目, 跳过安装"
else
    echo "[start.sh] 安装 @reboot cron 条目 ..."
    tmp=$(mktemp)
    crontab -l > "$tmp" 2>/dev/null || true > "$tmp"
    echo "@reboot /opt/homebrew/bin/python3 $DAEMON >> $PROJECT/logs/daemon-cron.log 2>&1 $CRON_MARKER" >> "$tmp"
    crontab "$tmp"
    rm -f "$tmp"
    echo "[start.sh] @reboot cron 已安装"
fi

# ── 2. 检查是否已在运行 ──────────────────────────────────────────

if [ -f "$PID_FILE" ]; then
    pid=$(cat "$PID_FILE" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "[start.sh] daemon 已在运行 (PID $pid)"
        exit 0
    fi
    echo "[start.sh] 残留 PID 文件已清理"
    rm -f "$PID_FILE"
fi

# ── 3. 确保日志目录存在 ─────────────────────────────────────────

mkdir -p "$PROJECT/logs"

# ── 4. 启动 daemon ───────────────────────────────────────────────

echo "[start.sh] 启动 daemon ..."
nohup /opt/homebrew/bin/python3 "$DAEMON" > /dev/null 2>&1 &
echo "[start.sh] daemon 已启动 (PID $!)"
