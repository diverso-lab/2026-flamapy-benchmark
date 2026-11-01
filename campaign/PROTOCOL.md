# Worker protocol

Every worker (Python `campaign/py_worker.py`, Java `FamaHarness`, Java `FideHarness`) writes one
JSON object per line on stdout. Anything else on stdout is ignored; stderr is kept only for crash
reports.

```
{"ev":"ready", ...}                                     first line, as soon as the process runs
{"ev":"begin","step":"setup"}                           before each timed step
{"ev":"end","step":"setup","status":"success","t":0.11,"cpu":0.11,"value":{...}}
{"ev":"begin","step":"parse"}            ...           parse the model file
{"ev":"begin","step":"transform"}        ...           build the solver model (not for FaMa)
{"ev":"begin","step":"op:CoreFeatures:0"}               operation, repetition
{"ev":"end","step":"op:CoreFeatures:0","status":"success","t":0.002,"cpu":0.002,"value":["A","B"]}
{"ev":"end", ..., "status":"error","error":"..."}       a failed step (the worker goes on with the next operation)
{"ev":"done"}                                           last line
```

* `t` is wall-clock seconds measured inside the worker around the step only; `cpu` is the CPU time
  of the measuring thread/process.
* `value` of an operation is normalised: booleans, counts as decimal strings, feature lists as
  sorted names, configurations as sorted lists of selected feature names.
* The runner enforces the timeout: if `end` does not arrive within `timeout_s` of `begin`, it kills
  the process group, records `timeout`, and starts a new worker for the operations not yet started.
