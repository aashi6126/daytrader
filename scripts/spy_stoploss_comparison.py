"""
SPY Stop-Loss Comparison
========================
Tests SPY across multiple signal types and stop-loss levels to determine
whether 16% is optimal or if we're leaving money on the table.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from stock_backtest_engine import StockBacktestParams, run_stock_backtest

DATE_RANGE = dict(start_date=date(2025, 8, 18), end_date=date(2026, 2, 13))

# Test multiple signal types relevant for SPY
STRATEGIES = {
    "orb_direction": dict(
        signal_type="orb_direction", ema_fast=5, ema_slow=55,
        profit_target_percent=30.0, trailing_stop_percent=15.0,
        trailing_stop_after_scale_out_percent=20.0,
        max_hold_minutes=120, rsi_period=9, orb_minutes=15,
        min_confluence=5, vol_threshold=1.5,
        orb_vwap_filter=True, orb_stop_mult=1.25, orb_target_mult=2.0,
    ),
    "orb": dict(
        signal_type="orb", ema_fast=8, ema_slow=21,
        profit_target_percent=30.0, trailing_stop_percent=20.0,
        trailing_stop_after_scale_out_percent=10.0,
        max_hold_minutes=90, orb_minutes=15,
        min_confluence=5, vol_threshold=1.5,
    ),
    "ema_cross": dict(
        signal_type="ema_cross", ema_fast=8, ema_slow=21,
        profit_target_percent=40.0, trailing_stop_percent=20.0,
        trailing_stop_after_scale_out_percent=10.0,
        max_hold_minutes=90, min_confluence=5, vol_threshold=1.5,
    ),
    "confluence": dict(
        signal_type="confluence", ema_fast=8, ema_slow=21,
        profit_target_percent=30.0, trailing_stop_percent=15.0,
        trailing_stop_after_scale_out_percent=10.0,
        max_hold_minutes=90, rsi_period=14,
        min_confluence=3, vol_threshold=1.5,
    ),
}

STOP_LOSS_VALUES = [8, 10, 12, 16, 20, 25, 30]

for strat_name, strat_params in STRATEGIES.items():
    print(f"\n{'='*120}")
    print(f"Strategy: {strat_name}")
    print(f"{'='*120}")
    print(f"{'SL%':>5} | {'P&L':>10} | {'Trades':>6} | {'Win%':>6} | {'PF':>6} | {'MaxDD':>8} | {'AvgWin':>8} | {'AvgLoss':>8} | {'LgWin':>8} | {'LgLoss':>8} | Exit Reasons")
    print("-" * 140)

    for sl in STOP_LOSS_VALUES:
        params = StockBacktestParams(
            ticker="SPY", bar_interval="5m", quantity=2,
            **DATE_RANGE, **strat_params,
            stop_loss_percent=float(sl),
        )
        result = run_stock_backtest(params)
        exits = ", ".join(f"{k}:{v}" for k, v in sorted(result.exit_reasons.items()))
        print(
            f"{sl:>4}% | "
            f"${result.total_pnl:>9.2f} | "
            f"{result.total_trades:>6} | "
            f"{result.win_rate:>5.1f}% | "
            f"{result.profit_factor:>6.2f} | "
            f"${result.max_drawdown:>7.2f} | "
            f"${result.avg_win:>7.2f} | "
            f"${result.avg_loss:>7.2f} | "
            f"${result.largest_win:>7.2f} | "
            f"${result.largest_loss:>7.2f} | "
            f"{exits}"
        )

# Also test QQQ since it's also 0DTE
print(f"\n\n{'#'*120}")
print(f"QQQ COMPARISON")
print(f"{'#'*120}")

QQQ_STRATEGIES = {
    "orb_direction (QQQ top)": dict(
        signal_type="orb_direction", ema_fast=5, ema_slow=13,
        profit_target_percent=30.0, trailing_stop_percent=30.0,
        trailing_stop_after_scale_out_percent=10.0,
        max_hold_minutes=90, orb_minutes=15,
        min_confluence=5, vol_threshold=1.5,
        orb_body_min_pct=0.3, orb_vwap_filter=True, orb_stop_mult=1.0, orb_target_mult=1.5,
    ),
    "orb (QQQ #2)": dict(
        signal_type="orb", ema_fast=21, ema_slow=13,
        profit_target_percent=20.0, trailing_stop_percent=20.0,
        trailing_stop_after_scale_out_percent=10.0,
        max_hold_minutes=60, orb_minutes=30,
        atr_period=14, atr_stop_mult=2.5,
        min_confluence=5, vol_threshold=1.5,
    ),
}

for strat_name, strat_params in QQQ_STRATEGIES.items():
    print(f"\n{'='*120}")
    print(f"Strategy: {strat_name}")
    print(f"{'='*120}")
    print(f"{'SL%':>5} | {'P&L':>10} | {'Trades':>6} | {'Win%':>6} | {'PF':>6} | {'MaxDD':>8} | {'AvgWin':>8} | {'AvgLoss':>8} | Exit Reasons")
    print("-" * 120)

    for sl in STOP_LOSS_VALUES:
        params = StockBacktestParams(
            ticker="QQQ", bar_interval="5m", quantity=2,
            **DATE_RANGE, **strat_params,
            stop_loss_percent=float(sl),
        )
        result = run_stock_backtest(params)
        exits = ", ".join(f"{k}:{v}" for k, v in sorted(result.exit_reasons.items()))
        print(
            f"{sl:>4}% | "
            f"${result.total_pnl:>9.2f} | "
            f"{result.total_trades:>6} | "
            f"{result.win_rate:>5.1f}% | "
            f"{result.profit_factor:>6.2f} | "
            f"${result.max_drawdown:>7.2f} | "
            f"${result.avg_win:>7.2f} | "
            f"${result.avg_loss:>7.2f} | "
            f"{exits}"
        )
