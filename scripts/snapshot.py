#!/usr/bin/env python3
"""
snapshot.py — 持仓快照 + 建议自动校准

三步闭环:
  1. save_snapshot()       — 每轮分析结束时保存 portfolio.csv 快照
  2. calibrate_from_snapshot() — 下一轮开始时 diff 快照，自动标记已执行建议
  3. is_in_cooldown()      — 冷却期检查，避免重复生成减仓建议

工作流集成位置:
  - Step 2.5: calibrate_from_snapshot(project_root)
  - Step 5:   is_in_cooldown(project_root, symbol) 拦截同方向建议
  - Step 7.5: save_snapshot(project_root)
"""
import csv
import os
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

MAX_SNAPSHOTS = 60
COOLDOWN_DAYS = 30
EMERGENCY_DROP_THRESHOLD = -0.20  # 暴跌 20% 可覆盖冷却期


def save_snapshot(project_root: Path) -> Path:
    """
    将当前 portfolio.csv 另存到 snapshots/ 目录。
    返回保存的快照路径。首次运行时自动创建 snapshots/ 目录。
    超过 MAX_SNAPSHOTS 份时删最旧的。
    """
    src = project_root / "portfolio.csv"
    if not src.exists():
        raise FileNotFoundError(f"portfolio.csv not found at {src}")

    snapshots_dir = project_root / "snapshots"
    snapshots_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d-%H%M")
    dst = snapshots_dir / f"portfolio-{ts}.csv"
    shutil.copy2(src, dst)

    # 清理旧快照，保留最近 MAX_SNAPSHOTS 份
    all_snapshots = sorted(snapshots_dir.glob("portfolio-*.csv"))
    if len(all_snapshots) > MAX_SNAPSHOTS:
        for old in all_snapshots[: len(all_snapshots) - MAX_SNAPSHOTS]:
            old.unlink()

    return dst


def _read_portfolio(path: Path) -> dict[str, dict]:
    """读取 portfolio.csv 返回 {symbol: row_dict}"""
    result = {}
    if not path.exists():
        return result
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sym = row.get("symbol", "").strip()
            if sym:
                result[sym] = row
    return result


def _read_recommendations(path: Path) -> list[dict]:
    """读取 recommendations.csv 返回 list[dict]"""
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _write_recommendations(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    """写入 recommendations.csv"""
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def calibrate_from_snapshot(project_root: Path) -> list[dict]:
    """
    对比当前 portfolio.csv 与最近快照，自动校准 pending 建议。

    返回校准结果列表: [{"symbol": "008888", "rec_id": "17", "action": "reduce"}, ...]
    首次运行（无快照时）返回空列表。
    """
    snapshots_dir = project_root / "snapshots"
    if not snapshots_dir.exists():
        return []

    all_snapshots = sorted(snapshots_dir.glob("portfolio-*.csv"))
    if not all_snapshots:
        return []

    # 找到最近快照
    last_snapshot = all_snapshots[-1]
    try:
        old_portfolio = _read_portfolio(last_snapshot)
    except Exception:
        # 快照损坏，跳过
        return []

    current_portfolio = _read_portfolio(project_root / "portfolio.csv")
    recs = _read_recommendations(project_root / "recommendations.csv")

    if not recs:
        return []

    # 提取快照日期，用于匹配"快照之后"的建议
    snapshot_ts = None
    for part in last_snapshot.stem.split("-", 1):
        try:
            snapshot_ts = datetime.strptime(last_snapshot.stem, "portfolio-%Y%m%d-%H%M")
            break
        except ValueError:
            continue

    changes = []
    now_utc = datetime.now(timezone.utc)

    for sym, cur_row in current_portfolio.items():
        if sym not in old_portfolio:
            # 新增持仓，跳过
            continue

        try:
            old_qty = float(old_portfolio[sym].get("quantity", 0))
            cur_qty = float(cur_row.get("quantity", 0))
        except (ValueError, TypeError):
            continue

        if cur_qty >= old_qty:
            # 未减仓或加仓（加仓不触发 reduce/sell 校准）
            continue

        # 减仓了 → 匹配 pending 的 reduce/sell 建议
        for rec in recs:
            rec_sym = rec.get("symbol", "").strip()
            if rec_sym != sym:
                continue
            if rec.get("status", "").strip() != "pending":
                continue
            if rec.get("action", "").strip().lower() not in ("reduce", "sell"):
                continue

            # 检查建议时间是否在快照之后
            rec_ts = rec.get("timestamp", "").strip()
            if not rec_ts:
                continue
            try:
                rec_dt = datetime.fromisoformat(rec_ts)
            except ValueError:
                continue

            if snapshot_ts and rec_dt < snapshot_ts.replace(tzinfo=None):
                # 建议在快照之前，不是本轮产生的
                continue

            # 检查是否超过 30 天
            try:
                if rec_dt.tzinfo is None:
                    rec_dt = rec_dt.replace(tzinfo=timezone.utc)
                if (now_utc - rec_dt).days > 30:
                    continue
            except (TypeError, OverflowError):
                continue

            # 匹配成功 → 标记 verified
            verified_at_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            rec["status"] = "verified"
            rec["verified_at"] = verified_at_str
            rec["outcome"] = "correct(user_acted)"
            changes.append({
                "symbol": sym,
                "rec_id": rec.get("id", "").strip(),
                "action": rec.get("action", "").strip(),
            })

    # 写回
    if changes:
        fieldnames = list(recs[0].keys()) if recs else []
        _write_recommendations(
            project_root / "recommendations.csv", recs, fieldnames
        )

    return changes


def get_cooldown_symbols(project_root: Path) -> list[dict]:
    """
    返回当前冷却期内的标的清单。

    冷却期定义: 过去 COOLDOWN_DAYS 天内，recommendations.csv 中存在
    status=verified, outcome="correct(user_acted)" 的标的。

    返回 [{"symbol": "008888", "verified_at": "2026-05-14T09:00:00Z", "rec_id": "17"}, ...]
    """
    recs = _read_recommendations(project_root / "recommendations.csv")
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=COOLDOWN_DAYS)
    cooldown = []

    for rec in recs:
        if rec.get("status", "").strip() != "verified":
            continue
        if rec.get("outcome", "").strip() != "correct(user_acted)":
            continue

        verified_at = rec.get("verified_at", "").strip()
        if not verified_at:
            continue
        try:
            vt = datetime.fromisoformat(verified_at)
            if vt.tzinfo is None:
                vt = vt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if vt < cutoff:
            continue

        cooldown.append({
            "symbol": rec.get("symbol", "").strip(),
            "verified_at": verified_at,
            "rec_id": rec.get("id", "").strip(),
            "action": rec.get("action", "").strip(),
        })

    return cooldown


