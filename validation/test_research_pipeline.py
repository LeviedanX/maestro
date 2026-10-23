from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import pandas as pd

from src.config import TIMEFRAME_SPECS
from src.data_loader import (
    detect_trade_segments,
    load_trade_dataset,
    parse_tradingview_number,
    parse_tradingview_timestamp,
)
from src.metrics import build_equity_curve, calculate_performance_metrics
from src.monte_carlo import run_monte_carlo
from src.sensitivity import run_sensitivity_analysis
from src.visualization import plot_sensitivity_heatmap


class DataLoaderTests(unittest.TestCase):
    def test_tradingview_number_variants(self) -> None:
        self.assertEqual(parse_tradingview_number("4,593.801"), 4593.801)
        self.assertEqual(parse_tradingview_number(chr(0x2212) + "0.801"), -0.801)
        self.assertEqual(parse_tradingview_number("1" + chr(0x202F) + "K"), 1000.0)
        self.assertEqual(parse_tradingview_number("+0.18%"), 0.18)

    def test_timestamp_supports_intraday_and_daily(self) -> None:
        self.assertEqual(
            parse_tradingview_timestamp("Aug 21, 2026, 11:05"),
            datetime(2026, 8, 21, 11, 5),
        )
        self.assertEqual(
            parse_tradingview_timestamp("Dec 23, 1977"),
            datetime(1977, 12, 23),
        )

    def test_append_segments_and_target_selection(self) -> None:
        daily_spec = next(spec for spec in TIMEFRAME_SPECS if spec.label == "1D")
        dataset = load_trade_dataset(daily_spec)
        self.assertEqual([segment.row_count for segment in dataset.segments], [61, 30, 28, 28, 28, 28])
        self.assertEqual(dataset.selected_segment_index, 5)
        self.assertEqual(len(dataset.trades), 28)
        self.assertTrue(dataset.trades["timeframe_consistent"].all())

    def test_detect_segments_on_synthetic_ids(self) -> None:
        frame = pd.DataFrame({"id": [5, 4, 1, 3, 2, 1]})
        segments = detect_trade_segments(frame)
        self.assertEqual([(item.start, item.end) for item in segments], [(0, 3), (3, 6)])


class MetricsTests(unittest.TestCase):
    @staticmethod
    def _fixture() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "timeframe": ["1h"] * 4,
                "trade_id": [1, 2, 3, 4],
                "side": ["long", "short", "long", "short"],
                "entry_timestamp": [datetime(2026, 1, 1, hour=i) for i in range(4)],
                "exit_timestamp": [datetime(2026, 1, 1, hour=i + 1) for i in range(4)],
                "net_pnl_usd": [2.0, -1.0, -3.0, 4.0],
                "is_closed": [True] * 4,
            }
        )

    def test_metrics_known_values(self) -> None:
        metrics = calculate_performance_metrics(self._fixture(), initial_capital=40.0)
        self.assertEqual(metrics["total_trades"], 4)
        self.assertEqual(metrics["winning_trades"], 2)
        self.assertAlmostEqual(metrics["win_rate_pct"], 50.0)
        self.assertAlmostEqual(metrics["gross_profit_usd"], 6.0)
        self.assertAlmostEqual(metrics["gross_loss_usd"], 4.0)
        self.assertAlmostEqual(metrics["profit_factor"], 1.5)
        self.assertAlmostEqual(metrics["expectancy_usd"], 0.5)
        self.assertAlmostEqual(metrics["max_drawdown_usd"], 4.0)
        self.assertAlmostEqual(metrics["recovery_factor"], 0.5)

    def test_equity_curve_reconciliation(self) -> None:
        curve = build_equity_curve(self._fixture(), initial_capital=40.0)
        np.testing.assert_allclose(curve["equity_usd"], [42.0, 41.0, 38.0, 42.0])
        np.testing.assert_allclose(curve["drawdown_usd"], [0.0, 1.0, 4.0, 0.0])
        self.assertAlmostEqual(curve.iloc[-1]["observed_cumulative_pnl_usd"], 2.0)


class MonteCarloTests(unittest.TestCase):
    def test_permutation_is_deterministic_and_terminal_return_invariant(self) -> None:
        pnl = np.array([2.0, -1.0, -3.0, 4.0])
        original = pnl.copy()
        first = run_monte_carlo(pnl, simulations=250, initial_capital=40.0, seed=17)
        second = run_monte_carlo(pnl, simulations=250, initial_capital=40.0, seed=17)

        np.testing.assert_array_equal(pnl, original)
        np.testing.assert_allclose(first.max_drawdown_usd, second.max_drawdown_usd)
        np.testing.assert_allclose(first.terminal_return_pct, 5.0)
        self.assertTrue(first.summary["terminal_return_invariant_under_permutation"])
        self.assertEqual(first.summary["simulations"], 250)

    def test_ruin_definition_uses_full_path(self) -> None:
        result = run_monte_carlo(
            [-25.0, 30.0],
            simulations=500,
            initial_capital=40.0,
            seed=1,
            ruin_equity_fraction=0.5,
        )
        probability = float(result.summary["ruin_probability_pct"])
        self.assertGreater(probability, 0.0)
        self.assertLess(probability, 100.0)

    def test_bootstrap_is_deterministic_and_varies_terminal_return(self) -> None:
        pnl = np.array([2.0, -1.0, -3.0, 4.0])
        first = run_monte_carlo(
            pnl,
            simulations=2_000,
            initial_capital=40.0,
            seed=23,
            method="bootstrap",
        )
        second = run_monte_carlo(
            pnl,
            simulations=2_000,
            initial_capital=40.0,
            seed=23,
            method="bootstrap",
        )
        np.testing.assert_allclose(first.terminal_return_pct, second.terminal_return_pct)
        self.assertGreater(float(np.std(first.terminal_return_pct)), 0.0)
        self.assertFalse(first.summary["terminal_return_invariant_under_permutation"])
        self.assertGreater(float(first.summary["probability_negative_return_pct"]), 0.0)
        self.assertLess(float(first.summary["probability_negative_return_pct"]), 100.0)


class SensitivityTests(unittest.TestCase):
    @staticmethod
    def _metrics_frame() -> pd.DataFrame:
        rows = []
        for index, timeframe in enumerate(["1m", "5m", "15m"]):
            rows.append(
                {
                    "timeframe": timeframe,
                    "sample_start": "2026-01-01T00:00",
                    "sample_end": "2026-01-02T00:00",
                    "total_trades": 10 + index,
                    "net_return_pct": 2.0 + index,
                    "win_rate_pct": 50.0 + index,
                    "profit_factor": 1.1 + index / 10,
                    "expectancy_usd": 0.1 + index / 100,
                    "max_drawdown_pct": 5.0 - index,
                    "trade_sharpe_approx": 0.5 + index / 10,
                }
            )
        return pd.DataFrame(rows)

    def test_missing_parameter_axes_are_not_estimable(self) -> None:
        table = run_sensitivity_analysis(self._metrics_frame())
        blocked = table.loc[table["analysis_dimension"] != "timeframe"]
        self.assertEqual(set(blocked["analysis_dimension"]), {"htf_bias_timeframe", "minimum_entry_score"})
        self.assertTrue((blocked["status"] == "not_estimable").all())

    def test_heatmap_renders(self) -> None:
        table = run_sensitivity_analysis(self._metrics_frame())
        with TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "sensitivity.png"
            plot_sensitivity_heatmap(table, target)
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 1_000)


if __name__ == "__main__":
    unittest.main()
