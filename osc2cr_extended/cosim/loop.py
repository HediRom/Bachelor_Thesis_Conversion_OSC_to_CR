"""
cosim.py
========
Closed-loop co-simulation — a CommonRoad motion planner drives the ego *inside*
the esmini OpenSCENARIO player, while the storyboard's conditions are observed
from both sides at once.

Why this exists
---------------
Converting a scenario and replaying it (``pipeline.py``) answers "what did the
storyboard do?".  It cannot answer "what would the storyboard do *to my
planner*?" — the traffic follows a trajectory recorded against esmini's own
ego, so a maneuver written as "cut in when the ego is 0.4 s behind" happens at
whatever second it happened during the recording, whoever is driving.  Putting
the planner inside the loop restores the coupling: esmini re-evaluates the
storyboard against the planner's actual ego, and the maneuvers re-time
themselves.

Two drivers
-----------
``esmini``
    esmini drives every entity, the scenario exactly as authored.  Nothing is
    rewritten.  This is the *validation* mode: our condition implementation and
    esmini's run side by side on identical world states with no planner in
    between, so any disagreement is a defect in one of the two.

``planner``
    the ego is externalised (see :func:`externalize_ego`) and driven by
    commonroad-rp.  The storyboard re-times around it.

The differential oracle
-----------------------
esmini exports two observation hooks that the converter never uses:

``SE_RegisterConditionCallback(name, timestamp)``
    every time a condition *triggers* — edge semantics already applied.
``SE_RegisterStoryBoardElementStateChangeCallback(name, type, state)``
    every act/event/action state transition.

Because the planner's ego is reported back into esmini, both players see the
same world at the same tick.  Comparing esmini's condition stream against
:class:`~osc2cr.live.EdgeAwareExecutor`'s is therefore a genuine differential
test of ``conditions_ext`` + the edge semantics against the reference
implementation, rather than an argument from inspection.

Version pinning
---------------
The signatures and enum values below were read from the *bundled* esmini
v2.29.3 header and then confirmed against the running library — they differ
from esmini master, so do not "fix" them from upstream docs:

* ``SE_RegisterStoryBoardElementStateChangeCallback`` takes **three** arguments
  in v2.29.3; master added a fourth (``full_path``).
* master's ``ElementType`` gained ``STORY_BOARD = 1`` at the front, shifting
  every other member up by one relative to v2.29.3.

This mirrors the note in ``cosim/esmini_interface.py`` about
``SE_ReportObjectPosXYH`` gaining a leading timestamp argument.
"""
from __future__ import annotations

import ctypes as ct
import json
import math
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import paths

paths.bootstrap()

from ..live import (  # noqa: E402  (import after bootstrap by design)
    EdgeAwareExecutor,
    LiveSession,
    map_obstacles_to_entities,
)

# ---------------------------------------------------------------------------
# esmini v2.29.3 constants
# ---------------------------------------------------------------------------

#: StoryboardElement::ElementType as emitted by the *bundled* v2.29.3 library.
#: Calibrated against a live run (cut-in_simple reports its ManeuverGroup as 3,
#: its Event as 5); master's header numbers these one higher.
ELEMENT_TYPE = {
    1: "Story", 2: "Act", 3: "ManeuverGroup",
    4: "Maneuver", 5: "Event", 6: "Action",
}
#: StoryboardElement::State — unchanged between v2.29.3 and master.
ELEMENT_STATE = {0: "init", 1: "standby", 2: "running", 3: "complete"}

_COND_CB = ct.CFUNCTYPE(None, ct.c_char_p, ct.c_double)
_SB_CB = ct.CFUNCTYPE(None, ct.c_char_p, ct.c_int, ct.c_int)

#: esmini's own ``externalController`` catalog entry, declared inline so the
#: rewritten scenario needs no ControllerCatalog on disk to resolve.
_EXTERNAL_CONTROLLER_NAME = "osc2crExternalController"


# ---------------------------------------------------------------------------
# Ego externalisation
# ---------------------------------------------------------------------------

@dataclass
class ExternalizationReport:
    """What :func:`externalize_ego` changed, and what it cost."""

    ego: str
    xosc_out: str
    controller_replaced: Optional[str] = None
    activate_action_added: bool = False
    #: ManeuverGroups that list the ego as an Actor.  Their actions are void
    #: once the planner owns the ego — declared here rather than dropped
    #: silently, the same discipline conditions_ext applies to condition types.
    voided_maneuver_groups: List[str] = field(default_factory=list)
    absolutized: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ego": self.ego,
            "xosc_out": self.xosc_out,
            "controller_replaced": self.controller_replaced,
            "activate_action_added": self.activate_action_added,
            "voided_maneuver_groups": self.voided_maneuver_groups,
            "absolutized_paths": self.absolutized,
        }


def ego_maneuver_groups(root: ET.Element, ego: str) -> List[str]:
    """Names of the ManeuverGroups that command ``ego`` as an Actor."""
    names = []
    for mg in root.iter("ManeuverGroup"):
        actors = mg.find("Actors")
        if actors is None:
            continue
        if any(e.get("entityRef") == ego for e in actors.findall("EntityRef")):
            names.append(mg.get("name") or "<unnamed>")
    return names


def _absolutize_paths(root: ET.Element, base: Path) -> int:
    """
    Rewrite every relative file reference against the original .xosc directory.

    The rewritten scenario is written into the bundle, not next to the source,
    so ``../xodr/straight_500m.xodr`` and the catalog directories would
    otherwise stop resolving.
    """
    n = 0
    for tag, attr in (
        ("LogicFile", "filepath"),
        ("SceneGraphFile", "filepath"),
        ("Directory", "path"),
    ):
        for el in root.iter(tag):
            value = el.get(attr)
            if not value or value.startswith("$"):
                continue
            p = Path(value)
            if p.is_absolute():
                continue
            el.set(attr, str((base / p).resolve()))
            n += 1
    return n



def stage_external_scenario(
    xosc_path: Path, bundle_dir: Path, ego: str,
) -> Tuple[ExternalizationReport, Path]:
    """
    Externalise the ego into a **symlink mirror of the source tree**.

    esmini resolves a scenario's relative references against the scenario
    file's own directory, and several of them cannot be rewritten in place:
    a controller's ``<File>`` usually lives in a *catalog* entry, not in the
    scenario, so ``_absolutize_paths`` never sees it.  ``cut-in_sumo`` dies on
    exactly that — its ``sumoController`` carries
    ``<File filepath="../sumo_inputs/e6mini.sumocfg"/>``, and writing the
    rewrite into the bundle makes that path point at ``output/sumo_inputs``::

        Failed to load SUMO config file ../sumo_inputs/e6mini.sumocfg
        Failed to initialize scenario player

    ``SE_AddPath`` does not help: it covers OpenDRIVE and 3D model files only.

    So instead of relocating the scenario, we recreate its neighbourhood.
    ``<bundle>/external/`` mirrors the source's grandparent directory with
    symlinks — every sibling of the scenario's folder, and every sibling of the
    scenario itself — and the rewrite is written at the mirrored position.
    Every relative path then resolves exactly as it does for the original, for
    every reference type at once, without writing into the corpus.
    """
    mirror_root = bundle_dir / "external"
    if mirror_root.exists():
        shutil.rmtree(mirror_root, ignore_errors=True)

    parent = xosc_path.parent           # …/resources/xosc
    grand = parent.parent               # …/resources
    staged_parent = mirror_root / grand.name / parent.name
    staged_parent.mkdir(parents=True, exist_ok=True)

    def _link_children(src: Path, dst: Path, skip: Optional[str] = None) -> None:
        for child in src.iterdir():
            if child.name == skip:
                continue
            target = dst / child.name
            if target.exists() or target.is_symlink():
                continue
            try:
                target.symlink_to(child)
            except OSError:
                pass  # a link we cannot make is not worth failing the run over

    _link_children(grand, mirror_root / grand.name, skip=parent.name)
    _link_children(parent, staged_parent)

    out = staged_parent / f"{xosc_path.stem}_external.xosc"
    if out.is_symlink():
        out.unlink()
    report = externalize_ego(xosc_path, ego, out)
    return report, out


