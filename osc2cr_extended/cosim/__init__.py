"""
Closed-loop co-simulation between esmini and a CommonRoad motion planner.

The conversion in :mod:`osc2cr_extended.pipeline` is open-loop: esmini plays the
scenario, the converter records it.  Here the ego is taken away from esmini and
driven by a planner instead, tick by tick, while the rest of the storyboard —
and its triggers — keeps running inside esmini.  That is what makes a converted
scenario a *test* rather than a recording: the triggers fire against whatever
the planner actually does.

Layout
------
:mod:`~.esmini_interface`   ctypes wrapper exposing esmini's step / read / write
                            primitives (the converter's own wrapper only runs a
                            scenario to completion in one go).
:mod:`~.loop`               The trigger-aware loop used by the CLI's ``cosim``
                            command: runs either driver, records a differential
                            between them, writes the result back to CommonRoad.
                            Re-exported here, so ``from osc2cr_extended.cosim
                            import run_cosim`` works.
:mod:`~.reactive_loop`,     The standalone reactive-planner co-simulation and
:mod:`~.cosimulation`,      its parameter sweep.
:mod:`~.scenario_sweep`
:mod:`~.frenetix`           The same loop driven by Frenetix-Motion-Planner.

Both planner backends are optional: the modules that need them import them
lazily, so ``esmini``-driven co-simulation works with neither installed.
"""
from .loop import (  # noqa: F401
    ExternalizationReport,
    ObservedEsmini,
    RecordingExecutor,
    cosim_isolated,
    differential,
    ego_maneuver_groups,
    externalize_ego,
    run_cosim,
    stage_external_scenario,
    write_cosim_commonroad,
)

__all__ = [
    "ExternalizationReport",
    "ObservedEsmini",
    "RecordingExecutor",
    "cosim_isolated",
    "differential",
    "ego_maneuver_groups",
    "externalize_ego",
    "run_cosim",
    "stage_external_scenario",
    "write_cosim_commonroad",
]
