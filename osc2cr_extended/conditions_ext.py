"""
conditions_ext.py
=================
Extends the thesis condition taxonomy to the OpenSCENARIO condition types that
``strategies/shared/condition_model.py`` does not carry.

Why here and not in ``shared/``
-------------------------------
``strategies/`` is the thesis's reference implementation and its outputs
are already written up; this tool therefore adds capability *around* it rather
than editing it, exactly as ``params.py`` (entity-reference resolution) and
``live.py`` (edge semantics, obstacle mapping) do.  Everything here is written
so it can be lifted into ``strategies/shared/condition_model.py`` +
``strategies/shared/storyboard_parser.py`` + ``strategies/condition_evaluator.py`` verbatim
if it is ever wanted upstream.

What it adds
------------
Across the 70 distinct ``.xosc`` files on this machine (318 conditions; the two
corpora share several files, so the raw count over both directories is higher)
the baseline model carries 84%.  The types it drops are:

    ReachPositionCondition        ← evaluable (needs road geometry, see roadmanager)
    CollisionCondition            ← evaluable
    AccelerationCondition         ← evaluable
    ParameterCondition            ← evaluable
    StandStillCondition           ← evaluable
    DistanceCondition             ← evaluable
    TrafficSignalCondition     8  ← declared, not evaluable here
    EndOfRoadCondition         5  ← declared, not evaluable here
    OffroadCondition           1  ← declared, not evaluable here
    RelativeClearanceCondition 1  ← declared, not evaluable here

With this attached the corpus reaches 100% preserved and 95.3% evaluable.

The evaluable ones are modelled and computed.  The rest are *declared* — they
are parsed, preserved, embedded in the CommonRoad file and shown in the viewer
with the reason they cannot be computed — rather than silently discarded.  That
distinction matters: a discarded condition leaves an empty trigger, which
OpenSCENARIO treats as unconditionally true, whereas a declared-unevaluable one
keeps its event honestly un-firable.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .coverage import SUPPORTED_TYPES, _condition_type


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def _base_fields(el: ET.Element, params: Dict[str, str]) -> Dict[str, Any]:
    from osc2cr_extended.strategies.shared.condition_model import ConditionEdge

    def _f(raw: Optional[str], default: float = 0.0) -> float:
        if raw is None:
            return default
        raw = params.get(raw.lstrip("$"), raw) if raw.startswith("$") else raw
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    edge_raw = el.get("conditionEdge", "none")
    try:
        edge = ConditionEdge(edge_raw)
    except ValueError:
        edge = ConditionEdge.NONE
    return {
        "name": el.get("name", ""),
        "delay": _f(el.get("delay"), 0.0),
        "edge": edge,
    }


@dataclass
class ExtCondition:
    """Common base so extension conditions slot into the parsed triggers."""

    name: str
    delay: float
    edge: Any
    entity_ref: str = ""

    #: Set when the type is understood but cannot be computed from a converted
    #: CommonRoad scenario; the reason is carried through to the output.
    unevaluable_reason: Optional[str] = None

    @property
    def type_name(self) -> str:
        return type(self).__name__

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "type": self.type_name,
            "name": self.name,
            "delay_s": self.delay,
            "edge": getattr(self.edge, "value", str(self.edge)),
            "extension": True,
        }
        if self.entity_ref:
            d["entity_ref"] = self.entity_ref
        if self.unevaluable_reason:
            d["unevaluable_reason"] = self.unevaluable_reason
        d.update(self._extra())
        return d

    def _extra(self) -> Dict[str, Any]:
        return {}

    def describe(self) -> str:
        return self.type_name


@dataclass
class AccelerationCondition(ExtCondition):
    value: float = 0.0
    rule: str = "greaterThan"
    direction: Optional[str] = None

    def _extra(self):
        return {"value_ms2": self.value, "rule": self.rule,
                "direction": self.direction}

    def describe(self):
        return f"{self.entity_ref}: acceleration {self.rule} {self.value} m/s²"


@dataclass
class StandStillCondition(ExtCondition):
    duration: float = 0.0

    def _extra(self):
        return {"duration_s": self.duration}

    def describe(self):
        return f"{self.entity_ref}: standing still for {self.duration} s"


@dataclass
class CollisionCondition(ExtCondition):
    target_entity: str = ""
    target_type: Optional[str] = None

    def _extra(self):
        return {"target_entity": self.target_entity,
                "target_type": self.target_type}

    def describe(self):
        who = self.target_entity or (f"any {self.target_type}"
                                     if self.target_type else "anything")
        return f"{self.entity_ref} collides with {who}"


@dataclass
class ReachPositionCondition(ExtCondition):
    tolerance: float = 0.0
    position: Optional["TargetPosition"] = None

    def _extra(self):
        d = {"tolerance_m": self.tolerance}
        if self.position:
            d["position"] = self.position.to_dict()
        return d

    def describe(self):
        where = self.position.describe() if self.position else "?"
        return (f"{self.entity_ref} reaches {where} "
                f"(tolerance {self.tolerance} m)")


@dataclass
class DistanceCondition(ExtCondition):
    value: float = 0.0
    rule: str = "lessThan"
    freespace: bool = False
    position: Optional["TargetPosition"] = None

    def _extra(self):
        d = {"value_m": self.value, "rule": self.rule,
             "freespace": self.freespace}
        if self.position:
            d["position"] = self.position.to_dict()
        return d

    def describe(self):
        where = self.position.describe() if self.position else "?"
        return f"{self.entity_ref}: distance to {where} {self.rule} {self.value} m"


@dataclass
class ParameterCondition(ExtCondition):
    parameter_ref: str = ""
    value: str = ""
    rule: str = "equalTo"
    resolved: Optional[str] = None

    def _extra(self):
        return {"parameter_ref": self.parameter_ref, "value": self.value,
                "rule": self.rule, "resolved": self.resolved}

    def describe(self):
        return (f"parameter ${self.parameter_ref} ({self.resolved}) "
                f"{self.rule} {self.value}")


@dataclass
class DeclaredCondition(ExtCondition):
    """A type we model structurally but cannot compute — kept, never guessed."""

    kind: str = "UnknownCondition"
    attributes: Dict[str, str] = field(default_factory=dict)

    @property
    def type_name(self) -> str:
        return self.kind

    def _extra(self):
        return {"attributes": dict(self.attributes)}

    def describe(self):
        who = f"{self.entity_ref}: " if self.entity_ref else ""
        return f"{who}{self.kind} (declared, not evaluable — {self.unevaluable_reason})"


# ---------------------------------------------------------------------------
# Target positions
# ---------------------------------------------------------------------------

@dataclass
class TargetPosition:
    """A ``<Position>`` target, resolved to world coordinates when possible."""

    kind: str
    x: Optional[float] = None
    y: Optional[float] = None
    heading: Optional[float] = None
    road_id: Optional[int] = None
    lane_id: Optional[int] = None
    s: Optional[float] = None
    lane_offset: float = 0.0
    resolved: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind, "x": self.x, "y": self.y,
            "road_id": self.road_id, "lane_id": self.lane_id, "s": self.s,
            "resolved": self.resolved,
        }

    def describe(self) -> str:
        if self.kind == "LanePosition":
            where = f"road {self.road_id} lane {self.lane_id} at s={self.s} m"
            if self.resolved:
                where += f" → ({self.x:.1f}, {self.y:.1f})"
            return where
        if self.x is not None and self.y is not None:
            return f"({self.x:.1f}, {self.y:.1f})"
        return self.kind


def _parse_position(pos_el: Optional[ET.Element],
                    params: Dict[str, str]) -> Optional[TargetPosition]:
    if pos_el is None:
        return None

    def _f(el: ET.Element, attr: str, default: Optional[float] = None):
        raw = el.get(attr)
        if raw is None:
            return default
        if raw.startswith("$"):
            raw = params.get(raw.lstrip("${}"), raw)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    world = pos_el.find("WorldPosition")
    if world is not None:
        return TargetPosition(
            kind="WorldPosition",
            x=_f(world, "x"), y=_f(world, "y"), heading=_f(world, "h"),
            resolved=True,
        )

    lane = pos_el.find("LanePosition")
    if lane is not None:
        road = _f(lane, "roadId")
        lane_id = _f(lane, "laneId")
        return TargetPosition(
            kind="LanePosition",
            road_id=int(road) if road is not None else None,
            lane_id=int(lane_id) if lane_id is not None else None,
            s=_f(lane, "s", 0.0),
            lane_offset=_f(lane, "offset", 0.0) or 0.0,
        )

    for other in ("RelativeLanePosition", "RelativeWorldPosition",
                  "RelativeObjectPosition", "RelativeRoadPosition",
                  "RoadPosition", "TrajectoryPosition"):
        if pos_el.find(other) is not None:
            return TargetPosition(kind=other)
    return TargetPosition(kind="UnknownPosition")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

#: Types this module can actually compute against a converted scenario.
EVALUABLE = {
    "AccelerationCondition", "StandStillCondition", "CollisionCondition",
    "ReachPositionCondition", "DistanceCondition", "ParameterCondition",
}

#: Types kept structurally, with the reason they cannot be computed.
DECLARED_ONLY = {
    "EndOfRoadCondition":
        "needs OpenDRIVE road topology; a CommonRoad lanelet network does not "
        "record where a road ends versus where the map is simply cut off",
    "OffroadCondition":
        "needs lane-level containment against the source OpenDRIVE, not the "
        "converted lanelets",
    "TrafficSignalCondition":
        "the converter does not carry esmini's traffic-signal state into the "
        "CommonRoad scenario",
    "RelativeClearanceCondition":
        "OpenSCENARIO 1.2 corridor geometry; needs lane-relative free-space "
        "computation the converted scenario does not provide",
}


def _entity_of(cond_el: ET.Element) -> str:
    trig = cond_el.find(".//TriggeringEntities/EntityRef")
    return trig.get("entityRef", "") if trig is not None else ""


def build_extension(cond_el: ET.Element,
                    params: Dict[str, str]) -> Optional[ExtCondition]:
    """Build an extension condition from a ``<Condition>`` element."""
    ctype = _condition_type(cond_el)
    if ctype in SUPPORTED_TYPES:
        return None

    base = _base_fields(cond_el, params)
    entity = _entity_of(cond_el)
    node = cond_el.find(f".//{ctype}")
    if node is None:
        return None

    def _num(attr: str, default: float = 0.0) -> float:
        raw = node.get(attr)
        if raw is None:
            return default
        if raw.startswith("$"):
            raw = params.get(raw.lstrip("${}"), raw)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    if ctype == "AccelerationCondition":
        return AccelerationCondition(
            **base, entity_ref=entity, value=_num("value"),
            rule=node.get("rule", "greaterThan"),
            direction=node.get("direction"),
        )

    if ctype == "StandStillCondition":
        return StandStillCondition(
            **base, entity_ref=entity, duration=_num("duration"),
        )

    if ctype == "CollisionCondition":
        target_el = node.find("EntityRef")
        by_type = node.find("ByType")
        return CollisionCondition(
            **base, entity_ref=entity,
            target_entity=(target_el.get("entityRef", "")
                           if target_el is not None else ""),
            target_type=(by_type.get("type") if by_type is not None else None),
        )

    if ctype == "ReachPositionCondition":
        return ReachPositionCondition(
            **base, entity_ref=entity, tolerance=_num("tolerance", 0.0),
            position=_parse_position(node.find("Position"), params),
        )

    if ctype == "DistanceCondition":
        return DistanceCondition(
            **base, entity_ref=entity, value=_num("value"),
            rule=node.get("rule", "lessThan"),
            freespace=node.get("freespace", "false") == "true",
            position=_parse_position(node.find("Position"), params),
        )

    if ctype == "ParameterCondition":
        ref = node.get("parameterRef", "")
        return ParameterCondition(
            **base, parameter_ref=ref, value=node.get("value", ""),
            rule=node.get("rule", "equalTo"), resolved=params.get(ref),
        )

    reason = DECLARED_ONLY.get(
        ctype, "not modelled by the condition taxonomy")
    return DeclaredCondition(
        **base, entity_ref=entity, kind=ctype,
        attributes={k: v for k, v in node.attrib.items()},
        unevaluable_reason=reason,
    )


def attach_extensions(
    storyboard: Any, xosc_path: str | Path, params: Dict[str, str],
) -> Tuple[int, Dict[str, int]]:
    """
    Parse the unsupported conditions and insert them into ``storyboard``.

    Returns ``(n_attached, per_type_counts)``.  Conditions are placed in the
    ConditionGroup they belong to: the shared parser drops a group once every
    condition in it is unsupported, so groups are matched by walking the XML
    and the parsed structure in parallel rather than by index.
    """
    try:
        root = ET.parse(Path(xosc_path)).getroot()
    except (ET.ParseError, OSError):
        return 0, {}

    counts: Dict[str, int] = {}

    def attach(trigger_el: Optional[ET.Element], trigger: List) -> None:
        if trigger_el is None:
            return
        parsed_idx = 0
        for cg_el in trigger_el.findall("ConditionGroup"):
            cond_els = cg_el.findall("Condition")
            supported = [c for c in cond_els
                         if _condition_type(c) in SUPPORTED_TYPES]
            exts = [build_extension(c, params) for c in cond_els
                    if _condition_type(c) not in SUPPORTED_TYPES]
            exts = [e for e in exts if e is not None]

            if supported:
                if parsed_idx < len(trigger):
                    trigger[parsed_idx].extend(exts)
                parsed_idx += 1
            elif exts:
                # the whole group was dropped by the shared parser; restore it
                trigger.append(exts)

            for e in exts:
                counts[e.type_name] = counts.get(e.type_name, 0) + 1

    stories = {s.name: s for s in storyboard.stories}
    for story_el in root.iter("Story"):
        story = stories.get(story_el.get("name", ""))
        if story is None:
            continue
        acts = {a.name: a for a in story.acts}
        for act_el in story_el.findall("Act"):
            act = acts.get(act_el.get("name", ""))
            if act is None:
                continue
            attach(act_el.find("StartTrigger"), act.start_trigger)
            if act.stop_trigger is None:
                act.stop_trigger = []
            attach(act_el.find("StopTrigger"), act.stop_trigger)

            events = {
                e.name: e
                for mg in act.maneuver_groups
                for m in mg.maneuvers
                for e in m.events
            }
            for event_el in act_el.iter("Event"):
                event = events.get(event_el.get("name", ""))
                if event is None:
                    continue
                attach(event_el.find("StartTrigger"), event.start_trigger)

    sb_el = root.find(".//Storyboard")
    if sb_el is not None:
        if getattr(storyboard, "stop_trigger", None) is None:
            storyboard.stop_trigger = []
        attach(sb_el.find("StopTrigger"), storyboard.stop_trigger)

    return sum(counts.values()), counts


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _apply_rule(lhs: float, rule: str, rhs: float) -> bool:
    if rule == "lessThan":        return lhs < rhs
    if rule == "lessOrEqual":     return lhs <= rhs
    if rule == "greaterThan":     return lhs > rhs
    if rule == "greaterOrEqual":  return lhs >= rhs
    if rule == "equalTo":         return math.isclose(lhs, rhs, abs_tol=1e-6)
    if rule == "notEqualTo":      return not math.isclose(lhs, rhs, abs_tol=1e-6)
    return False


class ExtensionEvaluator:
    """
    Evaluates the extension conditions against a per-step world snapshot.

    Stateful for the duration-based types (``StandStillCondition``), which need
    to know how long a predicate has already held.
    """

    def __init__(self, resolver: Any = None) -> None:
        self.resolver = resolver           # LanePositionResolver | None
        self._since: Dict[int, Optional[float]] = {}

    def reset(self) -> None:
        self._since.clear()

    # ------------------------------------------------------------------

    def _target_xy(self, pos: Optional[TargetPosition]) -> Optional[Tuple[float, float]]:
        if pos is None:
            return None
        if pos.x is not None and pos.y is not None:
            return pos.x, pos.y
        if (pos.kind == "LanePosition" and self.resolver is not None
                and pos.road_id is not None and pos.lane_id is not None):
            world = self.resolver.resolve(pos.road_id, pos.lane_id, pos.s or 0.0,
                                          pos.lane_offset)
            if world is not None:
                pos.x, pos.y, pos.heading = world
                pos.resolved = True
                return pos.x, pos.y
        return None

    def _held_for(self, cond: Any, holding: bool, time_s: float,
                  duration: float) -> bool:
        """True once ``holding`` has been continuously true for ``duration``."""
        key = id(cond)
        if not holding:
            self._since[key] = None
            return False
        start = self._since.get(key)
        if start is None:
            self._since[key] = time_s
            start = time_s
        return (time_s - start) >= duration

    # ------------------------------------------------------------------

    def evaluate(self, cond: ExtCondition, entities: Dict[str, Dict[str, float]],
                 time_s: float) -> Optional[bool]:
        """
        ``True``/``False``, or ``None`` when the condition cannot be computed.

        ``None`` is deliberately distinct from ``False``: an unevaluable
        condition must not make its event look like it simply did not trigger.
        """
        if isinstance(cond, DeclaredCondition):
            return None

        me = entities.get(cond.entity_ref)

        if isinstance(cond, AccelerationCondition):
            if me is None:
                return None
            return _apply_rule(me.get("acceleration", 0.0), cond.rule, cond.value)

        if isinstance(cond, StandStillCondition):
            if me is None:
                return None
            return self._held_for(cond, abs(me.get("speed", 0.0)) < 0.05,
                                  time_s, cond.duration)

        if isinstance(cond, CollisionCondition):
            if me is None:
                return None
            targets = ([entities[cond.target_entity]]
                       if cond.target_entity in entities
                       else [e for n, e in entities.items()
                             if n != cond.entity_ref])
            for other in targets:
                gap = math.hypot(me["x"] - other["x"], me["y"] - other["y"])
                clearance = (me.get("length", 4.5) + other.get("length", 4.5)) / 2.0
                if gap <= clearance:
                    return True
            return False

        if isinstance(cond, ReachPositionCondition):
            if me is None:
                return None
            target = self._target_xy(cond.position)
            if target is None:
                return None
            dist = math.hypot(me["x"] - target[0], me["y"] - target[1])
            return dist <= max(cond.tolerance, 1e-9)

        if isinstance(cond, DistanceCondition):
            if me is None:
                return None
            target = self._target_xy(cond.position)
            if target is None:
                return None
            dist = math.hypot(me["x"] - target[0], me["y"] - target[1])
            if cond.freespace:
                dist = max(0.0, dist - me.get("length", 4.5) / 2.0)
            return _apply_rule(dist, cond.rule, cond.value)

        if isinstance(cond, ParameterCondition):
            if cond.resolved is None:
                return None
            try:
                return _apply_rule(float(cond.resolved), cond.rule,
                                   float(cond.value))
            except (TypeError, ValueError):
                if cond.rule == "equalTo":
                    return str(cond.resolved) == str(cond.value)
                if cond.rule == "notEqualTo":
                    return str(cond.resolved) != str(cond.value)
                return None

        return None
