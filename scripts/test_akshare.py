#!/usr/bin/env python3
"""Integration verification: test akshare for A-share and HK stock data fetch.

Tests:
  - A-share historical daily: 600519 (贵州茅台), 000001 (平安银行)
  - HK stock historical daily: 00700 (腾讯控股), 09988 (阿里巴巴)
  - Data quality: missing values, date alignment, reasonable price ranges
"""

import sys
import traceback
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd


def green(s):
    return f"\033[92m{s}\033[0m"


def red(s):
    return f"\033[91m{s}\033[0m"


def yellow(s):
    return f"\033[93m{s}\033[0m"


def check_quality(df, symbol, market):
    """Run quality checks and return (passed, failures list)."""
    failures = []

    if df.empty:
        failures.append("DataFrame is empty")
        return False, failures

    # Check required columns
    required_cols = {"日期", "开盘", "收盘", "最高", "最低", "成交量"}
    if not required_cols.issubset(df.columns):
        missing = required_cols - set(df.columns)
        failures.append(f"Missing columns: {missing}")
        return False, failures

    # Check for excessive missing values (>5%)
    missing_pct = df[list(required_cols)].isnull().mean() * 100
    if (missing_pct > 5).any():
        cols = missing_pct[missing_pct > 5].to_dict()
        failures.append(f"High missing rate: {cols}")

    # Check date alignment (should be sorted, ascending)
    if not df["日期"].is_monotonic_increasing:
        failures.append("Dates not monotonically increasing")

    # Check reasonable price ranges
    if market == "a":
        if df["收盘"].max() > 10000:
            failures.append(f"Max close price {df['收盘'].max():.2f} seems unreasonably high for A-share")
        if df["收盘"].min() <= 0:
            failures.append(f"Min close price {df['收盘'].min():.2f} is <= 0")
    elif market == "hk":
        if df["收盘"].max() > 10000:
            failures.append(f"Max close price {df['收盘'].max():.2f} seems unreasonably high for HK stock")
        if df["收盘"].min() <= 0:
            failures.append(f"Min close price {df['收盘'].min():.2f} is <= 0")

    # Check date range
    if len(df) < 5:
        failures.append(f"Only {len(df)} rows returned")

    return len(failures) == 0, failures


def test_a_share():
    """Test A-share stock data fetch."""
    symbols = [
        ("600519", "贵州茅台", "sh"),
        ("000001", "平安银行", "sz"),
    ]
    start = "20260101"
    end = "20260501"
    all_ok = True

    for code, name, prefix in symbols:
        print(f"  Fetching {name} ({code}) ... ", end="", flush=True)
        try:
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start,
                end_date=end,
                adjust="qfq",
            )
            passed, failures = check_quality(df, f"{prefix}{code}", "a")
            if passed:
                print(green(f"OK ({len(df)} rows, close range {df['收盘'].min():.2f} - {df['收盘'].max():.2f})"))
            else:
                print(red(f"FAILED: {'; '.join(failures)}"))
                all_ok = False
        except Exception as e:
            print(red(f"ERROR: {e}"))
            traceback.print_exc()
            all_ok = False

    return all_ok


def test_hk_stock():
    """Test HK stock data fetch."""
    symbols = [
        ("00700", "腾讯控股"),
        ("09988", "阿里巴巴"),
    ]
    start = "20260101"
    end = "20260501"
    all_ok = True

    for code, name in symbols:
        print(f"  Fetching {name} ({code}) ... ", end="", flush=True)
        try:
            df = ak.stock_hk_hist(
                symbol=code,
                period="daily",
                start_date=start,
                end_date=end,
                adjust="qfq",
            )
            passed, failures = check_quality(df, code, "hk")
            if passed:
                print(green(f"OK ({len(df)} rows, close range {df['收盘'].min():.2f} - {df['收盘'].max():.2f})"))
            else:
                print(red(f"FAILED: {'; '.join(failures)}"))
                all_ok = False
        except Exception as e:
            print(red(f"ERROR: {e}"))
            traceback.print_exc()
            all_ok = False

    return all_ok


if __name__ == "__main__":
    print(f"=== akshare Integration Test ===\n{datetime.now().isoformat()}\n")

    # Test imports
    print(f"akshare version: {ak.__version__}")
    print(f"pandas version: {pd.__version__}\n")

    # Test import coexistence with OpenBB
    try:
        from openbb import obb  # noqa: F401
        print(green("OpenBB import OK — no conflicts with akshare\n"))
    except ImportError:
        print(yellow("OpenBB not installed (expected if this is a fresh venv without OpenBB)\n"))

    # Run tests
    print("--- A-Share Tests ---")
    a_ok = test_a_share()
    print()
    print("--- HK Stock Tests ---")
    hk_ok = test_hk_stock()

    # Summary
    print(f"\n--- Summary ---")
    print(f"A-share: {'PASS' if a_ok else red('FAIL')}")
    print(f"HK:      {'PASS' if hk_ok else red('FAIL')}")
    all_pass = a_ok and hk_ok
    print(f"Overall: {green('ALL PASS') if all_pass else red('FAILURES DETECTED')}")

    sys.exit(0 if all_pass else 1)
