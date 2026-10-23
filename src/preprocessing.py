"""Deterministic, non-destructive preprocessing for TradingView trade exports.

This module intentionally stops before performance analysis.  Raw CSV files are
only opened for reading; every normalized or rejected record is written to a
separate derived-data directory with source-row and source-hash provenance.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from io import StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable, Mapping
import json
import logging
import math
import os
import platform
import re

import numpy as np
import pandas as pd

from .config import (
    PROCESSED_TRADES_DIR,
    RAW_TRADES_DIR,
    REPORTS_DIR,
    ROOT,
    TABLES_DIR,
    TIMEFRAME_SPECS,
    TimeframeSpec,
)
from .schema import DATASET_SCHEMA, validate_dataframe_schema


LOGGER = logging.getLogger(__name__)
PIPELINE_VERSION = "1.0.0"
SUPPORTED_COLUMN_COUNTS = frozenset({20, 21})
TIMESTAMP_FORMATS: tuple[tuple[str, str], ...] = (
    ("%b %d, %Y, %H:%M", "minute"),
    ("%b %d, %Y", "date"),
)
NUMERIC_MISSING = frozenset({"", "-", "--", "n/a", "na", "nan", "none"})
TIMEFRAME_LABELS: Mapping[int, str] = {
    1: "1m",
    5: "5m",
    15: "15m",
    30: "30m",
    60: "1h",
    120: "2h",
    240: "4h",
    1_440: "1D",
}

NORMALIZED_COLUMNS = DATASET_SCHEMA.columns

REJECTED_COLUMNS = (
    "source_file",
    "source_sha256",
    "source_row",
    "source_segment",
    "expected_timeframe",
    "detected_timeframe",
    "trade_id",
    "reason_codes",
    "reason_details",
    "raw_row_json",
)


class PreprocessingError(RuntimeError):
    """Base exception for a controlled preprocessing failure."""


class CsvLoadError(PreprocessingError):
    """Raised when a raw CSV cannot be read safely."""


class SchemaValidationError(PreprocessingError):
    """Raised when an input CSV violates the supported structural schema."""


class TimeframeDetectionError(PreprocessingError):
    """Raised when the target timeframe segment cannot be identified."""


class DatatypeConversionError(ValueError):
    """Raised for one value that cannot be converted without guessing."""


@dataclass(frozen=True)
class RawSchema:
    """Validated positional schema of a TradingView UI export."""

    column_count: int
    has_commission_column: bool
    headers: tuple[str, ...]


@dataclass(frozen=True)
class TimeframeDetection:
    """Evidence-based timeframe inference for one trade-ID segment."""

    label: str | None
    nominal_minutes: int | None
    modal_elapsed_minutes_per_bar: float | None
    evidence_rows: int
    modal_rows: int
    confidence: float
    relative_error: float | None


@dataclass(frozen=True)
class TradeSegment:
    """Half-open raw row interval with monotonically decreasing trade IDs."""

    start: int
    end: int
    first_trade_id: int
    last_trade_id: int
    detection: TimeframeDetection

    @property
    def row_count(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class RowIssue:
    """Machine-readable reason for quarantining one target row."""

    code: str
    detail: str


@dataclass
class ConversionResult:
    """Typed rows plus conversion issues keyed by original DataFrame index."""

    frame: pd.DataFrame
    issues: dict[int, list[RowIssue]]
    raw_rows: dict[int, str]
    signal_orientation: str


@dataclass
class DatasetProcessingResult:
    """Complete preprocessing result for one configured source file."""

    spec: TimeframeSpec
    source_path: Path
    source_sha256: str
    source_size_bytes: int
    raw_row_count: int
    raw_column_count: int
    schema: RawSchema
    segments: list[TradeSegment]
    selected_segment_number: int
    signal_orientation: str
    accepted: pd.DataFrame
    rejected: pd.DataFrame
    missing_trade_ids_within_target_range: int


def hash_file(path: Path) -> str:
    """Return a streaming SHA-256 digest without mutating the file."""

    digest = sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CsvLoadError(f"Gagal menghitung hash {path}: {exc}") from exc
    return digest.hexdigest()


def load_csv(path: Path) -> pd.DataFrame:
    """Load one raw TradingView CSV as strings with explicit error handling."""

    if not path.is_file():
        raise CsvLoadError(f"File CSV tidak ditemukan: {path}")
    try:
        frame = pd.read_csv(
            path,
            encoding="utf-8-sig",
            dtype="string",
            keep_default_na=True,
        )
    except (OSError, UnicodeError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise CsvLoadError(f"Gagal membaca {path}: {exc}") from exc
    if frame.empty:
        raise CsvLoadError(f"CSV tidak memiliki record data: {path}")
    LOGGER.debug("Loaded %s with shape %s", path.name, frame.shape)
    return frame


def validate_schema(frame: pd.DataFrame, source_name: str = "<dataframe>") -> RawSchema:
    """Validate the supported 20/21-column positional TradingView schema."""

    column_count = int(frame.shape[1])
    if column_count not in SUPPORTED_COLUMN_COUNTS:
        raise SchemaValidationError(
            f"{source_name}: ditemukan {column_count} kolom; schema yang didukung 20 atau 21."
        )
    headers = tuple(str(column) for column in frame.columns)
    if len(set(headers)) != len(headers):
        raise SchemaValidationError(f"{source_name}: nama kolom tidak unik.")
    if "tradenumber" not in headers[0].casefold():
        raise SchemaValidationError(
            f"{source_name}: kolom pertama bukan penanda nomor trade TradingView."
        )
    if "long" not in headers[1].casefold() or "short" not in headers[-1].casefold():
        raise SchemaValidationError(
            f"{source_name}: kolom marker Long/Short tidak berada pada posisi yang diharapkan."
        )
    ids = pd.to_numeric(frame.iloc[:, 0], errors="coerce")
    invalid_ids = ids.isna() | (ids <= 0) | (ids % 1 != 0)
    if invalid_ids.any():
        csv_rows = (np.flatnonzero(invalid_ids.to_numpy()) + 2).tolist()
        raise SchemaValidationError(
            f"{source_name}: trade ID invalid pada baris CSV {csv_rows}."
        )
    return RawSchema(
        column_count=column_count,
        has_commission_column=column_count == 21,
        headers=headers,
    )


def parse_number(value: object, *, allow_missing: bool = False) -> float:
    """Convert TradingView numbers, Unicode signs, percent text, and K/M/B suffixes."""

    if value is None or pd.isna(value):
        if allow_missing:
            return math.nan
        raise DatatypeConversionError("nilai numerik kosong")
    text = str(value).strip()
    if text.casefold() in NUMERIC_MISSING:
        if allow_missing:
            return math.nan
        raise DatatypeConversionError(f"nilai numerik kosong: {text!r}")
    normalized = (
        text.replace("\u2212", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u202f", "")
        .replace("\u00a0", "")
        .replace(" ", "")
        .replace(",", "")
        .replace("$", "")
        .replace("%", "")
    )
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))([KMBkmb]?)", normalized)
    if match is None:
        raise DatatypeConversionError(f"angka TradingView tidak dikenali: {text!r}")
    multiplier = {"": 1.0, "k": 1e3, "m": 1e6, "b": 1e9}[match.group(2).casefold()]
    result = float(match.group(1)) * multiplier
    if not math.isfinite(result):
        raise DatatypeConversionError(f"angka tidak finite: {text!r}")
    return result


def _parse_timestamp_with_precision(value: object) -> tuple[datetime, str]:
    if value is None or pd.isna(value) or not str(value).strip():
        raise DatatypeConversionError("timestamp kosong")
    text = str(value).strip()
    for timestamp_format, precision in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, timestamp_format), precision
        except ValueError:
            continue
    raise DatatypeConversionError(f"timestamp TradingView tidak dikenali: {text!r}")


def parse_timestamp(value: object) -> datetime:
    """Parse supported English TradingView timestamps as timezone-naive datetime."""

    parsed, _ = _parse_timestamp_with_precision(value)
    return parsed


def detect_timeframe(frame: pd.DataFrame, start: int = 0, end: int | None = None) -> TimeframeDetection:
    """Infer timeframe from elapsed minutes divided by positive bars-in-trade."""

    if end is None:
        end = len(frame)
    ratios: list[float] = []
    timestamp_precisions: list[str] = []
    for _, row in frame.iloc[start:end].iterrows():
        try:
            exit_timestamp, exit_precision = _parse_timestamp_with_precision(row.iloc[2])
            entry_timestamp, entry_precision = _parse_timestamp_with_precision(row.iloc[3])
            bars = parse_number(row.iloc[-2])
        except DatatypeConversionError:
            continue
        elapsed_minutes = (exit_timestamp - entry_timestamp).total_seconds() / 60.0
        if bars > 0 and elapsed_minutes > 0:
            ratios.append(elapsed_minutes / bars)
            timestamp_precisions.extend((entry_precision, exit_precision))
    if not ratios:
        return TimeframeDetection(None, None, None, 0, 0, 0.0, None)

    rounded = [round(value, 6) for value in ratios]
    modal_value, modal_rows = Counter(rounded).most_common(1)[0]
    nominal_minutes = min(
        TIMEFRAME_LABELS,
        key=lambda candidate: abs(math.log(modal_value / candidate)),
    )
    relative_error = abs(modal_value - nominal_minutes) / nominal_minutes
    date_only_evidence = bool(timestamp_precisions) and set(timestamp_precisions) == {"date"}
    if date_only_evidence and modal_value >= 1_440:
        # Calendar weekends/closures make elapsed calendar days exceed the
        # count of TradingView daily bars. Date-only precision still provides
        # defensible evidence for the only supported daily-or-higher schema.
        nominal_minutes = 1_440
        relative_error = abs(modal_value - nominal_minutes) / nominal_minutes
        label = "1D"
    else:
        label = TIMEFRAME_LABELS[nominal_minutes] if relative_error <= 0.05 else None
    return TimeframeDetection(
        label=label,
        nominal_minutes=nominal_minutes if label is not None else None,
        modal_elapsed_minutes_per_bar=modal_value,
        evidence_rows=len(ratios),
        modal_rows=modal_rows,
        confidence=modal_rows / len(ratios),
        relative_error=relative_error,
    )


def detect_segments(frame: pd.DataFrame) -> list[TradeSegment]:
    """Split cumulative exports at non-decreasing trade-ID resets."""

    ids = pd.to_numeric(frame.iloc[:, 0], errors="raise").astype(int).tolist()
    starts = [0]
    for index in range(1, len(ids)):
        if ids[index] >= ids[index - 1]:
            starts.append(index)
    starts.append(len(ids))
    return [
        TradeSegment(
            start=starts[index],
            end=starts[index + 1],
            first_trade_id=ids[starts[index]],
            last_trade_id=ids[starts[index + 1] - 1],
            detection=detect_timeframe(frame, starts[index], starts[index + 1]),
        )
        for index in range(len(starts) - 1)
    ]


def _select_target_segment(segments: list[TradeSegment], expected: str) -> int:
    matching = [index for index, segment in enumerate(segments) if segment.detection.label == expected]
    if not matching:
        detected = [segment.detection.label for segment in segments]
        raise TimeframeDetectionError(
            f"Segmen timeframe {expected} tidak ditemukan; hasil deteksi: {detected}."
        )
    return matching[-1]


def _normalized_side(left: object, right: object) -> str:
    values = [
        str(value).strip().casefold()
        for value in (left, right)
        if value is not None and not pd.isna(value) and str(value).strip()
    ]
    unique = set(values)
    if len(unique) != 1 or not unique.issubset({"long", "short"}):
        raise DatatypeConversionError(f"marker arah trade ambigu: {values}")
    return values[0]


def _is_exit_marker(value: object) -> bool:
    if value is None or pd.isna(value):
        return False
    normalized = str(value).strip().casefold()
    return normalized == "open" or "exit" in normalized


def _detect_signal_orientation(frame: pd.DataFrame) -> str:
    column_a_score = sum(_is_exit_marker(value) for value in frame.iloc[:, 4])
    column_b_score = sum(_is_exit_marker(value) for value in frame.iloc[:, 5])
    return "column_a_exit" if column_a_score >= column_b_score else "column_b_exit"


def _resolve_signals(value_a: object, value_b: object, orientation: str) -> tuple[str, str]:
    signal_a = "" if value_a is None or pd.isna(value_a) else str(value_a).strip()
    signal_b = "" if value_b is None or pd.isna(value_b) else str(value_b).strip()
    a_is_exit = _is_exit_marker(signal_a)
    b_is_exit = _is_exit_marker(signal_b)
    if a_is_exit and not b_is_exit:
        return signal_b, signal_a
    if b_is_exit and not a_is_exit:
        return signal_a, signal_b
    if orientation == "column_a_exit":
        return signal_b, signal_a
    return signal_a, signal_b


def _raw_row_json(row: pd.Series) -> str:
    payload = {
        str(column): None if pd.isna(value) else str(value)
        for column, value in row.items()
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def convert_datatypes(
    frame: pd.DataFrame,
    schema: RawSchema,
    *,
    source_file: str,
    source_sha256: str,
    source_segment: int,
) -> ConversionResult:
    """Convert a selected positional segment and retain row-level conversion issues."""

    orientation = _detect_signal_orientation(frame)
    records: list[dict[str, Any]] = []
    issues_by_index: dict[int, list[RowIssue]] = {}
    raw_rows: dict[int, str] = {}

    for raw_index, row in frame.iterrows():
        index = int(raw_index)
        issues: list[RowIssue] = []
        raw_rows[index] = _raw_row_json(row)

        def number(position: int, field: str, *, percentage: bool = False) -> float:
            raw_value = row.iloc[position]
            if percentage and (
                raw_value is None
                or pd.isna(raw_value)
                or not str(raw_value).strip().endswith("%")
            ):
                issues.append(
                    RowIssue("INVALID_PERCENT_FORMAT", f"{field} tidak memiliki suffix persen.")
                )
            try:
                return parse_number(raw_value)
            except DatatypeConversionError as exc:
                issues.append(RowIssue("NUMERIC_PARSE_ERROR", f"{field}: {exc}"))
                return math.nan

        try:
            trade_id_float = parse_number(row.iloc[0])
            if trade_id_float <= 0 or trade_id_float % 1:
                raise DatatypeConversionError("trade_id harus integer positif")
            trade_id = int(trade_id_float)
        except DatatypeConversionError as exc:
            issues.append(RowIssue("INVALID_TRADE_ID", str(exc)))
            trade_id = -1

        try:
            side = _normalized_side(row.iloc[1], row.iloc[-1])
        except DatatypeConversionError as exc:
            issues.append(RowIssue("INVALID_SIDE", str(exc)))
            side = ""

        try:
            exit_timestamp, exit_precision = _parse_timestamp_with_precision(row.iloc[2])
        except DatatypeConversionError as exc:
            issues.append(RowIssue("TIMESTAMP_PARSE_ERROR", f"exit_timestamp: {exc}"))
            exit_timestamp, exit_precision = None, "invalid"
        try:
            entry_timestamp, entry_precision = _parse_timestamp_with_precision(row.iloc[3])
        except DatatypeConversionError as exc:
            issues.append(RowIssue("TIMESTAMP_PARSE_ERROR", f"entry_timestamp: {exc}"))
            entry_timestamp, entry_precision = None, "invalid"

        entry_signal, exit_signal = _resolve_signals(row.iloc[4], row.iloc[5], orientation)
        commission = number(12, "commission_usd") if schema.has_commission_column else math.nan

        records.append(
            {
                "trade_id": trade_id,
                "side": side,
                "entry_timestamp": entry_timestamp,
                "exit_timestamp": exit_timestamp,
                "entry_timestamp_precision": entry_precision,
                "exit_timestamp_precision": exit_precision,
                "entry_signal": entry_signal,
                "exit_signal": exit_signal,
                "entry_price": number(7, "entry_price"),
                "exit_price": number(6, "exit_price"),
                "contracts": number(8, "contracts"),
                "position_value_usd": number(9, "position_value_usd"),
                "net_pnl_usd": number(10, "net_pnl_usd"),
                "net_pnl_pct": number(11, "net_pnl_pct", percentage=True),
                "commission_usd": commission,
                "max_runup_usd": number(-8, "max_runup_usd"),
                "max_runup_pct": number(-7, "max_runup_pct", percentage=True),
                "max_drawdown_usd": number(-6, "max_drawdown_usd"),
                "max_drawdown_pct": number(-5, "max_drawdown_pct", percentage=True),
                "raw_cumulative_pnl_usd": number(-4, "raw_cumulative_pnl_usd"),
                "raw_cumulative_pnl_pct": number(
                    -3, "raw_cumulative_pnl_pct", percentage=True
                ),
                "bars_in_trade": number(-2, "bars_in_trade"),
                "source_file": source_file,
                "source_sha256": source_sha256,
                "source_row": index + 2,
                "source_segment": source_segment,
            }
        )
        issues_by_index[index] = issues

    converted = pd.DataFrame(records, index=frame.index)
    return ConversionResult(converted, issues_by_index, raw_rows, orientation)


def _record_id(source_hash: str, timeframe: str, row: pd.Series) -> str:
    components = (
        source_hash,
        timeframe,
        str(int(row["trade_id"])),
        row["entry_timestamp"].isoformat(timespec="minutes"),
        row["exit_timestamp"].isoformat(timespec="minutes"),
        str(row["side"]),
    )
    return sha256("|".join(components).encode("utf-8")).hexdigest()


def normalize_trades(
    conversion: ConversionResult,
    spec: TimeframeSpec,
    *,
    selected_segment_number: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate typed rows, normalize accepted trades, and quarantine invalid rows."""

    accepted_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    detection_label = spec.label

    for raw_index, row in conversion.frame.iterrows():
        index = int(raw_index)
        issues = list(conversion.issues[index])
        required_numeric = (
            "entry_price",
            "exit_price",
            "contracts",
            "position_value_usd",
            "net_pnl_usd",
            "net_pnl_pct",
            "max_runup_usd",
            "max_runup_pct",
            "max_drawdown_usd",
            "max_drawdown_pct",
            "raw_cumulative_pnl_usd",
            "raw_cumulative_pnl_pct",
            "bars_in_trade",
        )
        for field in required_numeric:
            value = row[field]
            if value is None or not np.isfinite(float(value)):
                issues.append(RowIssue("NON_FINITE_VALUE", f"{field} tidak finite."))

        entry_timestamp = row["entry_timestamp"]
        exit_timestamp = row["exit_timestamp"]
        elapsed_minutes = math.nan
        timeframe_consistent = False
        if isinstance(entry_timestamp, datetime) and isinstance(exit_timestamp, datetime):
            elapsed_minutes = (exit_timestamp - entry_timestamp).total_seconds() / 60.0
            if elapsed_minutes < 0:
                issues.append(RowIssue("EXIT_BEFORE_ENTRY", "exit_timestamp mendahului entry_timestamp."))

        for field in ("entry_price", "exit_price", "contracts", "position_value_usd"):
            value = row[field]
            if value is not None and np.isfinite(float(value)) and float(value) <= 0:
                issues.append(RowIssue("NON_POSITIVE_VALUE", f"{field} harus lebih besar dari nol."))
        bars = row["bars_in_trade"]
        if bars is not None and np.isfinite(float(bars)):
            if float(bars) < 0 or float(bars) % 1:
                issues.append(RowIssue("INVALID_BARS", "bars_in_trade harus integer non-negatif."))
            elif np.isfinite(elapsed_minutes):
                timeframe_consistent = (
                    float(bars) == 0
                    or elapsed_minutes + 1e-9 >= float(bars) * spec.minutes
                )
                if not timeframe_consistent:
                    issues.append(
                        RowIssue(
                            "TIMEFRAME_DURATION_MISMATCH",
                            "Durasi lebih pendek daripada bars_in_trade x timeframe nominal.",
                        )
                    )

        if (
            row["side"] in {"long", "short"}
            and all(np.isfinite(float(row[field])) for field in ("entry_price", "exit_price", "contracts", "net_pnl_usd"))
        ):
            price_move = (
                float(row["exit_price"]) - float(row["entry_price"])
                if row["side"] == "long"
                else float(row["entry_price"]) - float(row["exit_price"])
            )
            reconstructed_pnl = price_move * float(row["contracts"])
            tolerance = max(0.0011, abs(float(row["net_pnl_usd"])) * 1e-6)
            if abs(reconstructed_pnl - float(row["net_pnl_usd"])) > tolerance:
                issues.append(
                    RowIssue(
                        "PNL_RECONCILIATION_ERROR",
                        f"PnL tercatat {row['net_pnl_usd']} berbeda dari rekonstruksi {reconstructed_pnl:.6f}.",
                    )
                )

        if not str(row["entry_signal"]).strip() or not str(row["exit_signal"]).strip():
            issues.append(RowIssue("MISSING_SIGNAL", "Label entry/exit signal kosong."))

        if issues:
            rejected_rows.append(
                {
                    "source_file": row["source_file"],
                    "source_sha256": row["source_sha256"],
                    "source_row": int(row["source_row"]),
                    "source_segment": selected_segment_number,
                    "expected_timeframe": spec.label,
                    "detected_timeframe": detection_label,
                    "trade_id": int(row["trade_id"]) if int(row["trade_id"]) > 0 else "",
                    "reason_codes": "|".join(sorted({issue.code for issue in issues})),
                    "reason_details": " | ".join(issue.detail for issue in issues),
                    "raw_row_json": conversion.raw_rows[index],
                }
            )
            continue

        is_closed = str(row["exit_signal"]).strip().casefold() != "open"
        normalized = row.to_dict()
        normalized.update(
            {
                "record_id": _record_id(str(row["source_sha256"]), spec.label, row),
                "timeframe": spec.label,
                "timeframe_minutes": spec.minutes,
                "elapsed_minutes": elapsed_minutes,
                "is_closed": is_closed,
                "trade_status": "closed" if is_closed else "open",
                "timeframe_consistent": timeframe_consistent,
                "detected_timeframe": detection_label,
                "data_scope": "observed_target_segment_only",
            }
        )
        accepted_rows.append(normalized)

    accepted = pd.DataFrame(accepted_rows, columns=NORMALIZED_COLUMNS)
    rejected = pd.DataFrame(rejected_rows, columns=REJECTED_COLUMNS)
    if not accepted.empty:
        duplicate_mask = accepted["record_id"].duplicated(keep=False)
        if duplicate_mask.any():
            duplicate_ids = accepted.loc[duplicate_mask, "record_id"].tolist()
            raise PreprocessingError(f"Record ID hasil normalisasi tidak unik: {duplicate_ids}")
        accepted.sort_values(
            ["entry_timestamp", "exit_timestamp", "trade_id"],
            kind="stable",
            inplace=True,
        )
        accepted.reset_index(drop=True, inplace=True)
    return accepted, rejected


