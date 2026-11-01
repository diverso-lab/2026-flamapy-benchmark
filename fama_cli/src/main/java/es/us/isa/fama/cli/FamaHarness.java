package es.us.isa.fama.cli;

import es.us.isa.FAMA.Reasoner.QuestionTrader;
import es.us.isa.FAMA.Reasoner.questions.CoreFeaturesQuestion;
import es.us.isa.FAMA.Reasoner.questions.DetectErrorsQuestion;
import es.us.isa.FAMA.Reasoner.questions.NumberOfProductsQuestion;
import es.us.isa.FAMA.Reasoner.questions.ValidQuestion;
import es.us.isa.FAMA.errors.Error;
import es.us.isa.FAMA.errors.Observation;
import es.us.isa.FAMA.models.FAMAfeatureModel.errors.DeadFeatureError;
import es.us.isa.FAMA.models.FAMAfeatureModel.errors.DeadFeatureObservation;
import es.us.isa.FAMA.models.FAMAfeatureModel.errors.FalseMandatoryError;
import es.us.isa.FAMA.models.FAMAfeatureModel.errors.FalseMandatoryObservation;
import es.us.isa.FAMA.models.featureModel.GenericFeature;
import es.us.isa.FAMA.models.variabilityModel.VariabilityModel;

import java.io.File;
import java.io.PrintWriter;
import java.lang.management.ManagementFactory;
import java.lang.management.ThreadMXBean;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.List;

/**
 * Benchmark harness for FaMa. Speaks the campaign's JSON-lines protocol on stdout:
 *
 * <pre>
 *   {"ev":"ready", ...}                       once, as soon as main() starts
 *   {"ev":"begin","step":"..."}              before every timed step
 *   {"ev":"end","step":"...","status":...}   after it, with wall/CPU time and result
 *   {"ev":"done"}                             when every requested step has run
 * </pre>
 *
 * The parent process enforces the timeout: it kills the JVM when a step does not end in time.
 * One JVM runs every operation and repetition for one model, so repetition 0 is the first
 * (JIT-cold) call and later repetitions are JIT-warm. JVM start-up is reported separately
 * (the "ready" event), so the per-call cost of the old one-JVM-per-call setup can be rebuilt as
 * jvm_startup + setup + parse + rep0.
 *
 * Usage: FamaHarness model.fama Sat4j|Choco reps op1,op2,...
 * Operations: Satisfiable, CoreFeatures, DeadFeatures, FalseOptionalFeatures, ConfigurationsNumber
 */
public class FamaHarness {

    private static final ThreadMXBean THREADS = ManagementFactory.getThreadMXBean();


    /** Exit if the runner dies: an orphaned harness would otherwise run with no timeout. */
    private static void watchParent() {
        final long parent = ProcessHandle.current().parent().map(ProcessHandle::pid).orElse(-1L);
        Thread t = new Thread(() -> {
            while (true) {
                try { Thread.sleep(1000); } catch (InterruptedException e) { return; }
                long now = ProcessHandle.current().parent().map(ProcessHandle::pid).orElse(-1L);
                boolean alive = ProcessHandle.of(parent).map(ProcessHandle::isAlive).orElse(false);
                if (now != parent || !alive) { Runtime.getRuntime().halt(3); }
            }
        });
        t.setDaemon(true);
        t.start();
    }

    public static void main(String[] args) throws Exception {
        watchParent();
        long readyNanos = System.nanoTime();
        emit("{\"ev\":\"ready\",\"jvm_start_epoch_ms\":" + ManagementFactory.getRuntimeMXBean().getStartTime()
                + ",\"jvm_uptime_ms\":" + ManagementFactory.getRuntimeMXBean().getUptime()
                + ",\"java_version\":" + q(System.getProperty("java.version")) + "}");
        if (args.length != 4) {
            emit("{\"ev\":\"fatal\",\"msg\":\"usage: FamaHarness model.fama reasoner reps ops\"}");
            System.exit(1);
        }
        String modelPath = args[0];
        String reasoner = args[1];
        int reps = Integer.parseInt(args[2]);
        String[] ops = args[3].split(",");

        // ---- setup: build the QuestionTrader (loads FaMa's plugin machinery) ----
        final QuestionTrader[] qtHolder = new QuestionTrader[1];
        if (!step("setup", () -> {
            File cfg = writeConfig();
            QuestionTrader qt = new QuestionTrader(cfg.getAbsolutePath());
            qt.setSelectedReasoner(reasoner);
            java.lang.reflect.Field f = QuestionTrader.class.getDeclaredField("selectedReasoner");
            f.setAccessible(true);
            if (f.get(qt) == null) {
                throw new IllegalStateException("reasoner " + reasoner + " not registered");
            }
            qtHolder[0] = qt;
            return null;
        })) {
            emit("{\"ev\":\"done\"}");
            return;
        }
        QuestionTrader qt = qtHolder[0];

        // ---- parse: read the FaMa XML model ----
        final VariabilityModel[] vmHolder = new VariabilityModel[1];
        if (!step("parse", () -> {
            VariabilityModel vm = qt.openFile(modelPath);
            if (vm == null) {
                throw new IllegalStateException("openFile returned null");
            }
            qt.setVariabilityModel(vm);
            vmHolder[0] = vm;
            return null;
        })) {
            emit("{\"ev\":\"done\"}");
            return;
        }
        VariabilityModel vm = vmHolder[0];

        // FaMa translates the model to the solver inside every ask(); there is no separate,
        // reusable transformation step, so no "transform" event is emitted.
        for (String op : ops) {
            for (int rep = 0; rep < reps; rep++) {
                final String o = op;
                boolean ok = step("op:" + op + ":" + rep, () -> runOp(qt, vm, o));
                if (!ok) {
                    break; // an error repeats deterministically; skip the remaining repetitions
                }
            }
        }
        emit("{\"ev\":\"done\"}");
    }

