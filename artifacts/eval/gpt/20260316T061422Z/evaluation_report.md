# Testset Mechanism Benchmark (v0)

## Run Config
- test_csv: `/Users/wuguocheng/workshop/Uncertainty_aware_AIE/data/split_list/1_level.csv`
- run_lane: `atb_cache_only`
- model: `gpt-5.2`
- base_url: `http://35.220.164.252:3888/v1`
- reasoning_effort: `high`
- temperature: `0.2`
- llm_use_json_schema: `False`
- seed_supported: `False`

## Key Metrics
- top1_accuracy_including_other (ok subset): `0.44`
- top1_accuracy_excluding_other_gt (ok subset): `0.44`
- macro_f1 (ok subset): `0.375401`
- coverage (full set): `1.0`
- unknown_rate (covered subset): `0.02`

## Counts
- total_rows: `50`
- ok_rows: `50`
- covered_rows: `50`
- status: `{'ok': 50}`

## Per-class Precision/Recall/F1

| label | precision | recall | f1 | support |
|---|---:|---:|---:|---:|
| TICT | 0.5 | 0.142857 | 0.222222 | 14 |
| ICT | 0.3125 | 0.714286 | 0.434783 | 14 |
| ESIPT | 0.0 | 0.0 | 0.0 | 7 |
| neutral aromatic | 0.692308 | 0.75 | 0.72 | 12 |
| unknown | 1.0 | 0.333333 | 0.5 | 3 |

## Confusion Matrix (y_true rows, y_pred columns)

| y_true \\ y_pred | TICT | ICT | ESIPT | neutral aromatic | unknown |
|---|---|---|---|---|---|
| TICT | 2 | 12 | 0 | 0 | 0 |
| ICT | 2 | 10 | 0 | 2 | 0 |
| ESIPT | 0 | 6 | 0 | 1 | 0 |
| neutral aromatic | 0 | 3 | 0 | 9 | 0 |
| unknown | 0 | 1 | 0 | 1 | 1 |
