# MAESTRO-X v1.0 Quantitative Validation: Research Summary

## Research Status Statement

This document summarizes the evidence produced by the MAESTRO-X Research Framework for the MAESTRO-X v1.0 TradingView strategy on XAUUSD. The strategy source was treated as an immutable research object: the Python workflow did not alter the Pine Script, entry conditions, exit conditions, risk logic, or strategy parameters. The analysis is restricted to accepted rows from the available TradingView trade-log exports. It is therefore an observed-subset validation study, not a reconstruction of the complete TradingView backtest and not evidence of future profitability.

The computational framework is reproducible for the frozen files identified below. The empirical evidence is not presently eligible for a publication claim of statistical superiority, parameter robustness, or live-trading suitability because source completeness, experimental controls, execution-cost provenance, and out-of-sample validation remain unresolved.

## 1. Methodology

### 1.1 Research objective and unit of analysis

The objective was to assess the internal consistency, observed performance, path dependence, resampling robustness, and parameter-sensitivity evidence of MAESTRO-X v1.0 without modifying or optimizing the strategy. The primary unit of analysis was a realized trade row with a valid signed net profit or loss. Each chart timeframe was treated as an independent exported backtest. Results were not pooled across timeframes because the samples differ substantially in calendar period, record count, and completeness.

The analysis sequence was:

1. raw-export inspection and data-quality audit;
2. schema validation, segment detection, parsing, normalization, and quarantine;
3. realized-trade performance calculation and equity reconstruction;
4. Monte Carlo sequence permutation and nonparametric bootstrap resampling;
5. evidence-bounded parameter-sensitivity analysis; and
6. documentation of supported findings, limitations, and reproducibility conditions.

### 1.2 Dataset description

The source dataset comprises eight TradingView Strategy Tester trade-log CSV files for XAUUSD: 1m, 5m, 15m, 30m, 1h, 2h, 4h, and 1D. The eight raw files contain 918 rows in total. Five files are cumulative append containers that include blocks copied from lower-timeframe exports rather than isolated single-timeframe datasets. Trade-ID resets were consequently used to delimit candidate blocks. The provisional target for each file was the last block whose elapsed-time-per-bar evidence matched the timeframe encoded by the filename.

The raw exports use minified positional headers and occur in 20- and 21-column variants. Numeric fields include ASCII and Unicode minus signs, percentage tokens, thousands separators, and a narrow-space `K` suffix. Intraday timestamps use minute precision, whereas daily records may contain date-only values. No timezone is embedded in the exports; normalized timestamps are therefore timezone-naive and must not be interpreted as exchange-qualified timestamps.

| Timeframe | Raw container rows | Target candidates | Accepted | Realized | Open | Quarantined | Realized analysis window |
|---|---:|---:|---:|---:|---:|---:|---|
| 1m | 61 | 61 | 61 | 61 | 0 | 0 | 2026-08-16 23:46 to 2026-08-21 06:59 |
| 5m | 61 | 61 | 61 | 61 | 0 | 0 | 2026-07-27 11:25 to 2026-08-21 11:05 |
| 15m | 91 | 30 | 30 | 30 | 0 | 0 | 2026-06-02 03:00 to 2026-06-11 00:15 |
| 30m | 119 | 28 | 28 | 28 | 0 | 0 | 2026-01-08 02:30 to 2026-01-26 06:30 |
| 1h | 61 | 61 | 61 | 60 | 1 | 0 | 2025-01-09 11:00 to 2026-08-21 09:00 |
| 2h | 147 | 28 | 28 | 28 | 0 | 0 | 2025-01-16 10:00 to 2025-02-28 04:00 |
| 4h | 175 | 28 | 27 | 27 | 0 | 1 | 2023-01-31 10:00 to 2023-06-15 09:00 |
| 1D | 203 | 28 | 27 | 27 | 0 | 1 | 1975-01-06 00:00 to 1977-12-23 00:00 |
| **Total** | **918** | **325** | **323** | **322** | **1** | **2** | Heterogeneous and non-comparable |

The two rejected records were semantically shifted zero-PnL rows with invalid percentage formatting, one each in the 4h and 1D target segments. They were quarantined without imputation or silent repair. The accepted dataset contains unique content-derived record identifiers. Large trade-ID gaps remain in the 1m, 5m, and 1h target segments (212, 168, and 371 missing identifiers within their respective observed minimum-to-maximum ranges). These gaps were documented but never imputed.

