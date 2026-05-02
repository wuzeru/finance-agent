#!/usr/bin/env python3
"""
feishu-listener.py — 飞书消息监听守护进程
纯桥接：收飞书消息 → 交给 Claude → listener 负责发到飞书
"""
import json
import os
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


def preflight() -> bool:
    """前置检查"""
    if subprocess.run(["which", "lark-cli"], capture_output=True).returncode != 0:
        log("[FATAL] lark-cli 未安装")
        return False

    r = subprocess.run(
        ["lark-cli", "--profile", "finance-agent", "contact", "+get-user"],
        capture_output=True,
    )
    if r.returncode != 0:
        log("[FATAL] finance-agent profile 未登录")
        return False

    for key in ("ALLOWED_OPEN_ID", "FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        if key not in ENV:
            log(f"[FATAL] {key} must be set in .env")
            return False

    return True


def send_reply(user_id: str, text: str) -> bool:
    """用 lark-cli 发送 markdown 消息到飞书"""
    r = subprocess.run(
        [
            "lark-cli", "--profile", "finance-agent",
            "--as", "bot", "im", "+messages-send",
            "--user-id", user_id,
            "--markdown", text,
        ],
        capture_output=True,
        env=ENV,
    )
    return r.returncode == 0


def get_session_id(user_id: str) -> tuple[str, bool]:
    """从 .feishu_sessions/<open_id> 读取或创建持久化 session UUID"""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    session_file = SESSION_DIR / user_id
    if session_file.exists():
        return session_file.read_text().strip(), False
    new_id = str(uuid.uuid4()).upper()
    session_file.write_text(new_id)
    return new_id, True


def run_claude(user_id: str, content: str, session_id: str) -> str:
    """调用 claude -p 生成回复，始终使用 --session-id"""
    prompt = (
        f"飞书用户 {user_id} 说: {content}。"
        "直接输出你的回复内容（markdown 格式，中文），"
        "不要说你已发送消息。listener 会负责把回复推到飞书。"
    )
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
    )
    return r.stdout.decode("utf-8", errors="replace").strip()


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

    allowed = ENV["ALLOWED_OPEN_ID"]
    event_count = 0

    log("[feishu-listener] Starting WebSocket listener...")
    log(f"[feishu-listener] Whitelisted user: {allowed}")

    proc = subprocess.Popen(
        [
            "lark-cli", "--profile", "finance-agent", "--as", "bot",
            "event", "+subscribe",
            "--event-types", "im.message.receive_v1,im.message.reaction.created_v1",
            "--compact", "--quiet", "--force",
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

            content = event.get("content", "")
            sender_id = event.get("sender_id", "")
            msg_id = event.get("message_id", "")

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
                session_id, is_new = get_session_id(sender_id)
                if is_new:
                    log(f"[session] new session created for {sender_id}: {session_id}")
                else:
                    log(f"[session] resuming session for {sender_id}: {session_id}")

                log("[claude] thinking...")
                reply = run_claude(sender_id, content, session_id)
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
