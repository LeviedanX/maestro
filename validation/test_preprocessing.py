from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

import pandas as pd

from src.config import RAW_TRADES_DIR, TIMEFRAME_SPECS
from src.preprocessing import (
    DatatypeConversionError,
    SchemaValidationError,
    detect_segments,
    hash_file,
    load_csv,
    parse_number,
    parse_timestamp,
    process_dataset,
    run_preprocessing,
    validate_schema,
)


def _directory_hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


class PreprocessingUnitTests(unittest.TestCase):
    def test_number_converter_handles_tradingview_variants(self) -> None:
        self.assertEqual(parse_number("4,593.801"), 4593.801)
        self.assertEqual(parse_number("−0.801"), -0.801)
        self.assertEqual(parse_number("1\u202fK"), 1000.0)
        self.assertEqual(parse_number("+0.18%"), 0.18)

    def test_timestamp_parser_is_explicit_and_strict(self) -> None:
        self.assertEqual(
            parse_timestamp("Aug 21, 2026, 11:05"),
            datetime(2026, 8, 21, 11, 5),
        )
        self.assertEqual(
            parse_timestamp("Dec 23, 1977"),
            datetime(1977, 12, 23),
        )
        with self.assertRaises(DatatypeConversionError):
            parse_timestamp("2026/08/21 11:05")

    def test_schema_validator_rejects_unsupported_width(self) -> None:
        frame = pd.DataFrame([["1"] * 19], columns=[f"column_{index}" for index in range(19)])
        with self.assertRaises(SchemaValidationError):
            validate_schema(frame, "synthetic.csv")

    def test_timeframe_detector_identifies_all_daily_container_segments(self) -> None:
        spec = next(item for item in TIMEFRAME_SPECS if item.label == "1D")
        frame = load_csv(RAW_TRADES_DIR / spec.filename)
        validate_schema(frame, spec.filename)
        segments = detect_segments(frame)
        self.assertEqual(
            [segment.detection.label for segment in segments],
            ["5m", "15m", "30m", "2h", "4h", "1D"],
        )
        self.assertEqual([segment.row_count for segment in segments], [61, 30, 28, 28, 28, 28])

    def test_signal_orientation_is_normalized_for_one_hour_export(self) -> None:
        spec = next(item for item in TIMEFRAME_SPECS if item.label == "1h")
        result = process_dataset(RAW_TRADES_DIR / spec.filename, spec)
        self.assertEqual(result.signal_orientation, "column_b_exit")
        self.assertEqual(len(result.accepted), 61)
        self.assertEqual(int((~result.accepted["is_closed"]).sum()), 1)
        open_trade = result.accepted.loc[~result.accepted["is_closed"]].iloc[0]
        self.assertEqual(open_trade["entry_signal"], "MX BUY")
        self.assertEqual(open_trade["exit_signal"], "Open")


class PreprocessingIntegrationTests(unittest.TestCase):
    def test_pipeline_is_reconciled_non_destructive_and_repeatable(self) -> None:
        raw_hashes_before = {
            path.name: hash_file(path)
            for path in sorted(RAW_TRADES_DIR.glob("*.csv"))
        }
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            processed = root / "processed"
            report = root / "preprocessing_report.json"
            summary = root / "processed_dataset_summary.csv"

            first = run_preprocessing(
                processed_dir=processed,
                report_path=report,
                summary_path=summary,
            )
            first_hashes = _directory_hashes(root)
            second = run_preprocessing(
                processed_dir=processed,
                report_path=report,
                summary_path=summary,
            )
            second_hashes = _directory_hashes(root)

            self.assertEqual(first, second)
            self.assertEqual(first_hashes, second_hashes)
            self.assertEqual(first["raw_rows"], 918)
            self.assertEqual(first["target_candidate_rows"], 325)
            self.assertEqual(first["accepted_rows"], 323)
            self.assertEqual(first["rejected_rows"], 2)
            self.assertEqual(first["closed_rows"], 322)
            self.assertEqual(first["open_rows"], 1)

            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertFalse(payload["analysis_executed"])
            self.assertFalse(payload["raw_data_modified"])
            self.assertTrue(payload["input"]["all_hashes_unchanged"])
            self.assertEqual(payload["quarantine"]["reason_counts"], {"INVALID_PERCENT_FORMAT": 2})

            rejected = pd.read_csv(processed / "rejected_trades.csv")
            self.assertEqual(
                set(zip(rejected["source_file"], rejected["source_row"])),
                {("tf4jam.csv", 161), ("tf1hari.csv", 188)},
            )
            normalized = pd.read_csv(processed / "normalized_trades.csv")
            self.assertEqual(len(normalized), 323)
            self.assertEqual(normalized["record_id"].nunique(), 323)
            self.assertEqual(set(normalized["timeframe"]), {spec.label for spec in TIMEFRAME_SPECS})

        raw_hashes_after = {
            path.name: hash_file(path)
            for path in sorted(RAW_TRADES_DIR.glob("*.csv"))
        }
        self.assertEqual(raw_hashes_before, raw_hashes_after)


if __name__ == "__main__":
    unittest.main()
