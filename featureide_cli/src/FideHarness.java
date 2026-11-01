import de.ovgu.featureide.fm.core.FeatureModelAnalyzer;
import de.ovgu.featureide.fm.core.analysis.cnf.CNF;
import de.ovgu.featureide.fm.core.analysis.cnf.analysis.CountSolutionsAnalysis;
import de.ovgu.featureide.fm.core.analysis.cnf.formula.FeatureModelFormula;
import de.ovgu.featureide.fm.core.base.IFeature;
import de.ovgu.featureide.fm.core.base.IFeatureModel;
import de.ovgu.featureide.fm.core.init.FMCoreLibrary;
import de.ovgu.featureide.fm.core.init.LibraryManager;
import de.ovgu.featureide.fm.core.io.manager.FeatureModelManager;
import de.ovgu.featureide.fm.core.io.manager.FileHandler;
import de.ovgu.featureide.fm.core.job.monitor.NullMonitor;

import java.lang.management.ManagementFactory;
import java.lang.management.ThreadMXBean;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Optional;

/**
 * Benchmark harness for the FeatureIDE library (de.ovgu.featureide.lib.fm). Speaks the same
 * JSON-lines protocol as FamaHarness and the Python worker (see campaign/protocol.md).
 *
 * Steps: setup (register the FM core library), parse (read the UVL file with FeatureIDE's own
 * UVL format), transform (build the CNF through FeatureModelFormula), then each operation and
 * repetition. Every repetition uses a fresh FeatureModelAnalyzer, because the analyzer caches
 * results; the CNF is shared, as it is the transformation result.
 *
 * FeatureIDE's SAT solver has a built-in default timeout of 10 s after which analyses silently
 * return an empty result. The harness raises it to the campaign timeout, so the parent's
 * kill-on-timeout is the only timeout in force, as for every other tool.
 *
 * Usage: FideHarness model.uvl timeoutMs reps op1,op2,...
 */
public class FideHarness {

    private static final ThreadMXBean THREADS = ManagementFactory.getThreadMXBean();

    public static void main(String[] args) {
        emit("{\"ev\":\"ready\",\"jvm_start_epoch_ms\":" + ManagementFactory.getRuntimeMXBean().getStartTime()
                + ",\"jvm_uptime_ms\":" + ManagementFactory.getRuntimeMXBean().getUptime()
                + ",\"java_version\":" + q(System.getProperty("java.version")) + "}");
        if (args.length != 4) {
            emit("{\"ev\":\"fatal\",\"msg\":\"usage: FideHarness model.uvl timeoutMs reps ops\"}");
            System.exit(1);
        }
        String modelPath = args[0];
        long timeoutMs = Long.parseLong(args[1]);
        int reps = Integer.parseInt(args[2]);
        String[] ops = args[3].split(",");

        if (!step("setup", () -> {
            LibraryManager.registerLibrary(FMCoreLibrary.getInstance());
            return null;
        })) {
            emit("{\"ev\":\"done\"}");
            return;
        }

        final IFeatureModel[] fmHolder = new IFeatureModel[1];
        if (!step("parse", () -> {
            FileHandler<IFeatureModel> fh = FeatureModelManager.getFileHandler(Paths.get(modelPath));
            if (fh == null || fh.getLastProblems().containsError() || fh.getObject() == null) {
                throw new IllegalStateException(fh == null ? "no format for file"
                        : fh.getLastProblems().getErrors().toString());
            }
            fmHolder[0] = fh.getObject();
            return "{\"features\":" + fmHolder[0].getNumberOfFeatures() + ",\"constraints\":"
                    + fmHolder[0].getConstraintCount() + "}";
        })) {
            emit("{\"ev\":\"done\"}");
            return;
        }
        IFeatureModel fm = fmHolder[0];

        final FeatureModelFormula[] formulaHolder = new FeatureModelFormula[1];
        final CNF[] cnfHolder = new CNF[1];
        if (!step("transform", () -> {
            FeatureModelFormula formula = new FeatureModelFormula(fm);
            cnfHolder[0] = formula.getCNF();
            formulaHolder[0] = formula;
            return "{\"variables\":" + cnfHolder[0].getVariables().size() + ",\"clauses\":"
                    + cnfHolder[0].getClauses().size() + "}";
        })) {
            emit("{\"ev\":\"done\"}");
            return;
        }

        for (String op : ops) {
            for (int rep = 0; rep < reps; rep++) {
                final String o = op;
                boolean ok = step("op:" + op + ":" + rep,
                        () -> runOp(formulaHolder[0], cnfHolder[0], o, timeoutMs));
                if (!ok) {
                    break;
                }
            }
        }
        emit("{\"ev\":\"done\"}");
    }

