#!/usr/bin/env python3
"""Integration verification: test akshare for A-share and HK stock data fetch.

Tests:
  - A-share historical daily: 600519 (贵州茅台), 000001 (平安银行)
  - HK stock historical daily: 00700 (腾讯控股), 09988 (阿里巴巴)
  - Data quality: missing values, date alignment, reasonable price ranges
  - Dual data source: East Money (primary) → Sina Finance (fallback)
"""

import os
import sys
import time
import traceback
from datetime import datetime, timedelta

# Strip system proxy env vars — akshare scrapes Chinese financial sites
# which may reject proxied connections
for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
            "ALL_PROXY", "all_proxy"):
    os.environ.pop(key, None)

import akshare as ak
import pandas as pd
import requests


def _patch_requests_no_proxy():
    """Force requests library to ignore system-level proxy settings."""
    _orig_send = requests.adapters.HTTPAdapter.send

    def _no_proxy_send(self, request, **kwargs):
        kwargs.pop("proxies", None)
        return _orig_send(self, request, proxies={}, **kwargs)

    requests.adapters.HTTPAdapter.send = _no_proxy_send


_patch_requests_no_proxy()


# --- Column mapping: Sina English → Chinese (matching East Money output) ---
_SINA_COL_MAP = {
    "date": "日期",
    "open": "开盘",
    "high": "最高",
    "low": "最低",
    "close": "收盘",
    "volume": "成交量",
}


def green(s):
    return f"\033[92m{s}\033[0m"


def red(s):
    return f"\033[91m{s}\033[0m"


def yellow(s):
    return f"\033[93m{s}\033[0m"


def check_quality(df, symbol, market):
    """Run quality checks and return (passed, failures list)."""
    failures = []
    label = f"[{symbol}]"

    if df.empty:
        failures.append(f"{label} DataFrame is empty")
        return False, failures

    # Check required columns
    required_cols = {"日期", "开盘", "收盘", "最高", "最低", "成交量"}
    if not required_cols.issubset(df.columns):
        missing = required_cols - set(df.columns)
        failures.append(f"{label} Missing columns: {missing}")
        return False, failures

    # Check for excessive missing values (>5%)
    missing_pct = df[list(required_cols)].isnull().mean() * 100
    if (missing_pct > 5).any():
        cols = missing_pct[missing_pct > 5].to_dict()
        failures.append(f"{label} High missing rate: {cols}")

    # Check date alignment (should be sorted, ascending)
    if not df["日期"].is_monotonic_increasing:
        failures.append(f"{label} Dates not monotonically increasing")

    # Check reasonable price ranges
    if market == "a":
        if df["收盘"].max() > 10000:
            failures.append(f"{label} Max close price {df['收盘'].max():.2f} seems unreasonably high for A-share")
        if df["收盘"].min() <= 0:
            failures.append(f"{label} Min close price {df['收盘'].min():.2f} is <= 0")
    elif market == "hk":
        if df["收盘"].max() > 10000:
            failures.append(f"{label} Max close price {df['收盘'].max():.2f} seems unreasonably high for HK stock")
        if df["收盘"].min() <= 0:
            failures.append(f"{label} Min close price {df['收盘'].min():.2f} is <= 0")

    # Check date range
    if len(df) < 5:
        failures.append(f"{label} Only {len(df)} rows returned")

    return len(failures) == 0, failures


def is_connection_error(exc):
    """Check if exception is a network connectivity issue (not a usage error)."""
    msg = str(exc).lower()
    return ("connection" in msg or "timeout" in msg
            or "remotedisconnected" in msg or "proxy" in msg
            or "max retries" in msg)


def fetch_a_share_em(code, start_date, end_date):
    """Fetch A-share data via East Money."""
    return ak.stock_zh_a_hist(
        symbol=code, period="daily",
        start_date=start_date, end_date=end_date, adjust="qfq",
    )


def fetch_a_share_sina(code, prefix, start_date, end_date):
    """Fetch A-share data via Sina Finance (fallback)."""
    df = ak.stock_zh_a_daily(
        symbol=f"{prefix}{code}",
        start_date=start_date, end_date=end_date, adjust="qfq",
    )
    # Convert date column to string YYYYMMDD for consistency
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
    # Rename English columns to Chinese
    df = df.rename(columns=_SINA_COL_MAP)
    # Select only columns needed for quality checks
    df = df[["日期", "开盘", "收盘", "最高", "最低", "成交量"]]
    return df


