"""
Enriched CommonRoad Scenario builder
=====================================
Merges the two pipeline paths described in the thesis workflow:

  Blue path (existing converter)
      .xosc → esmini simulation → flat CR Scenario (trajectories only)

  Red path (this contribution)
      .xosc → StoryboardParser → ParsedStoryboard
            → Representation strategy (Transcription / Translation / Interpretation)

  Merge
      EnrichedScenario = flat trajectories + preserved conditional structure

Public API
----------
  merge(scenario, planning_problem_set, xosc_path, strategies, dt)
      → EnrichedScenario

  run_pipeline(xosc_path, converter_config, strategies, dt)
      → EnrichedScenario
      (runs the existing Osc2CrConverter internally, then calls merge)

  EnrichedScenario.save(output_dir)
      → writes scenario_enriched.xml  +  conditions_transcription.json  +  report_translation.txt
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set, Tuple, Union

_HERE = Path(__file__).resolve().parent

from osc2cr_extended.strategies.shared.storyboard_parser import StoryboardParser
from osc2cr_extended.strategies.shared.condition_model import ParsedStoryboard

from osc2cr_extended.strategies.transcription import annotate_scenario, AnnotatedScenario
from osc2cr_extended.strategies.translation import (
    map_storyboard, MappedPlanningProblem, MappingReport,
)
from osc2cr_extended.strategies.interpretation import StoryboardExecutor
from osc2cr_extended.strategies.condition_evaluator import EntityState, SimState


# ---------------------------------------------------------------------------
# EnrichedScenario
# ---------------------------------------------------------------------------

@dataclass
class EnrichedScenario:
    """
    The key artifact of the thesis pipeline.

    Contains both what the existing converter produced (flat trajectories in
    a standard CR Scenario) and what the storyboard parser preserved
    (conditional logic in one or more representation strategies).

    Attributes
    ----------
    scenario
        Standard CommonRoad Scenario object from the existing converter.
        All existing CR tools can consume this unmodified.
    planning_problem_set
        Planning problem set from the converter.  Translation may produce
        an enriched version in ``mapped_problems``.
    storyboard
        ParsedStoryboard — the full hierarchical condition model extracted
        from the same .xosc that produced ``scenario``.
    xosc_path
        Absolute path to the source .xosc file.
    dt
        Simulation time step [s] used for converting time → time_step.

    Transcription — always computed
    ----------------------------------------
    annotations
        AnnotatedScenario wrapping ``scenario`` with a JSON-serialisable
        event_annotations dict.  Every condition type is represented.

    Translation — computed when "translation" in strategies
    -----------------------------------------------------------
    mapped_problems
        List of MappedPlanningProblem: time/velocity goal intervals derived
        from ByValue conditions.  None when Translation was not requested.
    mapping_report
        MappingReport: which conditions were mapped and which were skipped.

    Interpretation — computed when "interpretation" in strategies
    -------------------------------------------------------
    executor
        StoryboardExecutor ready for step-by-step simulation.  Call
        executor.step(sim_state) each tick to get FiredEvent lists.
        None when Interpretation was not requested.
    """

    scenario: Any                                    # CR Scenario
    planning_problem_set: Any                        # CR PlanningProblemSet
    storyboard: ParsedStoryboard
    xosc_path: str
    dt: float
    strategies: Set[str]

    # Transcription — always present
    annotations: AnnotatedScenario = field(repr=False)

    # Translation — present when "translation" in strategies
    mapped_problems: Optional[List[MappedPlanningProblem]] = None
    mapping_report: Optional[MappingReport] = None

    # Interpretation — present when "interpretation" in strategies
    executor: Optional[StoryboardExecutor] = None

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def n_obstacles(self) -> int:
        if self.scenario is None:
            return 0
        return len(self.scenario.dynamic_obstacles)

    @property
    def n_events(self) -> int:
        return len(self.annotations.event_annotations)

    @property
    def n_conditions(self) -> int:
        return len(self.annotations.all_conditions)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def save(self, output_dir: str) -> Dict[str, str]:
        """
        Write one output folder per strategy.

        Files written
        -------------
        scenario_transcription.xml     — CR scenario (original PPS), with the
                                         conditions_transcription.json sidecar
        conditions_transcription.json  — every trigger event with actors and
                                         condition dicts
        scenario_translation.xml       — CR scenario with PPS enriched by
                                         trigger-derived goal intervals
        report_translation.txt         — what was mapped vs. skipped by
                                         Translation (human-readable)
        conditions_translation.json    — same mapping outcome, keyed by condition
                                         name like conditions_transcription.json,
                                         for cross-strategy comparison
        scenario_interpretation.xml    — CR scenario (original PPS), with the
                                         trace_interpretation.json sidecar
        trace_interpretation.json      — events that fired when replaying the
                                         actual trajectories through Interpretation

        Returns a dict mapping role → absolute file path.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        written: Dict[str, str] = {}

        # ── helpers ───────────────────────────────────────────────────────
        def _write_cr(xml_name: str, pps: Any) -> Optional[str]:
            """Write a CR .xml file; returns path or None if CR not available."""
            if self.scenario is None or pps is None:
                return None
            try:
                from commonroad.common.file_writer import (
                    CommonRoadFileWriter, OverwriteExistingFile,
                )
                p = out / xml_name
                CommonRoadFileWriter(
                    scenario=self.scenario,
                    planning_problem_set=pps,
                ).write_to_file(str(p), OverwriteExistingFile.ALWAYS)
                return str(p)
            except Exception as exc:
                written[f"{xml_name}_error"] = str(exc)
                return None

        # ── Transcription ────────────────────────────────────────────────────
        # CR file: original scenario + original PPS (trajectories only)
        # Sidecar: conditions_transcription.json carries the full trigger/condition metadata
        if "transcription" in self.strategies:
            p = _write_cr("scenario_transcription.xml", self.planning_problem_set)
            if p:
                written["transcription_scenario_xml"] = p
            transcription_json = out / "conditions_transcription.json"
            self.annotations.dump_json(str(transcription_json))
            written["transcription_conditions_json"] = str(transcription_json)

        # ── Translation ────────────────────────────────────────────────────
        # CR file: original scenario + ENRICHED PPS (goal intervals from conditions)
        # Report: which conditions mapped, which were skipped
        if "translation" in self.strategies and self.mapping_report is not None:
            report_txt = out / "report_translation.txt"
            report_txt.write_text(self.mapping_report.summary())
            written["translation_report_txt"] = str(report_txt)

            translation_json = out / "conditions_translation.json"
            translation_json.write_text(json.dumps(self.mapping_report.to_dict(), indent=2))
            written["translation_conditions_json"] = str(translation_json)

            if self.mapped_problems and self.planning_problem_set is not None:
                try:
                    from osc2cr_extended.strategies.translation import enrich_planning_problem_set
                    enriched_pps = enrich_planning_problem_set(
                        self.planning_problem_set, self.mapped_problems
                    )
                    p = _write_cr("scenario_translation.xml", enriched_pps)
                    if p:
                        written["translation_scenario_xml"] = p
                except Exception as exc:
                    written["C_scenario_xml_error"] = str(exc)

        # ── Interpretation ────────────────────────────────────────────────────
        # CR file: original scenario + original PPS
        # Sidecar: trace_interpretation.json — events that fired when replaying real trajectories
        if "interpretation" in self.strategies and self.executor is not None:
            p = _write_cr("scenario_interpretation.xml", self.planning_problem_set)
            if p:
                written["interpretation_scenario_xml"] = p

            trace = self._replay_trajectories()
            trace_json = out / "trace_interpretation.json"
            trace_json.write_text(json.dumps(trace, indent=2))
            written["interpretation_trace_json"] = str(trace_json)

        # ── triggers.json ─────────────────────────────────────────────────
        # Merged Transcription/Translation/Interpretation trigger view,
        # consumed by the crdesigner web overlay
        # (web_overlay/crdesigner_triggers.user.js). Built from the sidecar
        # files written above.
        try:
            from osc2cr_extended.strategies.shared.triggers_export import export_triggers_json
            tj = export_triggers_json(out, dt=self.dt, source_xosc=self.xosc_path)
            written["triggers_json"] = str(tj)
        except Exception as exc:
            written["triggers_json_error"] = str(exc)

        return written

    def _replay_trajectories(self) -> List[Dict]:
        """
        Step the Interpretation executor through the actual converted trajectories.

        Maps CR obstacle IDs to storyboard actor names by order (first obstacle
        → first actor name found in the storyboard, etc.), then feeds each
        time step's position/speed into the executor.
        """
        if self.scenario is None or self.executor is None:
            return []

        obstacles = sorted(
            self.scenario.dynamic_obstacles, key=lambda o: o.obstacle_id
        )
        if not obstacles:
            return []

        # Build obstacle_id → storyboard actor name mapping
        all_actor_names: List[str] = []
        for ann in self.annotations.event_annotations.values():
            for a in ann.actors:
                if a not in all_actor_names:
                    all_actor_names.append(a)
        # Also collect names from condition entity refs
        from osc2cr_extended.strategies.shared.condition_model import (
            RelativeDistanceCondition, RelativeSpeedCondition,
            TimeHeadwayCondition, TimeToCollisionCondition,
        )
        for cond in self.annotations.all_conditions:
            for attr in ("triggering_entity", "reference_entity"):
                name = getattr(cond, attr, None)
                if name and name not in all_actor_names:
                    all_actor_names.append(name)

        id_to_name: Dict[int, str] = {}
        for i, obs in enumerate(obstacles):
            id_to_name[obs.obstacle_id] = (
                all_actor_names[i] if i < len(all_actor_names)
                else f"entity_{obs.obstacle_id}"
            )

        # Collect all time steps across all obstacles
        all_ts: set = set()
        for obs in obstacles:
            if obs.prediction and obs.prediction.trajectory:
                for s in obs.prediction.trajectory.state_list:
                    all_ts.add(s.time_step)
        if not all_ts:
            return []

        dt = self.dt
        fired_log: List[Dict] = []

        # Build a per-obstacle lookup: time_step → state
        obs_lookup: Dict[int, Dict[int, Any]] = {}
        for obs in obstacles:
            lookup: Dict[int, Any] = {}
            if obs.initial_state is not None:
                lookup[obs.initial_state.time_step] = obs.initial_state
            if obs.prediction and obs.prediction.trajectory:
                for s in obs.prediction.trajectory.state_list:
                    lookup[s.time_step] = s
            obs_lookup[obs.obstacle_id] = lookup

        for ts in sorted(all_ts):
            t = round(ts * dt, 4)
            entities: Dict[str, Any] = {}

            for obs in obstacles:
                state = obs_lookup[obs.obstacle_id].get(ts)
                if state is None:
                    continue
                name = id_to_name[obs.obstacle_id]
                shape = obs.obstacle_shape
                length = float(getattr(shape, "length", 4.5))
                width = float(getattr(shape, "width", 1.8))
                from osc2cr_extended.strategies.condition_evaluator import EntityState
                entities[name] = EntityState(
                    entity_id=name,
                    x=float(state.position[0]),
                    y=float(state.position[1]),
                    speed=float(state.velocity) if state.velocity is not None else 0.0,
                    heading=float(state.orientation) if state.orientation is not None else 0.0,
                    length=length,
                    width=width,
                )

            if not entities:
                continue

            from osc2cr_extended.strategies.condition_evaluator import SimState
            sim_state = SimState(time=t, entities=entities)

            # update traveled distances
            for name, ent in entities.items():
                self.executor.evaluator.update_distances(name, ent.speed * dt)

            for ev in self.executor.step(sim_state):
                fired_log.append({
                    "time_s": ev.time,
                    "story": ev.story,
                    "act": ev.act,
                    "event": ev.event_name,
                    "actors": ev.actors,
                    "fire_count": ev.execution_count,
                })

        return fired_log

    def summary(self) -> str:
        strats = ", ".join(sorted(self.strategies))
        lines = [
            "=== EnrichedScenario ===",
            f"  source        : {self.xosc_path}",
            f"  strategies    : {strats}",
            f"  obstacles     : {self.n_obstacles}",
            f"  events parsed : {self.n_events}",
            f"  conditions    : {self.n_conditions}",
        ]
        if "transcription" in self.strategies:
            lines.append("\n-- Transcription --")
            lines.append(self.annotations.summary())
        if "translation" in self.strategies and self.mapping_report is not None:
            lines.append("\n-- Translation --")
            lines.append(self.mapping_report.summary())
            for p in (self.mapped_problems or []):
                ts = p.merged_time_step_interval()
                vel = p.merged_velocity_interval()
                lines.append(f"  PlanningProblem {p.original_problem_id}: "
                             f"time_step={ts}  velocity={vel}")
        if "interpretation" in self.strategies and self.executor is not None:
            lines.append("\n-- Interpretation --")
            lines.append("  StoryboardExecutor ready — call executor.step(SimState) each tick")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core merge function