def process_dataset(path: Path, spec: TimeframeSpec) -> DatasetProcessingResult:
    """Run every preprocessing stage for exactly one configured timeframe file."""

    LOGGER.info("Memproses %s sebagai timeframe %s", path.name, spec.label)
    source_hash = hash_file(path)
    source_size = path.stat().st_size
    raw = load_csv(path)
    schema = validate_schema(raw, path.name)
    segments = detect_segments(raw)
    selected_index = _select_target_segment(segments, spec.label)
    selected_segment = segments[selected_index]
    selected = raw.iloc[selected_segment.start : selected_segment.end].copy()
    conversion = convert_datatypes(
        selected,
        schema,
        source_file=path.name,
        source_sha256=source_hash,
        source_segment=selected_index + 1,
    )
    accepted, rejected = normalize_trades(
        conversion,
        spec,
        selected_segment_number=selected_index + 1,
    )
    target_ids = pd.to_numeric(selected.iloc[:, 0], errors="raise").astype(int)
    missing_ids = set(range(int(target_ids.min()), int(target_ids.max()) + 1)) - set(target_ids)
    LOGGER.info(
        "%s: %d target, %d accepted, %d quarantined",
        path.name,
        selected_segment.row_count,
        len(accepted),
        len(rejected),
    )
    return DatasetProcessingResult(
        spec=spec,
        source_path=path,
        source_sha256=source_hash,
        source_size_bytes=source_size,
        raw_row_count=len(raw),
        raw_column_count=raw.shape[1],
        schema=schema,
        segments=segments,
        selected_segment_number=selected_index + 1,
        signal_orientation=conversion.signal_orientation,
        accepted=accepted,
        rejected=rejected,
        missing_trade_ids_within_target_range=len(missing_ids),
    )