### 1.3 Experimental setup

MAESTRO-X v1.0 is a Pine Script v5 strategy evaluated through TradingView Strategy Tester exports. The current research object is `strategy/MAESTRO-X_v1.0.pine`, with SHA-256 `a4e94a39fdf2806db07b0142ad8a014ad1f60ea76efcead4c648e6ae744e0bc6`. Python did not execute the Pine strategy; it analyzed exported trade records only.

Equity was reconstructed independently for every timeframe. The working initial-capital assumption was USD 40, as stated in `dataset_info.yaml` and supported by the median ratio between exported cumulative PnL in currency and percentage terms. This assumption conflicts with the Pine declaration `initial_capital=10000`; all return percentages and ruin thresholds in this study are therefore conditional on the USD 40 interpretation and do not resolve the underlying provenance conflict.

Metadata records commission as zero and slippage as not configured. However, commission is absent from the 20-column export variant and complete TradingView Strategy Properties are unavailable. The analysis did not impute spread, commission, slippage, financing, latency, or alternative fill behavior. The single open 1h position was excluded from realized performance and resampling.

### 1.4 Data-processing and validation framework

The production preprocessing workflow performed CSV loading, positional-schema validation, trade-ID segmentation, timeframe detection, numeric conversion, explicit timestamp parsing, trade normalization, semantic validation, quarantine, atomic output writing, and before/after source hashing. Raw files were read-only, and all eight raw hashes were unchanged after processing.

Canonical records preserve source filename, source SHA-256, source row, source segment, detected timeframe, entry and exit fields, signed net PnL, cumulative-PnL fields, trade status, and a deterministic record identifier. Percentages are represented as percentage points. Timeframe consistency, timestamp order, positive price and contract constraints, directional PnL reconciliation, uniqueness, and boolean trade status were validated before downstream analysis.

Performance calculations were accepted only after the following identities were reconciled within numerical tolerance:

- total trades = winning + losing + breakeven trades;
- net profit = gross profit - absolute gross loss;
- expectancy × trade count = net profit;
- ending equity = initial capital + net profit; and
- reported maximum drawdown = maximum drawdown reconstructed from the observed equity path.

All eight rows in the production performance table passed these internal identities. Historical PDF reports were used only as secondary context. Their trade counts do not match the processed snapshots; consequently, none of the eight timeframe results was declared directly comparable with the PDF figures.

### 1.5 Performance measures

For realized trade PnL values \(x_i\), the principal estimators were:

\[
\text{Win Rate}=\frac{N(x_i>0)}{N}, \qquad
\text{Expectancy}=\frac{1}{N}\sum_{i=1}^{N}x_i,
\]

\[
\text{Profit Factor}=\frac{\sum_{x_i>0}x_i}{\left|\sum_{x_i<0}x_i\right|}.
\]

Observed equity was reconstructed as \(E_t=C_0+\sum_{i=1}^{t}x_i\), where \(C_0=40\) USD. At each trade, drawdown was the difference between the running equity peak and current equity; percentage drawdown used the contemporaneous running peak as denominator. Maximum drawdown was the maximum of this path. Recovery factor was net profit divided by maximum drawdown in currency units. No missing trade was reconstructed from cumulative-PnL endpoints.

### 1.6 Monte Carlo methodology

Two resampling designs were applied separately to each timeframe, with 10,000 simulations per method and timeframe (160,000 reconstructed paths in total):

1. **Trade-sequence permutation without replacement.** Each path contains the exact observed trades once, in randomized order. Terminal return is invariant by construction; this method evaluates ordering-dependent equity-path risk, maximum drawdown, minimum equity, and threshold breaches.
2. **IID nonparametric bootstrap with replacement.** Each path samples the observed trade PnL values with replacement, preserving the original sample size. This method produces a conditional distribution of terminal return and drawdown under the assumption that observed trades are independent and identically distributed representatives of the relevant process.

Every simulated path began at USD 40. Terminal return, minimum equity, and maximum drawdown were calculated from cumulative trade PnL. Operational ruin was defined as equity less than or equal to 50% of assumed initial capital, or USD 20. Fixed seeds from `20260822` through `20260837` made the timeframe-method batches deterministic.