    /** createQuestion returns null when the selected reasoner does not implement the question. */
    private static <T> T question(QuestionTrader qt, String id) {
        @SuppressWarnings("unchecked")
        T qn = (T) qt.createQuestion(id);
        if (qn == null) {
            throw new UnsupportedOperationException("question " + id + " not implemented by this reasoner");
        }
        return qn;
    }

    private static String runOp(QuestionTrader qt, VariabilityModel vm, String op) throws Exception {
        switch (op) {
            case "Satisfiable": {
                ValidQuestion qn = question(qt, "Valid");
                qt.ask(qn);
                return qn.isValid() ? "true" : "false";
            }
            case "ConfigurationsNumber": {
                NumberOfProductsQuestion qn = question(qt, "#Products");
                qt.ask(qn);
                // JSON string: counts can exceed the double range used by JSON numbers
                return q(String.valueOf((long) qn.getNumberOfProducts()));
            }
            case "CoreFeatures": {
                CoreFeaturesQuestion qn = question(qt, "CoreFeatures");
                qt.ask(qn);
                List<String> names = new ArrayList<>();
                for (GenericFeature f : qn.getCoreFeats()) {
                    names.add(f.getName());
                }
                return names(names);
            }
            case "DeadFeatures":
                return detect(qt, vm, true);
            case "FalseOptionalFeatures":
                return detect(qt, vm, false);
            default:
                throw new IllegalArgumentException("unknown operation " + op);
        }
    }

    /** DetectErrors restricted to one kind of observation, so each operation does only its own work. */
    private static String detect(QuestionTrader qt, VariabilityModel vm, boolean dead) throws Exception {
        DetectErrorsQuestion qn = question(qt, "DetectErrors");
        Collection<Observation> all = vm.getObservations();
        List<Observation> selected = new ArrayList<>();
        for (Observation ob : all) {
            if (dead ? ob instanceof DeadFeatureObservation : ob instanceof FalseMandatoryObservation) {
                selected.add(ob);
            }
        }
        qn.setObservations(selected);
        qt.ask(qn);
        List<String> names = new ArrayList<>();
        Collection<Error> errors = qn.getErrors();
        if (errors != null) {
            for (Error e : errors) {
                if (dead && e instanceof DeadFeatureError) {
                    names.add(((DeadFeatureError) e).getDeadFeature().getName());
                } else if (!dead && e instanceof FalseMandatoryError) {
                    names.add(((FalseMandatoryError) e).getFalseMandatoryFeature().getName());
                }
            }
        }
        return names(names);
    }

    // ------------------------------------------------------------------ protocol helpers

    interface Body {
        String run() throws Exception;
    }

    /** Runs one timed step and reports it. Returns false when the step failed. */
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

    private static String names(List<String> names) {
        List<String> sorted = new ArrayList<>(names);
        Collections.sort(sorted);
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < sorted.size(); i++) {
            if (i > 0) sb.append(',');
            sb.append(q(sorted.get(i)));
        }
        return sb.append(']').toString();
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

    private static File writeConfig() throws Exception {
        URL loc = FamaHarness.class.getProtectionDomain().getCodeSource().getLocation();
        String jar = new File(loc.toURI()).getAbsolutePath();
        File cfg = File.createTempFile("FaMaConfig", ".xml");
        cfg.deleteOnExit();
        try (PrintWriter pw = new PrintWriter(cfg, "UTF-8")) {
            pw.println("<?xml version=\"1.0\" encoding=\"UTF-8\"?>");
            pw.println("<questionTrader>");
            pw.println("  <reasoner id=\"Choco\" file=\"" + jar + "\" class=\"es.us.isa.ChocoReasoner.ChocoReasoner\"/>");
            pw.println("  <reasoner id=\"Sat4j\" file=\"" + jar + "\" class=\"es.us.isa.Sat4jReasoner.Sat4jReasoner\"/>");
            pw.println("  <criteriaSelector name=\"Default\" class=\"es.us.isa.FAMA.Reasoner.DefaultCriteriaSelector\"/>");
            pw.println("  <question id=\"Valid\" interface=\"es.us.isa.FAMA.Reasoner.questions.ValidQuestion\"/>");
            pw.println("  <question id=\"#Products\" interface=\"es.us.isa.FAMA.Reasoner.questions.NumberOfProductsQuestion\"/>");
            pw.println("  <question id=\"CoreFeatures\" interface=\"es.us.isa.FAMA.Reasoner.questions.CoreFeaturesQuestion\"/>");
            pw.println("  <question id=\"DetectErrors\" interface=\"es.us.isa.FAMA.Reasoner.questions.DetectErrorsQuestion\"/>");
            pw.println("  <models>");
            pw.println("    <reader extensions=\"xml,fama\" file=\"" + jar + "\" class=\"es.us.isa.FAMA.models.FAMAfeatureModel.fileformats.XMLReader\"/>");
            pw.println("  </models>");
            pw.println("</questionTrader>");
        }
        return cfg;
    }
}