    private static String runOp(FeatureModelFormula formula, CNF cnf, String op, long timeoutMs) throws Exception {
        if (op.equals("ConfigurationsNumber")) {
            CountSolutionsAnalysis count = new CountSolutionsAnalysis(cnf);
            count.setTimeout(timeoutMs);
            Long n = count.execute(new NullMonitor<>());
            if (n == null || n < 0) {
                throw new IllegalStateException("solver timeout after " + (n == null ? 0 : -n - 1) + " solutions");
            }
            return q(String.valueOf(n));
        }
        FeatureModelAnalyzer analyzer = new FeatureModelAnalyzer(formula);
        analyzer.setDefaultSolverTimeout(timeoutMs);
        switch (op) {
            case "Satisfiable":
                return analyzer.isValid(null, -1, timeoutMs).map(v -> v ? "true" : "false")
                        .orElseThrow(() -> new IllegalStateException("no result (solver timeout or error)"));
            case "CoreFeatures":
                return names(analyzer.getCoreFeatures(null, -1, timeoutMs));
            case "DeadFeatures":
                return names(analyzer.getDeadFeatures(null, -1, timeoutMs));
            case "FalseOptionalFeatures":
                return names(analyzer.getFalseOptionalFeatures(null, -1, timeoutMs));
            default:
                throw new IllegalArgumentException("unknown operation " + op);
        }
    }

    private static String names(Optional<List<IFeature>> features) {
        List<IFeature> list = features.orElseThrow(
                () -> new IllegalStateException("no result (solver timeout or error)"));
        List<String> sorted = new ArrayList<>();
        for (IFeature f : list) {
            sorted.add(f.getName());
        }
        Collections.sort(sorted);
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < sorted.size(); i++) {
            if (i > 0) sb.append(',');
            sb.append(q(sorted.get(i)));
        }
        return sb.append(']').toString();
    }

    // ------------------------------------------------------------------ protocol helpers

    interface Body {
        String run() throws Exception;
    }

    private static boolean step(String name, Body body) {
        emit("{\"ev\":\"begin\",\"step\":" + q(name) + "}");
        long cpu0 = THREADS.getCurrentThreadCpuTime();
        long t0 = System.nanoTime();
        try {
            String value = body.run();
            long t1 = System.nanoTime();
            long cpu1 = THREADS.getCurrentThreadCpuTime();
            emit("{\"ev\":\"end\",\"step\":" + q(name) + ",\"status\":\"success\",\"t\":" + (t1 - t0) / 1e9
                    + ",\"cpu\":" + (cpu1 - cpu0) / 1e9 + (value == null ? "" : ",\"value\":" + value) + "}");
            return true;
        } catch (Throwable e) {
            long t1 = System.nanoTime();
            String msg = e.getClass().getSimpleName() + ": " + String.valueOf(e.getMessage());
            emit("{\"ev\":\"end\",\"step\":" + q(name) + ",\"status\":\"error\",\"t\":" + (t1 - t0) / 1e9
                    + ",\"error\":" + q(msg.length() > 300 ? msg.substring(0, 300) : msg) + "}");
            return false;
        }
    }

    private static synchronized void emit(String json) {
        System.out.println(json);
        System.out.flush();
    }

    static String q(String s) {
        if (s == null) return "null";
        StringBuilder sb = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        return sb.append('"').toString();
    }
}
