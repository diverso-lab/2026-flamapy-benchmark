# RQ1 oracle: exhaustive enumeration on small Boolean models

1047 models (Boolean language level, at most 60 features, enumerable). Oracle: `rq1/oracle.py` (no solver, definitions in its docstring).

| Operation | flamapy-sat:glucose3 | flamapy-bdd | flamapy-z3 | fama-sat4j | fama-choco | featureide |
|---|---|---|---|---|---|---|
| Satisfiable | ok 1046, not_run 1 | ok 1046, not_run 1 | ok 1046, not_run 1 | ok 853, missing 194 | ok 853, missing 194 | ok 1046, not_run 1 |
| CoreFeatures | ok 1046, not_run 1 | ok 1046, not_run 1 | ok 1046, not_run 1 | missing 194 | ok 847, missing 194, MISMATCH 4, timeout 2 | ok 1046, not_run 1 |
| DeadFeatures | ok 1046, not_run 1 | ok 1046, not_run 1 | ok 1046, not_run 1 | ok 853, missing 194 | ok 853, missing 194 | ok 1042, MISMATCH 4, not_run 1 |
| FalseOptionalFeatures | ok 1034, ok (vacuous def.) 12, not_run 1 | ok 1034, ok (vacuous def.) 12, not_run 1 | ok 1034, ok (vacuous def.) 12, not_run 1 | ok 848, missing 194, ok (vacuous def.) 5 | ok 848, missing 194, ok (vacuous def.) 5 | ok 1034, ok (vacuous def.) 8, ok (live-parent def.) 4, not_run 1 |
| ConfigurationsNumber | ok 1046, not_run 1 | ok 1046, not_run 1 | ok 981, timeout 65, not_run 1 | ok 843, missing 194, timeout 10 | ok 851, missing 194, timeout 2 | ok 1036, timeout 10, not_run 1 |
| Configurations | ok 1046, not_run 1 | ok 1046, not_run 1 | ok 980, timeout 66, not_run 1 | missing 194 | missing 194 | not_run 1 |
| VariantFeatures | not_run 1 | ok 1046, not_run 1 | not_run 1 | missing 194 | missing 194 | not_run 1 |
| PureOptionalFeatures | not_run 1 | ok 1046, not_run 1 | not_run 1 | missing 194 | missing 194 | not_run 1 |
| UniqueFeatures | not_run 1 | ok 1046, not_run 1 | not_run 1 | missing 194 | missing 194 | not_run 1 |
| FeatureInclusionProbability | not_run 1 | ok 1046, not_run 1 | not_run 1 | missing 194 | missing 194 | not_run 1 |
| ProductDistribution | not_run 1 | ok 1046, not_run 1 | not_run 1 | missing 194 | missing 194 | not_run 1 |
| Homogeneity | not_run 1 | ok 1046, not_run 1 | not_run 1 | missing 194 | missing 194 | not_run 1 |
| Variability | not_run 1 | ok 1046, not_run 1 | not_run 1 | missing 194 | missing 194 | not_run 1 |

5104 mismatches/errors listed in `rq1/oracle_mismatches.csv`.
