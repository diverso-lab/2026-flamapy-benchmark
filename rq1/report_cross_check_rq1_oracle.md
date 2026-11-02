# RQ1 differential testing: rq1_oracle

Results: `results/rq1_oracle`. A result counts only when the tool finished within the timeout. Feature lists are restricted to the features declared in the model (FeatureIDE helper features removed: {'featureide': 9650, 'fama-choco': 5}).

## Agreement with the majority answer

agree / compared (minority = differs from a strict majority; tie = no majority).

| Operation | flamapy-sat:glucose3 | flamapy-bdd | flamapy-z3 | fama-sat4j | fama-choco | featureide |
|---|---|---|---|---|---|---|
| Satisfiable | 1046/1046 | 1046/1046 | 1046/1046 | 853/853 | 853/853 | 1046/1046 |
| CoreFeatures | 1046/1046 | 1046/1046 | 1046/1046 |  | 847/851 (minority 4) | 1046/1046 |
| DeadFeatures | 1046/1046 | 1046/1046 | 1046/1046 | 853/853 | 853/853 | 1042/1046 (minority 4) |
| FalseOptionalFeatures | 1046/1046 | 1046/1046 | 1046/1046 | 853/853 | 853/853 | 1042/1046 (minority 4) |
| ConfigurationsNumber | 1046/1046 | 1046/1046 | 981/981 | 843/843 | 851/851 | 1036/1036 |
| Configurations | 1046/1046 | 1046/1046 | 980/980 |  |  |  |
| VariantFeatures |  |  |  |  |  |  |
| PureOptionalFeatures |  |  |  |  |  |  |
| UniqueFeatures |  |  |  |  |  |  |
| FeatureInclusionProbability |  |  |  |  |  |  |
| ProductDistribution |  |  |  |  |  |  |
| Homogeneity |  |  |  |  |  |  |
| Variability |  |  |  |  |  |  |

## Pairwise agreement

