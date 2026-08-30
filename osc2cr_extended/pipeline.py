"""
pipeline.py
===========
OpenSCENARIO → CommonRoad conversion **with the triggers kept**.

What the stock converter does (the "blue path"): esmini executes the
storyboard, every trigger is evaluated and thrown away, and what lands in the
CommonRoad file is a set of flat trajectories.

What this pipeline adds (the "teal path"): the same ``.xosc`` is parsed a
second time, this time for its storyboard, and the resulting condition model is
carried through three representation strategies and written *into* the
CommonRoad file:

  Transcription   every condition preserved as metadata (full coverage)
  Translation     conditions that have a CommonRoad analogue become goal
                  time/velocity intervals on the planning problem
  Interpretation  conditions are re-evaluated against the converted
                  trajectories, so we know when each event actually fires

Every stage is timed individually (:class:`StageTimings`) — that is what feeds
the benchmark.

Output bundle (one folder per scenario)
---------------------------------------
  scenario.xml        CommonRoad + Translation-enriched planning problem + embedded triggers
  scenario_plain.xml  identical, trigger block stripped (strict-XSD consumers)
  triggers.json       the trigger model as a standalone sidecar
  timeline.json       per-condition truth value at every time step (viewer)
  conditions_transcription.json   Transcription raw output
  conditions_translation.json   Translation mapping outcome
  report_translation.txt        Translation human-readable report
  trace_interpretation.json        Interpretation replay — which events fired, when
  bundle.json         manifest: stats, timings, file roles
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths
from .conditions_ext import attach_extensions
from .coverage import condition_coverage
from .embed import embed_triggers, strip_triggers
from .params import load_parameters, resolve_entity_references
from .roadmanager import LanePositionResolver

paths.bootstrap()


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

@dataclass
class StageTimings:
    """Wall-clock seconds per pipeline stage."""

    cr_conversion: float = 0.0      # blue path: esmini simulation + CR build
    storyboard_parse: float = 0.0   # teal path: .xosc → ParsedStoryboard
    strategy_transcription: float = 0.0    # triggers as metadata
    strategy_translation: float = 0.0      # map onto native CR constructs
    strategy_interpretation: float = 0.0   # build executor
    interpretation_replay: float = 0.0     # re-evaluate conditions over trajectories
    trigger_merge: float = 0.0      # build the triggers.json document
    timeline: float = 0.0           # per-step condition truth matrix
    write_embed: float = 0.0        # write CR files + embed triggers + sidecars
    total: float = 0.0

    @property
    def trigger_preservation(self) -> float:
        """Everything the stock converter does not do — the tool's overhead."""
        return (
            self.storyboard_parse + self.strategy_transcription + self.strategy_translation
            + self.strategy_interpretation + self.interpretation_replay + self.trigger_merge
            + self.timeline
        )

    def to_dict(self) -> Dict[str, float]:
        d = {k: round(v, 4) for k, v in asdict(self).items()}
        d["trigger_preservation"] = round(self.trigger_preservation, 4)
        return d


_IMPORTS_READY = False


def ensure_imports() -> float:
    """
    Import the heavy dependencies once, outside any timed region.

    commonroad-io, crdesigner and the converter together take several seconds
    to import.  Doing it lazily inside a conversion would bill the first
    scenario for module loading and make its timing meaningless — especially
    under the benchmark's subprocess isolation, where every scenario would pay
    it.  Returns the seconds spent (0.0 when already loaded).
    """
    global _IMPORTS_READY
    if _IMPORTS_READY:
        return 0.0

    start = time.perf_counter()
    import commonroad.common.file_writer  # noqa: F401
    import osc_cr_converter.converter.osc2cr  # noqa: F401

    from osc2cr_extended.strategies import transcription  # noqa: F401
    from osc2cr_extended.strategies import translation  # noqa: F401
    from osc2cr_extended.strategies import interpretation  # noqa: F401
    from osc2cr_extended.strategies.shared import storyboard_parser, triggers_export  # noqa: F401

    _IMPORTS_READY = True
    return time.perf_counter() - start


