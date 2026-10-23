"""Performance metrics untuk trade closed yang benar-benar terobservasi."""

from __future__ import annotations

from datetime import datetime
import math

import numpy as np
import pandas as pd


class MetricsError(ValueError):
    """Input performance engine tidak memenuhi kontrak."""


def _closed_trades(trades: pd.DataFrame) -> pd.DataFrame:
    required = {"timeframe", "is_closed", "net_pnl_usd", "exit_timestamp", "side", "trade_id"}
    missing = required - set(trades.columns)
    if missing:
        raise MetricsError(f"Kolom wajib tidak tersedia: {sorted(missing)}")
    closed = trades.loc[trades["is_closed"] & trades["net_pnl_usd"].notna()].copy()
    if closed.empty:
        raise MetricsError("Tidak ada closed trade dengan Net PnL valid.")
    if closed["exit_timestamp"].isna().any():
        raise MetricsError("Closed trade memiliki exit_timestamp kosong.")
    return closed.sort_values(["exit_timestamp", "trade_id"], kind="stable").reset_index(drop=True)


def _max_streak(values: np.ndarray, predicate: str) -> int:
    if predicate == "win":
        matches = values > 0
    elif predicate == "loss":
        matches = values < 0
    else:
        raise ValueError(predicate)
    best = current = 0
    for matched in matches:
        current = current + 1 if matched else 0
        best = max(best, current)
    return best


def build_equity_curve(trades: pd.DataFrame, initial_capital: float) -> pd.DataFrame:
    """Rekonstruksi equity lokal dari PnL baris terobservasi, bukan raw cumulative PnL."""

    if initial_capital <= 0:
        raise MetricsError("initial_capital harus lebih besar dari nol.")
    closed = _closed_trades(trades)
    pnl = closed["net_pnl_usd"].to_numpy(dtype=float)
    if not np.isfinite(pnl).all():
        raise MetricsError("Net PnL mengandung nilai non-finite.")

    equity = initial_capital + np.cumsum(pnl)
    peaks = np.maximum.accumulate(np.concatenate(([initial_capital], equity)))[1:]
    drawdown_usd = peaks - equity
    drawdown_pct = np.divide(
        drawdown_usd,
        peaks,
        out=np.zeros_like(drawdown_usd),
        where=peaks != 0,
    ) * 100

    curve = closed[
        ["timeframe", "trade_id", "side", "entry_timestamp", "exit_timestamp", "net_pnl_usd"]
    ].copy()
    curve.insert(1, "observed_sequence", np.arange(1, len(curve) + 1))
    curve["initial_capital_usd"] = initial_capital
    curve["observed_cumulative_pnl_usd"] = np.cumsum(pnl)
    curve["equity_usd"] = equity
    curve["running_peak_usd"] = peaks
    curve["drawdown_usd"] = drawdown_usd
    curve["drawdown_pct"] = drawdown_pct
    curve["data_scope"] = "observed_rows_only"
    return curve


def calculate_performance_metrics(trades: pd.DataFrame, initial_capital: float) -> dict[str, object]:
    """Hitung metrik realized pada subset baris terobservasi.

    Sharpe approximation dihitung per trade: mean(PnL) / sample_std(PnL) * sqrt(N),
    tanpa risk-free rate dan tanpa annualisasi berbasis kalender.
    """

    closed = _closed_trades(trades)
    curve = build_equity_curve(closed, initial_capital)
    pnl = closed["net_pnl_usd"].to_numpy(dtype=float)
    positive = pnl[pnl > 0]
    negative = pnl[pnl < 0]
    zero = pnl[pnl == 0]
    gross_profit = float(positive.sum())
    gross_loss = float(-negative.sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else math.inf
    sample_std = float(np.std(pnl, ddof=1)) if len(pnl) > 1 else math.nan
    sharpe = (
        float(np.mean(pnl) / sample_std * math.sqrt(len(pnl)))
        if len(pnl) > 1 and sample_std > 0
        else math.nan
    )
    average_loss = float(negative.mean()) if len(negative) else math.nan
    average_win = float(positive.mean()) if len(positive) else math.nan
    payoff_ratio = average_win / abs(average_loss) if len(positive) and len(negative) else math.nan
    net_profit = float(pnl.sum())
    maximum_drawdown = float(curve["drawdown_usd"].max())
    recovery_factor = net_profit / maximum_drawdown if maximum_drawdown > 0 else math.inf

    start = closed["exit_timestamp"].iloc[0]
    end = closed["exit_timestamp"].iloc[-1]
    timeframe_values = closed["timeframe"].dropna().unique()
    if len(timeframe_values) != 1:
        raise MetricsError("calculate_performance_metrics menerima tepat satu timeframe.")

    return {
        "timeframe": str(timeframe_values[0]),
        "data_scope": "observed_rows_only",
        "sample_start": start.isoformat(timespec="minutes") if isinstance(start, datetime) else str(start),
        "sample_end": end.isoformat(timespec="minutes") if isinstance(end, datetime) else str(end),
        "initial_capital_usd": float(initial_capital),
        "total_trades": int(len(pnl)),
        "winning_trades": int(len(positive)),
        "losing_trades": int(len(negative)),
        "breakeven_trades": int(len(zero)),
        "win_rate_pct": float(len(positive) / len(pnl) * 100),
        "gross_profit_usd": gross_profit,
        "gross_loss_usd": gross_loss,
        "net_profit_usd": net_profit,
        "net_return_pct": float(net_profit / initial_capital * 100),
        "profit_factor": float(profit_factor),
        "expectancy_usd": float(np.mean(pnl)),
        "average_win_usd": average_win,
        "average_loss_usd": average_loss,
        "payoff_ratio": float(payoff_ratio),
        "max_drawdown_usd": maximum_drawdown,
        "max_drawdown_pct": float(curve["drawdown_pct"].max()),
        "recovery_factor": float(recovery_factor),
        "trade_sharpe_approx": sharpe,
        "max_consecutive_wins": _max_streak(pnl, "win"),
        "max_consecutive_losses": _max_streak(pnl, "loss"),
        "long_trades": int((closed["side"] == "long").sum()),
        "short_trades": int((closed["side"] == "short").sum()),
    }


def calculate_basic_metrics(trades: pd.DataFrame, initial_capital: float = 40.0) -> dict[str, object]:
    """Compatibility wrapper untuk API awal repository."""

    return calculate_performance_metrics(trades, initial_capital)
