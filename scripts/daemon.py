#!/usr/bin/env python3
"""
daemon.py — 统一守护进程
替代 launchd: 管理定时分析调度 + 飞书监听子进程存活 + 断线重连
"""
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).parent.parent.resolve()
PID_FILE = PROJECT_ROOT / ".daemon.pid"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "daemon.log"
LOG_MAX_BYTES = 1_048_576  # 1 MB

SCHEDULED_HOURS = [9, 13]
STARTUP_GRACE_SECONDS = 300  # 首次启动 5 分钟宽限期
SLOT_WINDOW_SECONDS = 60     # 常规触发窗口
TICK_INTERVAL = 30           # 主循环间隔 (秒)
BACKOFF_INITIAL = 5
BACKOFF_MAX = 300
BACKOFF_RESET_AFTER = 60     # 稳定运行 N 秒后重置退避

_listener_proc: Optional[subprocess.Popen] = None
_triggered: set[tuple[date, int]] = set()
_shutting_down = False
_backoff = BACKOFF_INITIAL
_backoff_until: float = 0.0
_stable_since: Optional[float] = None
_is_first_check = True


# ── logging ──────────────────────────────────────────────────────────

def _log(event: str, **extra) -> None:
    entry = {"ts": datetime.now().isoformat(), "event": event, **extra}
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False)
        _rotate_if_needed()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # 日志失败不阻塞主流程


def _rotate_if_needed() -> None:
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size >= LOG_MAX_BYTES:
            bak = LOG_FILE.with_suffix(LOG_FILE.suffix + ".1")
            bak.unlink(missing_ok=True)
            LOG_FILE.rename(bak)
    except OSError:
        pass


# ── pid guard ────────────────────────────────────────────────────────

def _acquire_pid_lock() -> None:
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            os.kill(old_pid, 0)
            print(f"daemon: Already running (PID {old_pid})", file=sys.stderr)
            sys.exit(1)
        except (OSError, ValueError):
            PID_FILE.unlink(missing_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def _release_pid_lock() -> None:
    PID_FILE.unlink(missing_ok=True)


# ── env setup ────────────────────────────────────────────────────────

def _setup_env() -> None:
    """确保 PATH 包含 /opt/homebrew/bin (claude / lark-cli 等)"""
    paths = os.environ.get("PATH", "").split(os.pathsep)
    for needed in ["/opt/homebrew/bin"]:
        if needed not in paths:
            paths.insert(0, needed)
    os.environ["PATH"] = os.pathsep.join(paths)

    dotenv_path = PROJECT_ROOT / ".env"
    if dotenv_path.exists():
        for line in dotenv_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ── scheduler ────────────────────────────────────────────────────────

def _is_weekday(now: datetime) -> bool:
    return now.weekday() < 5


def _should_trigger(now: datetime, hour: int) -> bool:
    global _is_first_check
    window = STARTUP_GRACE_SECONDS if _is_first_check else SLOT_WINDOW_SECONDS
    slot_dt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    diff = (now - slot_dt).total_seconds()
    return 0 <= diff <= window


def _fire_analysis() -> None:
    script = PROJECT_ROOT / "scripts" / "run-analysis.py"
    _log("analysis_trigger")
    try:
        subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        _log("analysis_trigger_failed", error=str(e))


# ── listener supervisor ──────────────────────────────────────────────

def _launch_listener() -> Optional[subprocess.Popen]:
    script = PROJECT_ROOT / "scripts" / "feishu-listener.py"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _log("listener_launch")
    try:
        err_f = open(LOG_DIR / "listener.log", "a", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=err_f,
        )
        err_f.close()  # child inherited the fd; parent doesn't need it
        return proc
    except Exception as e:
        _log("listener_launch_failed", error=str(e))
        return None


def _tick_listener() -> None:
    """每轮 tick 检查 listener 状态, 处理退出/重启/退避"""
    global _listener_proc, _backoff, _backoff_until, _stable_since

    if _listener_proc is None:
        _start_listener()
        return

    rc = _listener_proc.poll()
    if rc is None:
        # 子进程仍在运行
        if _stable_since is None:
            _stable_since = time.monotonic()
        elif time.monotonic() - _stable_since >= BACKOFF_RESET_AFTER:
            if _backoff > BACKOFF_INITIAL:
                _log("backoff_reset", previous_backoff=_backoff)
            _backoff = BACKOFF_INITIAL
        return

    # 子进程已退出
    _stable_since = None
    _log("listener_exited", returncode=rc, backoff=_backoff)
    _backoff_until = time.monotonic() + _backoff
    _listener_proc = None


def _start_listener() -> None:
    global _listener_proc, _backoff
    now = time.monotonic()
    if _backoff_until > 0 and now < _backoff_until:
        return
    _listener_proc = _launch_listener()
    if _listener_proc is not None:
        _backoff = min(_backoff * 2, BACKOFF_MAX)


# ── signal handling ──────────────────────────────────────────────────

def _shutdown(signum: int, _frame) -> None:
    global _shutting_down, _listener_proc
    _shutting_down = True
    _log("shutdown", signal=signum)

    if _listener_proc and _listener_proc.poll() is None:
        _listener_proc.terminate()
        try:
            _listener_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _listener_proc.kill()
            _listener_proc.wait()
        _log("listener_stopped")

    _release_pid_lock()
    sys.exit(0)


# ── main ─────────────────────────────────────────────────────────────

def main() -> None:
    global _is_first_check, _listener_proc

    _acquire_pid_lock()
    _setup_env()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    _log("daemon_start", pid=os.getpid())

    # 首次启动 listener
    _listener_proc = _launch_listener()

    while not _shutting_down:
        now = datetime.now()

        # ---- 调度检查 ----
        if _is_weekday(now):
            for hour in SCHEDULED_HOURS:
                key = (now.date(), hour)
                if key in _triggered:
                    continue
                if _should_trigger(now, hour):
                    _fire_analysis()
                    _triggered.add(key)

        _is_first_check = False

        # ---- listener 存活检查 ----
        _tick_listener()

        # ---- 清理过期的 triggered 记录 ----
        if len(_triggered) > 100:
            today = date.today()
            _triggered.clear()  # keep it simple: clear all on overflow
            _triggered.add((today, 0))  # marker

        time.sleep(TICK_INTERVAL)

    _shutdown(0, None)


if __name__ == "__main__":
    main()
