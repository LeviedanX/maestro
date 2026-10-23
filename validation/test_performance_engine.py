from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from src.config import PROCESSED_TRADES_DIR
from src.performance_engine import (
    OUTPUT_COLUMNS,
    build_performance_table,
    load_processed_trades,
    run_performance_analysis,
)


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _processed_hashes() -> dict[str, str]:
    return {
        path.name: _hash(path)
        for path in sorted(PROCESSED_TRADES_DIR.glob("*"))
        if path.is_file()
    }


class PerformanceEngineUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.trades = load_processed_trades(
            PROCESSED_TRADES_DIR / "normalized_trades.csv"
        )

    def test_processed_contract_and_closed_inventory(self) -> None:
        self.assertEqual(len(self.trades), 323)
        self.assertEqual(int(self.trades["is_closed"].sum()), 322)
        self.assertEqual(int((~self.trades["is_closed"]).sum()), 1)
        self.assertEqual(self.trades["record_id"].nunique(), 323)

    def test_performance_table_reconciles_required_metrics(self) -> None:
        table, curves = build_performance_table(
            self.trades,
            initial_capital=40.0,
            quarantine_counts={"4h": 1, "1D": 1},
        )
        self.assertEqual(tuple(table.columns), OUTPUT_COLUMNS)
        self.assertEqual(len(table), 8)
        self.assertEqual(int(table["total_trades"].sum()), 322)
        self.assertEqual(
            table.set_index("timeframe")["total_trades"].to_dict(),
            {"1m": 61, "5m": 61, "15m": 30, "30m": 28, "1h": 60, "2h": 28, "4h": 27, "1D": 27},
        )
        self.assertTrue(table["equity_reconciliation_passed"].all())
        self.assertTrue(table["implied_initial_capital_matches_assumption"].all())
        self.assertFalse(table["tv_snapshot_comparable"].any())
        self.assertEqual(
            set(table["tv_validation_status"]),
            {"not_comparable_snapshot_mismatch"},
        )
        self.assertEqual(len(curves), 322)

        for row in table.itertuples(index=False):
            self.assertEqual(
                row.total_trades,
                row.winning_trades + row.losing_trades + row.breakeven_trades,
            )
            self.assertAlmostEqual(
                row.net_profit_usd,
                row.gross_profit_usd - row.gross_loss_usd,
            )
            self.assertAlmostEqual(
                row.ending_equity_usd,
                row.initial_capital_usd + row.net_profit_usd,
            )
            self.assertAlmostEqual(
                row.recovery_factor,
                row.net_profit_usd / row.max_drawdown_usd,
            )


class PerformanceEngineIntegrationTests(unittest.TestCase):
    def test_run_is_non_destructive_and_deterministic(self) -> None:
        processed_before = _processed_hashes()
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            metrics_path = root / "performance_metrics.csv"
            figure_path = root / "equity_curve.png"

            first = run_performance_analysis(
                metrics_path=metrics_path,
                figure_path=figure_path,
            )
            first_hashes = (_hash(metrics_path), _hash(figure_path))
            second = run_performance_analysis(
                metrics_path=metrics_path,
                figure_path=figure_path,
            )
            second_hashes = (_hash(metrics_path), _hash(figure_path))

            self.assertEqual(first, second)
            self.assertEqual(first_hashes, second_hashes)
            self.assertEqual(first["closed_trades_analyzed"], 322)
            self.assertEqual(first["open_trades_excluded"], 1)
            self.assertEqual(first["upstream_quarantined_rows"], 2)
            self.assertEqual(first["tradingview_comparable_timeframes"], 0)
            self.assertTrue(first["processed_hashes_unchanged"])
            self.assertGreater(figure_path.stat().st_size, 100_000)

            metrics = pd.read_csv(metrics_path)
            self.assertEqual(len(metrics), 8)
            self.assertFalse(metrics["publication_eligible"].any())

        self.assertEqual(processed_before, _processed_hashes())


if __name__ == "__main__":
    unittest.main()
