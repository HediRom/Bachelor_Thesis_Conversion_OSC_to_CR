"""
osc2cr_extended — OpenSCENARIO → CommonRoad conversion that keeps the triggers.

An extension package for `commonroad-openscenario-converter
<https://commonroad.in.tum.de/tools/openscenario-converter>`_.  The stock
converter flattens a scenario: esmini evaluates every trigger and only
trajectories survive.  This package converts the same files while preserving
the storyboard's conditional structure, writes it *into* the CommonRoad XML,
replays it, co-simulates it against a motion planner, and serves the result to
an interactive viewer.

Install it beside the converter, or drop this folder into the converter's
repository root — it has no dependency on any particular checkout layout.

Entry points
------------
    osc2cr-ext convert   <scenario>...    convert and write bundles
    osc2cr-ext cosim     <bundle>         closed-loop run against a planner
    osc2cr-ext benchmark [scenario...]    timed batch + report
    osc2cr-ext serve                      interactive viewer on :8000
    osc2cr-ext list                       available .xosc corpora

``python -m osc2cr_extended <command>`` works identically and needs no install.

Layout
------
:mod:`~.pipeline`    the conversion itself, stage-timed
:mod:`~.embed`       reading/writing the ``<osc:triggers>`` block in CR XML
:mod:`~.live`        replaying a converted scenario with the triggers armed
:mod:`~.strategies`  the three representation strategies + condition taxonomy
:mod:`~.cosim`       closed-loop esmini ↔ planner co-simulation
:mod:`~.server`      HTTP server backing ``viewer/``
:mod:`~.paths`       dependency and asset discovery (see its docstring for the
                     environment variables that override it)
"""
from .paths import UnsupportedPythonError, require_python

# Checked before the submodules below, which pull in commonroad, crdesigner and
# the converter: on an unsupported interpreter that import chain either fails
# obscurely or — worse — succeeds and converts every scenario without a road
# network. `raise SystemExit(msg)` prints the message without a traceback.
try:
    require_python()
except UnsupportedPythonError as _exc:  # pragma: no cover - environment guard
    raise SystemExit(f"error: {_exc}")

from .embed import embed_triggers, extract_triggers, has_triggers, strip_triggers
from .pipeline import (
    ConversionResult, StageTimings, TriggerPreservingConverter, convert,
)

__all__ = [
    "ConversionResult",
    "StageTimings",
    "TriggerPreservingConverter",
    "convert",
    "embed_triggers",
    "extract_triggers",
    "has_triggers",
    "strip_triggers",
]

__version__ = "1.0.0"
