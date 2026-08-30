"""
Interpretation — condition evaluator (runtime layer)

Given a snapshot of entity states at a simulation tick, evaluate each parsed
OSC condition to True/False.  This restores the reactivity that is lost when
esmini discards the storyboard after flattening.

Fidelity  : high — reactivity survives; conditions are re-evaluated each step
Coverage  : targeted — ByEntity types that Transcription/Translation cannot handle
Cost      : high — requires a custom evaluator and per-step state plumbing
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional

import sys
from pathlib import Path


from osc2cr_extended.strategies.shared.condition_model import (
    Condition, Rule, DistanceType, Trigger, ConditionGroup,
    SimulationTimeCondition, SpeedCondition,
    TraveledDistanceCondition, EntityTraveledDistanceCondition,
    StoryboardElementStateCondition,
    RelativeDistanceCondition, RelativeSpeedCondition,
    TimeHeadwayCondition, TimeToCollisionCondition,
)


# ---------------------------------------------------------------------------
# State snapshot types
# ---------------------------------------------------------------------------

@dataclass
class EntityState:
    """
    Minimal state for one entity at one timestep.
    Fields mirror the esmini SEScenarioObjectState that the converter reads.
    """
    entity_id: str
    x: float           # world position [m]
    y: float
    speed: float       # longitudinal speed [m/s]
    heading: float     # yaw angle [rad]
    length: float = 4.5   # bounding box [m], used for freespace correction
    width: float = 1.8


@dataclass
class SimState:
    """
    Full world snapshot passed into the evaluator once per simulation step.

    storyboard_element_states maps storyboard element names to their current
    OSC state string: "standby" | "running" | "complete".
    """
    time: float                                     # simulation time [s]
    entities: Dict[str, EntityState]                # entity_id → state
    storyboard_element_states: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _apply_rule(lhs: float, rule: Rule, rhs: float) -> bool:
    if rule == Rule.LESS_THAN:          return lhs < rhs
    if rule == Rule.LESS_OR_EQUAL:      return lhs <= rhs
    if rule == Rule.EQUAL_TO:           return math.isclose(lhs, rhs, rel_tol=1e-6)
    if rule == Rule.GREATER_OR_EQUAL:   return lhs >= rhs
    if rule == Rule.GREATER_THAN:       return lhs > rhs
    if rule == Rule.NOT_EQUAL_TO:       return not math.isclose(lhs, rhs, rel_tol=1e-6)
    return False


def _cartesian_dist(a: EntityState, b: EntityState) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _longitudinal_dist(ego: EntityState, other: EntityState) -> float:
    """Signed projection of (other − ego) onto ego's heading direction."""
    dx, dy = other.x - ego.x, other.y - ego.y
    return dx * math.cos(ego.heading) + dy * math.sin(ego.heading)


def _lateral_dist(ego: EntityState, other: EntityState) -> float:
    dx, dy = other.x - ego.x, other.y - ego.y
    return abs(-dx * math.sin(ego.heading) + dy * math.cos(ego.heading))


