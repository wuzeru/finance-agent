#!/usr/bin/env python3
"""
feishu-listener.py — 飞书消息监听守护进程
纯桥接：收飞书消息 → 交给 Claude → listener 负责发到飞书
"""
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
LOCK_FILE = PROJECT_ROOT / ".analysis.lock"
SESSION_DIR = PROJECT_ROOT / ".feishu_sessions"


def dotenv():
    """加载 .env 文件"""
    env = {}
    dotenv_path = PROJECT_ROOT / ".env"
    if dotenv_path.exists():
        for line in dotenv_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = {**os.environ, **dotenv()}


def log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def _kill_stale_subscriptions():
    """杀死所有残留的 lark-cli event +subscribe 进程"""
    try:
        r = subprocess.run(
            ["pgrep", "-f", "lark-cli.*event.*subscribe"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            for pid in r.stdout.strip().split("\n"):
                pid = pid.strip()
                if not pid:
                    continue
                try:
                    os.kill(int(pid), signal.SIGKILL)
                    log(f"[cleanup] killed stale subscription pid={pid}")
                except (OSError, ValueError):
                    pass
    except Exception:
        pass


def preflight() -> bool:
    """前置检查"""
    if subprocess.run(["which", "lark-cli"], capture_output=True).returncode != 0:
        log("[FATAL] lark-cli 未安装")
        return False

    for key in ("ALLOWED_OPEN_ID", "FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        if key not in ENV:
            log(f"[FATAL] {key} must be set in .env")
            return False

    r = subprocess.run(
        ["lark-cli", "--profile", "finance-agent", "--as", "bot",
         "contact", "+get-user", "--user-id", ENV["ALLOWED_OPEN_ID"]],
        capture_output=True, env=ENV,
    )
    if r.returncode != 0:
        log(f"[FATAL] 飞书 bot 连通性检查失败: {r.stderr.decode().strip()}")
        return False

    return True


def _table_to_text(text: str) -> str:
    """将 markdown 表格转为飞书兼容的列表格式 (post 不支持 table 标签)"""
    lines = text.split("\n")
    out, headers, in_table = [], [], False
    for line in lines:
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            if not in_table:
                in_table = True
                headers = [c.strip() for c in s.split("|")[1:-1]]
                continue
            if set(s.replace(" ", "").replace("|", "")) <= {"-", ":"}:
                continue
            cells = [c.strip() for c in s.split("|")[1:-1]]
            row = " | ".join(f"{h}: {c}" for h, c in zip(headers, cells))
            out.append(row)
        else:
            if in_table:
                out.append("")  # 表格后加空行
            in_table = False
            out.append(line)
    return "\n".join(out)


def send_reply(user_id: str, text: str) -> bool:
    """用 lark-cli 发送消息到飞书，表格自动转列表"""
    # 检测并转换表格
    processed = _table_to_text(text)
    r = subprocess.run(
        [
            "lark-cli", "--profile", "finance-agent",
            "--as", "bot", "im", "+messages-send",
            "--user-id", user_id,
            "--markdown", processed,
        ],
        capture_output=True,
        env=ENV,
    )
    return r.returncode == 0


def get_session_id(user_id: str) -> str:
    """读取或创建持久化 session UUID"""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    session_file = SESSION_DIR / user_id
    if session_file.exists():
        return session_file.read_text().strip()
    new_id = str(uuid.uuid4()).upper()
    session_file.write_text(new_id)
    return new_id


def _new_session_id(user_id: str) -> str:
    """生成新 session 并持久化"""
    new_id = str(uuid.uuid4()).upper()
    (SESSION_DIR / user_id).write_text(new_id)
    return new_id


def run_claude(user_id: str, content: str, session_id: str) -> tuple[str, str]:
    """调用 claude -p, 返回 (reply, session_id). 若 session 被占用则自动换新重试."""
    prompt = (
        f"飞书用户 {user_id} 说: {content}。"
        "直接输出你的回复内容（markdown 格式，中文），"
        "不要说你已发送消息。listener 会负责把回复推到飞书。"
    )
    for attempt in range(2):
        r = subprocess.run(
            [
                "claude", "-p",
                "--session-id", session_id,
                "--permission-mode", "bypassPermissions",
                "--dangerously-skip-permissions",
                prompt,
            ],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            cwd=PROJECT_ROOT,
            env=ENV,
        )
        reply = r.stdout.decode("utf-8", errors="replace").strip()
        if reply:
            return reply, session_id

        stderr_text = r.stderr.decode(errors="replace")
        if r.returncode != 0:
            log(f"[claude] exit={r.returncode} stderr={stderr_text[:500]}")

        if "already in use" in stderr_text and attempt == 0:
            session_id = _new_session_id(user_id)
            log(f"[claude] session 被占用, 换新: {session_id}")
            continue

        break

    return "", session_id


def acquire_lock() -> bool:
    """非阻塞忙标志"""
    try:
        LOCK_FILE.touch(exist_ok=False)
        return True
    except FileExistsError:
        return False


def release_lock():
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def main():
    if not preflight():
        sys.exit(1)

    # 先清理残留订阅 (避免 "already in use" 错误)
    _kill_stale_subscriptions()
    time.sleep(2)  # 等 Feishu 服务端感知连接断开

    allowed = ENV["ALLOWED_OPEN_ID"]
    event_count = 0

    log("[feishu-listener] Starting WebSocket listener...")
    log(f"[feishu-listener] Whitelisted user: {allowed}")

    proc = subprocess.Popen(
        [
            "lark-cli", "--profile", "finance-agent", "--as", "bot",
            "event", "+subscribe",
            "--event-types", "im.message.receive_v1,im.message.reaction.created_v1",
            "--compact", "--quiet",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=ENV,
    )

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            event_count += 1

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") != "im.message.receive_v1":
                continue

            # --compact 格式字段在顶层; 兼容嵌套格式
            if "sender_id" in event:
                sender_id = event.get("sender_id", "")
            else:
                sender_id = event.get("sender", {}).get("open_id", "")
            if "message_id" in event:
                msg_id = event.get("message_id", "")
            else:
                msg_id = event.get("message", {}).get("message_id", "")
            if "content" in event:
                content_raw = event.get("content", "")
            else:
                content_raw = event.get("message", {}).get("content", "")
            # content 可能是 JSON 字符串 '{"text": "..."}' 或纯文本
            try:
                content = json.loads(content_raw).get("text", content_raw)
            except (json.JSONDecodeError, AttributeError):
                content = content_raw

            log(f"[event #{event_count}] {line}")
            log(f"[parsed] sender={sender_id} msg={msg_id} content={content}")

            if sender_id != allowed:
                log(f"[skip] 发送者 {sender_id} 不在白名单中")
                continue
            if not content:
                log("[skip] 消息内容为空")
                continue

            # 去 @ 前缀
            if content.startswith("@"):
                content = content.split(None, 1)[1] if " " in content else ""

            log(f"[dispatch] {content}")

            # 异步 OK 表情确认收到消息
            subprocess.Popen(
                [
                    "lark-cli", "--profile", "finance-agent", "im", "reactions", "create",
                    "--as", "bot",
                    "--params", json.dumps({"message_id": msg_id}),
                    "--data", '{"reaction_type": {"emoji_type": "OK"}}',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=ENV,
            )

            if not acquire_lock():
                log("[skip] claude busy")
                continue

            try:
                session_id = get_session_id(sender_id)
                log(f"[session] using session {sender_id}: {session_id}")

                log("[claude] thinking...")
                reply, session_id = run_claude(sender_id, content, session_id)
                log("[claude] done")

                if reply:
                    ok = send_reply(sender_id, reply)
                    if ok:
                        log(f"[sent] {reply[:200]}...")
                    else:
                        log("[error] lark-cli send failed")
                else:
                    log("[error] no reply from claude")
            finally:
                release_lock()

    except KeyboardInterrupt:
        log("[feishu-listener] Interrupted, shutting down...")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        release_lock()


if __name__ == "__main__":
    main()