Reported 2.5th and 97.5th percentiles are simulation percentile intervals. For permutation, the terminal-return interval is degenerate and carries no inferential content. For bootstrap, the interval is conditional on the observed IID empirical distribution; it is neither a population confidence interval under a validated data-generating model nor a forecast interval for future trading.

### 1.7 Parameter-sensitivity methodology

Three requested dimensions were audited: chart timeframe, HTF Bias Timeframe, and Minimum Entry Score. Chart timeframe is observed in the exports, but it is not a controlled parameter experiment because each timeframe uses a different calendar period and incomplete snapshot. It was therefore evaluated descriptively only. The immutable Pine source defines HTF Bias Timeframe as `60`, `240`, or `D` (default `60`) and Minimum Entry Score as integer values from 2 through 6 (default 4). No outcome-tagged controlled runs exist for either Pine parameter; their stability and response surfaces are formally `not_estimable`.

For the eight observed timeframe rows, seven descriptive measures were standardized across timeframes: win rate, profit factor, expectancy, observed maximum drawdown, recovery factor, bootstrap probability of negative return, and bootstrap terminal-return interval width. For each measure \(x\), robust standardization used

\[
z_r=\frac{x-\operatorname{median}(x)}{1.4826\operatorname{MAD}(x)},
\]

with interquartile-range and sample-standard-deviation fallbacks only when the median absolute deviation was zero. A timeframe's descriptive deviation magnitude was the root mean square of its seven robust scores. Its concentration share was that magnitude divided by the sum of magnitudes. Sensitivity concentration was summarized by \(HHI=\sum_i s_i^2\), with effective contributors defined as \(1/HHI\).

These quantities measure contribution to observed cross-timeframe dispersion, not desirability. They were not used as an objective function, ranking criterion, optimizer, or basis for changing the strategy. A robustness region cannot be identified without controlled parameter combinations, identical feed/range/cost settings, repeated windows, and out-of-sample evidence.

## 2. Results Summary

### 2.1 Data and validation outcomes

Of 325 provisional target-segment rows, 323 passed production validation. The realized analysis contains 322 closed trades; one open 1h record was retained in processed data but excluded from all realized metrics and simulations. Two malformed rows were quarantined. Record identifiers were unique, all accepted rows were timeframe-consistent under the implemented detector, and raw-source hashes remained unchanged.

The validation supports calculation on the accepted subset but does not establish that the exports are complete TradingView histories. The publication-claim gate remains blocked because source snapshots, initial capital, Strategy Properties, feed, timezone, and controlled parameter metadata are not reconciled.

### 2.2 Observed performance and bootstrap uncertainty

| Timeframe | Trades | Net return (%) | Win rate (%) | Profit factor | Expectancy (USD) | Observed max DD (%) | Recovery factor | Bootstrap terminal-return 95% PI (%) | P(negative return) (%) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1m | 61 | 15.58 | 54.10 | 1.335 | 0.1021 | 15.31 | 0.896 | [-12.90, 44.08] | 14.14 |
| 5m | 61 | 31.44 | 57.38 | 1.776 | 0.2062 | 8.16 | 3.549 | [2.92, 60.28] | 1.40 |
| 15m | 30 | 12.66 | 60.00 | 1.838 | 0.1687 | 3.28 | 3.317 | [-2.76, 27.75] | 5.83 |
| 30m | 28 | -1.21 | 46.43 | 0.932 | -0.0172 | 10.23 | -0.109 | [-15.36, 12.52] | 56.06 |
| 1h | 60 | 28.77 | 56.67 | 1.698 | 0.1918 | 5.82 | 4.152 | [-1.30, 58.85] | 3.01 |
| 2h | 28 | 7.07 | 53.57 | 1.448 | 0.1010 | 5.11 | 1.339 | [-7.63, 21.51] | 17.60 |
| 4h | 27 | 21.17 | 66.67 | 2.946 | 0.3137 | 2.99 | 5.757 | [5.82, 35.69] | 0.35 |
| 1D | 27 | 8.68 | 51.85 | 1.439 | 0.1285 | 3.94 | 1.959 | [-11.72, 28.49] | 20.37 |

