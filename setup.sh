#!/usr/bin/env bash
#
# One-shot installer for osc2cr_extended.
#
#     ./setup.sh                 full install (conversion + co-simulation)
#     ./setup.sh --no-cosim      skip the reactive planner
#     ./setup.sh --dev           also install pytest / playwright
#     ./setup.sh --lock          use requirements-lock.txt (exact versions)
#
# Run it inside an activated Python 3.11 environment — it installs into
# whichever interpreter `python3` currently resolves to, and refuses to touch a
# system interpreter it does not own.  See README.md for the conda/venv recipe.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

COSIM=1
DEV=0
LOCK=0
for arg in "$@"; do
    case "$arg" in
        --no-cosim) COSIM=0 ;;
        --dev)      DEV=1 ;;
        --lock)     LOCK=1 ;;
        -h|--help)  sed -n '2,14p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

PY="${PYTHON:-python3}"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
say "Checking the interpreter"
# ---------------------------------------------------------------------------
"$PY" - <<'EOF'
import sys
major, minor = sys.version_info[:2]
print(f"{sys.version.split()[0]}  {sys.executable}")
if (major, minor) < (3, 9):
    sys.exit("Python 3.9+ is required. On 3.8 every scenario converts with an "
             "empty lanelet network — see README.md.")
if (major, minor) >= (3, 12):
    sys.exit("Python 3.12+ is not supported: commonroad-reactive-planner pins "
             "<3.12. Use 3.11.")
if (major, minor) != (3, 11):
    print("  note: 3.11 is what the shipped results were measured on.")
EOF

if [ -z "${VIRTUAL_ENV:-}${CONDA_PREFIX:-}" ]; then
    echo
    echo "  No virtualenv or conda environment is active."
    echo "  This would install ~30 packages into the system interpreter."
    echo "  Create one first (README.md, step 1), then re-run ./setup.sh."
    exit 1
fi

# ---------------------------------------------------------------------------
say "Installing Python dependencies"
# ---------------------------------------------------------------------------
if [ "$LOCK" = 1 ]; then
    "$PY" -m pip install -r requirements-lock.txt
else
    "$PY" -m pip install -r requirements.txt
    if [ "$COSIM" = 1 ]; then
        "$PY" -m pip install -r requirements-cosim.txt
    fi
fi
if [ "$DEV" = 1 ]; then
    "$PY" -m pip install -r requirements-dev.txt
fi

# ---------------------------------------------------------------------------
say "Installing the patched dependencies from ./deps"
# ---------------------------------------------------------------------------
# --no-deps on both: their pinned ranges are already satisfied by the
# requirements files above, and letting pip re-resolve them pulls in versions
# that conflict (the reactive planner's `triangle` build, in particular, is not
# needed and does not build on every machine).
#
# Both checkouts already carry the patches in osc2cr_extended/patches/ —
# 0001+0002 in the converter, 0003 in the planner.  Nothing to apply by hand.
"$PY" -m pip install -e ./deps/commonroad-openscenario-converter --no-deps
if [ "$COSIM" = 1 ]; then
    "$PY" -m pip install -e ./deps/reactive-planner --no-deps
fi

# ---------------------------------------------------------------------------
say "Installing osc2cr_extended"
# ---------------------------------------------------------------------------
# Editable, so `osc2cr-ext` is on PATH while the source you read in
# ./osc2cr_extended is the source that runs.
"$PY" -m pip install -e ./osc2cr_extended --no-deps

# ---------------------------------------------------------------------------
say "Fetching esmini"
# ---------------------------------------------------------------------------
"$PY" scripts/fetch_esmini.py

# ---------------------------------------------------------------------------
say "Verifying"
# ---------------------------------------------------------------------------
"$PY" scripts/verify_install.py
