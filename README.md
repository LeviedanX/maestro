# MAESTRO-X Research Framework

Offline quantitative validation of TradingView trade-log exports for the
MAESTRO-X v1.0 XAUUSD strategy. The framework validates and normalizes trades,
calculates realized performance, and produces Monte Carlo and descriptive
sensitivity reports while preserving the source strategy and raw inputs.

> This repository is the trade-log research framework. The separate
> [LLM guardrails reproduction archive](https://github.com/LeviedanX/xauusd-llm-guardrails-reproduction)
> contains the 40-case, 160-attempt fixed-artifact admission study.
> These datasets and experiments must not be combined.

## Quick start

Use Python 3.13 and an isolated environment. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pip check
python -m pytest validation/
```

To generate a new **offline analysis run** from the supplied exports:

```powershell
python -m src.run_research_pipeline
```

The canonical pipeline stages output in a new run directory and publishes it
only after validation. Running it creates derived output; it is not required
to browse the stored results. The older `src.run_pipeline` entry point is a
compatibility shim. See [usage and methodology](docs/USAGE.md) for details.

## Repository guide

| Path | Purpose |
| --- | --- |
| `strategy/` | Source Pine strategy; preserve its research identity |
| `data/raw/trades/` | Original TradingView trade-log exports |
| `data/metadata/` | Recorded provenance and dataset assumptions |
| `src/` | Validation, metrics, resampling, sensitivity and reporting code |
| `validation/` | Unit and integration tests |
| `results/runs/` | Isolated run outputs and their manifests |
| `results/reports/`, `results/tables/` | Retained reports and earlier derived outputs |
| [docs/](docs/README.md) | Scope and detailed operating documentation |
| [SECURITY.md](SECURITY.md) | Confidential reporting and public-data policy |

## Interpretation and limitations

Monte Carlo uses 10,000 sequence permutations and 10,000 IID bootstrap samples
per timeframe with the recorded base seed `20260822`. Sensitivity results are
descriptive; they do not optimize the strategy or estimate missing controlled
parameter experiments.

The retained evidence has incomplete provenance, cumulative export segments,
non-comparable observation windows and an initial-capital conflict. Costs and
fill provenance are incomplete. Closed-trade drawdown omits intratrade equity
paths, and IID bootstrap does not establish independence or model regime change.
These limitations prevent claims of future performance, profit guarantees,
optimal parameters, publication readiness or suitability for live allocation.

The source strategy, raw data, recorded manifests and numeric results are
preserved. Private academic drafts and local workflow instructions are excluded
from the public repository. No new license or author-approval claim is added.