# ---------------------------------------------------------------------------

def merge(
    scenario: Any,
    planning_problem_set: Any,
    xosc_path: str,
    strategies: Union[Set[str], Tuple[str, ...], List[str]] = (
        "transcription", "translation", "interpretation",
    ),
    dt: float = 0.1,
) -> EnrichedScenario:
    """
    Merge the flat CR Scenario (blue path) with the parsed storyboard (red path).

    Parameters
    ----------
    scenario
        CommonRoad Scenario returned by Osc2CrConverter.run_conversion().
    planning_problem_set
        PlanningProblemSet from the converter (converter.conversion_result.planning_problem_set).
    xosc_path
        Path to the same .xosc file that produced ``scenario``.
    strategies
        Which representation strategies to apply.  Any subset of
        {"transcription", "translation", "interpretation"}.
        Transcription is always computed regardless — it's the safe floor.
    dt
        Simulation timestep [s] used by the CR scenario.

    Returns
    -------
    EnrichedScenario
    """
    strategies = set(strategies) | {"transcription"}   # Transcription is the floor

    # Parse the storyboard (red path input)
    storyboard: ParsedStoryboard = StoryboardParser(xosc_path).parse()

    # Transcription — annotate (always)
    annotations = annotate_scenario(scenario=scenario, storyboard=storyboard)

    # Translation — native map
    mapped_problems: Optional[List[MappedPlanningProblem]] = None
    mapping_report: Optional[MappingReport] = None
    if "translation" in strategies:
        problem_ids = (
            list(planning_problem_set.planning_problem_dict.keys())
            if planning_problem_set is not None
            else [0]
        )
        mapped_problems, mapping_report = map_storyboard(
            storyboard, problem_ids=problem_ids, dt=dt
        )

    # Interpretation — executor
    executor: Optional[StoryboardExecutor] = None
    if "interpretation" in strategies:
        executor = StoryboardExecutor.from_storyboard(storyboard)

    return EnrichedScenario(
        scenario=scenario,
        planning_problem_set=planning_problem_set,
        storyboard=storyboard,
        xosc_path=str(Path(xosc_path).resolve()),
        dt=dt,
        strategies=strategies,
        annotations=annotations,
        mapped_problems=mapped_problems,
        mapping_report=mapping_report,
        executor=executor,
    )


