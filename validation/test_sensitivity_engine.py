from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import pandas as pd

from src.sensitivity_engine import (
    OUTPUT_COLUMNS,
    SensitivityEngineError,
    build_sensitivity_evidence,
    parse_pine_parameter_definitions,
    render_parameter_surface,
    render_sensitivity_report,
    validate_phase_inputs,
)


TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1D")


def _pine_text() -> str:
    return """
higherTimeframeInput = input.string("60", "HTF Bias Timeframe", options=["60", "240", "D"], group=GROUP_HIGHER_TIMEFRAME)
minimumPrimaryScoreInput = input.int(4, "Minimum Entry Score (Max 6)", minval=2, maxval=6, group=GROUP_PRIMARY_QUALITY)
"""


def _performance() -> pd.DataFrame:
    rows = []
    counts = (8, 9, 10, 11, 12, 13, 14, 15)
    returns = (4.0, 5.0, 6.0, -1.0, 7.0, 3.0, 8.0, 2.0)
    for index, (timeframe, count, net_return) in enumerate(zip(TIMEFRAMES, counts, returns, strict=True)):
        rows.append(
            {
                "timeframe": timeframe,
                "data_scope": "observed_processed_closed_trades_only",
                "sample_start": f"2026-01-{index + 1:02d}T00:00",
                "sample_end": f"2026-02-{index + 1:02d}T00:00",
                "initial_capital_usd": 40.0,
                "initial_capital_provenance_status": "conflicted",
                "processed_rows": count,
                "total_trades": count,
                "win_rate_pct": 48.0 + index * 2.0,
                "expectancy_usd": net_return * 0.4 / count,
                "net_return_pct": net_return,
                "profit_factor": 0.9 + index * 0.2,
                "max_drawdown_pct": 12.0 - index,
                "recovery_factor": -0.1 + index * 0.6,
                "calculation_validation_status": "passed_internal_identities",
            }
        )
    return pd.DataFrame(rows)


def _monte_carlo() -> pd.DataFrame:
    rows = []
    performance = _performance()
    for index, record in enumerate(performance.itertuples(index=False)):
        rows.append(
            {
                "timeframe": record.timeframe,
                "resampling_method": "bootstrap",
                "simulations": 10_000,
                "trade_count": int(record.total_trades),
                "observed_terminal_return_pct": float(record.net_return_pct),
                "mean_terminal_return_pct": float(record.net_return_pct) + 0.1,
                "terminal_return_interval_95_lower_pct": float(record.net_return_pct) - 5.0,
                "terminal_return_interval_95_upper_pct": float(record.net_return_pct) + 5.0,
                "probability_negative_return_pct": 30.0 - index * 2.0,
                "median_max_drawdown_pct": 8.0 - index * 0.5,
                "max_drawdown_interval_95_upper_pct": 15.0 - index * 0.5,
                "confidence_interval_label": "95% nonparametric bootstrap percentile interval",
            }
        )
    return pd.DataFrame(rows)


def _trades() -> pd.DataFrame:
    rows = []
    for record in _performance().itertuples(index=False):
        for index in range(int(record.total_trades)):
            rows.append(
                {
                    "record_id": f"{record.timeframe}-{index}",
                    "timeframe": record.timeframe,
                    "is_closed": True,
                }
            )
    return pd.DataFrame(rows)


class SensitivityDefinitionTests(unittest.TestCase):
    def test_parameter_domains_are_parsed_from_pine(self) -> None:
        definitions = parse_pine_parameter_definitions(_pine_text())
        self.assertEqual(definitions["htf_bias_timeframe"].values, ("60", "240", "D"))
        self.assertEqual(definitions["htf_bias_timeframe"].default, "60")
        self.assertEqual(definitions["minimum_entry_score"].values, ("2", "3", "4", "5", "6"))
        self.assertEqual(definitions["minimum_entry_score"].default, "4")

    def test_missing_pine_definition_fails_closed(self) -> None:
        with self.assertRaises(SensitivityEngineError):
            parse_pine_parameter_definitions("minimumPrimaryScoreInput = input.int(4)")


class SensitivityValidationTests(unittest.TestCase):
    def test_phase_inputs_reconcile(self) -> None:
        performance, bootstrap, trades = validate_phase_inputs(
            _performance(), _monte_carlo(), _trades()
        )
        self.assertEqual(len(performance), 8)
        self.assertEqual(len(bootstrap), 8)
        self.assertEqual(len(trades), sum(range(8, 16)))

    def test_duplicate_timeframe_fails_closed(self) -> None:
        performance = _performance()
        performance.loc[1, "timeframe"] = "1m"
        with self.assertRaises(SensitivityEngineError):
            validate_phase_inputs(performance, _monte_carlo(), _trades())

    def test_uncontrolled_parameter_column_is_not_silently_used(self) -> None:
        trades = _trades()
        trades["minimum_entry_score"] = 4
        with self.assertRaises(SensitivityEngineError):
            validate_phase_inputs(_performance(), _monte_carlo(), trades)


class SensitivityCalculationTests(unittest.TestCase):
    def setUp(self) -> None:
        performance, bootstrap, _ = validate_phase_inputs(
            _performance(), _monte_carlo(), _trades()
        )
        self.result = build_sensitivity_evidence(
            performance,
            bootstrap,
            parse_pine_parameter_definitions(_pine_text()),
        )

    def test_table_has_all_defined_values_without_fabricated_outcomes(self) -> None:
        table = self.result.table
        self.assertEqual(tuple(table.columns), OUTPUT_COLUMNS)
        self.assertEqual(len(table), 16)
        timeframe = table.loc[table["parameter"] == "timeframe"]
        blocked = table.loc[table["parameter"] != "timeframe"]
        self.assertEqual(len(timeframe), 8)
        self.assertEqual(len(blocked), 8)
        self.assertTrue((blocked["evidence_status"] == "not_estimable_no_controlled_outcomes").all())
        self.assertTrue(blocked["expectancy_usd"].isna().all())
        self.assertFalse(table["optimization_performed"].any())
        self.assertFalse(table["future_profitability_claimed"].any())

    def test_concentration_is_normalized_and_non_optimizing(self) -> None:
        timeframe = self.result.table.loc[self.result.table["parameter"] == "timeframe"]
        self.assertAlmostEqual(float(timeframe["sensitivity_concentration_share_pct"].sum()), 100.0)
        hhi = float(timeframe["sensitivity_concentration_hhi"].iloc[0])
        self.assertGreaterEqual(hhi, 1.0 / 8.0)
        self.assertLessEqual(hhi, 1.0)
        self.assertEqual(
            self.result.diagnostics["robustness_region"],
            "not_identifiable_without_controlled_grid",
        )
        self.assertFalse(self.result.diagnostics["optimization_performed"])

    def test_rendering_is_deterministic_and_self_contained(self) -> None:
        first = render_parameter_surface(self.result)
        second = render_parameter_surface(self.result)
        self.assertEqual(sha256(first).hexdigest(), sha256(second).hexdigest())
        self.assertGreater(len(first), 20_000)
        report = render_sensitivity_report(
            self.result,
            first,
            {"fixture.csv": "0" * 64},
        )
        self.assertIn("data:image/png;base64,", report)
        self.assertIn("not_estimable", report)
        self.assertIn("NO OPTIMIZATION", report)
        self.assertNotIn("https://", report)


if __name__ == "__main__":
    unittest.main()