@contextmanager
def _timed(timings: StageTimings, field_name: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        setattr(timings, field_name,
                getattr(timings, field_name) + time.perf_counter() - start)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class ConversionResult:
    """Outcome of one .xosc → CommonRoad conversion."""

    name: str
    xosc_path: str
    ok: bool
    timings: StageTimings
    stats: Dict[str, Any] = field(default_factory=dict)
    files: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None
    bundle_dir: Optional[str] = None
    #: closed-loop artifacts removed because this re-conversion invalidated them
    discarded_cosim: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "xosc_path": self.xosc_path,
            "ok": self.ok,
            "error": self.error,
            "bundle_dir": self.bundle_dir,
            "timings_s": self.timings.to_dict(),
            "discarded_cosim": self.discarded_cosim,
            "stats": self.stats,
            "files": self.files,
        }

    def summary(self) -> str:
        if not self.ok:
            return f"✗ {self.name}: {self.error}"
        s, t = self.stats, self.timings
        cov = s.get("coverage", {})
        warn = ""
        if cov.get("unsupported_conditions"):
            warn += (f"  ⚠ {cov['unsupported_conditions']} unsupported condition(s): "
                     f"{', '.join(sorted(cov['unsupported']))}")
        if cov.get("declared_conditions"):
            warn += (f"  · {cov['declared_conditions']} declared but not "
                     f"evaluable: {', '.join(sorted(cov['declared_only']))}")
        if not s.get("lanelets"):
            road = s.get("road_network", {})
            reason = road.get("error") or "the .xodr produced no lanelets"
            warn += (f"  ⚠ no lanelet network — OpenDRIVE conversion failed: "
                     f"{reason}")
        return (
            f"✓ {self.name}: {s.get('obstacles', 0)} obstacles, "
            f"{s.get('lanelets', 0)} lanelets, {s.get('time_steps', 0)} steps | "
            f"{s.get('events', 0)} events / {s.get('conditions', 0)} conditions, "
            f"translation: {s.get('translation_mapped', 0)} mapped / "
            f"{s.get('translation_skipped', 0)} skipped, "
            f"interpretation: {s.get('interpretation_fired', 0)} fires | "
            f"{t.total:.2f}s" + warn
        )


