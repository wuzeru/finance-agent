#!/bin/bash
# run-analysis.sh — 定时分析包装脚本
# 由 launchd 在工作日 9:00 / 13:00 触发
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
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

# ── Step 1: Acquire Lock ──
exec 200>".analysis.lock"
flock 200

# 载入环境变量
set -a
# shellcheck disable=SC1091
source .env 2>/dev/null || true
set +a

# 检查必需的环境变量
if [[ -z "${ALLOWED_OPEN_ID:-}" ]]; then
  echo "[FATAL] ALLOWED_OPEN_ID 环境变量未设置，请在 .env 中配置。" >&2
  flock -u 200
  exit 1
fi

# shellcheck disable=SC1091
source venv/bin/activate

echo "{\"ts\":\"$(date -Iseconds)\",\"event\":\"start\",\"trigger\":\"launchd\"}" >> agent.log

# ── Step 2-6: 由 Claude Code 驱动完整分析工作流 ──
# Claude Code 读取 CLAUDE.md 并执行 Steps 2-6（加载状态、收集数据、验证历史、分析、生成报告、写入建议）
# --profile finance-agent 确保飞书推送使用正确的配置
claude "请按照 CLAUDE.md 中的完整 7-Step 定时分析工作流执行本次分析。

当前时间: $(date '+%Y-%m-%d %H:%M:%S')
触发方式: launchd 定时触发

注意事项:
- Step 7 飞书推送请使用: lark-cli --profile finance-agent im send --user \"$ALLOWED_OPEN_ID\" --msg \"...\"
- 完整报告归档到 reports/ 目录
- 日志写入 agent.log
- 结束后释放文件锁"

# ── Step 7: Push & Archive ──
# 报告推送由 Claude Code 在会话中完成（通过 lark-cli --profile finance-agent im send）
# 此处仅为兜底日志

echo "{\"ts\":\"$(date -Iseconds)\",\"event\":\"end\",\"trigger\":\"launchd\"}" >> agent.log

# 释放文件锁
flock -u 200
