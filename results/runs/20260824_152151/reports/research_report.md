# MAESTRO-X Canonical Research Run

Run ID: `20260824_152151`  
Pipeline: `python -m src.run_research_pipeline`  
Scope: validation of observed processed trades; no strategy optimization.

## Execution summary

- Raw rows inspected: 918
- Accepted normalized rows: 323
- Quarantined rows: 2
- Closed trades analyzed: 322
- Monte Carlo paths: 160000
- Monte Carlo seed base: 20260822
- Optimization performed: `false`

## Observed performance

| Timeframe | Closed trades | Net return (%) | Profit factor | Max drawdown (%) |
|---|---:|---:|---:|---:|
| 1m | 61 | 15.578 | 1.335 | 15.307 |
| 5m | 61 | 31.440 | 1.776 | 8.157 |
| 15m | 30 | 12.655 | 1.838 | 3.276 |
| 30m | 28 | -1.205 | 0.932 | 10.226 |
| 1h | 60 | 28.773 | 1.698 | 5.815 |
| 2h | 28 | 7.067 | 1.448 | 5.115 |
| 4h | 27 | 21.172 | 2.946 | 2.985 |
| 1D | 27 | 8.675 | 1.439 | 3.938 |

Negative observed timeframe subsets: 30m.
These are retained without filtering or reinterpretation.

## Monte Carlo robustness

Each timeframe uses 10,000 IID bootstrap paths in addition to sequence permutations. Bootstrap probability of negative terminal return ranges from 0.35% to 56.06% across observed subsets. These conditional distributions are not forecasts and do not establish future profitability.

## Sensitivity evidence

Blocked parameter dimensions: htf_bias_timeframe, minimum_entry_score.
Declared robustness-region result(s): not_identifiable, not_identifiable_noncontrolled_samples.
Timeframe comparisons remain descriptive because samples differ in period and completeness.

## Validation interpretation

The run is internally reproducible and artifact-isolated. It does not resolve the empirical limitations of incomplete TradingView exports, the initial-capital provenance conflict, absent cost/fill provenance, non-comparable timeframe windows, or missing controlled HTF Bias Timeframe and Minimum Entry Score experiments. Publication claims remain blocked.

## Artifact navigation

- `../tables/performance_metrics.csv`
- `../tables/monte_carlo_statistics.csv`
- `../tables/parameter_sensitivity.csv`
- `monte_carlo_report.html`
- `sensitivity_report.html`
- `../manifest.json`