def _merge_extension_conditions(transcription_dict: Dict[str, Any], storyboard: Any) -> None:
    """
    Add the extension conditions to Transcription's per-event dictionaries.

    ``strategies.transcription`` walks the storyboard and serialises the condition types it
    knows; the types added by ``conditions_ext`` come out as bare
    ``{type, name, delay_s, edge}``.  Replacing those entries with each
    condition's own ``to_dict()`` keeps the thresholds, targets and
    unevaluable-reasons in the trigger document that ends up embedded in the
    CommonRoad file.
    """
    from .conditions_ext import ExtCondition

    by_event: Dict[str, List[Any]] = {}
    for story in storyboard.stories:
        for act in story.acts:
            for mg in act.maneuver_groups:
                for maneuver in mg.maneuvers:
                    for event in maneuver.events:
                        exts = [
                            c for group in (event.start_trigger or [])
                            for c in group if isinstance(c, ExtCondition)
                        ]
                        if exts:
                            by_event.setdefault(event.name, []).extend(exts)

    for event_name, exts in by_event.items():
        entry = transcription_dict.get(event_name)
        if entry is None:
            continue
        conditions = entry.setdefault("conditions", [])
        existing = {c.get("name") for c in conditions}
        for ext in exts:
            payload = ext.to_dict()
            payload["text"] = ext.describe()
            if payload["name"] in existing:
                for i, c in enumerate(conditions):
                    if c.get("name") == payload["name"]:
                        conditions[i] = payload
                        break
            else:
                conditions.append(payload)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def diagnose_empty_conversion(xosc_path: Path, exc: Exception) -> Optional[str]:
    """
    Explain an empty-trajectory failure instead of leaking a CommonRoad assert.

    When esmini ends a scenario at t = 0 it records no object states, and the
    converter then builds ``Trajectory(state_list=[])``, which commonroad-io
    rejects with::

        AssertionError: <Trajectory/state_list>: argument state_list must
        contain at least one state. length of state_list: 0.

    That message says nothing about the cause, which is upstream and almost
    always a **version mismatch**: the bundled esmini is v2.29.3, while the
    scenarios in ``esmini/resources/xosc`` track whatever version is checked
    out (v3.1.0 here).  Two known cases, both hit by ``highway_driver.xosc``:

    * *No Act in the storyboard.*  v2.29.3 logs "All acts are done, quit now"
      at 0.000 s and sets the quit flag, ignoring the storyboard StopTrigger.
      Newer esmini keeps running until that trigger fires.
    * *A controller the bundled version does not know* — ``NaturalDriver``
      arrived in esmini v2.44.2, long after v2.29.3.

    Returns a diagnostic, or None when this failure is something else.
    """
    if "state_list must contain at least one state" not in str(exc):
        return None

    reasons = []
    try:
        root = ET.parse(xosc_path).getroot()
    except (ET.ParseError, OSError):
        root = None

    if root is not None:
        if not list(root.iter("Act")):
            reasons.append(
                "its storyboard declares no <Act> (Init-only scenario), and the "
                "bundled esmini v2.29.3 quits such scenarios at t=0 "
                "(\"All acts are done, quit now\") instead of honouring the "
                "storyboard StopTrigger"
            )
        unknown = sorted({
            c.get("entryName") or c.get("name")
            for c in root.iter("CatalogReference")
            if (c.get("entryName") or "") in _CONTROLLERS_AFTER_2_29_3
        } | {
            c.get("name") for c in root.iter("Controller")
            if (c.get("name") or "") in _CONTROLLERS_AFTER_2_29_3
        } - {None})
        if unknown:
            reasons.append(
                f"it uses controller(s) {', '.join(unknown)}, added to esmini "
                f"after the bundled v2.29.3"
            )

    detail = "; ".join(reasons) if reasons else (
        "esmini ended the scenario before recording any state"
    )
    return (
        f"esmini produced no time steps, so every obstacle got an empty "
        f"trajectory. Cause: {detail}. "
        f"The converter vendors esmini v2.29.3; this scenario needs a newer "
        f"one. Nothing is wrong with the .xosc — it runs under the esmini "
        f"checkout in esmini/bin."
    )


#: Controllers introduced after the vendored esmini v2.29.3, so a scenario
#: using one cannot run under it.  NaturalDriver: v2.44.2 (2025-01-16).
_CONTROLLERS_AFTER_2_29_3 = {"NaturalDriver", "ACCController"}


