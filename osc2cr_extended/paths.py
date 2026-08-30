"""
paths.py
========
Locates everything the package needs at runtime — the converter it extends,
esmini's binaries and scenario corpus, its own bundled assets — without
assuming any particular checkout layout.

This is the module that makes ``osc2cr_extended`` relocatable.  The thesis
version of this tool discovered its dependencies by walking up to a
thesis checkout directory and inserting sibling repositories onto
``sys.path``.  Nothing here does that any more:

* the **strategies** (transcription / translation / interpretation and the shared condition
  model) are subpackages of this package, imported normally;
* the **converter** is located through the installed ``osc_cr_converter``
  module, so it works whether this package sits inside the converter's
  repository or is pip-installed alongside it;
* **esmini** is found through :data:`ESMINI_HOME`, overridable by the
  ``OSC2CR_ESMINI_HOME`` environment variable, defaulting to the copy the
  converter already bundles;
* **output** is written under the current working directory, never inside the
  installed package.

Environment overrides
---------------------
``OSC2CR_ESMINI_HOME``   esmini installation (the directory holding ``bin/`` and
                         ``resources/``).
``OSC2CR_OUTPUT_DIR``    where converted bundles are written.
                         Default: ``./osc2cr_output``.
``OSC2CR_FRENETIX_HOME`` Frenetix-Motion-Planner's repository root, for a source
                         checkout that is not pip-installed.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# This package
# ---------------------------------------------------------------------------

#: osc2cr_extended/
PACKAGE_ROOT = Path(__file__).resolve().parent

#: The directory that must be importable for ``import osc2cr_extended`` to
#: work when the package is *not* installed — i.e. the converter repository
#: root once this folder has been dropped into it.  Used as the working
#: directory for the subprocess-isolated re-invocations of ``-m
#: osc2cr_extended`` in pipeline.py and cosim/loop.py.
TOOL_ROOT = PACKAGE_ROOT.parent

#: Module name to re-invoke with ``python -m`` for subprocess isolation.
MODULE_NAME = __name__.split(".")[0]

VIEWER_DIR = PACKAGE_ROOT / "viewer"
WEB_DIR = PACKAGE_ROOT / "web"
DATA_DIR = PACKAGE_ROOT / "data"
CONFIG_DIR = DATA_DIR / "configurations"
BENCHMARK_DIR = PACKAGE_ROOT / "benchmarks"

#: The .xosc corpus shipped with this package.
#:
#: A **fallback**, not a third corpus: it is searched only when the converter's
#: own ``scenarios/`` is absent, i.e. a wheel install with no source checkout
#: next to it.  Discovery otherwise resolves exactly the converter's corpus plus
#: esmini's, which is the corpus every number in the evaluation was measured
#: over — folding these samples in unconditionally would silently grow it and
#: make `convert $(list)` cover more scenarios than the reported results.
LOCAL_XOSC = DATA_DIR / "scenarios"


# ---------------------------------------------------------------------------
# commonroad-openscenario-converter
# ---------------------------------------------------------------------------

def _find_converter() -> Optional[Path]:
    """
    Locate the converter's package directory (``.../osc_cr_converter``).

    Resolved from the import system rather than the filesystem layout, so it is
    correct for an editable checkout, a wheel install, and this package sitting
    inside the converter repository alike.
    """
    try:
        spec = importlib.util.find_spec("osc_cr_converter")
    except (ImportError, ValueError):  # pragma: no cover - broken install
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(list(spec.submodule_search_locations)[0]).resolve()


#: ``.../osc_cr_converter`` — the converter *package*, or None when not installed.
CONVERTER_PKG_DIR = _find_converter()

#: The converter's repository/install root — the directory holding
#: ``osc_cr_converter/`` and, in a source checkout, ``scenarios/``.
CONVERTER_ROOT = CONVERTER_PKG_DIR.parent if CONVERTER_PKG_DIR else None

# Backwards-compatible alias: the thesis code called the repository root
# CONVERTER_PKG.
CONVERTER_PKG = CONVERTER_ROOT

#: The converter's own .xosc corpus.  Only present in a source checkout — a
#: wheel install has no ``scenarios/`` directory, which is why every lookup
#: below tolerates it being missing.
BUNDLED_XOSC = (
    CONVERTER_ROOT / "scenarios" / "from_esmini" / "xosc"
    if CONVERTER_ROOT else PACKAGE_ROOT / "_missing_converter"
)


# ---------------------------------------------------------------------------
# esmini
# ---------------------------------------------------------------------------

def _vendored_esmini():
    """The esmini the converter bundles for its own wrapper (v2.29.3)."""
    if CONVERTER_PKG_DIR:
        yield CONVERTER_PKG_DIR / "wrapper" / "esmini" / "esmini_v2.29.3" / "esmini"


def _standalone_esmini():
    """A full esmini checkout beside the converter (the thesis layout)."""
    if CONVERTER_ROOT:
        for sibling in ("esmini", "esmini-master/esmini", "esmini-master"):
            yield CONVERTER_ROOT.parent / sibling


def _esmini_candidates(library: Optional[str] = None):
    """
    esmini installations to try, best first.

    The converter's vendored v2.29.3 wins: it is the build the converter itself
    runs, it is present in every deployment this package supports, and both
    bindings target it -- ``cosim/`` for the scenario engine's callback
    signatures, ``roadmanager.py`` for the RoadManager's structure layout.  A
    standalone checkout is a fallback for installs that lack the vendored copy.

    ``library`` is accepted so callers can express which library they want; the
    ordering no longer depends on it, since ``roadmanager.py`` detects the ABI
    of whatever it loads.  ``OSC2CR_ESMINI_HOME`` overrides everything.
    """
    env = os.environ.get("OSC2CR_ESMINI_HOME")
    if env:
        yield Path(env).expanduser().resolve()

    yield from _vendored_esmini()
    yield from _standalone_esmini()


def _resolve_esmini(marker: str) -> Optional[Path]:
    """First candidate installation containing ``marker`` (a relative dir)."""
    for candidate in _esmini_candidates():
        if (candidate / marker).is_dir():
            return candidate
    return None


#: esmini installation providing the shared libraries, or None when none was
#: found.  The converter's bundled copy wins by default, which pins the
#: co-simulation to the same engine build the converter itself runs.
ESMINI_HOME = _resolve_esmini("bin")

#: esmini's own scenario corpus.  Resolved *separately* from ESMINI_HOME: the
#: converter bundles esmini's binaries without its ``resources/``, so the
#: installation that supplies the libraries is usually not the one that supplies
#: the scenarios.  Falls back to a non-existent path, which every lookup below
#: tolerates, when no full esmini checkout is present.
_ESMINI_RESOURCES = _resolve_esmini("resources/xosc")
ESMINI_XOSC = (
    _ESMINI_RESOURCES / "resources" / "xosc"
    if _ESMINI_RESOURCES else PACKAGE_ROOT / "_missing_esmini_resources"
)


def esmini_lib(name: str) -> Optional[Path]:
    """
    Absolute path to one of esmini's shared libraries, or None.

    ``name`` is the bare library name, e.g. ``"libesminiLib.so"`` (the
    scenario engine, used by the co-simulation) or ``"libesminiRMLib.so"``
    (the RoadManager, used for lane-position queries).
    """
    for candidate in _esmini_candidates(name):
        lib = candidate / "bin" / name
        if lib.is_file():
            return lib.resolve()
    return None


# ---------------------------------------------------------------------------
# Frenetix (optional planner backend)
# ---------------------------------------------------------------------------

def frenetix_home() -> Optional[str]:
    """
    Frenetix-Motion-Planner's repository root, or None.

    Frenetix resolves its logging and results directories relative to a "work
    directory" that must be the repository root, not the installed package —
    so this returns the parent of the installed ``frenetix_motion_planner``
    package, or whatever ``OSC2CR_FRENETIX_HOME`` points at.
    """
    env = os.environ.get("OSC2CR_FRENETIX_HOME")
    if env:
        return str(Path(env).expanduser().resolve())
    try:
        spec = importlib.util.find_spec("frenetix_motion_planner")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    return str(Path(list(spec.submodule_search_locations)[0]).resolve().parent)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _default_output_dir() -> Path:
    """
    Where converted bundles go.

    Deliberately *not* inside the package: an installed package may live in a
    read-only site-packages, and writing scenario output into it would make the
    tool's results depend on where pip put it.
    """
    env = os.environ.get("OSC2CR_OUTPUT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.cwd() / "osc2cr_output").resolve()


OUTPUT_DIR = _default_output_dir()


def set_output_dir(path) -> Path:
    """Point :data:`OUTPUT_DIR` somewhere else (used by the CLI's --output)."""
    global OUTPUT_DIR
    OUTPUT_DIR = Path(path).expanduser().resolve()
    return OUTPUT_DIR


# ---------------------------------------------------------------------------
# Interpreter check and bootstrap
# ---------------------------------------------------------------------------

#: commonroad-openscenario-converter requires Python 3.9 or newer, so this
#: package does too.  Developed and tested on 3.11.
MIN_PYTHON = (3, 9)

_BOOTSTRAPPED = False

#: Problems found while setting up, in the order they were detected.  These are
#: not fatal — conversion still runs — but they degrade the result, so every
#: entry point prints them rather than letting the damage be silent.
WARNINGS: list = []


class UnsupportedPythonError(RuntimeError):
    """Raised when the interpreter is older than the converter supports."""


def require_python() -> None:
    """
    Refuse to run on an interpreter older than :data:`MIN_PYTHON`.

    Failing here is deliberate.  An older interpreter often *looks* usable —
    commonroad, crdesigner and the converter may all import under Python 3.8 —
    but that crdesigner predates ``crdesigner.common.config``, so the
    geo-reprojection guard cannot be applied and **every** scenario silently
    converts without a lanelet network.  A clear refusal beats hours of empty
    maps.
    """
    if sys.version_info >= MIN_PYTHON:
        return

    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    needed = ".".join(str(p) for p in MIN_PYTHON)
    raise UnsupportedPythonError(
        f"{MODULE_NAME} needs Python {needed}+ (running {running} from "
        f"{sys.executable}).\n"
        f"commonroad-openscenario-converter itself requires {needed}+, and the "
        f"older crdesigner installed for {running} converts every scenario "
        f"without a road network."
    )


def bootstrap() -> None:
    """
    Check the interpreter, verify the converter is importable, and disable
    crdesigner's geo re-projection so lanelets and esmini trajectories share one
    coordinate frame (see ``strategies/shared/road_network.py``).

    Idempotent — safe to call from every entry point.
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return

    require_python()

    if CONVERTER_PKG_DIR is None:
        WARNINGS.append(
            "commonroad-openscenario-converter is not importable "
            "(no 'osc_cr_converter' module). Conversion will fail; install it "
            "with `pip install commonroad-openscenario-converter`, or drop this "
            "package into the converter's repository root."
        )

    # Without this, crdesigner re-projects lanelet geometry out of the frame
    # esmini reports positions in — and on crdesigner releases without the
    # config knob it tries to build a CRS from the .xodr's geoReference, which
    # fails outright when the referenced geoid grid is not installed. A failure
    # here means empty lanelet networks, so it is reported rather than hidden.
    try:
        from .strategies.shared.road_network import disable_lanelet_geo_reprojection
        disable_lanelet_geo_reprojection()
    except Exception as exc:  # noqa: BLE001
        WARNINGS.append(
            f"could not disable crdesigner's geo re-projection "
            f"({type(exc).__name__}: {exc}). Lanelet networks will very likely "
            f"be empty, and lanelets that do convert may not line up with the "
            f"trajectories."
        )

    _BOOTSTRAPPED = True


def warn_if_degraded(printer=print) -> bool:
    """Report bootstrap problems once.  True when something was reported."""
    for message in WARNINGS:
        printer(f"⚠ {message}")
    return bool(WARNINGS)


# ---------------------------------------------------------------------------
# Scenario lookup
# ---------------------------------------------------------------------------

def _corpora(prefer: str = "bundled"):
    """
    Corpora searched for a bare scenario name, in precedence order.

    :data:`LOCAL_XOSC` joins only as a fallback — see its docstring.
    """
    primary = ((ESMINI_XOSC, BUNDLED_XOSC) if prefer == "esmini"
               else (BUNDLED_XOSC, ESMINI_XOSC))
    if any(c.is_dir() for c in primary):
        return primary
    return primary + (LOCAL_XOSC,)


def colliding_stems() -> dict:
    """
    Scenario names that exist in *both* the converter's and esmini's corpora
    with **different content**.

    The two corpora are not copies of each other.  ``acc-test.xosc`` is the
    worst case: the converter's copy drives the ego with a ``UDPDriverController``
    and no Init speed, so headless — with no UDP client attached — the ego never
    moves at all, while esmini's copy uses an inline ``ACCController`` with an
    Init speed of 120 km/h and actually demonstrates adaptive cruise control.
    Same name, same command, entirely different scenario.

    Returns ``stem -> (bundled_path, esmini_path)``.
    """
    clashes = {}
    if not (BUNDLED_XOSC.is_dir() and ESMINI_XOSC.is_dir()):
        return clashes
    for a in sorted(BUNDLED_XOSC.glob("*.xosc")):
        b = ESMINI_XOSC / a.name
        if not b.exists():
            continue
        try:
            if a.read_bytes() != b.read_bytes():
                clashes[a.stem] = (a.resolve(), b.resolve())
        except OSError:
            continue
    return clashes


def resolve_xosc(name_or_path: str, prefer: str = "bundled") -> Path:
    """
    Resolve a scenario reference to a concrete .xosc path.

    Accepts a filesystem path, or a bare name looked up in the converter's
    bundled corpus, esmini's resources, and this package's own
    ``data/scenarios``.  ``prefer`` selects which corpus wins a name collision —
    ``"bundled"`` (the default, and what every existing bundle was built with)
    or ``"esmini"``.

    A bare name is **ambiguous** whenever both corpora hold that name with
    different content (see :func:`colliding_stems`).  Picking one silently is
    how a converted `acc-test` came to show a stationary ego while esmini's own
    demo shows adaptive cruise control, so the choice is reported through
    :data:`WARNINGS` rather than made quietly.  Pass a full path to be explicit.
    """
    p = Path(name_or_path)
    if p.exists():
        return p.resolve()

    stem = p.stem if p.suffix else p.name
    clash = colliding_stems().get(stem)

    for corpus in _corpora(prefer):
        candidate = corpus / f"{stem}.xosc"
        if not candidate.exists():
            continue
        if clash is not None and corpus in (BUNDLED_XOSC, ESMINI_XOSC):
            other = clash[1] if corpus == BUNDLED_XOSC else clash[0]
            WARNINGS.append(
                f"'{stem}' exists in both corpora with different content — "
                f"converting {candidate.resolve()} and ignoring {other}. "
                f"They are not the same scenario; pass a full path to choose."
            )
        return candidate.resolve()

    searched = ", ".join(str(c) for c in _corpora(prefer))
    raise FileNotFoundError(
        f"No .xosc found for '{name_or_path}'. Looked in {searched}."
    )


def available_xosc() -> dict:
    """
    Map scenario stem → path for every .xosc in the known corpora.

    Keyed by file stem, so a name present in two corpora resolves to one file —
    see :func:`colliding_stems` for the ones where that choice matters.
    """
    found: dict = {}
    # Listed last wins, so the converter's corpus takes precedence — it is what
    # the benchmark numbers and the committed bundles were produced from.
    for corpus in reversed(_corpora()):
        if not corpus.is_dir():
            continue
        for f in sorted(corpus.glob("*.xosc")):
            found[f.stem] = f.resolve()
    return found
