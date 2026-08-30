"""
Parse the <Storyboard> section of an OpenSCENARIO .xosc file into a
ParsedStoryboard object without running esmini.

Only the structural hierarchy and trigger/condition elements are extracted;
action payloads are kept as raw XML elements for downstream strategies.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, List

from .condition_model import (
    Condition, ConditionEdge, Rule, DistanceType,
    SimulationTimeCondition, SpeedCondition,
    TraveledDistanceCondition, StoryboardElementStateCondition,
    EntityTraveledDistanceCondition,
    RelativeDistanceCondition, RelativeSpeedCondition,
    TimeHeadwayCondition, TimeToCollisionCondition,
    Action, Event, Maneuver, ManeuverGroup, Act, Story,
    Trigger, ConditionGroup, ParsedStoryboard,
)


def _attr(el: ET.Element, key: str, default: str = "") -> str:
    return el.get(key, default)


def _resolve(raw: str, params: dict) -> str:
    """Substitute $name or ${name} parameter references."""
    import re
    def _sub(m):
        return str(params.get(m.group(1), m.group(0)))
    return re.sub(r'\$\{?(\w+)\}?', _sub, raw)


def _float(el: ET.Element, key: str, default: float = 0.0,
           params: Optional[dict] = None) -> float:
    raw = el.get(key)
    if raw is None:
        return default
    raw = _resolve(raw, params or {})
    try:
        return float(raw)
    except ValueError:
        return default   # unresolvable parameter → keep default


def _bool(el: ET.Element, key: str, default: bool = False) -> bool:
    return el.get(key, str(default)).lower() == "true"


def _edge(s: str) -> ConditionEdge:
    try:
        return ConditionEdge(s)
    except ValueError:
        return ConditionEdge.NONE


def _rule(s: str) -> Rule:
    return Rule(s)


def _dist_type(s: str) -> DistanceType:
    # OSC uses "cartesianDistance" or "euclidianDistance" (typo in spec)
    _map = {
        "longitudinal": DistanceType.LONGITUDINAL,
        "lateral": DistanceType.LATERAL,
        "cartesianDistance": DistanceType.CARTESIAN,
        "euclidianDistance": DistanceType.CARTESIAN,
    }
    return _map.get(s, DistanceType.CARTESIAN)


class StoryboardParser:
    def __init__(self, xosc_path: str):
        self.path = Path(xosc_path)
        tree = ET.parse(xosc_path)
        self._root = tree.getroot()
        self._params: dict = self._load_params()

    def _load_params(self) -> dict:
        """Read <ParameterDeclarations> so $name references resolve to floats."""
        params: dict = {}
        for pd in self._root.findall(".//ParameterDeclaration"):
            name = pd.get("name", "")
            value = pd.get("value", "")
            if name:
                try:
                    params[name] = float(value)
                except ValueError:
                    params[name] = value
        return params

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def parse(self) -> ParsedStoryboard:
        sb = self._root.find("Storyboard")
        if sb is None:
            raise ValueError(f"No <Storyboard> found in {self.path}")

        stories = [self._story(s) for s in sb.findall("Story")]

        stop_el = sb.find("StopTrigger")
        stop_trigger = self._trigger(stop_el) if stop_el is not None else None

        return ParsedStoryboard(stories=stories, stop_trigger=stop_trigger)

    # ------------------------------------------------------------------
    # Hierarchy
    # ------------------------------------------------------------------

    def _story(self, el: ET.Element) -> Story:
        return Story(
            name=_attr(el, "name"),
            acts=[self._act(a) for a in el.findall("Act")],
        )

    def _act(self, el: ET.Element) -> Act:
        stop_el = el.find("StopTrigger")
        return Act(
            name=_attr(el, "name"),
            maneuver_groups=[self._mg(m) for m in el.findall("ManeuverGroup")],
            start_trigger=self._trigger(el.find("StartTrigger")),
            stop_trigger=self._trigger(stop_el) if stop_el is not None else None,
        )

    def _mg(self, el: ET.Element) -> ManeuverGroup:
        actors_el = el.find("Actors")
        actor_refs: List[str] = []
        select_triggering = False
        if actors_el is not None:
            select_triggering = _bool(actors_el, "selectTriggeringEntities")
            actor_refs = [_attr(e, "entityRef") for e in actors_el.findall("EntityRef")]

        return ManeuverGroup(
            name=_attr(el, "name"),
            max_execution_count=int(_attr(el, "maximumExecutionCount", "1")),
            actor_refs=actor_refs,
            select_triggering_entities=select_triggering,
            maneuvers=[self._maneuver(m) for m in el.findall("Maneuver")],
        )

    def _maneuver(self, el: ET.Element) -> Maneuver:
        return Maneuver(
            name=_attr(el, "name"),
            events=[self._event(e) for e in el.findall("Event")],
        )

    def _event(self, el: ET.Element) -> Event:
        actions = [
            Action(name=_attr(a, "name"), xml_element=a)
            for a in el.findall("Action")
        ]
        return Event(
            name=_attr(el, "name"),
            priority=_attr(el, "priority", "overwrite"),
            max_execution_count=int(_attr(el, "maximumExecutionCount", "1")),
            actions=actions,
            start_trigger=self._trigger(el.find("StartTrigger")),
        )

    # ------------------------------------------------------------------
    # Triggers & conditions
    # ------------------------------------------------------------------

    def _trigger(self, el: Optional[ET.Element]) -> Trigger:
        if el is None:
            return []
        groups: Trigger = []
        for cg_el in el.findall("ConditionGroup"):
            group: ConditionGroup = []
            for c_el in cg_el.findall("Condition"):
                cond = self._condition(c_el)
                if cond is not None:
                    group.append(cond)
            if group:
                groups.append(group)
        return groups

    def _condition(self, el: ET.Element) -> Optional[Condition]:
        p = self._params
        base = dict(
            name=_attr(el, "name"),
            delay=_float(el, "delay", params=p),
            edge=_edge(_attr(el, "conditionEdge", "none")),
        )

        bv_el = el.find("ByValueCondition")
        be_el = el.find("ByEntityCondition")

        if bv_el is not None:
            return self._by_value(bv_el, base)
        if be_el is not None:
            return self._by_entity(be_el, base)
        return None

    # ByValue ----------------------------------------------------------

    def _by_value(self, el: ET.Element, base: dict) -> Optional[Condition]:
        p = self._params
        stc = el.find("SimulationTimeCondition")
        if stc is not None:
            return SimulationTimeCondition(
                **base,
                value=_float(stc, "value", params=p),
                rule=_rule(_attr(stc, "rule", "greaterThan")),
            )

        sc = el.find("SpeedCondition")
        if sc is not None:
            return SpeedCondition(
                **base,
                value=_float(sc, "value", params=p),
                rule=_rule(_attr(sc, "rule", "greaterThan")),
            )

        tdc = el.find("TraveledDistanceCondition")
        if tdc is not None:
            return TraveledDistanceCondition(**base, value=_float(tdc, "value", params=p))

        sesc = el.find("StoryboardElementStateCondition")
        if sesc is not None:
            return StoryboardElementStateCondition(
                **base,
                element_ref=_attr(sesc, "storyboardElementRef"),
                element_type=_attr(sesc, "storyboardElementType"),
                state=_attr(sesc, "state"),
            )

        return None  # unrecognised ByValue type

    # ByEntity ---------------------------------------------------------

    def _by_entity(self, el: ET.Element, base: dict) -> Optional[Condition]:
        p = self._params
        # The triggering entity (actor) comes from TriggeringEntities
        trig_el = el.find("TriggeringEntities")
        triggering_entity = ""
        if trig_el is not None:
            er = trig_el.find("EntityRef")
            if er is not None:
                triggering_entity = _attr(er, "entityRef")

        ec_el = el.find("EntityCondition")
        if ec_el is None:
            return None

        # TraveledDistance (ByEntity variant)
        tdc = ec_el.find("TraveledDistanceCondition")
        if tdc is not None:
            return EntityTraveledDistanceCondition(
                **base,
                entity_ref=triggering_entity,
                value=_float(tdc, "value", params=p),
            )

        rdc = ec_el.find("RelativeDistanceCondition")
        if rdc is not None:
            return RelativeDistanceCondition(
                **base,
                reference_entity=_attr(rdc, "entityRef"),
                triggering_entity=triggering_entity,
                distance_type=_dist_type(_attr(rdc, "relativeDistanceType", "cartesianDistance")),
                value=_float(rdc, "value", params=p),
                freespace=_bool(rdc, "freespace"),
                rule=_rule(_attr(rdc, "rule", "lessThan")),
            )

        rsc = ec_el.find("RelativeSpeedCondition")
        if rsc is not None:
            return RelativeSpeedCondition(
                **base,
                reference_entity=_attr(rsc, "entityRef"),
                triggering_entity=triggering_entity,
                value=_float(rsc, "value", params=p),
                rule=_rule(_attr(rsc, "rule", "lessThan")),
            )

        thc = ec_el.find("TimeHeadwayCondition")
        if thc is not None:
            return TimeHeadwayCondition(
                **base,
                reference_entity=_attr(thc, "entityRef"),
                triggering_entity=triggering_entity,
                value=_float(thc, "value", params=p),
                freespace=_bool(thc, "freespace"),
                rule=_rule(_attr(thc, "rule", "lessThan")),
            )

        ttcc = ec_el.find("TimeToCollisionCondition")
        if ttcc is not None:
            return TimeToCollisionCondition(
                **base,
                reference_entity=_attr(ttcc, "entityRef"),
                triggering_entity=triggering_entity,
                value=_float(ttcc, "value", params=p),
                freespace=_bool(ttcc, "freespace"),
                rule=_rule(_attr(ttcc, "rule", "lessThan")),
            )

        return None  # unrecognised ByEntity type