def externalize_ego(
    xosc_path: str | Path,
    ego: str,
    out_path: str | Path,
) -> ExternalizationReport:
    """
    Rewrite a scenario so ``ego`` is driven from outside esmini.

    Three edits, matching what esmini's own ``cut-in_external.xosc`` does:

    1. give the ego an ``ExternalController`` (declared inline, so no catalog
       lookup is needed),
    2. activate it in ``Init`` — without this esmini keeps driving the ego and
       ignores everything reported in,
    3. absolutise file references, since the result lives in the bundle.

    The ego's Init ``TeleportAction`` and ``SpeedAction`` are deliberately
    *kept*: they are the planning problem's initial state, and the controller
    runs in ``override`` mode so the first reported state wins from tick one.
    """
    xosc_path = Path(xosc_path)
    out_path = Path(out_path)

    # insert_comments keeps the source scenario's commentary in the artifact —
    # these files are read by humans when a run looks wrong.
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    tree = ET.parse(xosc_path, parser=parser)
    root = tree.getroot()

    report = ExternalizationReport(ego=ego, xosc_out=str(out_path))
    report.voided_maneuver_groups = ego_maneuver_groups(root, ego)

    scenario_object = next(
        (o for o in root.iter("ScenarioObject") if o.get("name") == ego), None
    )
    if scenario_object is None:
        raise ValueError(
            f"'{ego}' is not a ScenarioObject in {xosc_path.name}; "
            f"found {[o.get('name') for o in root.iter('ScenarioObject')]}"
        )

    # 1) controller ---------------------------------------------------------
    existing = scenario_object.find("ObjectController")
    if existing is not None:
        # e.g. the ALKS reference controller in alks_*.xosc — replacing it is
        # the whole point: the regulatory test now runs against our planner.
        named = existing.find("Controller")
        ref = existing.find("CatalogReference")
        report.controller_replaced = (
            (named.get("name") if named is not None else None)
            or (ref.get("entryName") if ref is not None else None)
            or "<unnamed>"
        )
        scenario_object.remove(existing)

    controller = ET.SubElement(scenario_object, "ObjectController")
    ctrl = ET.SubElement(controller, "Controller", {"name": _EXTERNAL_CONTROLLER_NAME})
    props = ET.SubElement(ctrl, "Properties")
    for key, value in (
        ("esminiController", "ExternalController"),
        ("useGhost", "false"),
        ("mode", "override"),
    ):
        ET.SubElement(props, "Property", {"name": key, "value": value})

    # 2) activate it in Init ------------------------------------------------
    private = next(
        (p for p in root.iter("Private") if p.get("entityRef") == ego), None
    )
    if private is None:
        storyboard = root.find("Storyboard")
        init = storyboard.find("Init") if storyboard is not None else None
        actions = init.find("Actions") if init is not None else None
        if actions is None:
            raise ValueError(f"{xosc_path.name} has no Storyboard/Init/Actions")
        private = ET.SubElement(actions, "Private", {"entityRef": ego})

    already_active = any(True for _ in private.iter("ActivateControllerAction"))
    if not already_active:
        # appended last: Init private actions apply in order, and activation
        # must follow the TeleportAction that places the ego.
        action = ET.SubElement(private, "PrivateAction")
        controller_action = ET.SubElement(action, "ControllerAction")
        ET.SubElement(
            controller_action,
            "ActivateControllerAction",
            {"longitudinal": "true", "lateral": "true"},
        )
        report.activate_action_added = True

    # 3) paths --------------------------------------------------------------
    report.absolutized = _absolutize_paths(root, xosc_path.parent)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    return report


# ---------------------------------------------------------------------------
# Instrumented esmini
# ---------------------------------------------------------------------------

class ObservedEsmini:
    """
    ``EsminiSimulation`` plus the two observation callbacks.

    Wraps rather than reimplements: the stepping/reporting primitives come from
    ``cosim.esmini_interface``.  Callbacks are registered *after*
    ``SE_Init`` because esmini clears them on every init.
    """

    def __init__(
        self,
        xosc_path: str | Path,
        dt: float,
        use_viewer: bool = False,
        random_seed: int = 0,
        search_paths: Optional[List[str | Path]] = None,
    ) -> None:
        import ctypes as _ct
        from osc2cr_extended.cosim.esmini_interface import ESMINI_LIB_PATH, EsminiSimulation

        # esmini resolves a scenario's relative references against the *scenario
        # file's* directory.  externalize_ego writes its rewrite into the bundle,
        # so anything still relative — notably a controller's <File>, which
        # _absolutize_paths cannot reach when it lives in a catalog entry — stops
        # resolving.  cut-in_sumo dies on exactly that:
        #     Failed to load SUMO config file ../sumo_inputs/e6mini.sumocfg
        # SE_AddPath registers extra search directories, and must be called
        # before SE_Init, so it happens here rather than inside the wrapper.
        if search_paths:
            lib = _ct.CDLL(ESMINI_LIB_PATH)
            lib.SE_AddPath.argtypes = [_ct.c_char_p]
            lib.SE_AddPath.restype = _ct.c_int
            for extra in search_paths:
                lib.SE_AddPath(str(Path(extra).resolve()).encode("ascii"))

        self.sim = EsminiSimulation(
            str(xosc_path), dt=dt, use_viewer=use_viewer, random_seed=random_seed
        )
        self._lib = self.sim._lib
        self.conditions: List[Dict[str, Any]] = []
        self.elements: List[Dict[str, Any]] = []

        self._configure_signatures()
        # Global collision detection is *off* by default, and with it off
        # SE_GetObjectNumberOfCollisions returns 0 for everything — a collision
        # oracle that silently always passes. drop-bike drives its ego straight
        # through a fallen bicycle and reported nothing until this was enabled.
        self._lib.SE_CollisionDetection(True)

        # ctypes callbacks must stay referenced for as long as C holds them;
        # letting these be collected segfaults esmini mid-run.
        self._cond_cb = _COND_CB(self._on_condition)
        self._sb_cb = _SB_CB(self._on_element)
        self._lib.SE_RegisterConditionCallback(self._cond_cb)
        self._lib.SE_RegisterStoryBoardElementStateChangeCallback(self._sb_cb)

    def _configure_signatures(self) -> None:
        lib = self._lib
        lib.SE_RegisterConditionCallback.argtypes = [_COND_CB]
        lib.SE_RegisterConditionCallback.restype = None
        lib.SE_RegisterStoryBoardElementStateChangeCallback.argtypes = [_SB_CB]
        lib.SE_RegisterStoryBoardElementStateChangeCallback.restype = None
        lib.SE_CollisionDetection.argtypes = [ct.c_bool]
        lib.SE_CollisionDetection.restype = None
        lib.SE_GetObjectNumberOfCollisions.argtypes = [ct.c_int]
        lib.SE_GetObjectNumberOfCollisions.restype = ct.c_int
        lib.SE_GetObjectCollision.argtypes = [ct.c_int, ct.c_int]
        lib.SE_GetObjectCollision.restype = ct.c_int

    # -- callbacks ---------------------------------------------------------

    def _on_condition(self, name: bytes, timestamp: float) -> None:
        self.conditions.append({
            "name": name.decode("utf-8", "replace") if name else "",
            "time_s": round(float(timestamp), 4),
        })

    def _on_element(self, name: bytes, etype: int, state: int) -> None:
        # this callback carries no timestamp, so stamp it from the clock — it
        # fires inside SE_StepDT, so the reading is the current tick's time
        self.elements.append({
            "name": name.decode("utf-8", "replace") if name else "",
            "type": ELEMENT_TYPE.get(int(etype), f"type{etype}"),
            "state": ELEMENT_STATE.get(int(state), f"state{state}"),
            "time_s": round(self.sim.sim_time(), 4),
        })

    # -- passthrough -------------------------------------------------------

    def step(self) -> None:
        self.sim.step()

    def sim_time(self) -> float:
        return self.sim.sim_time()

    def is_finished(self) -> bool:
        return self.sim.is_finished()

    def object_states(self) -> Dict[int, Any]:
        return self.sim.get_object_states()

    def object_name(self, object_id: int) -> str:
        return self.sim.get_object_name(object_id)

    def id_by_name(self, name: str) -> int:
        return self.sim.get_object_id_by_name(name)

    def report_ego(self, object_id: int, x: float, y: float, h: float, v: float) -> None:
        """Report a state already expressed at esmini's own reference point."""
        self.sim.set_ego_state(object_id, x, y, h, v)

    def report_ego_centre(
        self, object_id: int, cx: float, cy: float, h: float, v: float,
    ) -> None:
        """
        Report a *shape-centre* state, converting to esmini's reference point.

        Everything above this wrapper works in CommonRoad's convention, where a
        position is the centre of the obstacle's rectangle.  esmini reports and
        accepts the object's reference point instead — the rear axle for a
        vehicle catalog entry.  Reading in one frame and writing in the other
        displaces the ego by ``centerOffsetX`` (1.40 m for drop-bike's ego)
        every single tick.
        """
        from osc2cr_extended.cosim.esmini_interface import SEScenarioObjectState

        st = SEScenarioObjectState()
        if self._lib.SE_GetObjectState(object_id, ct.byref(st)) == 0:
            cx, cy = esmini_reference_point(st, cx, cy)
        self.sim.set_ego_state(object_id, cx, cy, h, v)

    def collisions(self, object_id: int) -> List[int]:
        """Ids of the objects currently overlapping ``object_id``."""
        n = self._lib.SE_GetObjectNumberOfCollisions(object_id)
        return [self._lib.SE_GetObjectCollision(object_id, i) for i in range(max(0, n))]

    def close(self) -> None:
        self.sim.close()