def _json_default(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()  # type: ignore[return-value]
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    except OSError as exc:
        raise PreprocessingError(f"Gagal menulis output {path}: {exc}") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _csv_text(frame: pd.DataFrame) -> str:
    serializable = frame.copy()
    for field in ("entry_timestamp", "exit_timestamp"):
        if field in serializable.columns:
            serializable[field] = serializable[field].map(
                lambda value: value.isoformat(timespec="minutes")
                if isinstance(value, datetime)
                else value
            )
    buffer = StringIO()
    serializable.to_csv(
        buffer,
        index=False,
        encoding="utf-8",
        lineterminator="\n",
        na_rep="",
        float_format="%.10g",
    )
    return buffer.getvalue()


def _relative_or_name(path: Path) -> str:
    resolved = path.resolve()
    for index, part in enumerate(resolved.parts):
        if part.startswith(".") and part.endswith(".staging"):
            remainder = resolved.parts[index + 1 :]
            return Path(*remainder).as_posix() if remainder else path.name
    try:
        relative = resolved.relative_to(ROOT.resolve())
    except ValueError:
        return path.name
    return relative.as_posix()


def _segment_payload(segment: TradeSegment, number: int, selected: int) -> dict[str, Any]:
    return {
        "segment_number": number,
        "csv_row_start": segment.start + 2,
        "csv_row_end": segment.end + 1,
        "row_count": segment.row_count,
        "trade_id_first": segment.first_trade_id,
        "trade_id_last": segment.last_trade_id,
        "detected_timeframe": segment.detection.label,
        "modal_elapsed_minutes_per_bar": segment.detection.modal_elapsed_minutes_per_bar,
        "evidence_rows": segment.detection.evidence_rows,
        "modal_rows": segment.detection.modal_rows,
        "confidence": round(segment.detection.confidence, 10),
        "relative_error": segment.detection.relative_error,
        "selected_as_target": number == selected,
    }


def _summary_frame(
    results: Iterable[DatasetProcessingResult],
    processed_paths: Mapping[str, Path],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for result in results:
        accepted = result.accepted
        target = result.segments[result.selected_segment_number - 1]
        rows.append(
            {
                "source_file": result.source_path.name,
                "expected_timeframe": result.spec.label,
                "detected_timeframe": target.detection.label,
                "timeframe_minutes": result.spec.minutes,
                "raw_rows": result.raw_row_count,
                "raw_columns": result.raw_column_count,
                "detected_segments": len(result.segments),
                "selected_segment": result.selected_segment_number,
                "target_candidate_rows": target.row_count,
                "accepted_rows": len(accepted),
                "rejected_rows": len(result.rejected),
                "closed_rows": int(accepted["is_closed"].sum()) if not accepted.empty else 0,
                "open_rows": int((~accepted["is_closed"]).sum()) if not accepted.empty else 0,
                "missing_trade_ids_within_target_range": result.missing_trade_ids_within_target_range,
                "entry_timestamp_min": accepted["entry_timestamp"].min().isoformat(timespec="minutes")
                if not accepted.empty
                else "",
                "entry_timestamp_max": accepted["entry_timestamp"].max().isoformat(timespec="minutes")
                if not accepted.empty
                else "",
                "signal_orientation": result.signal_orientation,
                "schema_variant": f"positional_{result.raw_column_count}_columns",
                "source_sha256": result.source_sha256,
                "processed_file": _relative_or_name(processed_paths[result.spec.label]),
                "status": "processed_with_quarantine"
                if not result.rejected.empty
                else "processed_with_source_limitations",
            }
        )
    return pd.DataFrame(rows)


def _validate_input_inventory(raw_dir: Path) -> dict[str, Path]:
    expected = {spec.filename for spec in TIMEFRAME_SPECS}
    discovered = {path.name for path in raw_dir.glob("*.csv") if path.is_file()}
    missing = sorted(expected - discovered)
    unexpected = sorted(discovered - expected)
    if missing or unexpected:
        raise CsvLoadError(
            f"Inventori CSV tidak sesuai konfigurasi; missing={missing}, unexpected={unexpected}."
        )
    return {name: raw_dir / name for name in sorted(discovered)}


def run_preprocessing(
    *,
    raw_dir: Path = RAW_TRADES_DIR,
    processed_dir: Path = PROCESSED_TRADES_DIR,
    report_path: Path = REPORTS_DIR / "preprocessing_report.json",
    summary_path: Path = TABLES_DIR / "processed_dataset_summary.csv",
) -> dict[str, Any]:
    """Run PHASE 2 preprocessing only and return a deterministic summary."""

    inventory = _validate_input_inventory(raw_dir)
    raw_hashes_before = {name: hash_file(path) for name, path in inventory.items()}
    results = [process_dataset(inventory[spec.filename], spec) for spec in TIMEFRAME_SPECS]

    processed_paths = {
        result.spec.label: processed_dir / f"trades_{result.spec.label.casefold()}.csv"
        for result in results
    }
    for result in results:
        validate_dataframe_schema(result.accepted, DATASET_SCHEMA)
        _atomic_write_text(processed_paths[result.spec.label], _csv_text(result.accepted))

    combined = pd.concat([result.accepted for result in results], ignore_index=True)
    rejected = pd.concat([result.rejected for result in results], ignore_index=True)
    validate_dataframe_schema(combined, DATASET_SCHEMA)
    combined_path = processed_dir / "normalized_trades.csv"
    rejected_path = processed_dir / "rejected_trades.csv"
    _atomic_write_text(combined_path, _csv_text(combined))
    _atomic_write_text(rejected_path, _csv_text(rejected))

    summary = _summary_frame(results, processed_paths)
    _atomic_write_text(summary_path, _csv_text(summary))

    raw_hashes_after = {name: hash_file(path) for name, path in inventory.items()}
    if raw_hashes_before != raw_hashes_after:
        raise PreprocessingError("Hash raw data berubah selama preprocessing; hasil tidak dapat dipercaya.")

    output_paths = [*processed_paths.values(), combined_path, rejected_path, summary_path]
    outputs = [
        {
            "path": _relative_or_name(path),
            "rows": int(
                len(combined)
                if path == combined_path
                else len(rejected)
                if path == rejected_path
                else len(summary)
                if path == summary_path
                else len(next(result.accepted for result in results if processed_paths[result.spec.label] == path))
            ),
            "size_bytes": path.stat().st_size,
            "sha256": hash_file(path),
        }
        for path in output_paths
    ]

    reason_counts = Counter(
        code
        for value in rejected.get("reason_codes", pd.Series(dtype="string")).tolist()
        for code in str(value).split("|")
        if code
    )
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "pipeline_version": PIPELINE_VERSION,
        "phase": "PHASE_2_PREPROCESSING_ONLY",
        "status": "completed_with_quarantined_rows"
        if len(rejected)
        else "completed_with_source_limitations",
        "analysis_executed": False,
        "raw_data_modified": False,
        "analysis_readiness": "blocked_pending_complete_isolated_exports_and_provenance_reconciliation",
        "libraries": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "logging": "python_standard_library",
        },
        "pipeline_stages": [
            "csv_loader",
            "schema_validator",
            "trade_id_segmentation",
            "timeframe_detector",
            "datatype_converter",
            "timestamp_parser",
            "trade_normalization",
            "semantic_validation",
            "quarantine",
            "atomic_output_write",
            "source_hash_verification",
        ],
        "input": {
            "directory": _relative_or_name(raw_dir),
            "csv_files": len(inventory),
            "sha256_before": raw_hashes_before,
            "sha256_after": raw_hashes_after,
            "all_hashes_unchanged": raw_hashes_before == raw_hashes_after,
        },
        "aggregate": {
            "raw_rows": sum(result.raw_row_count for result in results),
            "target_candidate_rows": sum(
                result.segments[result.selected_segment_number - 1].row_count
                for result in results
            ),
            "accepted_rows": len(combined),
            "rejected_rows": len(rejected),
            "closed_rows": int(combined["is_closed"].sum()),
            "open_rows": int((~combined["is_closed"]).sum()),
            "record_ids_unique": not combined["record_id"].duplicated().any(),
            "timeframe_consistent_rows": int(combined["timeframe_consistent"].sum()),
        },
        "canonical_schema": {
            "columns": list(NORMALIZED_COLUMNS),
            "timestamp_representation": "ISO-8601 minute precision; timezone intentionally unspecified",
            "numeric_representation": "base numeric units; percentages stored as percentage points",
            "commission_policy": "NaN when absent from 20-column exports; parsed from positional column 12 for 21-column exports",
            "row_order": "chronological within timeframe; configured timeframe order in combined output",
            "record_id": "SHA-256(source_sha256|timeframe|trade_id|entry|exit|side)",
        },
        "datasets": [
            {
                "source_file": result.source_path.name,
                "source_sha256": result.source_sha256,
                "source_size_bytes": result.source_size_bytes,
                "expected_timeframe": result.spec.label,
                "raw_shape": {
                    "rows": result.raw_row_count,
                    "columns": result.raw_column_count,
                },
                "schema_variant": f"positional_{result.raw_column_count}_columns",
                "commission_column_present": result.schema.has_commission_column,
                "segments": [
                    _segment_payload(segment, number, result.selected_segment_number)
                    for number, segment in enumerate(result.segments, start=1)
                ],
                "selected_segment_number": result.selected_segment_number,
                "signal_orientation": result.signal_orientation,
                "target_candidate_rows": result.segments[
                    result.selected_segment_number - 1
                ].row_count,
                "accepted_rows": len(result.accepted),
                "rejected_rows": len(result.rejected),
                "closed_rows": int(result.accepted["is_closed"].sum()),
                "open_rows": int((~result.accepted["is_closed"]).sum()),
                "missing_trade_ids_within_target_range": result.missing_trade_ids_within_target_range,
                "processed_file": _relative_or_name(processed_paths[result.spec.label]),
            }
            for result in results
        ],
        "quarantine": {
            "path": _relative_or_name(rejected_path),
            "rows": len(rejected),
            "reason_counts": dict(sorted(reason_counts.items())),
            "policy": "No imputation or silent repair; preserve source row and raw row JSON.",
        },
        "assumptions": [
            "Trade-ID resets delimit cumulative append segments.",
            "The last segment whose duration-per-bar evidence matches the configured filename timeframe is the provisional target segment.",
            "Date-only segments with calendar-time-per-bar of at least 1440 minutes are classified as 1D; weekend and market-closure gaps can raise the modal calendar ratio above 1440.",
            "Timestamps are timezone-naive because the exports provide no timezone.",
            "A 21st positional field is treated as commission; 20-column exports retain commission as unknown rather than forcing zero.",
            "Signal orientation is inferred from Exit/Open markers, with row-level override when one signal is explicitly an exit marker.",
        ],
        "limitations": [
            "Trade-ID ranges remain incomplete and five raw files contain cumulative append blocks.",
            "The 40 USD metadata versus Pine initial_capital=10000 provenance conflict is not resolved by preprocessing.",
            "No exchange/feed, chart timezone, exact date range, or complete Strategy Properties are available.",
            "Processed rows represent observed target segments only and must not be presented as complete TradingView backtests.",
            "No performance, robustness, sensitivity, or statistical analysis was executed.",
        ],
        "outputs": outputs,
        "report_path": _relative_or_name(report_path),
        "summary_path": _relative_or_name(summary_path),
        "reproducibility": {
            "deterministic_content": True,
            "wall_clock_timestamp_embedded": False,
            "atomic_writes": True,
        },
    }
    report_text = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
        default=_json_default,
    ) + "\n"
    _atomic_write_text(report_path, report_text)

    return {
        "status": report["status"],
        "raw_rows": report["aggregate"]["raw_rows"],
        "target_candidate_rows": report["aggregate"]["target_candidate_rows"],
        "accepted_rows": report["aggregate"]["accepted_rows"],
        "rejected_rows": report["aggregate"]["rejected_rows"],
        "closed_rows": report["aggregate"]["closed_rows"],
        "open_rows": report["aggregate"]["open_rows"],
        "report": _relative_or_name(report_path),
        "summary": _relative_or_name(summary_path),
    }
