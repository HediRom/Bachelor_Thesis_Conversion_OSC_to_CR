"""
Transcription

Keeps the CR Scenario's flat trajectories completely unchanged and attaches
the parsed trigger/condition structures as a JSON-serialisable metadata
side-car.  Existing CR tools see an unmodified Scenario; the annotations
are accessed through AnnotatedScenario.event_annotations.

Fidelity  : low  — descriptive only, no re-evaluation
Coverage  : full — every condition type can be tagged
Cost      : low  — parse XML once, attach dicts
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List

import sys
from pathlib import Path


from osc2cr_extended.strategies.shared.condition_model import (
    ParsedStoryboard, Condition, Trigger,
    SimulationTimeCondition, SpeedCondition,
    TraveledDistanceCondition, StoryboardElementStateCondition,
    EntityTraveledDistanceCondition,
    RelativeDistanceCondition, RelativeSpeedCondition,
    TimeHeadwayCondition, TimeToCollisionCondition,
)
from osc2cr_extended.strategies.shared.storyboard_parser import StoryboardParser


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EventAnnotation:
    """One annotated trigger event: which actors, which conditions."""
    event_name: str
    story: str
    act: str
    actors: List[str]
    conditions: List[Dict[str, Any]]  # JSON-friendly representations


@dataclass
class AnnotatedScenario:
    """
    A CommonRoad Scenario enriched with a conditional-logic side-car.

    The `scenario` field is the original CR Scenario object, untouched.
    `event_annotations` maps each Event name to its annotation.
    `all_conditions` is a flat list for quick iteration.
    """
    scenario: Any  # commonroad.scenario.scenario.Scenario
    event_annotations: Dict[str, EventAnnotation] = field(default_factory=dict)
    all_conditions: List[Condition] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            name: {
                "story": ann.story,
                "act": ann.act,
                "actors": ann.actors,
                "conditions": ann.conditions,
            }
            for name, ann in self.event_annotations.items()
        }

    def dump_json(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    def summary(self) -> str:
        lines = [f"AnnotatedScenario — {len(self.event_annotations)} events, "
                 f"{len(self.all_conditions)} conditions total"]
        for name, ann in self.event_annotations.items():
            actors = ", ".join(ann.actors) or "(no actors)"
            lines.append(f"  [{ann.story}/{ann.act}] {name}  actors={actors}")
            for c in ann.conditions:
                lines.append(f"    • {c['type']}  {_condition_oneliner(c)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Condition → dict
# ---------------------------------------------------------------------------

def _condition_oneliner(d: Dict[str, Any]) -> str:
    """Human-readable one-liner extracted from the already-serialised dict."""
    t = d.get("type", "")
    if t == "SimulationTimeCondition":
        return f"t {d.get('rule','')} {d.get('value_s')} s"
    if t == "SpeedCondition":
        return f"speed {d.get('rule','')} {d.get('value_ms')} m/s"
    if t == "TraveledDistanceCondition":
        return f"traveled >= {d.get('value_m')} m"
    if t == "EntityTraveledDistanceCondition":
        return f"{d.get('entity_ref')} traveled >= {d.get('value_m')} m"
    if t == "StoryboardElementStateCondition":
        return f"{d.get('element_ref')}.state == {d.get('state')}"
    if t in ("RelativeDistanceCondition", "TimeHeadwayCondition", "TimeToCollisionCondition"):
        return (f"{d.get('triggering_entity')} vs {d.get('reference_entity')}: "
                f"{d.get('rule','')} {d.get('value')} "
                f"{'(freespace)' if d.get('freespace') else ''}")
    if t == "RelativeSpeedCondition":
        return (f"{d.get('triggering_entity')} rel-speed vs {d.get('reference_entity')}: "
                f"{d.get('rule','')} {d.get('value_ms')} m/s")
    return str(d)


def _condition_to_dict(cond: Condition) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "type": type(cond).__name__,
        "name": cond.name,
        "delay_s": cond.delay,
        "edge": cond.edge.value,
    }
    if isinstance(cond, SimulationTimeCondition):
        d["value_s"] = cond.value
        d["rule"] = cond.rule.value

    elif isinstance(cond, SpeedCondition):
        d["value_ms"] = cond.value
        d["rule"] = cond.rule.value

    elif isinstance(cond, TraveledDistanceCondition):
        d["value_m"] = cond.value

    elif isinstance(cond, EntityTraveledDistanceCondition):
        d["entity_ref"] = cond.entity_ref
        d["value_m"] = cond.value

    elif isinstance(cond, StoryboardElementStateCondition):
        d["element_ref"] = cond.element_ref
        d["element_type"] = cond.element_type
        d["state"] = cond.state

    elif isinstance(cond, RelativeDistanceCondition):
        d["triggering_entity"] = cond.triggering_entity
        d["reference_entity"] = cond.reference_entity
        d["distance_type"] = cond.distance_type.value
        d["value"] = cond.value
        d["freespace"] = cond.freespace
        d["rule"] = cond.rule.value

    elif isinstance(cond, RelativeSpeedCondition):
        d["triggering_entity"] = cond.triggering_entity
        d["reference_entity"] = cond.reference_entity
        d["value_ms"] = cond.value
        d["rule"] = cond.rule.value

    elif isinstance(cond, TimeHeadwayCondition):
        d["triggering_entity"] = cond.triggering_entity
        d["reference_entity"] = cond.reference_entity
        d["value"] = cond.value
        d["freespace"] = cond.freespace
        d["rule"] = cond.rule.value

    elif isinstance(cond, TimeToCollisionCondition):
        d["triggering_entity"] = cond.triggering_entity
        d["reference_entity"] = cond.reference_entity
        d["value"] = cond.value
        d["freespace"] = cond.freespace
        d["rule"] = cond.rule.value

    return d


def _flatten_trigger(trigger: Trigger) -> List[Condition]:
    return [cond for group in trigger for cond in group]


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def annotate_scenario(scenario: Any, storyboard: ParsedStoryboard) -> AnnotatedScenario:
    """
    Attach all parsed trigger/condition structures as metadata.

    Parameters
    ----------
    scenario    : CommonRoad Scenario returned by the existing converter.
    storyboard  : ParsedStoryboard from StoryboardParser.parse().

    Returns
    -------
    AnnotatedScenario — wraps scenario unchanged, adds event_annotations.
    """
    result = AnnotatedScenario(scenario=scenario)

    for story in storyboard.stories:
        for act in story.acts:
            for mg in act.maneuver_groups:
                for maneuver in mg.maneuvers:
                    for event in maneuver.events:
                        flat = _flatten_trigger(event.start_trigger)
                        ann = EventAnnotation(
                            event_name=event.name,
                            story=story.name,
                            act=act.name,
                            actors=mg.actor_refs,
                            conditions=[_condition_to_dict(c) for c in flat],
                        )
                        result.event_annotations[event.name] = ann
                        result.all_conditions.extend(flat)

    return result


def from_xosc(scenario: Any, xosc_path: str) -> AnnotatedScenario:
    """Parse the .xosc and annotate in one call."""
    storyboard = StoryboardParser(xosc_path).parse()
    return annotate_scenario(scenario, storyboard)
