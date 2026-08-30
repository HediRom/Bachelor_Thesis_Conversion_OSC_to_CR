"""
coverage.py
===========
Measures how much of a scenario's trigger logic actually survived parsing.

Why this exists
---------------
The condition model in ``strategies/shared/condition_model.py`` covers the common
OpenSCENARIO condition types, not all of them.  When the parser meets a type it
does not model — ``ReachPositionCondition``, ``EndOfRoadCondition``,
``CollisionCondition``, … — the condition is dropped and the enclosing event is
kept with an *empty* start trigger.

That is dangerous downstream, because an empty trigger is unconditionally true
per the OpenSCENARIO spec, and the Interpretation evaluator implements exactly that
rule.  An event whose conditions were silently dropped therefore fires on the
first time step and looks like a successfully reconstructed trigger.
``esmini/resources/xosc/lane_change_simple.xosc`` is the concrete case: three
events, all of them position-based, none of them modelled — and a naive replay
reports seven confident fires that mean nothing.

So before trusting any trigger count, compare it against the source file:

    coverage = condition_coverage(xosc_path, storyboard)
    coverage["unsupported"]   → {} once conditions_ext is attached
    coverage["declared_only"] → {"EndOfRoadCondition": 3}
    coverage["preserved_pct"] → 100.0

:func:`unconditional_events` names the events whose triggers vanished, so the
replay can flag their fires instead of counting them as real.

``conditions_ext`` closes most of this gap — with it attached the corpus goes
from 84% modelled to 100% preserved / 95.3% evaluable — but this module stays
the arbiter, because "the parser produced something" and "the something can be
computed" are different claims and both need reporting.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Set

# Condition types the shared parser models (shared/storyboard_parser.py)
SUPPORTED_TYPES: Set[str] = {
    "SimulationTimeCondition",
    "SpeedCondition",
    "TraveledDistanceCondition",
    "StoryboardElementStateCondition",
    "RelativeDistanceCondition",
    "RelativeSpeedCondition",
    "TimeHeadwayCondition",
    "TimeToCollisionCondition",
}

# Wrapper elements that are not conditions themselves
_WRAPPERS = {
    "ByEntityCondition", "ByValueCondition", "EntityCondition",
    "TriggeringEntities", "EntityRef", "Position",
}


def _condition_type(condition_el: ET.Element) -> str:
    """
    Name the concrete condition type inside a ``<Condition>`` element.

    The type is the first descendant that is not one of the structural
    wrappers, e.g. ``ByEntityCondition/EntityCondition/TimeHeadwayCondition``
    → ``TimeHeadwayCondition``.
    """
    for child in condition_el.iter():
        if child is condition_el:
            continue
        if child.tag in _WRAPPERS:
            continue
        return child.tag
    return "UnknownCondition"


def source_conditions(xosc_path: str | Path) -> List[Dict[str, str]]:
    """Every ``<Condition>`` in the source file, with its concrete type."""
    try:
        root = ET.parse(Path(xosc_path)).getroot()
    except (ET.ParseError, OSError):
        return []

    found: List[Dict[str, str]] = []
    for cond_el in root.iter("Condition"):
        found.append({
            "name": cond_el.get("name", ""),
            "type": _condition_type(cond_el),
            "edge": cond_el.get("conditionEdge", ""),
        })
    return found


def parsed_condition_types(storyboard: Any) -> List[str]:
    """Concrete type names of every condition the parser kept."""
    from .live import collect_conditions

    return [ref.ctype for ref in collect_conditions(storyboard)]


def unconditional_events(storyboard: Any) -> List[str]:
    """
    Events left with an empty start trigger.

    Such an event fires on the first evaluated step because an empty trigger is
    unconditionally true.  That is correct when the source really declares no
    start trigger, and misleading when the conditions were dropped — compare
    with :func:`source_conditions` to tell the two apart.
    """
    names: List[str] = []
    for story in storyboard.stories:
        for act in story.acts:
            for mg in act.maneuver_groups:
                for maneuver in mg.maneuvers:
                    for event in maneuver.events:
                        if not event.start_trigger:
                            names.append(event.name)
    return names


def condition_coverage(xosc_path: str | Path, storyboard: Any) -> Dict[str, Any]:
    """
    Compare the conditions in the source file with those the model kept.

    Three buckets, deliberately kept apart:

    ``modelled``
        parsed *and* computable against a converted scenario;
    ``declared``
        parsed and preserved, but not computable here — the type needs data the
        CommonRoad conversion does not carry (road topology, signal state).
        These keep their event honestly un-firable;
    ``unsupported``
        not represented at all.  These are the dangerous ones: dropping every
        condition of a trigger leaves it empty, and an empty trigger is
        unconditionally true in OpenSCENARIO.
    """
    from .conditions_ext import DECLARED_ONLY, EVALUABLE

    src = source_conditions(xosc_path)
    parsed = parsed_condition_types(storyboard)

    src_types: Dict[str, int] = {}
    for c in src:
        src_types[c["type"]] = src_types.get(c["type"], 0) + 1

    modelled_types = set(SUPPORTED_TYPES) | set(EVALUABLE)

    modelled: Dict[str, int] = {}
    declared: Dict[str, int] = {}
    unsupported: Dict[str, int] = {}
    for ctype, count in src_types.items():
        if ctype in modelled_types:
            modelled[ctype] = count
        elif ctype in DECLARED_ONLY:
            declared[ctype] = count
        else:
            unsupported[ctype] = count

    n_src = len(src)
    n_parsed = len(parsed)
    n_declared = sum(declared.values())
    n_modelled = sum(modelled.values())

    empty_events = unconditional_events(storyboard)

    return {
        "source_conditions": n_src,
        "parsed_conditions": n_parsed,
        "source_types": src_types,
        # kept for compatibility with earlier bundles/reports
        "unsupported": unsupported,
        "unsupported_conditions": sum(unsupported.values()),
        "declared_only": declared,
        "declared_conditions": n_declared,
        "modelled_conditions": n_modelled,
        "preserved_pct": round(100.0 * n_parsed / n_src, 1) if n_src else 100.0,
        "evaluable_pct": round(100.0 * n_modelled / n_src, 1) if n_src else 100.0,
        "declared_reasons": {t: DECLARED_ONLY[t] for t in declared},
        "events_without_trigger": empty_events,
        # An event with no parsed trigger in a file that *did* declare
        # conditions fires unconditionally — its fires are not evidence.
        "unconditional_fires_possible": bool(empty_events and unsupported),
    }


def summary(coverage: Dict[str, Any]) -> str:
    """One-line human-readable coverage statement."""
    n_src = coverage["source_conditions"]
    n_parsed = coverage["parsed_conditions"]
    line = f"{n_parsed}/{n_src} conditions preserved ({coverage['preserved_pct']}%)"
    if coverage.get("declared_only"):
        types = ", ".join(
            f"{t}×{n}" for t, n in sorted(coverage["declared_only"].items())
        )
        line += f"; declared but not evaluable: {types}"
    if coverage.get("unsupported"):
        types = ", ".join(
            f"{t}×{n}" for t, n in sorted(coverage["unsupported"].items())
        )
        line += f"; UNSUPPORTED: {types}"
    return line