def fetch_hk_em(code, start_date, end_date):
    """Fetch HK stock data via East Money."""
    return ak.stock_hk_hist(
        symbol=code, period="daily",
        start_date=start_date, end_date=end_date, adjust="qfq",
    )


def fetch_hk_sina(code, start_date, end_date):
    """Fetch HK stock data via Sina Finance (fallback)."""
    df = ak.stock_hk_daily(symbol=code, adjust="qfq")
    # Convert date and filter range (Sina returns full history, no date params)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
    df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
    # Rename English columns to Chinese
    df = df.rename(columns=_SINA_COL_MAP)
    df = df[["日期", "开盘", "收盘", "最高", "最低", "成交量"]]
    return df


def test_a_share(end_date, start_date):
    """Test A-share stock data fetch with East Money → Sina fallback."""
    symbols = [
        ("600519", "贵州茅台", "sh"),
        ("000001", "平安银行", "sz"),
    ]
    all_ok = True

    for code, name, prefix in symbols:
        print(f"  Fetching {name} ({code}) ... ", end="", flush=True)
        try:
            df = fetch_a_share_em(code, start_date, end_date)
            source = "East Money"
        except Exception as e:
            if is_connection_error(e):
                try:
                    df = fetch_a_share_sina(code, prefix, start_date, end_date)
                    source = "Sina (fallback)"
                except Exception as e2:
                    print(red(f"ERROR (both sources): {e2}"))
                    traceback.print_exc()
                    all_ok = False
                    continue
            else:
                print(red(f"ERROR: {e}"))
                traceback.print_exc()
                all_ok = False
                continue

        passed, failures = check_quality(df, code, "a")
        if passed:
            print(green(f"OK [{source}] ({len(df)} rows, close range {df['收盘'].min():.2f} - {df['收盘'].max():.2f})"))
        else:
            print(red(f"FAILED: {'; '.join(failures)}"))
            all_ok = False
        time.sleep(1)

    return all_ok


def test_hk_stock(end_date, start_date):
    """Test HK stock data fetch with East Money → Sina fallback."""
    symbols = [
        ("00700", "腾讯控股"),
        ("09988", "阿里巴巴"),
    ]
    all_ok = True

    for code, name in symbols:
        print(f"  Fetching {name} ({code}) ... ", end="", flush=True)
        try:
            df = fetch_hk_em(code, start_date, end_date)
            source = "East Money"
        except Exception as e:
            if is_connection_error(e):
                try:
                    df = fetch_hk_sina(code, start_date, end_date)
                    source = "Sina (fallback)"
                except Exception as e2:
                    print(red(f"ERROR (both sources): {e2}"))
                    traceback.print_exc()
                    all_ok = False
                    continue
            else:
                print(red(f"ERROR: {e}"))
                traceback.print_exc()
                all_ok = False
                continue

        passed, failures = check_quality(df, code, "hk")
        if passed:
            print(green(f"OK [{source}] ({len(df)} rows, close range {df['收盘'].min():.2f} - {df['收盘'].max():.2f})"))
        else:
            print(red(f"FAILED: {'; '.join(failures)}"))
            all_ok = False
        time.sleep(1)

    return all_ok


if __name__ == "__main__":
    # Dynamic date range: last ~90 calendar days to ensure sufficient trading data
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")

    print(f"=== akshare Integration Test ===\n{datetime.now().isoformat()}")
    print(f"Date range: {start_date} - {end_date}\n")

    # Test imports
    print(f"akshare version: {ak.__version__}")
    print(f"pandas version:   {pd.__version__}\n")

    # Test import coexistence with OpenBB
    try:
        from openbb import obb  # noqa: F401
        print(green("OpenBB import OK — no conflicts with akshare\n"))
    except ImportError:
        print(yellow("OpenBB not installed (expected if this is a fresh venv without OpenBB)\n"))

    # Run tests
    print("--- A-Share Tests ---")
    a_ok = test_a_share(end_date, start_date)
    print()
    print("--- HK Stock Tests ---")
    hk_ok = test_hk_stock(end_date, start_date)

    # Summary
    print(f"\n--- Summary ---")
    print(f"A-share: {'PASS' if a_ok else red('FAIL')}")
    print(f"HK:      {'PASS' if hk_ok else red('FAIL')}")
    all_pass = a_ok and hk_ok
    print(f"Overall: {green('ALL PASS') if all_pass else red('FAILURES DETECTED')}")

    sys.exit(0 if all_pass else 1)
