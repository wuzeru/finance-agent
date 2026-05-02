#!/usr/bin/env python3
"""
run-analysis.py — 定时分析脚本
由 launchd 在工作日 9:00 / 13:00 触发
驱动 Path A 完整 7-Step 投资分析工作流
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
LOCK_FILE = PROJECT_ROOT / ".analysis.lock"
STALE_TIMEOUT = 1800  # 30 min
_LOG_LOCK = threading.Lock()


def dotenv() -> dict:
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


def log_ndjson(entry: dict) -> None:
    entry.setdefault("ts", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    with _LOG_LOCK:
        with open(PROJECT_ROOT / "agent.log", "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def preflight() -> bool:
    """前置检查：lark-cli / profile / 必要环境变量"""
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

    if "ALLOWED_OPEN_ID" not in ENV:
        log("[FATAL] ALLOWED_OPEN_ID must be set in .env")
        return False

    return True


def acquire_lock_blocking() -> None:
    """阻塞式文件锁（与 feishu-listener.py 的 touch 锁兼容）"""
    while True:
        # 清理过期锁 (>30 min)
        if LOCK_FILE.exists():
            try:
                age = time.time() - LOCK_FILE.stat().st_mtime
                if age > STALE_TIMEOUT:
                    LOCK_FILE.unlink()
                    log("[lock] stale lock (>30 min) removed")
            except FileNotFoundError:
                pass
            except PermissionError:
                log("[lock] cannot remove stale lock (permission denied)")

        try:
            LOCK_FILE.touch(exist_ok=False)
            return
        except FileExistsError:
            log("[lock] waiting for lock...")
            time.sleep(2)


def release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def main() -> None:
    if not preflight():
        sys.exit(1)

    run_id = f"scheduled-{datetime.now().strftime('%Y%m%d-%H%M')}"
    caffeinate_proc = None

    # ── 防休眠 (macOS) ──
    if subprocess.run(["which", "caffeinate"], capture_output=True).returncode == 0:
        caffeinate_proc = subprocess.Popen(
            ["caffeinate", "-i", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # ── cleanup ──
    def cleanup():
        if caffeinate_proc:
            caffeinate_proc.terminate()
            try:
                caffeinate_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                caffeinate_proc.kill()
        release_lock()

    signal.signal(signal.SIGINT, lambda *_: cleanup() or sys.exit(1))
    signal.signal(signal.SIGTERM, lambda *_: cleanup() or sys.exit(1))

    # ── 激活 venv ──
    activate_script = PROJECT_ROOT / "venv" / "bin" / "activate"
    if activate_script.exists():
        venv_bin = str(activate_script.parent)
        os.environ["PATH"] = f"{venv_bin}:{os.environ.get('PATH', '')}"
        os.environ["VIRTUAL_ENV"] = str(PROJECT_ROOT / "venv")
    else:
        log_ndjson({"run_id": run_id, "event": "warn", "detail": "venv not found"})

    # ── 创建必要目录 ──
    (PROJECT_ROOT / "reports").mkdir(exist_ok=True)
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)

    # ── 获取锁 ──
    acquire_lock_blocking()

    try:
        log_ndjson({"run_id": run_id, "event": "start"})

        log("[run-analysis] executing Path A workflow...")

        data_template = r'''\
import json, sys, time, os, warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from datetime import datetime

portfolio = [
    {"symbol": "AAPL", "market": "us"},
    {"symbol": "VOO", "market": "us"},
    {"symbol": "TSLA", "market": "us"},
    {"symbol": "HKG:0700", "market": "hk"},
]

results = {}

def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_bollinger(close, period=20):
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    return ma + 2*std, ma, ma - 2*std

def calc_macd(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast).mean()
    ema_slow = close.ewm(span=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def fetch_us(symbol):
    """US stocks: FMP first, yfinance fallback"""
    try:
        from openbb import obb
        os.environ.setdefault("OPENBB_FMP_API_KEY", os.environ.get("OPENBB_FMP_API_KEY", ""))
        one_year_ago = (pd.Timestamp.now() - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        # FMP quote → 返回 OBBject, 需 .to_dataframe()
        quote_df = obb.equity.price.quote(symbol, provider="fmp").to_dataframe()
        hist_df = obb.equity.price.historical(symbol, provider="fmp", start_date=one_year_ago, end_date=today).to_dataframe()
        if len(quote_df) > 0:
            r = quote_df.iloc[0]
            price = float(r["last_price"])
            change_pct = float(r.get("change_percent", r.get("changes_percentage", 0)))
            close = pd.Series(hist_df["close"].astype(float))
            ma20 = float(close.rolling(20).mean().iloc[-1])
            ma50 = float(close.rolling(50).mean().iloc[-1])
            ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else ma50
            rsi = float(calc_rsi(close).iloc[-1])
            upper, mid, lower = calc_bollinger(close)
            macd, signal, hist_macd = calc_macd(close)
            # fundamentals: FMP doesn't have PE/PB in quote, supplement with yfinance info
            pe = pb = None
            try:
                import yfinance as yf
                inf = yf.Ticker(symbol).info
                pe = inf.get("trailingPE") or inf.get("forwardPE")
                pb = inf.get("priceToBook")
            except Exception:
                pass
            return {
                "source": "fmp", "price": price, "change_pct": change_pct,
                "pe": pe, "pb": pb, "market_cap": float(r.get("market_cap", 0)) or None,
                "ma20": ma20, "ma50": ma50, "ma200": ma200,
                "rsi_14": rsi,
                "bollinger_upper": float(upper.iloc[-1]),
                "bollinger_mid": float(mid.iloc[-1]),
                "bollinger_lower": float(lower.iloc[-1]),
                "macd_line": float(macd.iloc[-1]),
                "macd_signal": float(signal.iloc[-1]),
                "macd_hist": float(hist_macd.iloc[-1]),
            }
    except Exception:
        pass

    # yfinance fallback
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.info
        price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose", 0)
        hist = ticker.history(period="1y")
        close = hist["Close"]
        change_pct = ((price / close.iloc[-2] - 1) * 100) if len(close) >= 2 else 0
        pe = info.get("trailingPE") or info.get("forwardPE")
        pb = info.get("priceToBook")
        mktcap = info.get("marketCap")
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma50 = float(close.rolling(50).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else ma50
        rsi = float(calc_rsi(close).iloc[-1])
        upper, mid, lower = calc_bollinger(close)
        macd, signal, hist_macd = calc_macd(close)
        return {
            "source": "yfinance", "price": price, "change_pct": change_pct,
            "pe": pe, "pb": pb, "market_cap": mktcap,
            "ma20": float(ma20), "ma50": float(ma50), "ma200": float(ma200),
            "rsi_14": float(rsi),
            "bollinger_upper": float(upper.iloc[-1]),
            "bollinger_mid": float(mid.iloc[-1]),
            "bollinger_lower": float(lower.iloc[-1]),
            "macd_line": float(macd.iloc[-1]),
            "macd_signal": float(signal.iloc[-1]),
            "macd_hist": float(hist_macd.iloc[-1]),
        }
    except Exception as e:
        return {"source": "error", "error": str(e)}

def fetch_hk(code):
    """HK stocks: akshare East Money -> Sina fallback"""
    import akshare as ak
    today = datetime.now().strftime("%Y%m%d")
    start = (pd.Timestamp.now() - pd.Timedelta(days=400)).strftime("%Y%m%d")
    try:
        df = ak.stock_hk_hist(symbol=code, period="daily", start_date=start, end_date=today, adjust="qfq")
    except Exception as e:
        if "connection" in str(e).lower() or "timeout" in str(e).lower():
            df = ak.stock_hk_daily(symbol=code, adjust="qfq")
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
            df = df[(df["date"] >= start) & (df["date"] <= today)]
            df = df.rename(columns={"date": "日期", "open": "开盘", "high": "最高",
                                     "low": "最低", "close": "收盘", "volume": "成交量"})
            source = "akshare_sina"
        else:
            raise
    else:
        source = "akshare_em"
    close = df["收盘"].astype(float)
    price = float(close.iloc[-1])
    change_pct = float((close.iloc[-1] / close.iloc[-2] - 1) * 100) if len(close) >= 2 else 0
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else ma50
    rsi = float(calc_rsi(close).iloc[-1])
    upper, mid, lower = calc_bollinger(close)
    macd, signal, hist_macd = calc_macd(close)
    return {
        "source": source, "price": price, "change_pct": change_pct,
        "ma20": ma20, "ma50": ma50, "ma200": ma200,
        "rsi_14": rsi,
        "bollinger_upper": float(upper.iloc[-1]),
        "bollinger_mid": float(mid.iloc[-1]),
        "bollinger_lower": float(lower.iloc[-1]),
        "macd_line": float(macd.iloc[-1]),
        "macd_signal": float(signal.iloc[-1]),
        "macd_hist": float(hist_macd.iloc[-1]),
    }

def fetch_macro():
    import yfinance as yf
    macro = {}
    for sym, name in [("^GSPC", "sp500"), ("^VIX", "vix"), ("^TNX", "us10y")]:
        try:
            t = yf.Ticker(sym)
            info = t.info
            macro[name] = info.get("regularMarketPrice") or info.get("previousClose")
        except Exception:
            macro[name] = None
    return macro

# Fetch all
for holding in portfolio:
    sym = holding["symbol"]
    print(f"Fetching {sym}...", file=sys.stderr)
    if holding["market"] == "hk":
        code = sym.split(":")[1]
        results[sym] = fetch_hk(code)
    else:
        results[sym] = fetch_us(sym)
    time.sleep(1)

results["macro"] = fetch_macro()
print(json.dumps(results, ensure_ascii=False, default=str))
'''

        prompt = (
            "执行 Path A 完整 7-Step 定时分析工作流（Step 1 锁获取已由脚本完成）：\n"
            "- Step 2: 读取 portfolio.csv 和 recommendations.csv 到上下文\n"
            "- Step 3: 数据获取。使用下面已验证的 Python 模板，"
            "把 data_template 写入 /tmp/fetch_data.py 后运行 "
            "`source venv/bin/activate && python /tmp/fetch_data.py`。"
            "模板已处理 FMP 字段名 (last_price) 和 akshare 墙问题 (Sina fallback)。"
            "先从 portfolio.csv 读取当前持仓，把 data_template 里的 symbols 替换为实际持仓标的，"
            "然后写入 /tmp/fetch_data.py 并运行"
            "在此基础上补充 fundamentals（PE/PB/市值）+ 宏观数据"
            "（S&P 500, VIX, 10Y 收益率, 联邦基金利率）\n"
            "- Step 3.5 (价格异动检测): 先检查 .alert_enabled 是否存在"
            " (test -f .alert_enabled)。"
            "若不存在，再检查旧命名 .alert-enabled（向后兼容）："
            "若 .alert-enabled 存在，重命名为 .alert_enabled"
            " (mv .alert-enabled .alert_enabled)。"
            "若两者都不存在，直接跳到 Step 4 并注明异动提醒功能未开启。"
            "若 .alert_enabled 存在（或已迁移），读取阈值: "
            "source scripts/alert_config.sh 2>/dev/null; echo ${ANOMALY_THRESHOLD:-5}。"
            "对每个标的检查 |change_pct| >= 阈值（使用 Step 3 已获取的涨跌幅数据）。"
            "记录所有触发标的（名称、当前价、涨跌幅含正负号、触发阈值），"
            "供 Step 7 异动推送使用。若所有标的均未触发，记录'今日无价格异动'。\n"
            "- Step 4: 验证 pending 建议（3个交易日内的跳过，买入/卖出按目标价验证，"
            "hold 验证无>5%不利波动，>30交易日无触发标为expired）；分析当前持仓\n"
            "- Step 5: 生成完整报告写入 reports/analysis-YYYY-MM-DD-HHmm.md"
            "（含市场概况、历史回顾、持仓诊断、建议表、风险提示）\n"
            "- Step 6: 追加新建议到 recommendations.csv（status=pending，顺序 ID）\n"
            "- Step 7a (异动推送): 仅当 Step 3.5 有触发标的时，"
            "用 lark-cli --profile finance-agent --as bot im +messages-send "
            "--user-id \"$ALLOWED_OPEN_ID\" --msg \"...\" 发送独立异动提醒消息 "
            "（格式: 🚨 价格异动提醒\\n当前阈值: ±N%\\n\\n"
            "• SYMBOL 名称 现价 $X.XX (↑/↓X.X%) 触发阈值 ±N%\\n"
            "多只以 --- 分隔）。"
            "若无触发标的，跳过此步。\n"
            "- Step 7b (报告推送): 通过 lark-cli --profile finance-agent --as bot im +messages-send "
            "--user-id \"$ALLOWED_OPEN_ID\" --msg \"...\" 推送报告摘要到飞书。\n"
            "- Step 7c (日志): 日志写入 agent.log\n\n"
            "用中文生成所有输出和报告。\n\n"
            "=== 数据获取模板 (写入 /tmp/fetch_data.py 后执行) ===\n"
            + data_template
        )

        log("[run-analysis] Claude 启动，stream-json 实时监控…")

        proc = subprocess.Popen(
            ["claude", "-p",
             "--output-format", "stream-json",
             "--include-partial-messages", "--verbose",
             "--permission-mode", "bypassPermissions",
             "--dangerously-skip-permissions", prompt],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=PROJECT_ROOT,
            env=ENV,
            text=True,
        )

        # 后台读 stderr（工具日志 / 警告），避免管道死锁
        def _read_stderr():
            for line in proc.stderr:
                s = line.rstrip()
                if s:
                    log(f"[claude] {s}")
        t = threading.Thread(target=_read_stderr, daemon=True)
        t.start()

        # 解析 stream-json 事件
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = ev.get("type", "")
            if etype == "system":
                log(f"[claude] model={ev.get('model', '?')}")
                log("[claude] ⏳ 数据获取与分析中…")
            elif etype == "stream_event":
                inner = ev.get("event", {})
                delta = inner.get("delta", {})
                if delta.get("type") == "text_delta":
                    sys.stderr.write(delta.get("text", ""))
                    sys.stderr.flush()
                elif inner.get("type") == "content_block_start":
                    cb = inner.get("content_block", {})
                    if cb.get("type") == "tool_use":
                        log(f"[claude] 🔧 {cb.get('name', '?')}")
            elif etype == "result":
                log("[claude] ✅ 分析完成")

        try:
            exit_code = proc.wait(timeout=900)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            exit_code = 124
            log("[run-analysis] ⚠️ timeout after 15 min, killed")
        log(f"[run-analysis] claude exited (code={exit_code})")
        log_ndjson({"run_id": run_id, "event": "end", "exit_code": exit_code})
        sys.exit(exit_code)

    finally:
        cleanup()


if __name__ == "__main__":
    main()
