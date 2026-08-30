from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Any


class ConditionEdge(Enum):
    NONE = "none"
    RISING = "rising"
    FALLING = "falling"
    RISING_OR_FALLING = "risingOrFalling"


class Rule(Enum):
    LESS_THAN = "lessThan"
    LESS_OR_EQUAL = "lessOrEqual"
    EQUAL_TO = "equalTo"
    GREATER_OR_EQUAL = "greaterOrEqual"
    GREATER_THAN = "greaterThan"
    NOT_EQUAL_TO = "notEqualTo"


class DistanceType(Enum):
    CARTESIAN = "cartesianDistance"
    LONGITUDINAL = "longitudinal"
    LATERAL = "lateral"


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

@dataclass
class Condition:
    name: str
    delay: float
    edge: ConditionEdge


# ---------------------------------------------------------------------------
# ByValue conditions  (threshold on global simulation state)
# ---------------------------------------------------------------------------

@dataclass
class SimulationTimeCondition(Condition):
    value: float   # seconds
    rule: Rule


@dataclass
class SpeedCondition(Condition):
    value: float   # m/s
    rule: Rule


@dataclass
class TraveledDistanceCondition(Condition):
    """ByValue variant — global traveled distance (no entity reference)."""
    value: float   # metres


@dataclass
class StoryboardElementStateCondition(Condition):
    element_ref: str
    element_type: str   # story | act | maneuverGroup | maneuver | event | action
    state: str          # standby | running | complete


# ---------------------------------------------------------------------------
# ByEntity conditions  (relational between actors)
# ---------------------------------------------------------------------------

@dataclass
class EntityTraveledDistanceCondition(Condition):
    """ByEntity variant — entity must have traveled this far."""
    entity_ref: str
    value: float   # metres


@dataclass
class RelativeDistanceCondition(Condition):
    reference_entity: str    # the fixed reference entity
    triggering_entity: str   # the actor that must satisfy the condition
    distance_type: DistanceType
    value: float             # metres
    freespace: bool
    rule: Rule


@dataclass
class RelativeSpeedCondition(Condition):
    reference_entity: str
    triggering_entity: str
    value: float   # m/s
    rule: Rule


@dataclass
class TimeHeadwayCondition(Condition):
    reference_entity: str
    triggering_entity: str
    value: float   # seconds
    freespace: bool
    rule: Rule


@dataclass
class TimeToCollisionCondition(Condition):
    reference_entity: str
    triggering_entity: str
    value: float   # seconds
    freespace: bool
    rule: Rule


# ---------------------------------------------------------------------------
# Trigger / storyboard hierarchy
# ---------------------------------------------------------------------------

# ConditionGroup: all conditions joined with AND
ConditionGroup = List[Condition]

# Trigger: list of ConditionGroups joined with OR
Trigger = List[ConditionGroup]


@dataclass
class Action:
    name: str
    xml_element: Any   # raw xml.etree.ElementTree.Element kept for extensibility


@dataclass
class Event:
    name: str
    priority: str
    max_execution_count: int
    actions: List[Action]
    start_trigger: Trigger


@dataclass
class Maneuver:
    name: str
    events: List[Event]


@dataclass
class ManeuverGroup:
    name: str
    max_execution_count: int
    actor_refs: List[str]
    select_triggering_entities: bool
    maneuvers: List[Maneuver]


@dataclass
class Act:
    name: str
    maneuver_groups: List[ManeuverGroup]
    start_trigger: Trigger
    stop_trigger: Optional[Trigger]


@dataclass
class Story:
    name: str
    acts: List[Act]


@dataclass
class ParsedStoryboard:
    stories: List[Story]
    stop_trigger: Optional[Trigger]   # global stop trigger
