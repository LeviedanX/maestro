from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from src.config import PROCESSED_TRADES_DIR
from src.monte_carlo_engine import (
    InstitutionalMonteCarloError,
    MINIMUM_SIMULATIONS,
    STATISTICS_COLUMNS,
    run_institutional_monte_carlo,
    run_simulations,
)
from src.performance_engine import load_processed_trades


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _processed_hashes() -> dict[str, str]:
    return {
        path.name: _hash(path)
        for path in sorted(PROCESSED_TRADES_DIR.glob("*"))
        if path.is_file()
    }


class InstitutionalMonteCarloUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.trades = load_processed_trades(
            PROCESSED_TRADES_DIR / "normalized_trades.csv"
        )

    def test_institutional_minimum_is_enforced(self) -> None:
        with self.assertRaises(InstitutionalMonteCarloError):
            run_simulations(
                self.trades,
                simulations=MINIMUM_SIMULATIONS - 1,
                initial_capital=40.0,
            )


class InstitutionalMonteCarloIntegrationTests(unittest.TestCase):
    def test_outputs_are_complete_non_destructive_and_repeatable(self) -> None:
        hashes_before = _processed_hashes()
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            report = root / "monte_carlo_report.html"
            statistics = root / "monte_carlo_statistics.csv"
            figure = root / "monte_carlo_distribution.png"

            first = run_institutional_monte_carlo(
                report_path=report,
                statistics_path=statistics,
                figure_path=figure,
            )
            hashes_first = (_hash(report), _hash(statistics), _hash(figure))
            second = run_institutional_monte_carlo(
                report_path=report,
                statistics_path=statistics,
                figure_path=figure,
            )
            hashes_second = (_hash(report), _hash(statistics), _hash(figure))

            self.assertEqual(first, second)
            self.assertEqual(hashes_first, hashes_second)
            self.assertEqual(first["total_simulated_paths"], 160_000)
            self.assertEqual(first["closed_trades_used"], 322)
            self.assertEqual(first["open_trades_excluded"], 1)
            self.assertEqual(first["upstream_quarantined_rows"], 2)
            self.assertFalse(first["future_profitability_claimed"])

            table = pd.read_csv(statistics)
            self.assertEqual(tuple(table.columns), STATISTICS_COLUMNS)
            self.assertEqual(len(table), 16)
            self.assertTrue((table["simulations"] == 10_000).all())
            self.assertEqual(set(table["resampling_method"]), {"permutation", "bootstrap"})
            permutation = table.loc[table["resampling_method"] == "permutation"]
            bootstrap = table.loc[table["resampling_method"] == "bootstrap"]
            self.assertTrue(permutation["terminal_return_invariant_under_permutation"].all())
            self.assertFalse(bootstrap["terminal_return_invariant_under_permutation"].any())
            self.assertTrue(
                (
                    permutation["terminal_return_interval_95_lower_pct"]
                    == permutation["terminal_return_interval_95_upper_pct"]
                ).all()
            )
            self.assertTrue(
                (
                    bootstrap["terminal_return_interval_95_lower_pct"]
                    <= bootstrap["terminal_return_interval_95_upper_pct"]
                ).all()
            )
            self.assertGreater(figure.stat().st_size, 200_000)
            html = report.read_text(encoding="utf-8")
            self.assertIn("Trade sequence permutation", html)
            self.assertIn("IID bootstrap resampling", html)
            self.assertIn("tidak menjamin profitabilitas masa depan", html)
            self.assertIn("data:image/png;base64,", html)

        self.assertEqual(hashes_before, _processed_hashes())


if __name__ == "__main__":
    unittest.main()
