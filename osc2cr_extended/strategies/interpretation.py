"""
Interpretation (orchestration layer)

Drives a ParsedStoryboard forward one simulation timestep at a time.
Each call to step() evaluates all event start triggers and returns the
events that fired this tick.  An ExecutionTrace accumulates the full log.

This is the PoC upper-bound: full reactivity restored, but requires the
caller to feed in per-tick entity states and to handle the fired events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import sys
from pathlib import Path


from osc2cr_extended.strategies.shared.condition_model import ParsedStoryboard
from osc2cr_extended.strategies.shared.storyboard_parser import StoryboardParser
from .condition_evaluator import ConditionEvaluator, SimState


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class FiredEvent:
    time: float
    story: str
    act: str
    event_name: str
    actors: List[str]
    execution_count: int   # 1-based: how many times this event has fired total


@dataclass
class ExecutionTrace:
    """Complete log of which events fired and when."""
    fired_events: List[FiredEvent] = field(default_factory=list)

    def summary(self) -> str:
        if not self.fired_events:
            return "ExecutionTrace: no events fired."
        lines = [f"ExecutionTrace — {len(self.fired_events)} fires:"]
        for e in self.fired_events:
            actors = ", ".join(e.actors) or "(no actors)"
            lines.append(
                f"  t={e.time:7.3f}s  #{e.execution_count:2d}  "
                f"{e.story}/{e.act}/{e.event_name}  [{actors}]"
            )
        return "\n".join(lines)

    def events_at(self, time: float, tol: float = 1e-6) -> List[FiredEvent]:
        return [e for e in self.fired_events if abs(e.time - time) <= tol]


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class StoryboardExecutor:
    """
    Drives a ParsedStoryboard one tick at a time.

    Usage
    -----
    executor = StoryboardExecutor.from_xosc("scenario.xosc")

    for tick in simulation_loop:
        state = SimState(
            time=tick.time,
            entities={
                "Ego":  EntityState("Ego",  x=..., y=..., speed=..., heading=...),
                "NPC1": EntityState("NPC1", x=..., y=..., speed=..., heading=...),
            },
        )
        # Update traveled distances from esmini deltas before calling step()
        executor.evaluator.update_distances("Ego",  ego_delta_m)
        executor.evaluator.update_distances("NPC1", npc_delta_m)

        fired = executor.step(state)
        for event in fired:
            print(f"  → {event.event_name} fired at t={event.time:.2f}s")

    print(executor.trace.summary())
    """

    def __init__(self, storyboard: ParsedStoryboard) -> None:
        self.storyboard = storyboard
        self.evaluator = ConditionEvaluator()
        self.trace = ExecutionTrace()
        # key: "story/act/event" → fires so far
        self._exec_counts: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public step interface
    # ------------------------------------------------------------------

    def step(self, state: SimState) -> List[FiredEvent]:
        """
        Evaluate all event start triggers against `state`.

        Act start triggers gate the whole act: if an act's start trigger
        is not satisfied yet, none of its events are checked.

        Returns the list of events that fired this tick.
        """
        newly_fired: List[FiredEvent] = []

        for story in self.storyboard.stories:
            for act in story.acts:
                if not self.evaluator.evaluate_trigger(act.start_trigger, state):
                    continue

                for mg in act.maneuver_groups:
                    for maneuver in mg.maneuvers:
                        for event in maneuver.events:
                            key = f"{story.name}/{act.name}/{event.name}"
                            fires_so_far = self._exec_counts.get(key, 0)

                            if fires_so_far >= event.max_execution_count:
                                continue

                            if self.evaluator.evaluate_trigger(event.start_trigger, state):
                                fires_so_far += 1
                                self._exec_counts[key] = fires_so_far

                                fired = FiredEvent(
                                    time=state.time,
                                    story=story.name,
                                    act=act.name,
                                    event_name=event.name,
                                    actors=list(mg.actor_refs),
                                    execution_count=fires_so_far,
                                )
                                self.trace.fired_events.append(fired)
                                newly_fired.append(fired)

        return newly_fired

    def reset(self) -> None:
        """Reset execution state (counts + trace) without re-parsing."""
        self._exec_counts.clear()
        self.trace = ExecutionTrace()
        self.evaluator = ConditionEvaluator()

    # ------------------------------------------------------------------
    # Convenience constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_storyboard(cls, storyboard: ParsedStoryboard) -> "StoryboardExecutor":
        return cls(storyboard)

    @classmethod
    def from_xosc(cls, xosc_path: str) -> "StoryboardExecutor":
        """Parse the .xosc and build an executor ready to step."""
        storyboard = StoryboardParser(xosc_path).parse()
        return cls(storyboard)
