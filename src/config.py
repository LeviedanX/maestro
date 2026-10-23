"""Konfigurasi terpusat untuk pipeline riset MAESTRO-X."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
METADATA = ROOT / "data" / "metadata" / "dataset_info.yaml"
RAW_TRADES_DIR = ROOT / "data" / "raw" / "trades"
PROCESSED_TRADES_DIR = ROOT / "data" / "processed"
RESULTS_DIR = ROOT / "results"
FIGURES_DIR = RESULTS_DIR / "figures"
TABLES_DIR = RESULTS_DIR / "tables"
REPORTS_DIR = RESULTS_DIR / "reports"
RUNS_DIR = RESULTS_DIR / "runs"

MONTE_CARLO_SIMULATIONS = 10_000
RANDOM_SEED = 20_260_822
RUIN_EQUITY_FRACTION = 0.50


@dataclass(frozen=True)
class TimeframeSpec:
    """Metadata timeframe yang tidak mengubah perilaku strategi."""

    label: str
    filename: str
    minutes: int
    order: int


TIMEFRAME_SPECS: tuple[TimeframeSpec, ...] = (
    TimeframeSpec("1m", "tf1menit.csv", 1, 0),
    TimeframeSpec("5m", "tf5menit.csv", 5, 1),
    TimeframeSpec("15m", "tf15menit.csv", 15, 2),
    TimeframeSpec("30m", "tf30menit.csv", 30, 3),
    TimeframeSpec("1h", "tf1jam.csv", 60, 4),
    TimeframeSpec("2h", "tf2jam.csv", 120, 5),
    TimeframeSpec("4h", "tf4jam.csv", 240, 6),
    TimeframeSpec("1D", "tf1hari.csv", 1_440, 7),
)


def load_metadata() -> dict[str, Any]:
    """Membaca metadata proyek dan memastikan root dokumen berupa mapping."""

    try:
        with METADATA.open("r", encoding="utf-8") as handle:
            metadata = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Gagal membaca metadata {METADATA}: {exc}") from exc

    if not isinstance(metadata, dict):
        raise ValueError(f"Metadata {METADATA} harus berupa mapping YAML.")
    return metadata


def get_initial_capital(metadata: dict[str, Any]) -> float:
    """Mengambil initial capital positif dari metadata."""

    try:
        raw_value = metadata["backtest"]["initial_capital"]
        if isinstance(raw_value, str):
            match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(?:USD)?\s*", raw_value, re.I)
            if match is None:
                raise ValueError(raw_value)
            value = float(match.group(1))
        else:
            value = float(raw_value)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("backtest.initial_capital tidak valid di metadata.") from exc
    if value <= 0:
        raise ValueError("backtest.initial_capital harus lebih besar dari nol.")
    return value


def ensure_output_directories() -> None:
    """Membuat direktori output tanpa menyentuh data mentah."""

    for directory in (FIGURES_DIR, TABLES_DIR, REPORTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