Seven of eight observed subsets had positive expectancy and profit factor above one. The 30m subset was the sole exception, with a net return of -1.205%, profit factor of 0.932, and bootstrap probability of negative terminal return of 56.06%. These are descriptive snapshot results. They do not establish a stable ordering across timeframes because the samples are neither contemporaneous nor complete.

Six of eight bootstrap terminal-return intervals crossed zero. The intervals for 5m and 4h did not cross zero within the conditional IID bootstrap experiment, but this observation is not a formal significance result, does not correct for data selection or multiple testing, and does not imply future profitability.

### 2.3 Monte Carlo path-risk results

Under permutation, median maximum drawdown ranged from 2.90% for 4h to 8.99% for 1m. The largest worst-path drawdown among the permutation batches was 24.01% for 5m. As expected, permutation terminal returns were identical to the corresponding observed returns.

Under bootstrap, median maximum drawdown ranged from 2.89% for 4h to 9.14% for 1m. The most adverse maximum drawdown observed across bootstrap batches was 52.25% in the 1m batch. One 1m bootstrap path breached the operational USD 20 ruin threshold, producing a within-batch estimate of 0.01%; all other bootstrap batches and all permutation batches recorded no breach. A zero simulated breach rate is not evidence that the population ruin probability is zero.

### 2.4 Sensitivity and overfitting evidence

The descriptive concentration HHI was 0.1603, corresponding to 6.24 effective contributors among eight timeframe observations. The 4h and 30m rows contributed 23.05% and 22.60%, respectively, or 45.65% jointly, to the standardized dispersion magnitude. The two rows represent opposite performance directions; their concentration shares therefore describe unusualness relative to the cross-timeframe median, not parameter quality.

No empirical response surface exists for HTF Bias Timeframe or Minimum Entry Score, and no defensible robustness region was identified. Parameter stability is not formally estimable. Overfitting risk is likewise indeterminate, with elevated evidentiary concern due to the absence of a preregistered controlled grid, multiple-testing history, repeated windows, walk-forward analysis, and out-of-sample evaluation. No parameter was optimized, selected, or recommended.

## 3. Limitations

1. **Incomplete and cumulative source exports.** Five CSV files contain appended blocks from other timeframes, and major trade-ID gaps remain in three selected segments. The provisional last-matching-block rule is auditable but does not prove snapshot completeness.
2. **Snapshot non-equivalence.** The secondary PDFs report substantially larger trade counts than the accepted exports. None of the eight processed samples has a matching PDF trade count, so numerical agreement with the PDF backtests cannot be tested on a like-for-like basis.
3. **Heterogeneous sampling.** Calendar periods range from 1975–1977 for 1D to August 2026 for intraday samples, with only 27–61 realized trades per timeframe. Cross-timeframe differences may reflect market regime, sample duration, data availability, or unrecorded settings rather than timeframe sensitivity.
4. **Unknown market-data provenance.** Exchange-qualified symbol, broker or exchange feed, chart timezone, exact chart range, and feed snapshot are unavailable. Timestamps cannot be aligned confidently with sessions or external market data.
5. **Initial-capital conflict.** Metadata and exported cumulative-PnL ratios support USD 40, whereas the current Pine declaration specifies USD 10,000. Return, drawdown-percentage, and ruin calculations are conditional on USD 40 and should not be generalized until the discrepancy is resolved.
6. **Incomplete execution-cost specification.** Commission is unavailable in six 20-column exports, metadata states zero commission, and slippage is unspecified. Spread, financing, latency, market impact, and broker fill behavior are not modeled.
7. **Pine equivalence is not current runtime evidence.** `results/validation/equivalence.json` reports successful static checks for candidate SHA-256 `c73fb6a864c5d51547ae8758d6eac89753d96fc301d1a16b64c90f3d61eb906b`, which does not match the current research-object SHA-256 `a4e94a39fdf2806db07b0142ad8a014ad1f60ea76efcead4c648e6ae744e0bc6`. Runtime TradingView compilation and broker-emulator equivalence were not executed. The historical static artifact must therefore be regenerated before it can support the current object directly.
8. **Conditional Monte Carlo assumptions.** Sequence permutation changes order but not the empirical trade set. IID bootstrap ignores serial dependence, volatility clustering, nonstationarity, regime shifts, missing trades, and parameter uncertainty. Simulation percentiles are conditional summaries, not validated population probabilities or forecasts.
9. **No controlled sensitivity design.** HTF Bias Timeframe and Minimum Entry Score outcomes were not exported. Timeframe samples do not share identical feeds, windows, costs, or settings. Formal stability, robustness regions, and sensitivity gradients are consequently unidentified.
10. **No out-of-sample validation.** No frozen training/test split, walk-forward experiment, cross-feed replication, cost-stress study, or multiple-testing correction is available. Statistical significance and overfitting probability were not claimed.
11. **Limited external validity.** The evidence concerns one strategy, one nominal asset, and partial historical exports. It does not support capital-allocation advice, a profitability guarantee, or an expectation of future performance.