def is_in_cooldown(project_root: Path, symbol: str, current_price: float | None = None) -> dict:
    """
    检查单个标的是否在冷却期内。

    返回 {"in_cooldown": bool, "reason": str}

    若 current_price 传入，则检查是否需要因暴跌覆盖冷却期：
    当价格相对 30 天内下跌 ≥20% 时，即使冷却期内也返回 in_cooldown=False。
    """
    cooldown_symbols = get_cooldown_symbols(project_root)
    matching = [c for c in cooldown_symbols if c["symbol"] == symbol]

    if not matching:
        return {"in_cooldown": False, "reason": ""}

    latest = matching[0]  # 按写入顺序，最新的在最后
    reason = f"上月{latest['action']}建议已执行 (rec_id={latest['rec_id']})，当前在{COOLDOWN_DAYS}天冷却期内"

    # 暴跌覆盖检查
    if current_price is not None:
        # 检查该标的在过去 30 天内是否暴跌 20%+
        # 通过快照历史估算：比较当前价格与最早可用快照价格
        try:
            portfolio = _read_portfolio(project_root / "portfolio.csv")
            if symbol in portfolio:
                avg_cost = float(portfolio[symbol].get("avg_cost", 0))
                if avg_cost > 0:
                    drop = (current_price - avg_cost) / avg_cost
                    if drop <= EMERGENCY_DROP_THRESHOLD:
                        return {
                            "in_cooldown": False,
                            "reason": (
                                f"价格相对成本下跌 {abs(drop) * 100:.1f}%，"
                                f"超过 {abs(EMERGENCY_DROP_THRESHOLD) * 100:.0f}% 阈值，"
                                f"冷却期覆盖"
                            ),
                        }
        except Exception:
            pass

    return {"in_cooldown": True, "reason": reason}


if __name__ == "__main__":
    import sys

    p = Path(__file__).parent.parent.resolve()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "save":
        dst = save_snapshot(p)
        print(f"[snapshot] saved: {dst}")
    elif cmd == "calibrate":
        changes = calibrate_from_snapshot(p)
        if changes:
            print(f"[snapshot] calibrated {len(changes)} recommendation(s):")
            for c in changes:
                print(f"  - {c['symbol']} rec_id={c['rec_id']} action={c['action']}")
        else:
            print("[snapshot] no changes detected")
    elif cmd == "cooldown":
        cooldown = get_cooldown_symbols(p)
        if cooldown:
            print(f"[snapshot] {len(cooldown)} asset(s) in cooldown:")
            for c in cooldown:
                print(f"  - {c['symbol']} {c['action']} verified_at={c['verified_at']}")
        else:
            print("[snapshot] no assets in cooldown")
    elif cmd == "check":
        sym = sys.argv[2] if len(sys.argv) > 2 else ""
        result = is_in_cooldown(p, sym)
        print(f"[snapshot] {sym} in_cooldown={result['in_cooldown']}")
        if result["reason"]:
            print(f"  reason: {result['reason']}")
    else:
        print("Usage: python snapshot.py [save|calibrate|cooldown|check SYMBOL]")