class TriggerPreservingConverter:
    """
    Runs the full conversion for one .xosc file.

    Parameters
    ----------
    dt
        CommonRoad time step [s].  Also used to convert trigger times to steps.
    keep_plain_copy
        Also write ``scenario_plain.xml`` without the embedded trigger block.
    compute_timeline
        Evaluate every condition at every time step for the viewer's activity
        strips.  Costs one extra pass over the trajectories.
    """

    def __init__(
        self,
        dt: float = 0.1,
        keep_plain_copy: bool = True,
        compute_timeline: bool = True,
        fix_xodr: bool = False,
    ) -> None:
        self.dt = dt
        self.keep_plain_copy = keep_plain_copy
        self.compute_timeline = compute_timeline
        # Opt-in: repair OpenDRIVE constructs crdesigner's parser rejects.
        # Off by default because a repair approximates the source topology, and
        # inventing connectivity silently is the defect class this tool exposes.
        self.fix_xodr = fix_xodr

    # ------------------------------------------------------------------

    def convert(
        self,
        xosc: str | Path,
        output_dir: Optional[str | Path] = None,
        prefer: str = "bundled",
    ) -> ConversionResult:
        """Convert one scenario and write its bundle.  Never raises."""
        xosc_path = paths.resolve_xosc(str(xosc), prefer=prefer)
        name = xosc_path.stem
        out_dir = Path(output_dir) if output_dir else paths.OUTPUT_DIR / name

        # Load heavy modules before the clock starts, so the measured time is
        # conversion work rather than Python import machinery.
        ensure_imports()

        timings = StageTimings()
        t_start = time.perf_counter()

        try:
            result = self._convert_inner(xosc_path, name, out_dir, timings)
        except Exception as exc:  # noqa: BLE001 — a failed scenario must not stop a batch
            timings.total = time.perf_counter() - t_start
            self._discard_empty_dir(out_dir)
            # An empty-trajectory assert says nothing about why; name the cause
            diagnosis = diagnose_empty_conversion(xosc_path, exc)
            return ConversionResult(
                name=name,
                xosc_path=str(xosc_path),
                ok=False,
                timings=timings,
                error=diagnosis or f"{type(exc).__name__}: {exc}",
            )

        if not result.ok:
            self._discard_empty_dir(out_dir)

        timings.total = time.perf_counter() - t_start
        result.timings = timings
        self._write_manifest(result, out_dir)
        return result

    # ------------------------------------------------------------------

    def _convert_inner(
        self,
        xosc_path: Path,
        name: str,
        out_dir: Path,
        timings: StageTimings,
    ) -> ConversionResult:
        from osc_cr_converter.converter.base import EFailureReason
        from osc_cr_converter.converter.osc2cr import Osc2CrConverter
        from osc_cr_converter.utility.configuration import ConverterParams

        from osc2cr_extended.strategies.transcription import annotate_scenario
        from osc2cr_extended.strategies.translation import (
            enrich_planning_problem_set, map_storyboard,
        )
        from osc2cr_extended.strategies.shared.storyboard_parser import StoryboardParser
        from osc2cr_extended.strategies.shared.triggers_export import build_triggers

        from .live import (
            EdgeAwareExecutor, build_condition_timeline, replay_storyboard,
        )

        out_dir.mkdir(parents=True, exist_ok=True)

        # ── Blue path — esmini simulation + CommonRoad scenario ───────────
        from . import xodr_repair

        xodr_repair.reset()
        if self.fix_xodr and not xodr_repair.enable():
            paths.WARNINGS.append(
                "--fix-xodr requested but the converter's opendrive_to_commonroad "
                "could not be hooked; road networks will convert unrepaired."
            )

        with _timed(timings, "cr_conversion"):
            config = ConverterParams()
            config.scenario.dt_cr = self.dt
            config.debug.write_to_xml = False
            converter = Osc2CrConverter(config)
            conv = converter.run_conversion(str(xosc_path))

        if isinstance(conv, EFailureReason):
            return ConversionResult(
                name=name, xosc_path=str(xosc_path), ok=False, timings=timings,
                error=f"converter failed: {conv.name}",
            )

        cr = converter.conversion_result
        scenario, pps = cr.scenario, cr.planning_problem_set

        # The converter treats a failed OpenDRIVE→lanelet conversion as a
        # warning: it records the error and returns a scenario with real
        # trajectories (esmini reads the .xodr itself) but an *empty* lanelet
        # network.  That silently yields a map-less CommonRoad file, so keep
        # the reason and surface it instead of leaving the user to wonder.
        xodr_err = getattr(cr, "xodr_conversion_error", None)
        road_network = {
            "xodr_file": getattr(cr, "xodr_file", None),
            "error": (getattr(xodr_err, "exception_text", str(xodr_err))
                      if xodr_err is not None else None),
            # what --fix-xodr changed, so a repaired map is never mistaken for
            # a clean conversion
            "repairs": list(xodr_repair.LAST_REPAIRS),
        }

        # ── Teal path — storyboard → condition model ──────────────────────
        with _timed(timings, "storyboard_parse"):
            storyboard = StoryboardParser(str(xosc_path)).parse()
            # Entity names may be parameterised ($owner); resolve them or every
            # ByEntity condition would silently evaluate to False downstream.
            n_resolved, unresolved = resolve_entity_references(storyboard, xosc_path)
            # Carry the condition types the baseline taxonomy drops (see
            # conditions_ext); without this they vanish, and an event whose
            # whole trigger vanished fires unconditionally.
            n_ext, ext_counts = attach_extensions(
                storyboard, xosc_path, load_parameters(xosc_path),
            )

        # Lane positions in ReachPosition/Distance conditions need the road
        # geometry; esmini's RoadManager reads the same .xodr the scenario uses.
        resolver = (
            LanePositionResolver(road_network["xodr_file"])
            if road_network.get("xodr_file") else None
        )

        with _timed(timings, "strategy_transcription"):
            annotations = annotate_scenario(scenario=scenario, storyboard=storyboard)

        with _timed(timings, "strategy_translation"):
            problem_ids = (
                list(pps.planning_problem_dict.keys()) if pps is not None else [0]
            )
            mapped_problems, mapping_report = map_storyboard(
                storyboard, problem_ids=problem_ids, dt=self.dt
            )

        with _timed(timings, "strategy_interpretation"):
            executor = EdgeAwareExecutor(storyboard, resolver)

        # ── Interpretation replay — when does each event actually fire? ───────
        with _timed(timings, "interpretation_replay"):
            interpretation_trace = replay_storyboard(
                scenario=scenario, storyboard=storyboard,
                xosc_path=xosc_path, dt=self.dt, executor=executor,
            )

        # ── How much of the source trigger logic actually survived? ───────
        with _timed(timings, "trigger_merge"):
            coverage = condition_coverage(xosc_path, storyboard)

        # ── Merge the three views into one trigger document ───────────────
        with _timed(timings, "trigger_merge"):
            transcription_dict = annotations.to_dict()
            # Transcription serialises the baseline taxonomy only; give the
            # extension conditions the same treatment so they reach the
            # CommonRoad file and the viewer instead of stopping at the parser.
            _merge_extension_conditions(transcription_dict, storyboard)
            translation_dict = mapping_report.to_dict() if mapping_report else {}
            triggers = build_triggers(
                transcription_events=transcription_dict,
                translation_conditions=translation_dict,
                interpretation_trace=interpretation_trace,
                dt=self.dt,
                scenario_name=name,
                source_xosc=str(xosc_path),
            )
            # Carried into the CommonRoad file so a consumer can tell a
            # faithfully preserved trigger set from a partially parsed one.
            triggers["coverage"] = coverage
            triggers["counts"]["interpretation_fired_unconditional"] = sum(
                1 for f in interpretation_trace if f.get("unconditional")
            )

        # ── Per-step condition truth matrix (drives the viewer strips) ────
        timeline: Dict[str, Any] = {}
        if self.compute_timeline:
            with _timed(timings, "timeline"):
                timeline = build_condition_timeline(
                    scenario=scenario, storyboard=storyboard,
                    xosc_path=xosc_path, dt=self.dt, resolver=resolver,
                )

        # ── Write the bundle ──────────────────────────────────────────────
        with _timed(timings, "write_embed"):
            files = self._write_bundle(
                out_dir=out_dir,
                scenario=scenario,
                pps=pps,
                mapped_problems=mapped_problems,
                enrich_pps=enrich_planning_problem_set,
                annotations=annotations,
                mapping_report=mapping_report,
                interpretation_trace=interpretation_trace,
                triggers=triggers,
                timeline=timeline,
            )

        stats = self._collect_stats(
            scenario=scenario, triggers=triggers, timeline=timeline,
        )
        stats["entity_refs_resolved"] = n_resolved
        stats["entity_refs_unresolved"] = unresolved
        stats["coverage"] = coverage
        stats["road_network"] = road_network
        stats["interpretation_fired_unconditional"] = sum(
            1 for f in interpretation_trace if f.get("unconditional")
        )

        return ConversionResult(
            name=name,
            xosc_path=str(xosc_path),
            ok=True,
            timings=timings,
            stats=stats,
            files=files,
            bundle_dir=str(out_dir),
            discarded_cosim=[
                x.strip() for x in files.pop("discarded_cosim", "").split(",") if x.strip()
            ],
        )

    # ------------------------------------------------------------------

    def _write_bundle(
        self,
        out_dir: Path,
        scenario: Any,
        pps: Any,
        mapped_problems: Any,
        enrich_pps: Any,
        annotations: Any,
        mapping_report: Any,
        interpretation_trace: List[Dict],
        triggers: Dict[str, Any],
        timeline: Dict[str, Any],
    ) -> Dict[str, str]:
        from commonroad.common.file_writer import (
            CommonRoadFileWriter, OverwriteExistingFile,
        )

        files: Dict[str, str] = {}

        # Everything is written to a staging directory and only moved into
        # place once the whole bundle exists.  Writing directly into out_dir
        # meant a re-conversion that failed midway left the old bundle
        # half-overwritten — a fresh scenario.xml (possibly without its trigger
        # block) next to the *previous* run's triggers.json.  The viewer then
        # silently fell back to the stale sidecar and showed a scenario whose
        # geometry and triggers came from different runs.
        stage = out_dir / ".staging"
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True, exist_ok=True)

        # Planning problem set enriched with Translation's goal intervals.
        # Fall back to the original set if enrichment is not applicable.
        write_pps = pps
        if mapped_problems and pps is not None:
            try:
                write_pps = enrich_pps(pps, mapped_problems)
            except Exception as exc:  # noqa: BLE001
                files["pps_enrichment_error"] = str(exc)
                write_pps = pps

        scenario_xml = stage / "scenario.xml"
        CommonRoadFileWriter(
            scenario=scenario, planning_problem_set=write_pps,
        ).write_to_file(str(scenario_xml), OverwriteExistingFile.ALWAYS)

        if self.keep_plain_copy:
            strip_triggers(scenario_xml, stage / "scenario_plain.xml")

        # The contribution: triggers travel inside the CommonRoad file
        embed_triggers(scenario_xml, triggers)

        def _dump(fname: str, payload: Any, role: str) -> None:
            p = stage / fname
            p.write_text(json.dumps(payload, indent=2))
            files[role] = str(out_dir / fname)

        _dump("triggers.json", triggers, "triggers_json")
        _dump("conditions_transcription.json", annotations.to_dict(),
              "conditions_transcription_json")
        if mapping_report is not None:
            _dump("conditions_translation.json", mapping_report.to_dict(),
                  "conditions_translation_json")
            (stage / "report_translation.txt").write_text(mapping_report.summary())
            files["report_translation_txt"] = str(out_dir / "report_translation.txt")
        _dump("trace_interpretation.json", interpretation_trace, "trace_interpretation_json")
        if timeline:
            _dump("timeline.json", timeline, "timeline_json")

        # The bundle is complete — publish it.  Stale files from a previous
        # run are removed first so a bundle never mixes two conversions.
        discarded = self._publish_stage(stage, out_dir)
        if discarded:
            files["discarded_cosim"] = ", ".join(discarded)

        files["scenario_xml"] = str(out_dir / "scenario.xml")
        if self.keep_plain_copy:
            files["scenario_plain_xml"] = str(out_dir / "scenario_plain.xml")
        return files

    # ------------------------------------------------------------------

    @staticmethod
    def _publish_stage(stage: Path, out_dir: Path) -> List[str]:
        """
        Move a completed staging directory's files into the bundle.

        Anything the run did not produce is dropped, which includes every
        closed-loop artifact (``cosim_*``, ``solution_*``, ``external/``).
        That is deliberate: a co-simulation trace describes the geometry of the
        conversion it was run against, and showing it beside a *newer* replay
        would compare two different scenarios while looking entirely plausible —
        the failure mode this whole tool exists to prevent.

        Deleting them silently is its own version of that problem, though, so
        the discarded names are returned and reported.
        """
        produced = {p.name for p in stage.iterdir()}
        discarded: List[str] = []

        for old in out_dir.iterdir():
            if old.name == stage.name:
                continue
            if old.is_dir():
                # `external/` is the staged scenario tree of a previous
                # planner run; it belongs to that run, not to this conversion
                if old.name == "external":
                    shutil.rmtree(old, ignore_errors=True)
                    discarded.append("external/")
                continue
            if old.name not in produced and old.name != "bundle.json":
                if old.name.startswith(("cosim_", "solution_")):
                    discarded.append(old.name)
                old.unlink(missing_ok=True)

        for item in stage.iterdir():
            os.replace(item, out_dir / item.name)

        stage.rmdir()
        return sorted(discarded)

    # ------------------------------------------------------------------

    @staticmethod
    def _collect_stats(
        scenario: Any, triggers: Dict[str, Any], timeline: Dict[str, Any],
    ) -> Dict[str, Any]:
        obstacles = list(scenario.dynamic_obstacles)
        max_step = max(
            (
                s.time_step
                for o in obstacles
                if o.prediction is not None and o.prediction.trajectory is not None
                for s in o.prediction.trajectory.state_list
            ),
            default=0,
        )
        counts = triggers.get("counts", {})
        return {
            "dt": scenario.dt,
            "obstacles": len(obstacles),
            "lanelets": len(scenario.lanelet_network.lanelets),
            "time_steps": int(max_step) + 1,
            "duration_s": round((int(max_step)) * scenario.dt, 2),
            "events": counts.get("events", 0),
            "conditions": counts.get("conditions", 0),
            "translation_mapped": counts.get("translation_mapped", 0),
            "translation_skipped": counts.get("translation_skipped", 0),
            "interpretation_fired": counts.get("interpretation_fired", 0),
            "timeline_conditions": len(timeline.get("conditions", [])),
        }

    # ------------------------------------------------------------------

    @staticmethod
    def _discard_empty_dir(out_dir: Path) -> None:
        """
        Clean up after a failed conversion.

        Any half-written staging directory is removed, so a failure never
        leaves partial artefacts behind and any previously published bundle
        stays intact and usable.  The bundle directory itself is removed only
        when it is empty, i.e. nothing was ever successfully converted here.
        """
        try:
            stage = out_dir / ".staging"
            if stage.is_dir():
                shutil.rmtree(stage, ignore_errors=True)
            if out_dir.is_dir() and not any(out_dir.iterdir()):
                out_dir.rmdir()
        except OSError:
            pass

    @staticmethod
    def _write_manifest(result: ConversionResult, out_dir: Path) -> None:
        if not result.ok:
            return
        manifest = out_dir / "bundle.json"
        manifest.write_text(json.dumps(result.to_dict(), indent=2))
        result.files["bundle_json"] = str(manifest)


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def convert(
    xosc: str | Path,
    output_dir: Optional[str | Path] = None,
    dt: float = 0.1,
) -> ConversionResult:
    """Convert a single scenario with default settings."""
    return TriggerPreservingConverter(dt=dt).convert(xosc, output_dir)