# ---------------------------------------------------------------------------
# Full pipeline convenience function
# ---------------------------------------------------------------------------

def run_pipeline(
    xosc_path: str,
    converter_config: Any = None,
    strategies: Union[Set[str], Tuple[str, ...], List[str]] = (
        "transcription", "translation", "interpretation",
    ),
    dt: float = 0.1,
) -> EnrichedScenario:
    """
    Run the full thesis pipeline from a single .xosc file.

    1. Blue path : run Osc2CrConverter → flat CR Scenario + PlanningProblemSet
    2. Red path  : run StoryboardParser → ParsedStoryboard
    3. Merge     : apply requested strategies → EnrichedScenario

    Parameters
    ----------
    xosc_path
        Path to the OpenSCENARIO .xosc file.
    converter_config
        Optional ConverterParams instance.  If None, uses default config
        (no output file written, dt_cr=0.1).
    strategies
        Subset of {"transcription","translation","interpretation"}.
        Transcription is always included.
    dt
        Simulation timestep [s].  Should match converter_config.scenario.dt_cr.

    Returns
    -------
    EnrichedScenario

    Raises
    ------
    ImportError
        If the commonroad-openscenario-converter package is not on sys.path.
    RuntimeError
        If the converter fails (e.g. esmini not found, invalid scenario).
    """
    try:
        from osc_cr_converter.converter.osc2cr import Osc2CrConverter
        from osc_cr_converter.converter.base import EFailureReason
        from osc_cr_converter.utility.configuration import ConverterParams
        from osc2cr_extended.strategies.shared.road_network import disable_lanelet_geo_reprojection
        disable_lanelet_geo_reprojection()
    except ImportError as exc:
        raise ImportError(
            "commonroad-openscenario-converter not importable. "
            "Either install it (pip install -e commonroad-openscenario-converter/) "
            "or provide the scenario externally and call merge() directly.\n"
            f"Original error: {exc}"
        ) from exc

    if converter_config is None:
        converter_config = ConverterParams()
        converter_config.scenario.dt_cr = dt
        converter_config.debug.write_to_xml = False

    converter = Osc2CrConverter(converter_config)
    result = converter.run_conversion(xosc_path)

    if isinstance(result, EFailureReason):
        raise RuntimeError(
            f"Converter failed for '{xosc_path}': {result.name}\n"
            "If esmini is not available, obtain the CR Scenario externally "
            "and call merge() directly."
        )

    # converter.conversion_result holds the full Osc2CrConverterResult
    cr_result = converter.conversion_result
    scenario = cr_result.scenario
    pps = cr_result.planning_problem_set

    return merge(
        scenario=scenario,
        planning_problem_set=pps,
        xosc_path=xosc_path,
        strategies=strategies,
        dt=dt,
    )