def _freespace_gap(a: EntityState, b: EntityState) -> float:
    """Subtract half the combined bounding-box length as a clearance correction."""
    return (a.length + b.length) / 2.0


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class ConditionEvaluator:
    """
    Evaluates a single OSC Condition against a SimState snapshot.

    TraveledDistance conditions need integration over time; they cannot be
    evaluated from a single snapshot.  The evaluator exposes an
    accumulated_distance dict that the caller must update each step:

        evaluator.accumulated_distance["Car1"] += distance_driven_this_step

    This makes the evaluator stateful but still purely functional per condition.
    """

    def __init__(self) -> None:
        # entity_id → total meters driven since simulation start
        self.accumulated_distance: Dict[str, float] = {}

    def update_distances(self, entity_id: str, delta_m: float) -> None:
        """Call once per step per entity with the distance driven this tick."""
        self.accumulated_distance[entity_id] = (
            self.accumulated_distance.get(entity_id, 0.0) + delta_m
        )

    # ------------------------------------------------------------------
    # Single-condition evaluation
    # ------------------------------------------------------------------

    def evaluate(self, cond: Condition, state: SimState) -> bool:
        """Return True if `cond` is satisfied given `state`."""

        if isinstance(cond, SimulationTimeCondition):
            return _apply_rule(state.time, cond.rule, cond.value)

        if isinstance(cond, SpeedCondition):
            # Applies to any entity — True if *any* entity satisfies it
            # (OSC ByValue SpeedCondition is global; if you need per-entity
            # semantics use RelativeSpeedCondition or a ByEntity variant)
            return any(
                _apply_rule(e.speed, cond.rule, cond.value)
                for e in state.entities.values()
            )

        if isinstance(cond, TraveledDistanceCondition):
            total = sum(self.accumulated_distance.values())
            return total >= cond.value

        if isinstance(cond, EntityTraveledDistanceCondition):
            dist = self.accumulated_distance.get(cond.entity_ref, 0.0)
            return dist >= cond.value

        if isinstance(cond, StoryboardElementStateCondition):
            actual = state.storyboard_element_states.get(cond.element_ref, "standby")
            return actual == cond.state

        if isinstance(cond, RelativeDistanceCondition):
            return self._eval_relative_distance(cond, state)

        if isinstance(cond, RelativeSpeedCondition):
            return self._eval_relative_speed(cond, state)

        if isinstance(cond, TimeHeadwayCondition):
            return self._eval_time_headway(cond, state)

        if isinstance(cond, TimeToCollisionCondition):
            return self._eval_ttc(cond, state)

        return False  # unknown condition type

    # ------------------------------------------------------------------
    # ByEntity helpers
    # ------------------------------------------------------------------

    def _get_pair(
        self,
        triggering_id: str,
        reference_id: str,
        state: SimState,
    ) -> Optional[tuple]:
        trig = state.entities.get(triggering_id)
        ref = state.entities.get(reference_id)
        if trig is None or ref is None:
            return None
        return trig, ref

    def _eval_relative_distance(
        self, cond: RelativeDistanceCondition, state: SimState
    ) -> bool:
        pair = self._get_pair(cond.triggering_entity, cond.reference_entity, state)
        if pair is None:
            return False
        trig, ref = pair

        if cond.distance_type == DistanceType.CARTESIAN:
            dist = _cartesian_dist(trig, ref)
        elif cond.distance_type == DistanceType.LONGITUDINAL:
            dist = abs(_longitudinal_dist(ref, trig))
        else:  # LATERAL
            dist = _lateral_dist(ref, trig)

        if cond.freespace:
            dist = max(0.0, dist - _freespace_gap(trig, ref))

        return _apply_rule(dist, cond.rule, cond.value)

    def _eval_relative_speed(
        self, cond: RelativeSpeedCondition, state: SimState
    ) -> bool:
        pair = self._get_pair(cond.triggering_entity, cond.reference_entity, state)
        if pair is None:
            return False
        trig, ref = pair
        rel_speed = abs(trig.speed - ref.speed)
        return _apply_rule(rel_speed, cond.rule, cond.value)

    def _eval_time_headway(
        self, cond: TimeHeadwayCondition, state: SimState
    ) -> bool:
        pair = self._get_pair(cond.triggering_entity, cond.reference_entity, state)
        if pair is None:
            return False
        trig, ref = pair

        long_dist = _longitudinal_dist(trig, ref)
        if cond.freespace:
            long_dist = max(0.0, long_dist - _freespace_gap(trig, ref))

        if trig.speed < 1e-3:  # stationary — THW undefined
            return False
        thw = long_dist / trig.speed
        return _apply_rule(thw, cond.rule, cond.value)

    def _eval_ttc(
        self, cond: TimeToCollisionCondition, state: SimState
    ) -> bool:
        pair = self._get_pair(cond.triggering_entity, cond.reference_entity, state)
        if pair is None:
            return False
        trig, ref = pair

        dist = _cartesian_dist(trig, ref)
        if cond.freespace:
            dist = max(0.0, dist - _freespace_gap(trig, ref))

        closing_speed = trig.speed - ref.speed
        if closing_speed <= 1e-3:  # not approaching
            return False
        ttc = dist / closing_speed
        return _apply_rule(ttc, cond.rule, cond.value)

    # ------------------------------------------------------------------
    # Trigger / ConditionGroup evaluation
    # ------------------------------------------------------------------

    def evaluate_group(self, group: ConditionGroup, state: SimState) -> bool:
        """All conditions in a group must hold (AND semantics)."""
        return all(self.evaluate(c, state) for c in group)

    def evaluate_trigger(self, trigger: Trigger, state: SimState) -> bool:
        """Any condition group must hold (OR semantics).
        An empty trigger (no ConditionGroups) is unconditionally true per the OSC spec."""
        if not trigger:
            return True
        return any(self.evaluate_group(g, state) for g in trigger)
