"""Audit schema, provenance, kelengkapan, dan konsistensi trade log."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any
import math
import platform

import numpy as np
import pandas as pd

from .data_loader import NormalizedDataset, TradeSegment, parse_tradingview_number, parse_tradingview_timestamp


CANONICAL_TIMEFRAMES = {
    1: "1m",
    5: "5m",
    15: "15m",
    30: "30m",
    60: "1h",
    120: "2h",
    240: "4h",
    1_440: "1D",
}
REQUIRED_NORMALIZED_FIELDS = (
    "trade_id",
    "side",
    "entry_timestamp",
    "exit_timestamp",
    "entry_price",
    "exit_price",
    "contracts",
    "net_pnl_usd",
    "bars_in_trade",
)


def _iso_or_none(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    return None


def _contiguous_ranges(values: list[int]) -> list[str]:
    if not values:
        return []
    unique = sorted(set(values), reverse=True)
    ranges: list[str] = []
    start = previous = unique[0]
    for current in unique[1:]:
        if current == previous - 1:
            previous = current
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = current
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ranges


def _segment_report(dataset: NormalizedDataset, segment: TradeSegment, index: int) -> dict[str, Any]:
    raw = dataset.raw.iloc[segment.start : segment.end]
    ratios: list[float] = []
    parsed_timestamps = 0
    for _, row in raw.iterrows():
        exit_time = parse_tradingview_timestamp(row.iloc[2])
        entry_time = parse_tradingview_timestamp(row.iloc[3])
        try:
            bars = parse_tradingview_number(row.iloc[-2])
        except ValueError:
            bars = math.nan
        if exit_time is not None and entry_time is not None:
            parsed_timestamps += 1
            if math.isfinite(bars) and bars > 0:
                ratios.append((exit_time - entry_time).total_seconds() / 60 / bars)

    rounded_ratios = [round(value, 6) for value in ratios if value > 0]
    modal_ratio = Counter(rounded_ratios).most_common(1)[0][0] if rounded_ratios else None
    inferred = None
    if modal_ratio is not None:
        nearest = min(CANONICAL_TIMEFRAMES, key=lambda value: abs(math.log(modal_ratio / value)))
        inferred = CANONICAL_TIMEFRAMES[nearest]
    return {
        "segment_number": index + 1,
        "csv_row_start": segment.start + 2,
        "csv_row_end": segment.end + 1,
        "row_count": segment.row_count,
        "trade_id_first": segment.first_trade_id,
        "trade_id_last": segment.last_trade_id,
        "entry_timestamp_first": _iso_or_none(parse_tradingview_timestamp(raw.iloc[0, 3])),
        "entry_timestamp_last": _iso_or_none(parse_tradingview_timestamp(raw.iloc[-1, 3])),
        "timestamp_rows_parsed": parsed_timestamps,
        "modal_elapsed_minutes_per_bar": modal_ratio,
        "inferred_timeframe": inferred,
        "selected_as_target": index == dataset.selected_segment_index,
    }


def _dataset_report(dataset: NormalizedDataset) -> dict[str, Any]:
    trades = dataset.trades
    trade_ids = trades["trade_id"].astype(int).tolist()
    id_min = min(trade_ids)
    id_max = max(trade_ids)
    missing_ids = sorted(set(range(id_min, id_max + 1)) - set(trade_ids))
    required_missing = {
        field: int(trades[field].isna().sum())
        for field in REQUIRED_NORMALIZED_FIELDS
        if int(trades[field].isna().sum()) > 0
    }
    duplicate_ids = int(trades["trade_id"].duplicated().sum())
    duplicate_rows = int(trades.duplicated(subset=["trade_id", "entry_timestamp", "exit_timestamp"]).sum())
    timestamp_invalid = int(trades["entry_timestamp"].isna().sum() + trades["exit_timestamp"].isna().sum())
    timeframe_mismatches = int((~trades["timeframe_consistent"]).sum())
    open_positions = int((~trades["is_closed"]).sum())
    observed_closed = int(trades["is_closed"].sum())
    has_gaps = bool(missing_ids)
    appended = len(dataset.segments) > 1

    warnings: list[str] = []
    if appended:
        warnings.append(
            "File memuat beberapa blok append; hanya blok terakhir diasumsikan sebagai timeframe target."
        )
    if has_gaps:
        warnings.append("Trade ID tidak kontigu; dataset target adalah cuplikan, bukan log lengkap.")
    if open_positions:
        warnings.append("Posisi berstatus Open dikeluarkan dari metrik realized dan Monte Carlo.")
    if timestamp_invalid:
        warnings.append("Terdapat timestamp yang tidak dapat diparse.")
    if timeframe_mismatches:
        warnings.append("Durasi trade lebih pendek daripada bars_in_trade x timeframe nominal.")
    if required_missing:
        warnings.append("Terdapat nilai wajib yang hilang setelah normalisasi.")

    eligible = (
        observed_closed >= 2
        and not required_missing
        and duplicate_rows == 0
        and timestamp_invalid == 0
        and timeframe_mismatches == 0
    )
    completeness = "partial" if has_gaps or appended else "not_proven_complete"
    return {
        "timeframe": dataset.spec.label,
        "source_file": dataset.source_path.name,
        "sha256": dataset.source_sha256,
        "size_bytes": dataset.source_size_bytes,
        "raw_shape": {"rows": int(dataset.raw.shape[0]), "columns": int(dataset.raw.shape[1])},
        "raw_columns": dataset.raw.columns.tolist(),
        "detected_segments": [
            _segment_report(dataset, segment, index)
            for index, segment in enumerate(dataset.segments)
        ],
        "selected_segment_number": dataset.selected_segment_index + 1,
        "selected_rows": int(len(trades)),
        "observed_closed_trades": observed_closed,
        "open_positions": open_positions,
        "trade_id_min": id_min,
        "trade_id_max": id_max,
        "trade_id_ranges": _contiguous_ranges(trade_ids),
        "missing_trade_id_count_within_range": len(missing_ids),
        "missing_trade_id_preview": missing_ids[:25],
        "duplicate_trade_ids": duplicate_ids,
        "duplicate_normalized_rows": duplicate_rows,
        "invalid_timestamp_values": timestamp_invalid,
        "timeframe_mismatch_rows": timeframe_mismatches,
        "missing_required_values": required_missing,
        "data_completeness": completeness,
        "analysis_eligible_observed_rows": eligible,
        "warnings": warnings,
    }


def build_data_validation_report(
    datasets: dict[str, NormalizedDataset],
    initial_capital: float,
) -> dict[str, Any]:
    """Bangun laporan audit deterministik tanpa mengubah file sumber."""

    per_dataset = [_dataset_report(dataset) for dataset in datasets.values()]
    eligible = all(item["analysis_eligible_observed_rows"] for item in per_dataset)
    partial = any(item["data_completeness"] == "partial" for item in per_dataset)
    source_hashes_unique = len({item["sha256"] for item in per_dataset}) == len(per_dataset)

    return {
        "schema_version": "1.0",
        "overall_status": "usable_with_material_limitations" if eligible else "invalid_for_analysis",
        "analysis_scope": "observed_normalized_rows_only",
        "publication_claim_gate": "blocked" if partial or not eligible else "requires_external_verification",
        "raw_data_modified": False,
        "source_hashes_unique": source_hashes_unique,
        "initial_capital_usd": initial_capital,
        "execution_environment": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
        "assumptions": [
            "Nama file digunakan sebagai label timeframe target.",
            "Pada file append kumulatif, blok terakhir diasumsikan sebagai blok target.",
            "Timestamp bersifat naive karena timezone chart tidak tersedia.",
            "Net PnL memakai nilai realized yang tercantum pada baris ekspor; biaya yang tidak tercatat tidak diimputasi.",
            "Metrik tidak diekstrapolasi untuk trade ID yang tidak hadir.",
        ],
        "limitations": [
            "Sebagian trade log hanya memuat rentang ID terpisah atau blok target terbatas.",
            "Exchange feed, chart timezone, rentang chart, dan Strategy Properties tidak tersimpan dalam CSV.",
            "Data tidak memuat HTF bias timeframe atau minimum entry score per eksperimen.",
            "Hasil tidak membuktikan performa masa depan atau signifikansi statistik.",
        ],
        "datasets": per_dataset,
    }