## 4. Reproducibility Notes

### 4.1 Software environment

The validated local execution environment used Python 3.13.5, pandas 2.3.1, NumPy 2.3.1, SciPy 1.17.1, Matplotlib 3.10.7, and PyYAML 6.0.2. These versions match the pinned core dependencies in `requirements.txt`. Plotly is declared in `requirements.txt` but was not installed in the validation environment and is not imported by the Phase 2–5 commands documented below.

### 4.2 Execution sequence

From the repository root, install the declared dependencies and execute each production phase explicitly:

```powershell
python -m pip install -r requirements.txt
python -m src.run_preprocessing
python -m src.run_performance
python -m src.run_monte_carlo --simulations 10000
python -m src.run_sensitivity
python -m unittest discover -s validation -v
```

The standalone commands are authoritative for the production Phase 2–5 artifacts summarized here. The legacy `python -m src.run_pipeline` command generates an earlier parallel artifact set and should not be used as a substitute for these exact outputs.

### 4.3 Determinism and integrity controls

Monte Carlo batches use fixed seeds `20260822`–`20260837`. CSV serialization, figures, and HTML reports are deterministic for unchanged inputs; production writes are atomic. Preprocessing records all raw-file hashes before and after execution and fails if source integrity changes. Raw CSV files and the Pine source are never output targets.

Key snapshot identifiers are:

| Object | SHA-256 |
|---|---|
| `strategy/MAESTRO-X_v1.0.pine` | `a4e94a39fdf2806db07b0142ad8a014ad1f60ea76efcead4c648e6ae744e0bc6` |
| `data/processed/normalized_trades.csv` | `87ff031b35593348b48f95b93cffbaad5cd2821ef6d3a8b1552f10ce644981ef` |
| `results/tables/performance_metrics.csv` | `2c4d5d547d0600d0398c25d31a23baf2a37b11b80ac4640d9e2a658c4e615151` |
| `results/tables/monte_carlo_statistics.csv` | `7637994341ba3ce6b46ba734d3e34132b243e164f9475b4370b052b726240eee` |
| `results/tables/parameter_sensitivity.csv` | `207eaedea58a0f12de666a8c40841df52975bdf4f295f08027a2a319e76b7ca6` |

Reproduction claims apply only when these identifiers and the raw-source hashes recorded in the preprocessing report match. They establish computational identity for the local analysis; they do not reproduce TradingView's Pine compiler, broker emulator, market-data feed, or missing Strategy Properties.

### 4.4 Primary evidence map

- [Data-validation report](data_validation_report.json)
- [Preprocessing report](preprocessing_report.json)
- [Processed dataset summary](../tables/processed_dataset_summary.csv)
- [Performance metrics](../tables/performance_metrics.csv)
- [Observed equity figure](../figures/equity_curve.png)
- [Monte Carlo report](monte_carlo_report.html)
- [Monte Carlo statistics](../tables/monte_carlo_statistics.csv)
- [Monte Carlo distribution figure](../figures/monte_carlo_distribution.png)
- [Sensitivity report](sensitivity_report.html)
- [Parameter-sensitivity table](../tables/parameter_sensitivity.csv)
- [Parameter evidence surface](../figures/parameter_surface.png)

### 4.5 Interpretation boundary

Successful reproduction means that the same accepted records yield the same deterministic tables, figures, and reports under the documented software environment. It does not validate the completeness of the source exports, resolve the capital and execution-setting conflicts, establish statistical significance, identify robust parameters, or imply future profitability.