| Operation | Tool A | Tool B | agree | disagree |
|---|---|---|---|---|
| Configurations | flamapy-bdd | flamapy-z3 | 980 | 0 |
| Configurations | flamapy-sat:glucose3 | flamapy-bdd | 1046 | 0 |
| Configurations | flamapy-sat:glucose3 | flamapy-z3 | 980 | 0 |
| ConfigurationsNumber | fama-choco | featureide | 842 | 0 |
| ConfigurationsNumber | fama-sat4j | fama-choco | 841 | 0 |
| ConfigurationsNumber | fama-sat4j | featureide | 842 | 0 |
| ConfigurationsNumber | flamapy-bdd | fama-choco | 851 | 0 |
| ConfigurationsNumber | flamapy-bdd | fama-sat4j | 843 | 0 |
| ConfigurationsNumber | flamapy-bdd | featureide | 1036 | 0 |
| ConfigurationsNumber | flamapy-bdd | flamapy-z3 | 981 | 0 |
| ConfigurationsNumber | flamapy-sat:glucose3 | fama-choco | 851 | 0 |
| ConfigurationsNumber | flamapy-sat:glucose3 | fama-sat4j | 843 | 0 |
| ConfigurationsNumber | flamapy-sat:glucose3 | featureide | 1036 | 0 |
| ConfigurationsNumber | flamapy-sat:glucose3 | flamapy-bdd | 1046 | 0 |
| ConfigurationsNumber | flamapy-sat:glucose3 | flamapy-z3 | 981 | 0 |
| ConfigurationsNumber | flamapy-z3 | fama-choco | 796 | 0 |
| ConfigurationsNumber | flamapy-z3 | fama-sat4j | 798 | 0 |
| ConfigurationsNumber | flamapy-z3 | featureide | 981 | 0 |
| CoreFeatures | fama-choco | featureide | 847 | 4 |
| CoreFeatures | flamapy-bdd | fama-choco | 847 | 4 |
| CoreFeatures | flamapy-bdd | featureide | 1046 | 0 |
| CoreFeatures | flamapy-bdd | flamapy-z3 | 1046 | 0 |
| CoreFeatures | flamapy-sat:glucose3 | fama-choco | 847 | 4 |
| CoreFeatures | flamapy-sat:glucose3 | featureide | 1046 | 0 |
| CoreFeatures | flamapy-sat:glucose3 | flamapy-bdd | 1046 | 0 |
| CoreFeatures | flamapy-sat:glucose3 | flamapy-z3 | 1046 | 0 |
| CoreFeatures | flamapy-z3 | fama-choco | 847 | 4 |
| CoreFeatures | flamapy-z3 | featureide | 1046 | 0 |
| DeadFeatures | fama-choco | featureide | 852 | 1 |
| DeadFeatures | fama-sat4j | fama-choco | 853 | 0 |
| DeadFeatures | fama-sat4j | featureide | 852 | 1 |
| DeadFeatures | flamapy-bdd | fama-choco | 853 | 0 |
| DeadFeatures | flamapy-bdd | fama-sat4j | 853 | 0 |
| DeadFeatures | flamapy-bdd | featureide | 1042 | 4 |
| DeadFeatures | flamapy-bdd | flamapy-z3 | 1046 | 0 |
| DeadFeatures | flamapy-sat:glucose3 | fama-choco | 853 | 0 |
| DeadFeatures | flamapy-sat:glucose3 | fama-sat4j | 853 | 0 |
| DeadFeatures | flamapy-sat:glucose3 | featureide | 1042 | 4 |
| DeadFeatures | flamapy-sat:glucose3 | flamapy-bdd | 1046 | 0 |
| DeadFeatures | flamapy-sat:glucose3 | flamapy-z3 | 1046 | 0 |
| DeadFeatures | flamapy-z3 | fama-choco | 853 | 0 |
| DeadFeatures | flamapy-z3 | fama-sat4j | 853 | 0 |
| DeadFeatures | flamapy-z3 | featureide | 1042 | 4 |
| FalseOptionalFeatures | fama-choco | featureide | 852 | 1 |
| FalseOptionalFeatures | fama-sat4j | fama-choco | 853 | 0 |
| FalseOptionalFeatures | fama-sat4j | featureide | 852 | 1 |
| FalseOptionalFeatures | flamapy-bdd | fama-choco | 853 | 0 |
| FalseOptionalFeatures | flamapy-bdd | fama-sat4j | 853 | 0 |
| FalseOptionalFeatures | flamapy-bdd | featureide | 1042 | 4 |
| FalseOptionalFeatures | flamapy-bdd | flamapy-z3 | 1046 | 0 |
| FalseOptionalFeatures | flamapy-sat:glucose3 | fama-choco | 853 | 0 |
| FalseOptionalFeatures | flamapy-sat:glucose3 | fama-sat4j | 853 | 0 |
| FalseOptionalFeatures | flamapy-sat:glucose3 | featureide | 1042 | 4 |
| FalseOptionalFeatures | flamapy-sat:glucose3 | flamapy-bdd | 1046 | 0 |
| FalseOptionalFeatures | flamapy-sat:glucose3 | flamapy-z3 | 1046 | 0 |
| FalseOptionalFeatures | flamapy-z3 | fama-choco | 853 | 0 |
| FalseOptionalFeatures | flamapy-z3 | fama-sat4j | 853 | 0 |
| FalseOptionalFeatures | flamapy-z3 | featureide | 1042 | 4 |
| Satisfiable | fama-choco | featureide | 853 | 0 |
| Satisfiable | fama-sat4j | fama-choco | 853 | 0 |
| Satisfiable | fama-sat4j | featureide | 853 | 0 |
| Satisfiable | flamapy-bdd | fama-choco | 853 | 0 |
| Satisfiable | flamapy-bdd | fama-sat4j | 853 | 0 |
| Satisfiable | flamapy-bdd | featureide | 1046 | 0 |
| Satisfiable | flamapy-bdd | flamapy-z3 | 1046 | 0 |
| Satisfiable | flamapy-sat:glucose3 | fama-choco | 853 | 0 |
| Satisfiable | flamapy-sat:glucose3 | fama-sat4j | 853 | 0 |
| Satisfiable | flamapy-sat:glucose3 | featureide | 1046 | 0 |
| Satisfiable | flamapy-sat:glucose3 | flamapy-bdd | 1046 | 0 |
| Satisfiable | flamapy-sat:glucose3 | flamapy-z3 | 1046 | 0 |
| Satisfiable | flamapy-z3 | fama-choco | 853 | 0 |
| Satisfiable | flamapy-z3 | fama-sat4j | 853 | 0 |
| Satisfiable | flamapy-z3 | featureide | 1046 | 0 |

12 (model, operation) pairs with any disagreement: `rq1/disagreements_rq1_oracle.csv`.
