"""Loader dan normalizer trade log hasil ekspor TradingView."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import math
import re

import numpy as np
import pandas as pd

from .config import RAW_TRADES_DIR, TIMEFRAME_SPECS, TimeframeSpec


TIMESTAMP_FORMATS = ("%b %d, %Y, %H:%M", "%b %d, %Y")
NUMERIC_MISSING = {"", "-", "--", "n/a", "na", "nan", "none"}


class TradeDataError(ValueError):
    """Kesalahan terstruktur pada schema atau nilai trade log."""


@dataclass(frozen=True)
class TradeSegment:
    """Rentang baris dengan nomor trade menurun ketat."""

    start: int
    end: int
    first_trade_id: int
    last_trade_id: int

    @property
    def row_count(self) -> int:
        return self.end - self.start


@dataclass
class NormalizedDataset:
    """Hasil pembacaan satu file beserta provenance segmentasinya."""

    spec: TimeframeSpec
    source_path: Path
    source_sha256: str
    source_size_bytes: int
    raw: pd.DataFrame
    segments: list[TradeSegment]
    selected_segment_index: int
    trades: pd.DataFrame


def parse_tradingview_number(value: object) -> float:
    """Parse angka UI TradingView, termasuk Unicode minus dan suffix K/M/B."""

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return math.nan
    text = str(value).strip()
    if text.lower() in NUMERIC_MISSING:
        return math.nan

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
        raise TradeDataError(f"Nilai numerik TradingView tidak dikenali: {text!r}")
    multiplier = {"": 1.0, "k": 1e3, "m": 1e6, "b": 1e9}[match.group(2).lower()]
    return float(match.group(1)) * multiplier


def parse_tradingview_timestamp(value: object) -> datetime | None:
    """Parse timestamp UI TradingView sebagai datetime naive; timezone tidak tersedia."""

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    for timestamp_format in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, timestamp_format)
        except ValueError:
            continue
    return None


def read_trade_csv(path: Path) -> pd.DataFrame:
    """Membaca satu CSV secara read-only dan memvalidasi bentuk schema UI."""

    try:
        frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise TradeDataError(f"Gagal membaca {path}: {exc}") from exc
    if frame.empty:
        raise TradeDataError(f"Dataset kosong: {path}")
    if frame.shape[1] not in (20, 21):
        raise TradeDataError(
            f"Schema {path.name} memiliki {frame.shape[1]} kolom; diharapkan 20 atau 21."
        )
    return frame


def detect_trade_segments(frame: pd.DataFrame) -> list[TradeSegment]:
    """Pisahkan blok append berdasarkan reset nomor trade.

    File yang tersedia berisi satu atau beberapa blok dengan trade ID menurun.
    Reset ke ID yang lebih besar/sama menandai awal blok berikutnya.
    """

    ids = pd.to_numeric(frame.iloc[:, 0], errors="coerce")
    if ids.isna().any() or (ids <= 0).any():
        invalid_rows = (ids.isna() | (ids <= 0)).to_numpy().nonzero()[0] + 2
        raise TradeDataError(f"Trade ID invalid pada baris CSV {invalid_rows.tolist()}.")
    trade_ids = ids.astype(int).tolist()
    starts = [0]
    for index in range(1, len(trade_ids)):
        if trade_ids[index] >= trade_ids[index - 1]:
            starts.append(index)
    starts.append(len(trade_ids))
    return [
        TradeSegment(starts[i], starts[i + 1], trade_ids[starts[i]], trade_ids[starts[i + 1] - 1])
        for i in range(len(starts) - 1)
    ]


def _normalized_side(left_value: object, right_value: object) -> str | None:
    values = [str(value).strip().lower() for value in (left_value, right_value) if pd.notna(value)]
    values = [value for value in values if value]
    unique = set(values)
    if len(unique) != 1 or not unique.issubset({"long", "short"}):
        return None
    return values[0]


def _parse_numeric_series(series: pd.Series, field: str) -> pd.Series:
    values: list[float] = []
    errors: list[int] = []
    for row_number, value in enumerate(series.tolist(), start=2):
        try:
            values.append(parse_tradingview_number(value))
        except TradeDataError:
            values.append(math.nan)
            errors.append(row_number)
    if errors:
        raise TradeDataError(f"Kolom {field} memiliki angka invalid pada baris {errors}.")
    return pd.Series(values, index=series.index, dtype=float)


def normalize_selected_segment(
    frame: pd.DataFrame,
    spec: TimeframeSpec,
    source_path: Path,
    segments: list[TradeSegment],
) -> tuple[int, pd.DataFrame]:
    """Normalisasi blok terakhir yang diasumsikan sesuai label file.

    Asumsi ini eksplisit karena file multi-blok dibentuk melalui append kumulatif.
    Semua baris non-target tetap dipertahankan di file mentah dan dilaporkan oleh audit.
    """

    selected_index = len(segments) - 1
    segment = segments[selected_index]
    selected = frame.iloc[segment.start : segment.end].copy()
    column_count = selected.shape[1]
    extra_commission_column = column_count == 21

    timestamp_entry = selected.iloc[:, 3].map(parse_tradingview_timestamp)
    timestamp_exit = selected.iloc[:, 2].map(parse_tradingview_timestamp)
    sides = [
        _normalized_side(left, right)
        for left, right in zip(selected.iloc[:, 1], selected.iloc[:, -1], strict=True)
    ]

    normalized = pd.DataFrame(index=selected.index)
    normalized["timeframe"] = spec.label
    normalized["timeframe_minutes"] = spec.minutes
    normalized["trade_id"] = pd.to_numeric(selected.iloc[:, 0], errors="raise").astype(int)
    normalized["side"] = sides
    normalized["entry_timestamp"] = timestamp_entry
    normalized["exit_timestamp"] = timestamp_exit
    normalized["entry_signal"] = selected.iloc[:, 5].str.strip()
    normalized["exit_signal"] = selected.iloc[:, 4].str.strip()
    normalized["entry_price"] = _parse_numeric_series(selected.iloc[:, 7], "entry_price")
    normalized["exit_price"] = _parse_numeric_series(selected.iloc[:, 6], "exit_price")
    normalized["contracts"] = _parse_numeric_series(selected.iloc[:, 8], "contracts")
    normalized["position_value_usd"] = _parse_numeric_series(selected.iloc[:, 9], "position_value")
    normalized["net_pnl_usd"] = _parse_numeric_series(selected.iloc[:, 10], "net_pnl")
    normalized["net_pnl_pct"] = _parse_numeric_series(selected.iloc[:, 11], "net_pnl_pct")
    normalized["commission_usd"] = (
        _parse_numeric_series(selected.iloc[:, 12], "commission")
        if extra_commission_column
        else np.nan
    )
    normalized["max_runup_usd"] = _parse_numeric_series(selected.iloc[:, -8], "max_runup")
    normalized["max_runup_pct"] = _parse_numeric_series(selected.iloc[:, -7], "max_runup_pct")
    normalized["max_drawdown_usd"] = _parse_numeric_series(selected.iloc[:, -6], "max_drawdown")
    normalized["max_drawdown_pct"] = _parse_numeric_series(selected.iloc[:, -5], "max_drawdown_pct")
    normalized["raw_cumulative_pnl_usd"] = _parse_numeric_series(
        selected.iloc[:, -4], "cumulative_pnl"
    )
    normalized["raw_cumulative_pnl_pct"] = _parse_numeric_series(
        selected.iloc[:, -3], "cumulative_pnl_pct"
    )
    normalized["bars_in_trade"] = _parse_numeric_series(selected.iloc[:, -2], "bars_in_trade")
    normalized["is_closed"] = normalized["exit_signal"].str.casefold().ne("open")
    normalized["source_file"] = source_path.name
    normalized["source_row"] = selected.index.to_numpy() + 2
    normalized["source_segment"] = selected_index + 1
    normalized["data_scope"] = "observed_rows_only"

    elapsed_minutes = [
        (exit_time - entry_time).total_seconds() / 60
        if entry_time is not None and exit_time is not None
        else math.nan
        for entry_time, exit_time in zip(timestamp_entry, timestamp_exit, strict=True)
    ]
    normalized["elapsed_minutes"] = elapsed_minutes
    positive_bars = normalized["bars_in_trade"] > 0
    normalized["timeframe_consistent"] = True
    normalized.loc[positive_bars, "timeframe_consistent"] = (
        normalized.loc[positive_bars, "elapsed_minutes"] + 1e-9
        >= normalized.loc[positive_bars, "bars_in_trade"] * spec.minutes
    )
    normalized.reset_index(drop=True, inplace=True)
    return selected_index, normalized


def load_trade_dataset(spec: TimeframeSpec) -> NormalizedDataset:
    """Baca dan normalisasi satu dataset timeframe."""

    path = RAW_TRADES_DIR / spec.filename
    if not path.is_file():
        raise TradeDataError(f"File dataset tidak ditemukan: {path}")
    raw_bytes = path.read_bytes()
    frame = read_trade_csv(path)
    segments = detect_trade_segments(frame)
    selected_index, trades = normalize_selected_segment(frame, spec, path, segments)
    return NormalizedDataset(
        spec=spec,
        source_path=path,
        source_sha256=sha256(raw_bytes).hexdigest(),
        source_size_bytes=len(raw_bytes),
        raw=frame,
        segments=segments,
        selected_segment_index=selected_index,
        trades=trades,
    )


def load_all_trade_datasets() -> dict[str, NormalizedDataset]:
    """Baca seluruh timeframe dalam urutan konfigurasi."""

    return {spec.label: load_trade_dataset(spec) for spec in TIMEFRAME_SPECS}
