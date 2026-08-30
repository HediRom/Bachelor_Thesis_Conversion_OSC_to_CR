"""
run_examples.py
===============
Full pipeline for each .xosc in input/:

  Blue path  : Osc2CrConverter  → CR Scenario + PlanningProblemSet (trajectories)
  Teal path  : StoryboardParser → ParsedStoryboard → Transcription, Translation, Interpretation
  Merge      : EnrichedScenario.save() writes per-TranslationR files to output/

Output layout for each scenario
--------------------------------
  output/<name>/
    scenario_transcription.xml       CR scenario (original PPS) — trajectories intact
    conditions_transcription.json    every trigger event with actors + full condition dicts
    scenario_translation.xml       CR scenario with PPS enriched by trigger-derived goal intervals
    report_translation.txt         which conditions mapped to CR goals, which were skipped
    conditions_translation.json    same mapping outcome, keyed by condition name (like conditions_transcription.json)
    scenario_interpretation.xml       CR scenario (original PPS) — same as B
    trace_interpretation.json         events that fired when replaying actual trajectories through D

Run with the cr-osc-converter conda environment:
    python run_examples.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Silence converter's verbose INFO logging
logging.basicConfig(level=logging.ERROR)

# Runnable straight from a checkout, without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from osc2cr_extended import paths

BUNDLED_XOSC = paths.BUNDLED_XOSC

from osc2cr_extended.strategies.shared.road_network import disable_lanelet_geo_reprojection
disable_lanelet_geo_reprojection()

INPUT_DIR  = paths.LOCAL_XOSC
OUTPUT_DIR = paths.OUTPUT_DIR

# Use the original bundled .xosc paths so relative ../xodr/ references resolve.
# The input/ folder keeps copies for reference.
SCENARIOS = [
    BUNDLED_XOSC / "cut-in_simple.xosc",
    BUNDLED_XOSC / "acc-test.xosc",
    BUNDLED_XOSC / "drop-bike.xosc",
]


# ---------------------------------------------------------------------------
# Per-scenario processing
# ---------------------------------------------------------------------------

def process(xosc_path: Path) -> None:
    name = xosc_path.stem
    out_dir = OUTPUT_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*65}")
    print(f"  {name}")
    print(f"{'='*65}")

    # ── Blue path: run the converter ──────────────────────────────────
    print("  [1/3] Running Osc2CrConverter (esmini) …")
    from osc_cr_converter.converter.osc2cr import Osc2CrConverter
    from osc_cr_converter.converter.base import EFailureReason
    from osc_cr_converter.utility.configuration import ConverterParams

    config = ConverterParams()
    config.debug.write_to_xml = False
    converter = Osc2CrConverter(config)
    result = converter.run_conversion(str(xosc_path))

    if isinstance(result, EFailureReason):
        print(f"  ✗ Converter failed: {result.name}")
        return

    cr_result  = converter.conversion_result
    scenario   = cr_result.scenario
    pps        = cr_result.planning_problem_set
    n_obs      = len(scenario.dynamic_obstacles)
    duration_s = max(
        (s.time_step for obs in scenario.dynamic_obstacles
         if obs.prediction and obs.prediction.trajectory
         for s in obs.prediction.trajectory.state_list),
        default=0,
    ) * config.scenario.dt_cr

    print(f"  ✓ Converted: {n_obs} obstacles, {duration_s:.1f} s trajectory")

    # ── Teal path + merge ─────────────────────────────────────────────
    print("  [2/3] Parsing storyboard + applying B / C / D …")
    from osc2cr_extended.strategies.merge import merge

    enriched = merge(
        scenario=scenario,
        planning_problem_set=pps,
        xosc_path=str(xosc_path),
        strategies=("transcription", "translation", "interpretation"),
        dt=config.scenario.dt_cr,
    )

    print(f"  ✓ Events parsed: {enriched.n_events}  |  conditions: {enriched.n_conditions}")
    if enriched.mapping_report:
        n_mapped  = len(enriched.mapping_report.mapped_time) + len(enriched.mapping_report.mapped_velocity)
        n_skipped = len(enriched.mapping_report.skipped)
        print(f"  ✓ Translation: {n_mapped} mapped, {n_skipped} skipped")

    # ── Save outputs ──────────────────────────────────────────────────
    print("  [3/3] Writing outputs …")
    written = enriched.save(str(out_dir))

    # Also save a human-readable summary
    summary_path = out_dir / "summary.txt"
    summary_path.write_text(enriched.summary())
    written["summary_txt"] = str(summary_path)

    print(f"\n  Files written to output/{name}/")
    for role, path in written.items():
        print(f"    {role:28s}  {Path(path).name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    present = [p for p in SCENARIOS if p.exists()]
    if not present:
        print(f"No input files found. Expected in {INPUT_DIR}")
        sys.exit(1)

    print(f"Processing {len(present)} scenario(s) …")
    for xosc in present:
        process(xosc)

    print(f"\n{'='*65}")
    print(f"Done. All outputs under output/")
