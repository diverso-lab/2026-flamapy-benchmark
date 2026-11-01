#!/usr/bin/env bash
# =============================================================================
# build_java.sh - build the two Java baselines used by the campaign
#
#   * FaMa       -> fama_cli/target/fama-cli-1.0.0-jar-with-dependencies.jar
#                   (contains FamaCLI, the legacy one-call wrapper, and FamaHarness)
#   * FeatureIDE -> featureide_cli/build/FideHarness.class + featureide_cli/lib/*.jar
#
# Portable (Linux and macOS): file patching is done with Python, not `sed -i`.
# Needs: git, curl, a JDK 11+ (`java`, `javac`), Maven 3.6+, python3.
# Idempotent: re-running skips what is already built. FORCE=1 rebuilds everything.
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="${ROOT}/.build"
FAMA_SRC="${BUILD}/fama_src"
FAMA_REPO="${FAMA_REPO:-https://github.com/diverso-lab/FaMA}"
FIDE_VERSION="${FIDE_VERSION:-v3.12.0}"
FIDE_DIR="${ROOT}/featureide_cli"
PY="${PYTHON:-python3}"
FORCE="${FORCE:-0}"

info() { echo "[build_java] $*"; }
die()  { echo "[build_java] ERROR: $*" >&2; exit 1; }

for tool in git curl java javac mvn "${PY}"; do
    command -v "${tool}" >/dev/null 2>&1 || die "${tool} not found on PATH"
done
mkdir -p "${BUILD}"

# ----------------------------------------------------------------------------- FaMa
FAMA_JAR="${ROOT}/fama_cli/target/fama-cli-1.0.0-jar-with-dependencies.jar"
if [ "${FORCE}" = "1" ] || [ ! -f "${FAMA_JAR}" ]; then
    if [ ! -d "${FAMA_SRC}/.git" ]; then
        info "cloning FaMa from ${FAMA_REPO}"
        git clone -q --depth 1 "${FAMA_REPO}" "${FAMA_SRC}"
    fi
    info "FaMa source commit: $(git -C "${FAMA_SRC}" rev-parse HEAD)"

    info "patching FaMa POMs and sources"
    FAMA_SRC="${FAMA_SRC}" "${PY}" - <<'PYEOF'
import os, pathlib, re
src = pathlib.Path(os.environ["FAMA_SRC"])

# Java 5 source/target levels are rejected by modern JDKs.
for pom in src.rglob("pom.xml"):
    text = pom.read_text(encoding="utf-8", errors="replace")
    new = text.replace("<source>5</source>", "<source>8</source>").replace("<target>5</target>", "<target>8</target>")
    new = new.replace("<source>1.5</source>", "<source>8</source>").replace("<target>1.5</target>", "<target>8</target>")
    if new != text:
        pom.write_text(new, encoding="utf-8")

def add_deps(rel, marker, deps):
    pom = src / rel
    text = pom.read_text(encoding="utf-8")
    if marker in text:
        return
    pom.write_text(text.replace("</dependencies>", deps + "  </dependencies>", 1), encoding="utf-8")

dep = lambda g, a, v: f"    <dependency><groupId>{g}</groupId><artifactId>{a}</artifactId><version>{v}</version></dependency>\n"
add_deps("FaMaFeatureModel/pom.xml", "<artifactId>antlr</artifactId>",
         dep("antlr", "antlr", "2.7.7") + dep("fmapi", "Fmapi", "1.0") + dep("net.sourceforge.javacsv", "javacsv", "1.0"))
add_deps("reasoner_choco_2/pom.xml", "<artifactId>choco</artifactId>", dep("choco", "choco", "2.1.0"))
add_deps("reasoner_sat4j/pom.xml", "<artifactId>org.sat4j.core</artifactId>",
         dep("org.ow2.sat4j", "org.sat4j.core", "2.3") + dep("org.ow2.sat4j", "org.sat4j.maxsat", "2.3")
         + dep("org.ow2.sat4j", "sat4j-maxsat", "2.3"))

choco_pom = src / "reasoner_choco_2/pom.xml"
text = choco_pom.read_text(encoding="utf-8")
if "ISO-8859-1" not in text and "<properties>" in text:
    choco_pom.write_text(text.replace("<properties>", "<properties>\n    <project.build.sourceEncoding>ISO-8859-1</project.build.sourceEncoding>", 1), encoding="utf-8")

# ANTLR-generated token interfaces that are not committed upstream (plain-text format only;
# never used at run time, the harness reads FaMa XML).
plain = src / "FaMaFeatureModel/src/es/us/isa/FAMA/models/FAMAfeatureModel/fileformats/plain"
common = ("int EOF=1; int NULL_TREE_LOOKAHEAD=3; int SALTO=4; int BLANCO=5; int LETRA=6; int BARRA_BAJA=7; "
          "int GUION=8; int DIGITO=9; int COMILLA=10; int PUNTO=11; int ALMOADILLA=12; int LIT_STRING=13; "
          "int NUMERO=14; int LIT_ENTERO=15; int MAS=16; int COMA=17; int PyC=18; int DOSPUNTOS=19; "
          "int PARENTESIS_ABRIR=20; int PARENTESIS_CERRAR=21; int LLAVE_ABRIR=22; int LLAVE_CERRAR=23; "
          "int CORCHETE_ABRIR=24; int CORCHETE_CERRAR=25; int VIRGULA=26; int VERSION=27; int COMENT_LIN=28; "
          "int IDENT=29; int SECCION_RELACIONES=30; int SECCION_CONSTRAINTS=31;")
