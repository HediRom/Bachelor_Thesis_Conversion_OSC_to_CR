"""
live.py
=======
Re-evaluates OpenSCENARIO conditions against CommonRoad motion — the piece
that makes the viewer *interactive* rather than a replay.

Two things live here:

``build_condition_timeline``
    Walks the converted trajectories once and records, for every parsed
    condition, whether it held at each time step.  The result is a truth
    matrix the viewer draws as activity strips under the timeline, so you can
    see a trigger arm and fire while scrubbing.

``LiveSession``
    Loads a converted bundle and answers what-if questions: move an entity to
    an arbitrary position/speed and ask which conditions would hold there.
    This is what the stock converter cannot do at all — once esmini has
    flattened the storyboard, the predicates are gone.

Entity ↔ obstacle mapping
-------------------------
The converter names nothing in the CommonRoad file, but it assigns obstacle
IDs deterministically (``osc2cr.py::_create_obstacles_from_state_lists``):
the ego vehicle gets the lowest ID, then the remaining entities in
alphabetical order.  :func:`map_obstacles_to_entities` reproduces that rule
from the ``.xosc`` entity list, which gives an exact mapping instead of a
guess.  If the counts disagree the mapping is reported as ``"positional"`` so
callers can treat the labels as unreliable.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import paths
from .params import resolve_entity_references

paths.bootstrap()

# The converter's default ego filter (utility/configuration.py)
_EGO_PATTERN = re.compile(r".*ego.*", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Entity mapping
# ---------------------------------------------------------------------------

def xosc_entity_names(xosc_path: str | Path) -> List[str]:
    """Entity names declared in ``<Entities>`` of an OpenSCENARIO file."""
    try:
        root = ET.parse(Path(xosc_path)).getroot()
    except (ET.ParseError, OSError):
        return []
    names: List[str] = []
    for obj in root.iter("ScenarioObject"):
        name = obj.get("name")
        if name:
            names.append(name)
    return names


def map_obstacles_to_entities(
    scenario: Any,
    xosc_path: str | Path,
    keep_ego_vehicle: bool = True,
) -> Tuple[Dict[int, str], str, Optional[str]]:
    """
    Reproduce the converter's obstacle-ID assignment.

    Returns ``(obstacle_id → entity_name, confidence, ego_name)`` where
    confidence is ``"exact"`` when the entity count matches the obstacle count
    and ``"positional"`` when it does not (some obstacle was dropped, so names
    may be off by one).
    """
    obstacle_ids = sorted(o.obstacle_id for o in scenario.dynamic_obstacles)
    names = xosc_entity_names(xosc_path)

    if not names:
        return (
            {oid: f"entity_{oid}" for oid in obstacle_ids},
            "positional",
            None,
        )

    ego_candidates = sorted(n for n in names if _EGO_PATTERN.match(n))
    ego = ego_candidates[0] if ego_candidates else sorted(names)[0]

    ordered = [ego] + sorted(n for n in names if n != ego)
    if not keep_ego_vehicle:
        ordered = ordered[1:]

    confidence = "exact" if len(ordered) == len(obstacle_ids) else "positional"
    mapping = {
        oid: (ordered[i] if i < len(ordered) else f"entity_{oid}")
        for i, oid in enumerate(obstacle_ids)
    }
    return mapping, confidence, ego


# ---------------------------------------------------------------------------
# Trajectory sampling
# ---------------------------------------------------------------------------

def _obstacle_state_lookup(obstacle: Any) -> Dict[int, Any]:
    """time_step → state, including the initial state."""
    lookup: Dict[int, Any] = {}
    if obstacle.initial_state is not None:
        lookup[obstacle.initial_state.time_step] = obstacle.initial_state
    pred = obstacle.prediction
    if pred is not None and pred.trajectory is not None:
        for st in pred.trajectory.state_list:
            lookup[st.time_step] = st
    return lookup


def sample_scenario(
    scenario: Any, xosc_path: str | Path, dt: float = 0.1,
) -> Tuple[List[int], Dict[int, Dict[str, Any]], Dict[int, str], str, Optional[str]]:
    """
    Flatten a CommonRoad scenario into per-time-step entity snapshots.

    Returns ``(time_steps, states, id_to_name, confidence, ego_name)`` where
    ``states`` maps time_step → {entity_name: {x, y, speed, heading, length,
    width, acceleration}}.

    ``acceleration`` is differentiated from the velocity series — CommonRoad
    states carry it only when the source provided it, and esmini-converted
    scenarios generally do not, but ``AccelerationCondition`` needs it.
    """
    id_to_name, confidence, ego = map_obstacles_to_entities(scenario, xosc_path)

    obstacles = sorted(scenario.dynamic_obstacles, key=lambda o: o.obstacle_id)
    lookups = {o.obstacle_id: _obstacle_state_lookup(o) for o in obstacles}

    all_steps = sorted({ts for lk in lookups.values() for ts in lk})
    states: Dict[int, Dict[str, Any]] = {}

    prev_speed: Dict[str, Tuple[int, float]] = {}
    for ts in all_steps:
        snapshot: Dict[str, Any] = {}
        for obs in obstacles:
            st = lookups[obs.obstacle_id].get(ts)
            if st is None:
                continue
            shape = obs.obstacle_shape
            name = id_to_name[obs.obstacle_id]
            speed = float(getattr(st, "velocity", 0.0) or 0.0)

            accel = getattr(st, "acceleration", None)
            if accel is None:
                last = prev_speed.get(name)
                if last is not None and ts > last[0]:
                    accel = (speed - last[1]) / ((ts - last[0]) * dt)
                else:
                    accel = 0.0
            prev_speed[name] = (ts, speed)

            snapshot[name] = {
                "x": float(st.position[0]),
                "y": float(st.position[1]),
                "speed": speed,
                "heading": float(getattr(st, "orientation", 0.0) or 0.0),
                "length": float(getattr(shape, "length", 4.5)),
                "width": float(getattr(shape, "width", 1.8)),
                "acceleration": float(accel),
            }
        states[ts] = snapshot

    return all_steps, states, id_to_name, confidence, ego


# ---------------------------------------------------------------------------
# Unified condition evaluation
# ---------------------------------------------------------------------------

class ConditionEngine:
    """
    One evaluation path for both taxonomies.

    Baseline conditions go to ``strategies.condition_evaluator.ConditionEvaluator``; the types added
    in ``conditions_ext`` go to :class:`ExtensionEvaluator`.  Returns ``None``
    for a condition that is understood but cannot be computed here, which
    callers must keep distinct from ``False``.
    """

    def __init__(self, resolver: Any = None) -> None:
        from osc2cr_extended.strategies.condition_evaluator import ConditionEvaluator

        from .conditions_ext import ExtensionEvaluator

        self.base = ConditionEvaluator()
        self.ext = ExtensionEvaluator(resolver)

    def update_distances(self, entity_id: str, delta_m: float) -> None:
        self.base.update_distances(entity_id, delta_m)

    def evaluate(
        self,
        cond: Any,
        sim_state: Any,
        entities: Dict[str, Dict[str, float]],
        time_s: float,
    ) -> Optional[bool]:
        from .conditions_ext import ExtCondition

        if isinstance(cond, ExtCondition):
            return self.ext.evaluate(cond, entities, time_s)
        try:
            return bool(self.base.evaluate(cond, sim_state))
        except Exception:  # noqa: BLE001 — a bad condition must not stop the run
            return False


# ---------------------------------------------------------------------------
# Condition inventory
# ---------------------------------------------------------------------------

@dataclass
class ConditionRef:
    """One parsed condition, tagged with where it sits in the storyboard."""

    key: str            # unique: "<event>::<condition name>::<index>"
    name: str
    ctype: str
    story: str
    act: str
    event: str
    scope: str          # "event" | "act_start" | "act_stop" | "storyboard_stop"
    condition: Any      # the parsed Condition dataclass


def collect_conditions(storyboard: Any) -> List[ConditionRef]:
    """Flatten every condition in the storyboard, keeping its context."""
    refs: List[ConditionRef] = []
    counter = 0

    def add(cond: Any, story: str, act: str, event: str, scope: str) -> None:
        nonlocal counter
        # extension conditions report the OpenSCENARIO type they stand for,
        # not the Python class that happens to carry them
        ctype = getattr(cond, "type_name", None) or type(cond).__name__
        refs.append(ConditionRef(
            key=f"{event or act}::{getattr(cond, 'name', '') or ctype}::{counter}",
            name=getattr(cond, "name", "") or ctype,
            ctype=ctype,
            story=story, act=act, event=event, scope=scope,
            condition=cond,
        ))
        counter += 1

    for story in storyboard.stories:
        for act in story.acts:
            for group in (act.start_trigger or []):
                for cond in group:
                    add(cond, story.name, act.name, "", "act_start")
            for group in (act.stop_trigger or []):
                for cond in group:
                    add(cond, story.name, act.name, "", "act_stop")
            for mg in act.maneuver_groups:
                for maneuver in mg.maneuvers:
                    for event in maneuver.events:
                        for group in (event.start_trigger or []):
                            for cond in group:
                                add(cond, story.name, act.name, event.name, "event")

    for group in (getattr(storyboard, "stop_trigger", None) or []):
        for cond in group:
            add(cond, "", "", "", "storyboard_stop")

    return refs


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

def build_condition_timeline(
    scenario: Any, storyboard: Any, xosc_path: str | Path, dt: float = 0.1,
    resolver: Any = None,
) -> Dict[str, Any]:
    """
    Evaluate every condition at every time step of the converted motion.

    ``xosc_path`` is required: ByEntity conditions reference entities by their
    OpenSCENARIO name, so the obstacle→name mapping must come from the source
    file, not from the CommonRoad IDs.

    The returned document is written to ``timeline.json`` and consumed by the
    viewer: ``conditions[i].values`` is a list of 0/1, one per entry in
    ``time_steps``.
    """
    from osc2cr_extended.strategies.condition_evaluator import EntityState, SimState

    time_steps, states, id_to_name, confidence, ego_name = sample_scenario(
        scenario, xosc_path, dt,
    )
    id_to_name_json = {str(k): v for k, v in id_to_name.items()}

    refs = collect_conditions(storyboard)
    if not refs or not time_steps:
        return {
            "dt": dt,
            "time_steps": time_steps,
            "entities": list(id_to_name.values()),
            "id_to_name": id_to_name_json,
            "ego": ego_name,
            "mapping_confidence": confidence,
            "conditions": [],
        }

    engine = ConditionEngine(resolver)
    values: List[List[int]] = [[] for _ in refs]
    evaluable: List[bool] = [False for _ in refs]

    prev_step: Optional[int] = None
    for ts in time_steps:
        snapshot = states.get(ts, {})
        entities = {
            name: EntityState(
                entity_id=name,
                x=s["x"], y=s["y"], speed=s["speed"], heading=s["heading"],
                length=s["length"], width=s["width"],
            )
            for name, s in snapshot.items()
        }

        # Integrate traveled distance so distance conditions can be evaluated
        if prev_step is not None:
            step_dt = (ts - prev_step) * dt
            for name, ent in entities.items():
                engine.update_distances(name, ent.speed * step_dt)
        prev_step = ts

        time_s = round(ts * dt, 6)
        sim_state = SimState(time=time_s, entities=entities)

        for i, ref in enumerate(refs):
            held = engine.evaluate(ref.condition, sim_state, snapshot, time_s)
            if held is not None:
                evaluable[i] = True
            values[i].append(1 if held else 0)

    conditions = []
    for i, ref in enumerate(refs):
        series = values[i]
        first_true = next((j for j, v in enumerate(series) if v), None)
        cond_obj = ref.condition
        conditions.append({
            "key": ref.key,
            "name": ref.name,
            "type": ref.ctype,
            "story": ref.story,
            "act": ref.act,
            "event": ref.event,
            "scope": ref.scope,
            "values": series,
            "first_true_step": time_steps[first_true] if first_true is not None else None,
            "true_steps": sum(series),
            "evaluable": evaluable[i],
            "text": (cond_obj.describe() if hasattr(cond_obj, "describe") else None),
            "unevaluable_reason": getattr(cond_obj, "unevaluable_reason", None),
        })

    return {
        "dt": dt,
        "time_steps": time_steps,
        "entities": sorted({n for snap in states.values() for n in snap}),
        "id_to_name": id_to_name_json,
        "ego": ego_name,
        "mapping_confidence": confidence,
        "conditions": conditions,
    }


# ---------------------------------------------------------------------------
# Edge-aware execution
# ---------------------------------------------------------------------------

class EdgeAwareExecutor:
    """
    Fires storyboard events honouring each condition's ``conditionEdge``.

    ``strategies.interpretation.StoryboardExecutor`` compares condition *values* only: an event
    fires on every step its trigger evaluates true.  OpenSCENARIO instead says
    a condition with ``conditionEdge="rising"`` is satisfied at the *moment* it
    goes false→true, not for as long as it stays true.

    The difference is not cosmetic.  ``drive_when_close.xosc`` declares two
    events with ``maximumExecutionCount="100"`` and rising-edge relative
    distance conditions; evaluating values alone re-fires them on every step
    the vehicles are close, exhausting all 100 executions and reporting 100
    "fires" for what is really two.

    Semantics implemented here:

    ==================  ====================================================
    ``rising``          satisfied on the false→true transition
    ``falling``         satisfied on the true→false transition
    ``risingOrFalling`` satisfied on any change
    ``none``            satisfied whenever the value is true (no edge)
    ==================  ====================================================

    Edges are per *condition*; groups AND their conditions and triggers OR
    their groups, as in the OpenSCENARIO model.  Acts latch: once an act's
    start trigger has been satisfied the act stays running, which matters
    because a rising-edge act trigger is only satisfied for a single step.

    ``delay`` is still not modelled — see the README's known limits.
    """

    def __init__(self, storyboard: Any, resolver: Any = None) -> None:
        self.storyboard = storyboard
        self.engine = ConditionEngine(resolver)
        self._prev: Dict[int, bool] = {}       # id(condition) → previous value
        self._act_running: Dict[str, bool] = {}
        self._exec_counts: Dict[str, int] = {}
        #: conditions that could not be computed at all during this replay
        self.unevaluable: set = set()
        self._entities: Dict[str, Dict[str, float]] = {}
        self._time_s: float = 0.0

    # backwards-compatible alias: callers used to reach the raw evaluator
    @property
    def evaluator(self) -> Any:
        return self.engine.base

    # ------------------------------------------------------------------

    def _condition_satisfied(self, cond: Any, state: Any) -> bool:
        """Apply the condition's edge to its raw value."""
        from osc2cr_extended.strategies.shared.condition_model import ConditionEdge

        raw = self.engine.evaluate(cond, state, self._entities, self._time_s)
        if raw is None:
            # Understood but not computable here — never fires, and never
            # pretends the predicate was simply false.
            self.unevaluable.add(getattr(cond, "name", "") or type(cond).__name__)
            return False
        value = bool(raw)

        key = id(cond)
        # esmini initialises a condition's previous result to false, so a
        # condition that is already true on the first evaluated step counts as
        # a rising edge and triggers there.
        previous = self._prev.get(key, False)
        self._prev[key] = value

        edge = getattr(cond, "edge", None)
        if edge == ConditionEdge.RISING:
            return value and not previous
        if edge == ConditionEdge.FALLING:
            return (not value) and previous
        if edge == ConditionEdge.RISING_OR_FALLING:
            return value != previous
        return value  # ConditionEdge.NONE, or unspecified

    def _trigger_satisfied(self, trigger: Any, state: Any) -> bool:
        """
        OR of groups, AND within a group. An empty trigger is always true.

        Every condition is evaluated every step, deliberately: edge detection
        needs each condition's previous value, so short-circuiting an AND or an
        OR would leave the skipped conditions holding a stale value and fire
        them spuriously later.  Hence the fully-materialised lists rather than
        the usual lazy ``all``/``any`` over generators.
        """
        if not trigger:
            return True
        group_results = [
            all([self._condition_satisfied(c, state) for c in group])
            for group in trigger
        ]
        return any(group_results)

    # ------------------------------------------------------------------

    def step(
        self,
        state: Any,
        entities: Optional[Dict[str, Dict[str, float]]] = None,
        time_s: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Advance one tick; return the events that fired."""
        self._entities = entities or {}
        self._time_s = time_s if time_s is not None else getattr(state, "time", 0.0)
        fired: List[Dict[str, Any]] = []

        for story in self.storyboard.stories:
            for act in story.acts:
                act_key = f"{story.name}/{act.name}"
                if not self._act_running.get(act_key):
                    if self._trigger_satisfied(act.start_trigger, state):
                        self._act_running[act_key] = True
                    else:
                        continue

                for mg in act.maneuver_groups:
                    for maneuver in mg.maneuvers:
                        for event in maneuver.events:
                            key = f"{act_key}/{event.name}"
                            count = self._exec_counts.get(key, 0)
                            if count >= event.max_execution_count:
                                # still evaluate, to keep edge state coherent
                                self._trigger_satisfied(event.start_trigger, state)
                                continue

                            if self._trigger_satisfied(event.start_trigger, state):
                                count += 1
                                self._exec_counts[key] = count
                                fired.append({
                                    "story": story.name,
                                    "act": act.name,
                                    "event": event.name,
                                    "actors": list(mg.actor_refs),
                                    "fire_count": count,
                                })
        return fired


# ---------------------------------------------------------------------------
# Interpretation replay
# ---------------------------------------------------------------------------

def replay_storyboard(
    scenario: Any,
    storyboard: Any,
    xosc_path: str | Path,
    dt: float = 0.1,
    executor: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Step the parsed storyboard through the converted trajectories and log which
    events fire when.

    Same output shape as ``merge.EnrichedScenario._replay_trajectories`` — a
    list of ``{time_s, story, act, event, actors, fire_count}`` — but the
    obstacle→entity mapping comes from :func:`map_obstacles_to_entities`
    instead of the order in which actor names happen to appear in the parsed
    conditions.  That ordering assigns the ego's trajectory to the wrong entity
    whenever the first-seen actor is not the ego, which inverts every relative
    condition in the trace.

    Pass ``executor`` to reuse an already-built :class:`EdgeAwareExecutor`
    (the pipeline does, so the cost of building it is billed to its own stage).
    """
    from osc2cr_extended.strategies.condition_evaluator import EntityState, SimState

    time_steps, states, _id_to_name, _confidence, _ego = sample_scenario(
        scenario, xosc_path, dt,
    )
    if not time_steps:
        return []

    if executor is None:
        executor = EdgeAwareExecutor(storyboard)
    trace: List[Dict[str, Any]] = []

    # Events whose start trigger is empty fire unconditionally on the first
    # step.  Flag them so a dropped condition cannot masquerade as a
    # reconstructed trigger (see coverage.py).
    from .coverage import unconditional_events
    empty_trigger_events = set(unconditional_events(storyboard))

    prev_step: Optional[int] = None
    for ts in time_steps:
        entities = {
            name: EntityState(
                entity_id=name,
                x=s["x"], y=s["y"], speed=s["speed"], heading=s["heading"],
                length=s["length"], width=s["width"],
            )
            for name, s in states.get(ts, {}).items()
        }
        if not entities:
            continue

        if prev_step is not None:
            step_dt = (ts - prev_step) * dt
            for name, ent in entities.items():
                executor.engine.update_distances(name, ent.speed * step_dt)
        prev_step = ts

        time_s = round(ts * dt, 6)
        sim_state = SimState(time=time_s, entities=entities)
        snapshot = states.get(ts, {})
        for ev in executor.step(sim_state, snapshot, time_s):
            trace.append({
                "time_s": round(ts * dt, 6),
                "time_step": ts,
                "story": ev["story"],
                "act": ev["act"],
                "event": ev["event"],
                "actors": ev["actors"],
                "fire_count": ev["fire_count"],
                "unconditional": ev["event"] in empty_trigger_events,
            })

    return trace


# ---------------------------------------------------------------------------
# Interactive session
# ---------------------------------------------------------------------------

def _resolver_for(manifest: Dict[str, Any]) -> Any:
    """A lane-position resolver for the bundle's road network, if we have one."""
    from .roadmanager import LanePositionResolver

    xodr = (manifest.get("stats", {}).get("road_network") or {}).get("xodr_file")
    return LanePositionResolver(xodr) if xodr else None

class LiveSession:
    """
    Re-evaluates a bundle's conditions on demand.

    Built from a converted bundle directory; re-parses the source ``.xosc`` so
    the full condition model (not just its JSON projection) is available.
    """

    def __init__(self, bundle_dir: str | Path) -> None:
        from osc2cr_extended.strategies.shared.storyboard_parser import StoryboardParser

        self.bundle_dir = Path(bundle_dir)
        manifest_path = self.bundle_dir / "bundle.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"No bundle.json in {self.bundle_dir}")

        self.manifest = json.loads(manifest_path.read_text())
        self.xosc_path = self.manifest["xosc_path"]
        self.dt = float(self.manifest.get("stats", {}).get("dt") or 0.1)

        timeline_file = self.bundle_dir / "timeline.json"
        self.timeline = (
            json.loads(timeline_file.read_text()) if timeline_file.exists() else {}
        )
        if self.timeline.get("dt"):
            self.dt = float(self.timeline["dt"])

        from .conditions_ext import attach_extensions
        from .params import load_parameters

        self.storyboard = StoryboardParser(self.xosc_path).parse()
        # Same resolution the pipeline applies — without it, conditions still
        # reference "$owner" and every ByEntity predicate evaluates to False.
        self.entity_refs_resolved, self.unresolved_refs = resolve_entity_references(
            self.storyboard, self.xosc_path,
        )
        # …and the same taxonomy extension, so what-if covers exactly the
        # conditions the bundle carries.
        attach_extensions(
            self.storyboard, self.xosc_path, load_parameters(self.xosc_path),
        )
        self.conditions = collect_conditions(self.storyboard)
        self.resolver = _resolver_for(self.manifest)

    # ------------------------------------------------------------------

    def evaluate_state(
        self,
        entities: Dict[str, Dict[str, float]],
        time_s: float = 0.0,
        traveled: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate every condition against a caller-supplied world state.

        ``entities`` maps entity name → ``{x, y, speed, heading, length, width}``.
        This is the what-if endpoint: drag an actor in the viewer and see which
        predicates flip.
        """
        from osc2cr_extended.strategies.condition_evaluator import EntityState, SimState

        engine = ConditionEngine(getattr(self, "resolver", None))
        for name, dist in (traveled or {}).items():
            engine.update_distances(name, float(dist))

        normalised = {
            name: {
                "x": float(e.get("x", 0.0)),
                "y": float(e.get("y", 0.0)),
                "speed": float(e.get("speed", 0.0)),
                "heading": float(e.get("heading", 0.0)),
                "length": float(e.get("length", 4.5)),
                "width": float(e.get("width", 1.8)),
                "acceleration": float(e.get("acceleration", 0.0)),
            }
            for name, e in entities.items()
        }

        state = SimState(
            time=float(time_s),
            entities={
                name: EntityState(
                    entity_id=name, x=e["x"], y=e["y"], speed=e["speed"],
                    heading=e["heading"], length=e["length"], width=e["width"],
                )
                for name, e in normalised.items()
            },
        )

        results = []
        for ref in self.conditions:
            held = engine.evaluate(ref.condition, state, normalised, float(time_s))
            results.append({
                "key": ref.key,
                "name": ref.name,
                "type": ref.ctype,
                "event": ref.event,
                "act": ref.act,
                "scope": ref.scope,
                # None means "understood but not computable from this state"
                "holds": bool(held),
                "evaluable": held is not None,
                "error": None,
            })

        return {
            "time_s": time_s,
            "entities": list(entities),
            "conditions": results,
            "n_holding": sum(1 for r in results if r["holds"]),
            "n_unevaluable": sum(1 for r in results if not r["evaluable"]),
        }
