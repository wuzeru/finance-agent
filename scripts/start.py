#!/usr/bin/env python3
"""
start.py — finance-agent 统一入口
   python3 scripts/start.py          # 前台启动 (日志可见)
   python3 scripts/start.py start    # 同上
   python3 scripts/start.py bg       # 后台启动
   python3 scripts/start.py install  # 安装开机自启
   python3 scripts/start.py stop     # 停止 daemon
   python3 scripts/start.py status   # 查看状态
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).parent.parent.resolve()
DAEMON = PROJECT / "scripts" / "daemon.py"
PID_FILE = PROJECT / ".daemon.pid"
LOG_FILE = PROJECT / "logs" / "daemon.log"
CRON_MARKER = "# finance-agent daemon"
PYTHON = sys.executable


# ── helpers ───────────────────────────────────────────────────────────

def _pid_alive() -> bool:
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, OSError):
        return False


def _send_signal(sig: int) -> bool:
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, sig)
        return True
    except (FileNotFoundError, ValueError, OSError):
        return False


# ── commands ──────────────────────────────────────────────────────────

def cmd_start(bg: bool = False) -> None:
    if _pid_alive():
        print(f"daemon 已在运行 (pid={PID_FILE.read_text().strip()})")
        return

    if bg:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(LOG_FILE, "a", encoding="utf-8")
        proc = subprocess.Popen(
            [PYTHON, str(DAEMON)],
            cwd=str(PROJECT),
            stdout=log_f,
            stderr=subprocess.DEVNULL,  # daemon._log 已写文件, 无需重复
            start_new_session=True,
        )
        log_f.close()
        print(f"daemon 已后台启动 (pid={proc.pid})")
    else:
        print("═" * 50)
        print("finance-agent daemon 启动中...")
        print(f"Python : {PYTHON}")
        print(f"项目目录: {PROJECT}")
        print(f"日志文件: {LOG_FILE}")
        print("═" * 50)
        # 前台运行: stdout/stderr 透传到终端
        proc = subprocess.run(
            [PYTHON, str(DAEMON)],
            cwd=str(PROJECT),
        )
        sys.exit(proc.returncode)


def cmd_stop() -> None:
    if not _pid_alive():
        print("daemon 未运行")
        return
    print("正在停止 daemon...")
    if _send_signal(signal.SIGTERM):
        # 等 daemon 自己清理
        for _ in range(30):  # 最多等 30 秒
            if not _pid_alive():
                print("daemon 已停止")
                return
            time.sleep(0.5)
        # 还没退, 强杀
        _send_signal(signal.SIGKILL)
        time.sleep(0.5)
        print("daemon 已强制停止")
    PID_FILE.unlink(missing_ok=True)


def cmd_status() -> None:
    if _pid_alive():
        print(f"daemon 运行中 (pid={PID_FILE.read_text().strip()})")
    else:
        print("daemon 未运行")


def cmd_install() -> None:
    entry = f"@reboot {PYTHON} {DAEMON} >> {LOG_FILE} 2>&1 {CRON_MARKER}"

    # 读已有 crontab
    result = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True
    )
    if result.returncode != 0 and "no crontab" not in result.stderr:
        print(f"读取 crontab 失败: {result.stderr}")
        sys.exit(1)

    existing = result.stdout

    if CRON_MARKER in existing:
        print("开机自启已安装")
        return

    new_content = existing.rstrip("\n") + "\n" + entry + "\n"
    proc = subprocess.run(
        ["crontab", "-"], input=new_content, capture_output=True, text=True
    )
    if proc.returncode != 0:
        print(f"crontab 安装失败: {proc.stderr}")
        sys.exit(1)

    print("开机自启已安装 (@reboot crontab)")
    print(f"  {entry}")


def cmd_uninstall() -> None:
    result = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True
    )
    if result.returncode != 0:
        print("无 crontab 条目")
        return

    lines = [l for l in result.stdout.splitlines() if CRON_MARKER not in l]
    new_content = "\n".join(lines).strip() + "\n" if lines else ""

    proc = subprocess.run(
        ["crontab", "-"], input=new_content, capture_output=True, text=True
    )
    if proc.returncode != 0:
        print(f"crontab 卸载失败: {proc.stderr}")
        sys.exit(1)

    print("开机自启已移除")


def cmd_help() -> None:
    print(__doc__)


# ── entry ─────────────────────────────────────────────────────────────

def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"

    if cmd == "start":
        cmd_start(bg=False)
    elif cmd == "bg":
        cmd_start(bg=True)
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "status":
        cmd_status()
    elif cmd == "install":
        cmd_install()
    elif cmd == "uninstall":
        cmd_uninstall()
    elif cmd in ("help", "-h", "--help"):
        cmd_help()
    else:
        print(f"未知命令: {cmd}")
        cmd_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