def convert_isolated(
    name: str,
    out_root: Optional[Path] = None,
    dt: float = 0.1,
    timeout: float = 900.0,
    fix_xodr: bool = False,
) -> Dict[str, Any]:
    """
    Convert in a child process and return the result as a plain dict.

    esmini is native code reached through a process-wide handle, and some
    scenarios crash it outright — ``esmini/resources/xosc/cut-in.xosc``
    segfaults on this machine.  In-process that kills whatever is driving the
    conversion: the whole benchmark, or the viewer's server.  Isolated, a crash
    costs one scenario and is reported like any other failure.

    The shape matches :meth:`ConversionResult.to_dict`, with ``ok`` False and
    an ``error`` string when the child died.
    """
    import subprocess
    import tempfile

    root = out_root or paths.OUTPUT_DIR

    with tempfile.TemporaryDirectory() as tmp:
        result_path = Path(tmp) / "result.json"
        cmd = [
            sys.executable, "-m", paths.MODULE_NAME, "convert", name,
            "--dt", str(dt),
            "--output", str(root),
            "--json-out", str(result_path),
            *(["--fix-xodr"] if fix_xodr else []),
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=str(paths.TOOL_ROOT), capture_output=True,
                text=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            return {"name": name, "ok": False,
                    "error": f"timed out after {timeout:.0f}s"}

        if result_path.exists():
            try:
                return json.loads(result_path.read_text())
            except json.JSONDecodeError as exc:
                return {"name": name, "ok": False,
                        "error": f"unreadable result: {exc}"}

        # No result file — the child died before writing one
        if proc.returncode < 0:
            signame = signal.Signals(-proc.returncode).name
            return {"name": name, "ok": False,
                    "error": f"esmini crashed the interpreter ({signame})"}

        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = tail[-1] if tail else "no output"
        return {"name": name, "ok": False,
                "error": f"exit {proc.returncode}: {detail[:160]}"}