# ---------------------------------------------------------------------------
# Shadow evaluation
# ---------------------------------------------------------------------------

class RecordingExecutor(EdgeAwareExecutor):
    """
    :class:`EdgeAwareExecutor` that also records *which conditions* were
    satisfied each tick, not merely which events fired.

    esmini reports at condition granularity, so a fair comparison needs the
    same granularity from our side.  Act latching and execution-count limits
    are inherited unchanged, which matters: esmini only evaluates an event's
    trigger while its act is running, and so do we.
    """

    def __init__(
        self,
        storyboard: Any,
        resolver: Any = None,
        condition_refs: Optional[List[Any]] = None,
        dt: float = 0.1,
    ) -> None:
        super().__init__(storyboard, resolver)
        self._dt = dt or 0.1
        self.satisfied_now: List[Tuple[int, str]] = []
        self.fires: List[Dict[str, Any]] = []

        # Per-step truth for every condition, in timeline.json's shape, so the
        # viewer can draw a closed-loop run's activity strips with the code it
        # already uses for the offline replay.  Truth is the *raw* predicate
        # value, not the edge-applied one — a strip shows when a condition
        # holds, while `fires` records when it triggers.
        self._refs = list(condition_refs or [])
        self._truth_now: Dict[int, bool] = {}
        self.truth_steps: List[int] = []
        self.truth_rows: List[List[int]] = [[] for _ in self._refs]

        # EdgeAwareExecutor keeps evaluating an exhausted event's trigger so its
        # edge state stays coherent, but does not fire it.  esmini stops
        # reporting such a condition once the event completes, so those
        # evaluations must not reach the comparison — counting them made
        # alks_decelerate look like 81 fires against esmini's 1.
        self._owner_event: Dict[int, str] = {}
        self._max_count: Dict[str, int] = {}
        for story in storyboard.stories:
            for act in story.acts:
                act_key = f"{story.name}/{act.name}"
                for mg in act.maneuver_groups:
                    for maneuver in mg.maneuvers:
                        for event in maneuver.events:
                            key = f"{act_key}/{event.name}"
                            self._max_count[key] = event.max_execution_count
                            for group in (event.start_trigger or []):
                                for cond in group:
                                    self._owner_event[id(cond)] = key

    def _condition_satisfied(self, cond: Any, state: Any) -> bool:
        ok = super()._condition_satisfied(cond, state)
        # the base class stores the raw value it just computed
        self._truth_now[id(cond)] = bool(self._prev.get(id(cond), False))
        if ok:
            name = getattr(cond, "name", "") or getattr(
                cond, "type_name", type(cond).__name__
            )
            self.satisfied_now.append((id(cond), name))
        return ok

    def timeline(self, dt: float) -> Dict[str, Any]:
        """The recorded truth matrix, in ``timeline.json``'s shape."""
        conditions = []
        for ref, values in zip(self._refs, self.truth_rows):
            first = next((i for i, v in enumerate(values) if v), None)
            conditions.append({
                "key": ref.key, "name": ref.name, "type": ref.ctype,
                "story": ref.story, "act": ref.act, "event": ref.event,
                "scope": ref.scope, "values": values,
                "first_true_step": (
                    self.truth_steps[first] if first is not None else None
                ),
                "true_steps": sum(values),
                "evaluable": ref.name not in self.unevaluable,
                "text": None, "unevaluable_reason": None,
            })
        return {
            "dt": dt,
            "time_steps": list(self.truth_steps),
            "conditions": conditions,
        }

    def prime(
        self,
        state: Any,
        entities: Optional[Dict[str, Dict[str, float]]] = None,
        time_s: float = 0.0,
    ) -> None:
        """
        Evaluate every trigger once against the initial state, firing nothing.

        esmini evaluates the storyboard at t=0, before the first step, so a
        condition that is *already* true when the scenario starts has no rising
        edge once the run begins.  Starting our edge detector from "false"
        instead invents one on the first tick: drive_when_close's StopCondition
        fired at 0.1 and again at 40.1 where esmini reported only 40.1, and
        cut-in_simple's acceleration condition fired at 0.1 rather than 10.3.

        Triggers are walked directly rather than through :meth:`step` so that
        execution counts and act latching stay untouched — priming must change
        only what each condition remembers.
        """
        self._entities = entities or {}
        self._time_s = time_s
        for story in self.storyboard.stories:
            for act in story.acts:
                self._trigger_satisfied(act.start_trigger, state)
                self._trigger_satisfied(getattr(act, "stop_trigger", None), state)
                for mg in act.maneuver_groups:
                    for maneuver in mg.maneuvers:
                        for event in maneuver.events:
                            self._trigger_satisfied(event.start_trigger, state)
        self._trigger_satisfied(
            getattr(self.storyboard, "stop_trigger", None), state
        )
        self.satisfied_now = []

    def step(  # type: ignore[override]
        self,
        state: Any,
        entities: Optional[Dict[str, Dict[str, float]]] = None,
        time_s: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        self.satisfied_now = []
        # snapshot before the step: an event that exhausts itself *during* this
        # step still fired during it, and belongs in the stream
        exhausted = {
            key for key, limit in self._max_count.items()
            if self._exec_counts.get(key, 0) >= limit
        }
        fired = super().step(state, entities, time_s)

        # The storyboard's own stop trigger sits outside the story/act walk, but
        # esmini reports its conditions too — evaluate it so the two streams
        # cover the same set rather than showing phantom "esmini only" hits.
        stop_trigger = getattr(self.storyboard, "stop_trigger", None)
        if stop_trigger:
            self._trigger_satisfied(stop_trigger, state)

        t = time_s if time_s is not None else getattr(state, "time", 0.0)
        for cond_id, name in self.satisfied_now:
            if self._owner_event.get(cond_id) in exhausted:
                continue
            self.fires.append({"name": name, "time_s": round(float(t), 4)})

        # A condition in an act that has not started is never evaluated, and is
        # recorded as not holding — which is what it is, from the storyboard's
        # point of view.
        if self._refs:
            self.truth_steps.append(int(round(float(t) / (self._dt or 1.0))))
            for i, ref in enumerate(self._refs):
                self.truth_rows[i].append(
                    1 if self._truth_now.get(id(ref.condition)) else 0
                )
            self._truth_now = {}
        return fired


def _sim_state(entities: Dict[str, Dict[str, float]], time_s: float) -> Any:
    """Build the evaluator's SimState from an esmini world snapshot."""
    from osc2cr_extended.strategies.condition_evaluator import EntityState, SimState

    return SimState(
        time=float(time_s),
        entities={
            name: EntityState(
                entity_id=name, x=e["x"], y=e["y"], speed=e["speed"],
                heading=e["heading"], length=e["length"], width=e["width"],
            )
            for name, e in entities.items()
        },
    )


# ---------------------------------------------------------------------------
# Differential comparison
# ---------------------------------------------------------------------------

#: esmini labels a ``<Condition>`` with no ``name`` attribute "no name <Type>";
#: collect_conditions falls back to the bare type.  Same condition, two labels —
#: normalise or every unnamed condition reads as two separate disagreements.
_UNNAMED = re.compile(r"^\s*no name\s+(.+?)\s*$", re.IGNORECASE)


def normalise_condition_name(name: str) -> str:
    match = _UNNAMED.match(name or "")
    return match.group(1) if match else (name or "")


def _match_times(
    reference: List[float], candidate: List[float], tol: float
) -> Tuple[List[Tuple[float, float]], List[float], List[float]]:
    """Greedy nearest-neighbour pairing of two fire-time lists."""
    remaining = sorted(candidate)
    matched: List[Tuple[float, float]] = []
    unmatched_ref: List[float] = []

    for t in sorted(reference):
        best, best_d = None, tol
        for c in remaining:
            d = abs(c - t)
            if d <= best_d:
                best, best_d = c, d
        if best is None:
            unmatched_ref.append(t)
        else:
            remaining.remove(best)
            matched.append((t, best))
    return matched, unmatched_ref, remaining


def differential(
    esmini_fires: List[Dict[str, Any]],
    shadow_fires: List[Dict[str, Any]],
    dt: float,
    modelled: Optional[set] = None,
    delays: Optional[Dict[str, float]] = None,
    end_time: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compare esmini's condition stream against ours, per condition name.

    Tolerance is 1.5·dt: esmini timestamps a trigger at the tick it evaluated,
    and our replay can sit one tick either side of that without being wrong.

    Verdicts
        ``agree``            same fire count, every time matched
        ``time_mismatch``    both fired, at least one time off by > tolerance
        ``count_mismatch``   both fired, different number of times
        ``esmini_only``      esmini fired it, we never did
        ``shadow_only``      we fired it, esmini never did
        ``not_modelled``     esmini fired a condition we do not carry at all

    A divergent condition that declares a non-zero ``delay`` is tagged
    ``declares_delay``: the executor documents delay as parsed-but-unmodelled,
    so esmini firing later is the expected consequence of a known limitation
    rather than an unexplained disagreement.  Tagged, not excused — the counts
    still record it as a mismatch.

    ``end_time`` guards the other direction.  A run that stops early — the
    planner reaches its goal, the step cap hits — leaves our last tick
    uncorroborated: we evaluate the state after stepping to *t*, esmini would
    have reported at *t + dt*, and that step never happened.  Fires confined to
    the final tick become ``inconclusive_at_end`` rather than counting against
    either implementation.
    """
    tol = 1.5 * dt
    by_name: Dict[str, Dict[str, List[float]]] = {}
    for src, key in ((esmini_fires, "esmini"), (shadow_fires, "shadow")):
        for f in src:
            name = normalise_condition_name(f["name"])
            slot = by_name.setdefault(name, {"esmini": [], "shadow": []})
            slot[key].append(float(f["time_s"]))

    rows: List[Dict[str, Any]] = []
    for name in sorted(by_name):
        e_times = by_name[name]["esmini"]
        s_times = by_name[name]["shadow"]
        matched, e_only, s_only = _match_times(e_times, s_times, tol)

        if e_times and not s_times:
            verdict = "not_modelled" if (
                modelled is not None and name not in modelled
            ) else "esmini_only"
        elif s_times and not e_times:
            verdict = "shadow_only"
        elif len(e_times) != len(s_times):
            verdict = "count_mismatch"
        elif e_only or s_only:
            verdict = "time_mismatch"
        else:
            verdict = "agree"

        # everything that disagrees only because the run ended is not evidence
        if verdict != "agree" and end_time is not None:
            unresolved = (s_only if verdict != "esmini_only" else e_only) or (
                s_times if verdict == "shadow_only" else []
            )
            if unresolved and all(t >= end_time - tol for t in unresolved):
                verdict = "inconclusive_at_end"

        delay = float((delays or {}).get(name) or 0.0)
        rows.append({
            "name": name,
            "verdict": verdict,
            "esmini_fires": [round(t, 3) for t in sorted(e_times)],
            "shadow_fires": [round(t, 3) for t in sorted(s_times)],
            "max_delta_s": round(
                max((abs(a - b) for a, b in matched), default=0.0), 4
            ),
            "delay_s": delay,
            "declares_delay": bool(delay > 0.0 and verdict != "agree"),
        })

    summary = {"compared": len(rows)}
    for row in rows:
        summary[row["verdict"]] = summary.get(row["verdict"], 0) + 1
    # inconclusive rows carry no evidence either way, so they leave the
    # denominator rather than diluting the score
    conclusive = len(rows) - summary.get("inconclusive_at_end", 0)
    summary["conclusive"] = conclusive
    # None, not 0.0: a scenario where neither side fired anything has nothing
    # to agree or disagree about, and scoring it zero would libel it
    summary["agreement_pct"] = (
        round(100.0 * summary.get("agree", 0) / conclusive, 1) if conclusive else None
    )
    summary["divergent_declaring_delay"] = sum(
        1 for r in rows if r["declares_delay"]
    )
    return {"summary": summary, "conditions": rows}


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def _entities_from_esmini(sim: ObservedEsmini) -> Dict[str, Dict[str, float]]:
    """
    esmini object states → the entity dict the condition evaluator wants.

    ``SE_ScenarioObjectState`` carries no acceleration field, so acceleration is
    left at zero here and filled in by the loop from the speed difference
    between ticks.  Leaving it at zero would make every ``AccelerationCondition``
    read "0 == 0" and fire on the first step — the differential caught exactly
    that, which is what it is for.
    """
    out: Dict[str, Dict[str, float]] = {}
    for object_id, st in sim.object_states().items():
        name = sim.object_name(object_id) or f"entity_{object_id}"
        cx, cy = esmini_centre(st)
        out[name] = {
            "x": cx, "y": cy, "speed": float(st.speed),
            "heading": float(st.h), "length": float(st.length),
            "width": float(st.width), "acceleration": 0.0,
        }
    return out


def esmini_centre(st: Any) -> Tuple[float, float]:
    """
    esmini's reported (x, y) → the bounding-box centre CommonRoad expects.

    ``SE_ScenarioObjectState.x/y`` is the object's *reference point* — for a
    vehicle catalog entry that is the rear axle — while a CommonRoad
    ``DynamicObstacle`` is positioned at the centre of its shape.  The gap is
    ``centerOffset*`` in the object's own frame, so it rotates with heading.

    Measured on drop-bike: 1.40 m for the ego, 1.30 m for the Target, 0.50 m
    for the bike, 0.00 m for the static Box — which is exactly the offset by
    which this loop's trajectories disagreed with the converter's until the
    conversion was applied.
    """
    h = float(st.h)
    ox, oy = float(st.centerOffsetX), float(st.centerOffsetY)
    return (float(st.x) + ox * math.cos(h) - oy * math.sin(h),
            float(st.y) + ox * math.sin(h) + oy * math.cos(h))


def esmini_reference_point(st: Any, cx: float, cy: float) -> Tuple[float, float]:
    """Inverse of :func:`esmini_centre` — a centre position back to what
    ``SE_ReportObjectPosXYH`` expects, using ``st`` only for its offsets."""
    h = float(st.h)
    ox, oy = float(st.centerOffsetX), float(st.centerOffsetY)
    return (cx - ox * math.cos(h) + oy * math.sin(h),
            cy - ox * math.sin(h) - oy * math.cos(h))


@dataclass
class PlannerHandle:
    """Everything the planner-driven loop needs, or the reason there is none."""

    ok: bool
    reason: Optional[str] = None
    planner: Any = None
    config: Any = None
    scenario: Any = None
    planning_problem: Any = None
    road_boundary: Any = None
    templates: List[Any] = field(default_factory=list)
    obstacle_names: Dict[int, str] = field(default_factory=dict)


def _plan_reference_path(scenario: Any, planning_problem: Any) -> Any:
    """
    Plan the ego's reference path, across both route-planner API generations.

    commonroad-route-planner 2025.1 dropped ``Route`` (and with it
    ``plan_routes().retrieve_first_route()``) in favour of a ``ReferencePath``
    built through ``fast_api``; ``RoutePlanner`` also takes a lanelet network
    now, not a scenario.  Both call shapes are tried so the co-simulation is
    not pinned to whichever generation happens to be installed — the reactive
    planner itself pins no upper bound.
    """
    try:  # >= 2025.1
        from commonroad_route_planner.fast_api.fast_api import (
            generate_reference_path_from_scenario_and_planning_problem,
        )
    except ImportError:
        pass
    else:
        return generate_reference_path_from_scenario_and_planning_problem(
            scenario, planning_problem
        ).reference_path

    from commonroad_route_planner.route_planner import RoutePlanner  # <= 2024.x

    return (
        RoutePlanner(scenario, planning_problem)
        .plan_routes()
        .retrieve_first_route()
        .reference_path
    )


def _setup_planner(
    bundle_dir: Path, xosc_path: Path, dt: float, config_path: Optional[Path],
    ego_override: Optional[str],
) -> Tuple[PlannerHandle, Optional[str]]:
    """
    Load the bundle's CommonRoad scenario and stand up commonroad-rp on it.

    Returns ``(handle, ego_name)``.  Failure is reported, never silently
    downgraded — a run that could not plan is not a run whose triggers mean
    anything.
    """
    from commonroad.common.file_reader import CommonRoadFileReader

    scenario_file = bundle_dir / "scenario.xml"
    if not scenario_file.exists():
        return PlannerHandle(False, f"no scenario.xml in {bundle_dir}"), None

    # Re-reading the bundle rather than re-converting saves the whole esmini
    # conversion (~100 s in the benchmark); the triggers we need come from the
    # .xosc via LiveSession, not from the CommonRoad file.
    scenario, pps = CommonRoadFileReader(str(scenario_file)).open()

    mapping, confidence, ego_name = map_obstacles_to_entities(scenario, xosc_path)
    ego_name = ego_override or ego_name
    if not ego_name:
        return PlannerHandle(False, "could not identify the ego entity"), None

    if not scenario.lanelet_network.lanelets:
        return PlannerHandle(
            False,
            "no lanelet network in the bundle — the planner has no reference "
            "path (see the bundle's road_network.error)",
        ), ego_name

    ego_ids = [oid for oid, name in mapping.items() if name == ego_name]
    templates = [o for o in scenario.dynamic_obstacles if o.obstacle_id not in ego_ids]
    obstacle_names = {
        o.obstacle_id: mapping.get(o.obstacle_id, f"entity_{o.obstacle_id}")
        for o in templates
    }
    # the planner owns the ego; esmini's recorded ego must not also be traffic
    for obs in list(scenario.dynamic_obstacles):
        if obs.obstacle_id in ego_ids:
            scenario.remove_obstacle(obs)

    problems = list(pps.planning_problem_dict.values())
    if not problems:
        return PlannerHandle(False, "bundle has no planning problem"), ego_name
    planning_problem = problems[0]
    if planning_problem.initial_state.acceleration is None:
        planning_problem.initial_state.acceleration = 0.0

    try:
        from commonroad_dc.boundary.boundary import create_road_boundary_obstacle
        from commonroad_rp.reactive_planner import ReactivePlanner
        from commonroad_rp.utility.config import ReactivePlannerConfiguration
        from commonroad_rp.utility.logger import initialize_logger

        config = ReactivePlannerConfiguration.load(str(config_path))
        config.planning.dt = dt
        config.update(scenario=scenario, planning_problem=planning_problem)
        config.planning_problem_set = pps
        initialize_logger(config)

        reference_path = _plan_reference_path(
            config.scenario, config.planning_problem
        )
        planner = ReactivePlanner(config)
        planner.set_reference_path(reference_path)
        _, road_boundary = create_road_boundary_obstacle(scenario)
    except Exception as exc:  # noqa: BLE001 — any planner-stack failure is data
        return PlannerHandle(
            False, f"planner setup failed ({type(exc).__name__}: {exc})"
        ), ego_name

    return PlannerHandle(
        ok=True, planner=planner, config=config, scenario=scenario,
        planning_problem=planning_problem, road_boundary=road_boundary,
        templates=templates, obstacle_names=obstacle_names,
    ), ego_name


def _live_obstacle(template: Any, st: Any, time_step: int, dt: float, horizon: int) -> Any:
    """
    A CommonRoad obstacle at esmini's current state, with a constant-velocity
    short-horizon prediction.

    esmini reports only the present, but the planner's collision checker needs
    a future — extrapolating at constant velocity/heading is the standard
    coupling for a simulator that does not expose other agents' plans.
    """
    import numpy as np
    from commonroad.geometry.shape import Rectangle
    from commonroad.prediction.prediction import TrajectoryPrediction
    from commonroad.scenario.obstacle import DynamicObstacle, ObstacleType
    from commonroad.scenario.state import CustomState, InitialState
    from commonroad.scenario.trajectory import Trajectory

    x, y = esmini_centre(st)
    h, v = float(st.h), float(st.speed)
    shape = Rectangle(width=float(st.width), length=float(st.length))
    states = [
        CustomState(
            time_step=time_step + i,
            position=np.array([x + v * dt * i * math.cos(h),
                               y + v * dt * i * math.sin(h)]),
            orientation=h, velocity=v,
        )
        for i in range(1, horizon + 1)
    ]
    return DynamicObstacle(
        obstacle_id=template.obstacle_id,
        obstacle_type=ObstacleType.CAR,
        obstacle_shape=shape,
        initial_state=InitialState(
            time_step=time_step, position=np.array([x, y]), orientation=h,
            velocity=v, acceleration=0.0, yaw_rate=0.0,
        ),
        prediction=TrajectoryPrediction(
            Trajectory(initial_time_step=time_step + 1, state_list=states), shape
        ),
    )


DEFAULT_PLANNER_CONFIG = paths.CONFIG_DIR / "cosim.yaml"


def run_cosim(
    bundle_dir: str | Path,
    driver: str = "esmini",
    max_steps: Optional[int] = None,
    config_path: Optional[str | Path] = None,
    desired_velocity: Optional[float] = None,
    viewer: bool = False,
    ego: Optional[str] = None,
    write: bool = True,
    out_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run one scenario with conditions observed from both players.

    ``driver="esmini"`` validates our condition implementation against the
    reference player.  ``driver="planner"`` externalises the ego and hands it
    to commonroad-rp.  Each writes ``cosim_trace_<driver>.json`` into the
    bundle — separate files so the two legs can be compared against each other
    and against ``trace_interpretation.json``, whose ``events`` shape they share.
    """
    out_name = out_name or f"cosim_trace_{driver}.json"
    bundle_dir = Path(bundle_dir).resolve()
    manifest = json.loads((bundle_dir / "bundle.json").read_text())
    xosc_path = Path(manifest["xosc_path"])
    dt = float(manifest.get("stats", {}).get("dt") or 0.1)
    if max_steps is None:
        max_steps = int(manifest.get("stats", {}).get("time_steps") or 300) + 20

    session = LiveSession(bundle_dir)
    executor = RecordingExecutor(
        session.storyboard, session.resolver, session.conditions, dt,
    )
    modelled = {ref.name for ref in session.conditions}
    delays = {
        ref.name: float(getattr(ref.condition, "delay", 0.0) or 0.0)
        for ref in session.conditions
    }

    result: Dict[str, Any] = {
        "scenario": manifest.get("name") or bundle_dir.name,
        "bundle_dir": str(bundle_dir),
        "xosc_path": str(xosc_path),
        "driver": driver,
        "dt": dt,
        # carried from the bundle so the viewer can line a CommonRoad obstacle
        # up with the entity name this run records motion under
        "id_to_name": (session.timeline or {}).get("id_to_name") or {},
        "ego": (session.timeline or {}).get("ego"),
        "entities": (session.timeline or {}).get("entities") or [],
        "externalization": None,
        "planner": {"status": "not-requested"},
        "events": [],
        "collisions": [],
        "ego_trajectory": [],
        "storyboard_elements": [],
        # every entity's motion during *this* run, not the recorded one — the
        # traffic re-times around the planner, so the ego is not the only thing
        # that differs and the viewer needs all of it to draw the run
        "entity_trajectories": {},
        # recorded once, so the run can be rebuilt as a CommonRoad scenario
        # without having to reach back into the bundle for shapes
        "entity_shapes": {},
    }

    # ── planner (optional) ────────────────────────────────────────────────
    handle = PlannerHandle(False, "not requested")
    # The ego is needed in *both* modes, not just the planner's: without it the
    # collision check has no subject and silently reports nothing.
    ego_name = ego or (session.timeline or {}).get("ego")
    if driver == "planner":
        handle, detected = _setup_planner(
            bundle_dir, xosc_path,
            dt, Path(config_path or DEFAULT_PLANNER_CONFIG), ego,
        )
        ego_name = ego or detected
        if not handle.ok:
            result["planner"] = {"status": "failed", "reason": handle.reason}
            return _finish(result, executor, None, dt, modelled, delays,
                           bundle_dir, write, out_name)
        result["planner"] = {"status": "running"}

    # ── scenario to actually run ──────────────────────────────────────────
    run_xosc = xosc_path
    if driver == "planner":
        report, run_xosc = stage_external_scenario(
            xosc_path, bundle_dir, ego_name,
        )
        result["externalization"] = report.to_dict()

    sim = ObservedEsmini(run_xosc, dt=dt, use_viewer=viewer)
    ego_id = sim.id_by_name(ego_name) if ego_name else -1

    planner = handle.planner
    if handle.ok:
        planner.record_state_and_input(planner.x_0)

    # esmini has already placed every entity at its Init position; evaluating
    # that state primes each condition's edge so the run starts from the same
    # baseline the reference player does
    initial = _entities_from_esmini(sim)
    executor.prime(_sim_state(initial, 0.0), initial, 0.0)

    previous: Dict[str, Tuple[float, float, float]] = {
        name: (e["x"], e["y"], e["speed"]) for name, e in initial.items()
    }
    optimal = None
    step_index = 0
    last_time = -1.0

    try:
        while not sim.is_finished() and step_index < max_steps:
            if handle.ok and planner.goal_reached():
                # reaching the goal before stepping means the planning problem
                # is satisfied by its own initial state — nothing was tested
                result["planner"]["status"] = (
                    "goal-already-satisfied" if step_index == 0 else "goal-reached"
                )
                break

            sim.step()
            t = sim.sim_time()
            # esmini's quit flag lags the final step by one, so the clock can
            # repeat; evaluating that tick twice double-counts every fire.
            if t <= last_time:
                break
            last_time = t
            entities = _entities_from_esmini(sim)

            for name, e in entities.items():
                px, py, pv = previous.get(name, (e["x"], e["y"], e["speed"]))
                # traveled distance feeds TraveledDistanceCondition, which is
                # path-length dependent and not recoverable from a snapshot
                executor.engine.update_distances(
                    name, math.hypot(e["x"] - px, e["y"] - py)
                )
                e["acceleration"] = (e["speed"] - pv) / dt if dt else 0.0
                previous[name] = (e["x"], e["y"], e["speed"])

            step_no = int(round(t / dt))
            for name, e in entities.items():
                result["entity_shapes"].setdefault(name, {
                    "length": round(e["length"], 4),
                    "width": round(e["width"], 4),
                })
                result["entity_trajectories"].setdefault(name, []).append({
                    "step": step_no, "t": round(t, 4),
                    "x": round(e["x"], 4), "y": round(e["y"], 4),
                    "h": round(e["heading"], 5), "v": round(e["speed"], 4),
                })

            for fired in executor.step(_sim_state(entities, t), entities, t):
                fired.update({"time_s": round(t, 4),
                              "time_step": step_no,
                              "unconditional": False})
                result["events"].append(fired)

            if ego_id >= 0:
                hits = sim.collisions(ego_id)
                if hits:
                    result["collisions"].append({
                        "time_s": round(t, 4),
                        "with": [sim.object_name(h) or str(h) for h in hits],
                    })

            if not handle.ok:
                step_index += 1
                continue

            # ── planner tick ───────────────────────────────────────────────
            count = len(planner.record_state_list) - 1
            scenario = handle.scenario
            for obs in list(scenario.dynamic_obstacles):
                scenario.remove_obstacle(obs)
            live = sim.object_states()
            for template in handle.templates:
                name = handle.obstacle_names.get(template.obstacle_id)
                oid = sim.id_by_name(name) if name else -1
                if oid in live:
                    scenario.add_objects(_live_obstacle(
                        template, live[oid], count + 1, dt,
                        handle.config.planning.time_steps_computation,
                    ))

            planner.set_collision_checker(
                scenario=scenario, road_boundary_obstacle=handle.road_boundary
            )
            replan_every = handle.config.planning.replanning_frequency
            if count % replan_every == 0:
                planner.set_desired_velocity(
                    desired_velocity=desired_velocity,
                    current_speed=planner.x_0.velocity,
                )
                optimal = planner.plan()
                if not optimal:
                    result["planner"] = {
                        "status": "infeasible",
                        "reason": f"no feasible trajectory at step {count}",
                        "steps": count,
                    }
                    break
                offset = 1
            else:
                offset = 1 + (count % replan_every)

            next_state = optimal[0].state_list[offset]
            next_curv = (optimal[2][offset], optimal[3][offset])
            planner.record_state_and_input(next_state)
            planner.reset(
                initial_state_cart=next_state, initial_state_curv=next_curv,
                collision_checker=planner.collision_checker,
                coordinate_system=planner.coordinate_system,
            )

            # commonroad-rp plans at the rear axle; shift to the shape centre
            # for CommonRoad, then back out to esmini's own reference point —
            # otherwise the ego is written in one frame and read back in
            # another, and the loop quietly drifts by centerOffsetX every tick.
            centre = next_state.shift_positions_to_center(
                handle.config.vehicle.wb_rear_axle
            )
            sim.report_ego_centre(ego_id, float(centre.position[0]),
                                  float(centre.position[1]),
                                  float(centre.orientation),
                                  float(next_state.velocity))
            result["ego_trajectory"].append({
                "t": round(t, 4),
                "x": round(float(next_state.position[0]), 4),
                "y": round(float(next_state.position[1]), 4),
                "v": round(float(next_state.velocity), 4),
            })
            step_index += 1
    finally:
        result["storyboard_elements"] = sim.elements
        esmini_conditions = list(sim.conditions)
        sim.close()

    result["steps"] = step_index
    if handle.ok and result["planner"].get("status") == "running":
        result["planner"] = {
            "status": "completed",
            "steps": step_index,
            "goal_reached": bool(planner.goal_reached()),
        }
    return _finish(result, executor, esmini_conditions, dt, modelled, delays,
                   bundle_dir, write, out_name, end_time=last_time)


def cosim_isolated(
    bundle_dir: str | Path,
    driver: str = "esmini",
    timeout: float = 900.0,
    extra_args: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Run one bundle in a child process and read the trace back.

    Same reasoning as ``pipeline.convert_isolated``: esmini is reached through a
    process-wide handle, it leaks state between scenarios (a long run truncates
    the next one), and some scenarios crash it outright.  A batch that shares
    one interpreter therefore produces quietly wrong traces at best and dies at
    worst.  The child writes ``cosim_trace.json`` itself, so the artifact *is*
    the result — no temporary file is needed.
    """
    import subprocess
    import sys

    bundle_dir = Path(bundle_dir).resolve()
    trace = bundle_dir / f"cosim_trace_{driver}.json"
    stale = trace.stat().st_mtime if trace.exists() else None

    cmd = [
        sys.executable, "-m", paths.MODULE_NAME, "cosim", str(bundle_dir),
        "--driver", driver, "--no-isolate", *(extra_args or []),
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(paths.TOOL_ROOT), capture_output=True,
            text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"scenario": bundle_dir.name, "ok": False,
                "error": f"timed out after {timeout:.0f}s"}

    if trace.exists() and (stale is None or trace.stat().st_mtime > stale):
        try:
            result = json.loads(trace.read_text())
            result["ok"] = True
            return result
        except json.JSONDecodeError as exc:
            return {"scenario": bundle_dir.name, "ok": False,
                    "error": f"unreadable trace: {exc}"}

    if proc.returncode < 0:
        import signal as _signal
        name = _signal.Signals(-proc.returncode).name
        return {"scenario": bundle_dir.name, "ok": False,
                "error": f"esmini crashed the interpreter ({name})"}

    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return {"scenario": bundle_dir.name, "ok": False,
            "error": f"exit {proc.returncode}: {(tail[-1] if tail else 'no output')[:160]}"}


def _finish(
    result: Dict[str, Any],
    executor: RecordingExecutor,
    esmini_conditions: Optional[List[Dict[str, Any]]],
    dt: float,
    modelled: set,
    delays: Dict[str, float],
    bundle_dir: Path,
    write: bool,
    out_name: str,
    end_time: Optional[float] = None,
) -> Dict[str, Any]:
    """Attach the differential, then persist next to ``trace_interpretation.json``."""
    result["esmini_conditions"] = esmini_conditions or []
    result["shadow_conditions"] = executor.fires
    # same shape as timeline.json, so the viewer draws closed-loop activity
    # strips with the code it already uses for the replay
    result["timeline"] = executor.timeline(dt)
    result["differential"] = differential(
        result["esmini_conditions"], executor.fires, dt, modelled, delays,
        end_time,
    ) if esmini_conditions is not None else {
        "summary": {"compared": 0, "agreement_pct": 0.0}, "conditions": []
    }
    result["unevaluable"] = sorted(executor.unevaluable)

    if write:
        out = bundle_dir / out_name
        result["written_to"] = str(out)

        # The same run as a CommonRoad scenario, triggers embedded, so it is
        # readable by the ecosystem rather than only by this tool.  Done before
        # the JSON is dumped so the trace records what was written — including
        # the reason if it could not be.
        try:
            result["commonroad"] = write_cosim_commonroad(
                bundle_dir, result, str(result.get("driver") or "esmini"),
            )
        except Exception as exc:  # noqa: BLE001
            # the trace is the primary artifact; failing to also render it as
            # CommonRoad is reported, not fatal
            result["commonroad"] = {"error": f"{type(exc).__name__}: {exc}"}

        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(result, indent=1))
        tmp.replace(out)  # atomic, as pipeline.py does for the bundle
    return result


# ---------------------------------------------------------------------------
# CommonRoad output
# ---------------------------------------------------------------------------

def _obstacle_type_for(name: str, length: float, width: float) -> Any:
    """Best-effort CommonRoad type from the entity's name and footprint."""
    from commonroad.scenario.obstacle import ObstacleType

    lowered = name.lower()
    if any(k in lowered for k in ("ped", "human", "walker")):
        return ObstacleType.PEDESTRIAN
    if any(k in lowered for k in ("bike", "bicycle", "cyclist")):
        return ObstacleType.BICYCLE
    if any(k in lowered for k in ("truck", "lorry")):
        return ObstacleType.TRUCK
    if any(k in lowered for k in ("box", "obstacle", "barrier")):
        return ObstacleType.CONSTRUCTION_ZONE
    # a footprint too small for a car is far more likely a two-wheeler
    return ObstacleType.BICYCLE if (length < 3.0 and width < 1.2) else ObstacleType.CAR


def write_cosim_commonroad(
    bundle_dir: str | Path,
    result: Dict[str, Any],
    driver: str,
) -> Dict[str, str]:
    """
    Write the closed-loop run as a **CommonRoad scenario**, triggers embedded.

    Why this and not only the JSON trace: a co-simulation run *is* a scenario —
    a road network plus every actor's motion over time — which is exactly what
    CommonRoad represents.  Emitting it only as a private JSON blob would make
    the run unopenable by crdesigner, commonroad-io or anything else in the
    ecosystem this tool exists to plug into, and would contradict the project's
    own claim that the triggers travel *inside* the CommonRoad file.

    So the closed loop mirrors the replay exactly:

    ==========================  ==================================
    replay                      closed loop
    ==========================  ==================================
    ``scenario.xml``            ``cosim_<driver>.xml``
    ``triggers.json``           ``cosim_trace_<driver>.json``
    ==========================  ==================================

    The ``.xml`` carries the lanelet network, every actor's *driven*
    trajectory, the planning problem, and an ``<osc:triggers>`` block whose
    ``firedAt`` values are this run's fire times.  The JSON stays as the
    sidecar for the same reason ``triggers.json`` does — commonroad-io drops
    the embedded block on a read/write round trip.

    For a planner run a CommonRoad **solution** file is written too, that being
    the canonical artifact for "a planner's trajectory for this planning
    problem".

    Returns ``{role: path}`` for whatever was written.
    """
    import numpy as np
    from commonroad.common.file_reader import CommonRoadFileReader
    from commonroad.common.file_writer import CommonRoadFileWriter
    from commonroad.common.writer.file_writer_interface import OverwriteExistingFile
    from commonroad.geometry.shape import Rectangle
    from commonroad.prediction.prediction import TrajectoryPrediction
    from commonroad.scenario.obstacle import DynamicObstacle
    from commonroad.scenario.state import CustomState, InitialState
    from commonroad.scenario.trajectory import Trajectory

    from ..embed import embed_triggers

    bundle_dir = Path(bundle_dir)
    written: Dict[str, str] = {}

    source = bundle_dir / "scenario.xml"
    tracks = result.get("entity_trajectories") or {}
    if not source.exists() or not tracks:
        return written

    scenario, pps = CommonRoadFileReader(str(source)).open()
    # keep the road, replace the motion: the obstacles in the bundle are the
    # *recorded* run, and this file is about a different one
    for obstacle in list(scenario.dynamic_obstacles):
        scenario.remove_obstacle(obstacle)

    name_to_id = {v: int(k) for k, v in (result.get("id_to_name") or {}).items()}
    shapes = result.get("entity_shapes") or {}
    next_id = max(name_to_id.values(), default=0) + 1

    for name, states in sorted(tracks.items()):
        if len(states) < 2:
            continue
        dims = shapes.get(name, {})
        length = float(dims.get("length", 4.5))
        width = float(dims.get("width", 1.8))
        shape = Rectangle(length=length, width=width)

        obstacle_id = name_to_id.get(name)
        if obstacle_id is None:
            obstacle_id = next_id
            next_id += 1

        first = states[0]
        initial = InitialState(
            time_step=int(first["step"]),
            position=np.array([first["x"], first["y"]]),
            orientation=float(first["h"]),
            velocity=float(first["v"]),
            acceleration=0.0,
            yaw_rate=0.0,
            slip_angle=0.0,
        )
        rest = [
            CustomState(
                time_step=int(s["step"]),
                position=np.array([s["x"], s["y"]]),
                orientation=float(s["h"]),
                velocity=float(s["v"]),
            )
            for s in states[1:]
        ]
        scenario.add_objects(DynamicObstacle(
            obstacle_id=obstacle_id,
            obstacle_type=_obstacle_type_for(name, length, width),
            obstacle_shape=shape,
            initial_state=initial,
            prediction=TrajectoryPrediction(
                Trajectory(initial_time_step=rest[0].time_step, state_list=rest),
                shape,
            ),
        ))

    cr_path = bundle_dir / f"cosim_{driver}.xml"
    CommonRoadFileWriter(
        scenario=scenario, planning_problem_set=pps,
    ).write_to_file(str(cr_path), OverwriteExistingFile.ALWAYS)

    triggers = _cosim_triggers(bundle_dir, result, driver)
    if triggers is not None:
        embed_triggers(cr_path, triggers)
    written[f"cosim_{driver}_xml"] = str(cr_path)

    solution = _write_solution(bundle_dir, result, driver)
    if solution:
        written[f"cosim_{driver}_solution"] = solution
    return written


def _cosim_triggers(
    bundle_dir: Path, result: Dict[str, Any], driver: str,
) -> Optional[Dict[str, Any]]:
    """
    The bundle's trigger document with *this run's* fire times substituted.

    The conditions are the same — they come from the same ``.xosc``. What
    changes is when, or whether, each event fired, which is the entire point of
    running closed-loop.
    """
    sidecar = bundle_dir / "triggers.json"
    if not sidecar.exists():
        return None
    try:
        triggers = json.loads(sidecar.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    fires: Dict[str, List[Dict[str, Any]]] = {}
    for i, event in enumerate(result.get("events") or []):
        fires.setdefault(event.get("event") or "", []).append({
            "time_s": event.get("time_s"),
            "time_step": event.get("time_step"),
            "fire_count": event.get("fire_count", i + 1),
        })

    for event in triggers.get("events") or []:
        mine = fires.get(event.get("name"), [])
        event["interpretation"] = {"fired": bool(mine), "fires": mine}

    triggers["run"] = {
        "driver": driver,
        "source": f"cosim_trace_{driver}.json",
        "planner": (result.get("planner") or {}).get("status"),
        "collisions": len(result.get("collisions") or []),
    }
    counts = triggers.setdefault("counts", {})
    counts["fired"] = sum(len(v) for v in fires.values())
    return triggers


def _write_solution(
    bundle_dir: Path, result: Dict[str, Any], driver: str,
) -> Optional[str]:
    """A CommonRoad solution file for the ego's driven trajectory."""
    status = (result.get("planner") or {}).get("status")
    if driver != "planner" or status not in ("completed", "goal-reached"):
        return None

    import numpy as np
    from commonroad.common.file_reader import CommonRoadFileReader
    from commonroad.common.solution import (
        CommonRoadSolutionWriter, CostFunction, PlanningProblemSolution,
        Solution, VehicleModel, VehicleType,
    )
    from commonroad.scenario.state import KSState
    from commonroad.scenario.trajectory import Trajectory

    ego = result.get("ego")
    states = (result.get("entity_trajectories") or {}).get(ego) or []
    if len(states) < 2:
        return None

    try:
        scenario, pps = CommonRoadFileReader(str(bundle_dir / "scenario.xml")).open()
        problem_id = next(iter(pps.planning_problem_dict))
    except Exception:  # noqa: BLE001 — a missing bundle must not fail the run
        return None

    trajectory = Trajectory(
        initial_time_step=int(states[0]["step"]),
        state_list=[
            KSState(
                time_step=int(s["step"]),
                position=np.array([s["x"], s["y"]]),
                orientation=float(s["h"]),
                velocity=float(s["v"]),
                steering_angle=0.0,
            )
            for s in states
        ],
    )
    try:
        solution = Solution(scenario.scenario_id, [PlanningProblemSolution(
            planning_problem_id=problem_id,
            vehicle_model=VehicleModel.KS,
            vehicle_type=VehicleType.BMW_320i,
            cost_function=CostFunction.JB1,
            trajectory=trajectory,
        )])
        CommonRoadSolutionWriter(solution).write_to_file(
            output_path=str(bundle_dir), overwrite=True,
        )
    except Exception as exc:  # noqa: BLE001
        return f"solution not written ({type(exc).__name__}: {exc})"

    hits = sorted(bundle_dir.glob("*solution*.xml")) or sorted(bundle_dir.glob("solution*.xml"))
    return str(hits[-1]) if hits else None
