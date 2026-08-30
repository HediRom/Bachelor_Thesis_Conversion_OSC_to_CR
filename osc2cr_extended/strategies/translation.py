"""
Translation

Maps ByValue trigger conditions onto CommonRoad's first-class constructs.
Only conditions that have a faithful CR analogue are mapped; everything
else is logged in MappingReport.skipped so the caller knows what was lost.

Mappable conditions
-------------------
SimulationTimeCondition  → GoalState.time_step  (Interval over time steps)
SpeedCondition           → GoalState.velocity   (Interval over m/s)

Not mappable here (→ Interpretation)
---------------------------------
RelativeDistanceCondition, RelativeSpeedCondition, TimeHeadwayCondition,
TimeToCollisionCondition — all require comparing two entities at runtime.

Fidelity  : partial — faithful where a CR analogue exists
Coverage  : narrow  — only conditions with a CR equivalent
Cost      : medium  — per-condition mapping logic
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import sys
from pathlib import Path


from osc2cr_extended.strategies.shared.condition_model import (
    ParsedStoryboard, Condition, Trigger,
    SimulationTimeCondition, SpeedCondition,
    TraveledDistanceCondition, StoryboardElementStateCondition,
    EntityTraveledDistanceCondition,
    RelativeDistanceCondition, RelativeSpeedCondition,
    TimeHeadwayCondition, TimeToCollisionCondition,
    Rule,
)
from osc2cr_extended.strategies.shared.storyboard_parser import StoryboardParser


# ---------------------------------------------------------------------------
# Interval helpers
# ---------------------------------------------------------------------------

@dataclass
class FloatInterval:
    start: float
    end: float

    def __repr__(self) -> str:
        end_str = "∞" if self.end == float("inf") else str(self.end)
        return f"[{self.start}, {end_str}]"


def _rule_to_interval(value: float, rule: Rule, eps: float = 0.0) -> FloatInterval:
    """Convert a threshold + comparison rule into a closed interval."""
    INF = float("inf")
    if rule == Rule.GREATER_THAN:
        return FloatInterval(start=value + eps, end=INF)
    elif rule == Rule.GREATER_OR_EQUAL:
        return FloatInterval(start=value, end=INF)
    elif rule == Rule.LESS_THAN:
        return FloatInterval(start=0.0, end=max(0.0, value - eps))
    elif rule == Rule.LESS_OR_EQUAL:
        return FloatInterval(start=0.0, end=value)
    elif rule == Rule.EQUAL_TO:
        return FloatInterval(start=value, end=value)
    else:  # NOT_EQUAL_TO — unbounded; cannot be represented faithfully
        return FloatInterval(start=0.0, end=INF)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class TimeGoal:
    """A time-step interval derived from a SimulationTimeCondition."""
    source_condition: str
    time_interval_s: FloatInterval
    time_step_interval: Tuple[int, int]  # (start_step, end_step)


@dataclass
class VelocityGoal:
    """A velocity interval derived from a SpeedCondition."""
    source_condition: str
    velocity_interval_ms: FloatInterval


@dataclass
class MappingReport:
    mapped_time: List[TimeGoal]
    mapped_velocity: List[VelocityGoal]
    skipped: List[Tuple[str, str]]  # (condition_name, reason)

    def summary(self) -> str:
        lines = ["=== Translation Report ==="]
        if self.mapped_time:
            lines.append(f"Time goals ({len(self.mapped_time)}):")
            for g in self.mapped_time:
                lines.append(f"  '{g.source_condition}' → time_step ∈ "
                             f"[{g.time_step_interval[0]}, {g.time_step_interval[1]}]  "
                             f"({g.time_interval_s})")
        if self.mapped_velocity:
            lines.append(f"Velocity goals ({len(self.mapped_velocity)}):")
            for g in self.mapped_velocity:
                lines.append(f"  '{g.source_condition}' → velocity ∈ "
                             f"{g.velocity_interval_ms} m/s")
        if self.skipped:
            lines.append(f"Skipped ({len(self.skipped)}):")
            for name, reason in self.skipped:
                lines.append(f"  '{name}' — {reason}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        """
        Per-condition mapping outcome, keyed by condition name — the same
        keying convention as Transcription's conditions_transcription.json, so the
        two outputs can be cross-referenced for the same source .xosc.

        Unbounded interval ends (float('inf')) are serialised as None so
        the result is strict JSON.
        """
        def _end(value: float) -> Optional[float]:
            return None if value == float("inf") else value

        out: Dict[str, Dict[str, Any]] = {}
        for g in self.mapped_time:
            out[g.source_condition] = {
                "status": "mapped_time",
                "time_interval_s": {"start": g.time_interval_s.start, "end": _end(g.time_interval_s.end)},
                "time_step_interval": list(g.time_step_interval),
            }
        for g in self.mapped_velocity:
            out[g.source_condition] = {
                "status": "mapped_velocity",
                "velocity_interval_ms": {
                    "start": g.velocity_interval_ms.start,
                    "end": _end(g.velocity_interval_ms.end),
                },
            }
        for name, reason in self.skipped:
            out[name] = {"status": "skipped", "reason": reason}
        return out


@dataclass
class MappedPlanningProblem:
    """
    Enriched planning problem expressed as CommonRoad-compatible goal intervals.

    Rather than mutating the actual PlanningProblem object (which requires
    commonroad-io to be installed), we expose the derived intervals here so
    the caller can inject them into whatever CR object they hold.
    """
    original_problem_id: int
    time_goals: List[TimeGoal]
    velocity_goals: List[VelocityGoal]

    def merged_time_step_interval(self) -> Optional[Tuple[int, int]]:
        """Merge all time goals into a single bounding interval."""
        if not self.time_goals:
            return None
        start = min(g.time_step_interval[0] for g in self.time_goals)
        ends = [g.time_step_interval[1] for g in self.time_goals]
        end = max(e for e in ends)
        return (start, end)

    def merged_velocity_interval(self) -> Optional[FloatInterval]:
        """Merge all velocity goals into a single bounding interval."""
        if not self.velocity_goals:
            return None
        start = min(g.velocity_interval_ms.start for g in self.velocity_goals)
        end = max(g.velocity_interval_ms.end for g in self.velocity_goals)
        return FloatInterval(start=start, end=end)


# ---------------------------------------------------------------------------
# Condition classifier
# ---------------------------------------------------------------------------

_UNMAPPABLE_REASONS: Dict[type, str] = {
    RelativeDistanceCondition: "ByEntity — needs runtime state comparison (→ Interpretation)",
    RelativeSpeedCondition:    "ByEntity — needs runtime state comparison (→ Interpretation)",
    TimeHeadwayCondition:      "ByEntity — needs runtime state comparison (→ Interpretation)",
    TimeToCollisionCondition:  "ByEntity — needs runtime state comparison (→ Interpretation)",
    TraveledDistanceCondition: "global traveled distance — no CR goal analogue",
    EntityTraveledDistanceCondition: "entity traveled distance — no CR goal analogue",
    StoryboardElementStateCondition: "storyboard meta-condition — no CR analogue",
}


def _map_condition(
    cond: Condition,
    dt: float,
    report: MappingReport,
) -> Optional[Any]:
    """Return a TimeGoal or VelocityGoal, or None if not mappable."""
    if isinstance(cond, SimulationTimeCondition):
        iv = _rule_to_interval(cond.value, cond.rule, eps=dt)
        ts_start = max(0, int(iv.start / dt))
        ts_end = int(iv.end / dt) if iv.end != float("inf") else 999_999
        goal = TimeGoal(
            source_condition=cond.name,
            time_interval_s=iv,
            time_step_interval=(ts_start, ts_end),
        )
        report.mapped_time.append(goal)
        return goal

    if isinstance(cond, SpeedCondition):
        iv = _rule_to_interval(cond.value, cond.rule, eps=0.0)
        goal = VelocityGoal(source_condition=cond.name, velocity_interval_ms=iv)
        report.mapped_velocity.append(goal)
        return goal

    reason = _UNMAPPABLE_REASONS.get(type(cond), f"{type(cond).__name__} — not yet handled")
    report.skipped.append((cond.name, reason))
    return None


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def map_storyboard(
    storyboard: ParsedStoryboard,
    problem_ids: Optional[List[int]] = None,
    dt: float = 0.1,
) -> Tuple[List[MappedPlanningProblem], MappingReport]:
    """
    Derive CR-compatible goal intervals from all mappable trigger conditions.

    Parameters
    ----------
    storyboard   : ParsedStoryboard from StoryboardParser.parse().
    problem_ids  : IDs of existing PlanningProblems to enrich.  If None,
                   a single synthetic problem with id=0 is returned.
    dt           : Simulation time step [s], used to convert times to steps.

    Returns
    -------
    (problems, report)
        problems  — one MappedPlanningProblem per id in problem_ids.
        report    — what was mapped and what was skipped.
    """
    report = MappingReport(mapped_time=[], mapped_velocity=[], skipped=[])

    # Collect all conditions from Act start triggers and the global stop trigger
    all_conditions: List[Condition] = []

    for story in storyboard.stories:
        for act in story.acts:
            for cg in act.start_trigger:
                all_conditions.extend(cg)
            if act.stop_trigger:
                for cg in act.stop_trigger:
                    all_conditions.extend(cg)
            for mg in act.maneuver_groups:
                for maneuver in mg.maneuvers:
                    for event in maneuver.events:
                        for cg in event.start_trigger:
                            all_conditions.extend(cg)

    if storyboard.stop_trigger:
        for cg in storyboard.stop_trigger:
            all_conditions.extend(cg)

    # Map each condition; classify into time / velocity goals
    for cond in all_conditions:
        _map_condition(cond, dt, report)

    # Build one MappedPlanningProblem per requested id
    ids = problem_ids if problem_ids is not None else [0]
    problems = [
        MappedPlanningProblem(
            original_problem_id=pid,
            time_goals=list(report.mapped_time),
            velocity_goals=list(report.mapped_velocity),
        )
        for pid in ids
    ]

    return problems, report


def enrich_planning_problem_set(
    original_pps: Any,
    mapped_problems: List[MappedPlanningProblem],
) -> Any:
    """
    Return a new PlanningProblemSet where each problem's goal states have
    time_step intervals (and velocity intervals when available) derived from
    the mapped trigger conditions.

    Requires commonroad-io to be installed.  Called from merge.py when
    writing the Translation output file.
    """
    from commonroad.planning.goal import GoalRegion
    from commonroad.planning.planning_problem import PlanningProblem, PlanningProblemSet
    from commonroad.scenario.state import CustomState
    from commonroad.common.util import Interval

    enriched: List[PlanningProblem] = []

    for mp in mapped_problems:
        pp = original_pps.find_planning_problem_by_id(mp.original_problem_id)
        if pp is None:
            continue

        ts_interval = mp.merged_time_step_interval()
        vel_interval = mp.merged_velocity_interval()

        new_goal_states = []
        for gs in pp.goal.state_list:
            kwargs: Dict[str, Any] = {}

            # Carry over position if the original goal had one
            if hasattr(gs, "position") and gs.position is not None:
                kwargs["position"] = gs.position

            # Carry over original velocity if nothing from conditions
            if vel_interval is not None and vel_interval.end != float("inf"):
                kwargs["velocity"] = Interval(
                    start=vel_interval.start, end=vel_interval.end
                )
            elif hasattr(gs, "velocity") and gs.velocity is not None:
                kwargs["velocity"] = gs.velocity

            # Inject time constraint from trigger conditions
            if ts_interval is not None:
                kwargs["time_step"] = Interval(
                    start=ts_interval[0], end=ts_interval[1]
                )
            elif hasattr(gs, "time_step") and gs.time_step is not None:
                kwargs["time_step"] = gs.time_step

            new_goal_states.append(CustomState(**kwargs))

        enriched.append(
            PlanningProblem(
                planning_problem_id=pp.planning_problem_id,
                initial_state=pp.initial_state,
                goal_region=GoalRegion(state_list=new_goal_states),
            )
        )

    return PlanningProblemSet(enriched)


def from_xosc(
    xosc_path: str,
    problem_ids: Optional[List[int]] = None,
    dt: float = 0.1,
) -> Tuple[List[MappedPlanningProblem], MappingReport]:
    """Parse the .xosc and map in one call."""
    storyboard = StoryboardParser(xosc_path).parse()
    return map_storyboard(storyboard, problem_ids=problem_ids, dt=dt)
