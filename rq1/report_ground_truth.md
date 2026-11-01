# RQ1 ground truth: FaMa test suite

Results: `results/rq1_fama_suite`. Expected values: `rq1/fama_suite_expected.json` (extracted from flamapy/tests/test_fama_suite_*.py).

Cells: passed / cases (FAIL = wrong answer; n/a = operation not offered by the tool).

| Operation | flamapy-sat:glucose3 | flamapy-bdd | flamapy-z3 | fama-sat4j | fama-choco | featureide |
|---|---|---|---|---|---|---|
| Satisfiable | 24/24 | 24/24 | 24/24 | 24/24 | 24/24 | 24/24 |
| CoreFeatures | 6/6 | 6/6 | 6/6 | n/a | 6/6 | 6/6 |
| DeadFeatures | 8/8 | 8/8 | 8/8 | 8/8 | 8/8 | 6/8 (FAIL 2) |
| FalseOptionalFeatures | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 |
| ConfigurationsNumber | 21/21 | 21/21 | 21/21 | 21/21 | 21/21 | 21/21 |
| Configurations | 21/21 | 21/21 | 21/21 | n/a | n/a | n/a |

## Disagreements and errors

| Model | Operation | Tool | Got | Expected |
|---|---|---|---|---|
| error-guessing/dead-features/case5/df-case5.xml | DeadFeatures | featureide | got `[]` | `["A", "B", "C"]` |
| error-guessing/dead-features/case7/df-case7.xml | DeadFeatures | featureide | got `[]` | `["A", "B", "C"]` |