extra = ("int EXCLUDES=32; int FEATURE_MODEL=33; int CONSTRAINTS=34; int CONSTRAINT=35; int FEATURE=36; "
         "int DOMINIO=37; int LIT_REAL=38; int RELACIONES=39; int RELACION=40; int ATRIBUTO=41; int INTEGER=42; "
         "int ENUM=43; int DEF_VALUE=44; int NULL_VALUE=45; int VALORES=46; int RANGOS=47; int RANGO=48; "
         "int CARDINALIDAD=49; int FEATURES=50; int INVARIANTES=51; int REQUIRES=52;")
pkg = "package es.us.isa.FAMA.models.FAMAfeatureModel.fileformats.plain;\n"
(plain / "AnalexTokenTypes.java").write_text(pkg + "public interface AnalexTokenTypes { " + common + " }\n")
(plain / "FaMaTreeParserTokenTypes.java").write_text(pkg + "public interface FaMaTreeParserTokenTypes { " + common + " " + extra + " }\n")
PYEOF

    info "installing FaMa's bundled jars into the local Maven repository"
    inst() { mvn -q --batch-mode install:install-file -Dpackaging=jar -Dfile="$1" -DgroupId="$2" -DartifactId="$3" -Dversion="$4"; }
    inst "${FAMA_SRC}/FaMaFeatureModel/resources/lib/antlr.jar"   antlr antlr 2.7.7
    inst "${FAMA_SRC}/FaMaFeatureModel/resources/lib/Fmapi.jar"   fmapi Fmapi 1.0
    inst "${FAMA_SRC}/FaMaFeatureModel/resources/lib/javacsv.jar" net.sourceforge.javacsv javacsv 1.0
    inst "${FAMA_SRC}/reasoner_choco_2/resources/lib/choco-2.1.0-basic+old.jar" choco choco 2.1.0
    inst "${FAMA_SRC}/reasoner_sat4j/resources/lib/org.sat4j.core.jar"   org.ow2.sat4j org.sat4j.core 2.3
    inst "${FAMA_SRC}/reasoner_sat4j/resources/lib/org.sat4j.maxsat.jar" org.ow2.sat4j org.sat4j.maxsat 2.3
    inst "${FAMA_SRC}/reasoner_sat4j/resources/lib/sat4j-maxsat.jar"     org.ow2.sat4j sat4j-maxsat 2.3

    for module in FaMaSDK FaMaFeatureModel reasoner_choco_2 reasoner_sat4j; do
        info "building FaMa module ${module}"
        (cd "${FAMA_SRC}/${module}" && mvn -q --batch-mode install -DskipTests)
    done
    info "building the FaMa harness fat jar"
    (cd "${ROOT}/fama_cli" && mvn -q --batch-mode package -DskipTests)
    [ -f "${FAMA_JAR}" ] || die "FaMa jar not produced at ${FAMA_JAR}"
fi
info "FaMa ready: ${FAMA_JAR}"

# ----------------------------------------------------------------------------- FeatureIDE
mkdir -p "${FIDE_DIR}/lib" "${FIDE_DIR}/build"
FIDE_JAR="${FIDE_DIR}/lib/de.ovgu.featureide.lib.fm-${FIDE_VERSION}.jar"
if [ ! -f "${FIDE_JAR}" ]; then
    info "downloading FeatureIDE ${FIDE_VERSION} library"
    curl -fsSL -o "${FIDE_JAR}" \
        "https://github.com/FeatureIDE/FeatureIDE/releases/download/${FIDE_VERSION}/de.ovgu.featureide.lib.fm-${FIDE_VERSION}.jar"
fi
for dep in SPLCAT.jar antlr-3.4.jar commons-math-2.2.jar org.ow2.sat4j.core-2.3.6.jar org.sat4j.pb.jar splar.jar uvl-parser.jar; do
    if [ ! -f "${FIDE_DIR}/lib/${dep}" ]; then
        info "downloading FeatureIDE dependency ${dep}"
        curl -fsSL -o "${FIDE_DIR}/lib/${dep}" \
            "https://raw.githubusercontent.com/FeatureIDE/FeatureIDE/${FIDE_VERSION}/plugins/de.ovgu.featureide.fm.core/lib/${dep}"
    fi
done
info "compiling FideHarness"
javac -nowarn -encoding UTF-8 -cp "${FIDE_DIR}/lib/*" -d "${FIDE_DIR}/build" "${FIDE_DIR}/src/FideHarness.java"
info "FeatureIDE ready: ${FIDE_DIR}/build (classpath: build:lib/*)"

# Record exactly what was built, for the replication package.
{
    echo "fama_commit=$(git -C "${FAMA_SRC}" rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "featureide_version=${FIDE_VERSION}"
    echo "java=$(java -version 2>&1 | head -1)"
    echo "built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "${BUILD}/java_versions.txt"
info "versions recorded in ${BUILD}/java_versions.txt"
