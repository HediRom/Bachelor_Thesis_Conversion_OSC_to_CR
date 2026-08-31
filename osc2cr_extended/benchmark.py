"""
benchmark.py
============
Times the conversion of a set of real OpenSCENARIO files and reports where the
time goes.

The question the benchmark answers is not "how fast is esmini" but **what does
preserving the triggers cost**.  Every scenario is therefore split into

  cr_conversion          what the stock converter already does
                         (esmini simulation + CommonRoad scenario construction)
  trigger_preservation   everything this tool adds on top: storyboard parse,
                         Transcription/Translation/Interpretation, the replay,
                         the condition timeline
  write_embed            writing the bundle, including embedding the triggers

Python imports are warmed up before the first measured run, otherwise the first
scenario absorbs several seconds of module loading that has nothing to do with
conversion.

Usage
-----
    python -m osc2cr benchmark                    # default suite
    python -m osc2cr benchmark cut-in_simple ...  # explicit scenarios
    python -m osc2cr benchmark --repeat 3         # median of 3 runs
"""
from __future__ import annotations

import json
import platform
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths
from .pipeline import (
    ConversionResult, TriggerPreservingConverter, convert_isolated,
    ensure_imports,
)

paths.bootstrap()

# Scenarios exercised by default: the converter's own corpus (their relative
# ../xodr/ references resolve) plus esmini files that cover trigger types the
# bundled ones do not.
DEFAULT_SUITE: List[str] = [
    "cut-in_simple",                 # TimeHeadway triggers, lane change + brake
    "cut-in_sloppy",                 # relative-distance triggers
    "acc-test",                      # ACC controller, long run
    "drop-bike",                     # bicycle drops into lane
    "pedestrian_collision",          # VRU collision
    "pedestrian",                    # VRU crossing, no storyboard triggers
    "alks_cut-in",                   # ALKS regulation cut-in
    "alks_decelerate",               # ALKS lead-vehicle deceleration
    "alks_r157_cut_in_quick_brake",  # ALKS cut-in + emergency brake
    "distance_test",                 # distance conditions
    "drive_when_close",              # relative-distance gated driving
    "highway_merge",                 # multi-actor merge
    "lane_change_simple",            # position-based triggers (partly unsupported)
]

# Scenarios in these corpora that the *existing converter* cannot handle on
# this machine.  Kept out of the default suite but recorded here because they
# motivate the benchmark's subprocess isolation:
#   cut-in             — segfaults esmini (SIGSEGV), taking the interpreter down
#   bicycle_fall_over  — produces an obstacle with an empty trajectory
#   follow_trajectory  — SIMULATION_FAILED_CREATING_OUTPUT (UDP driver controller)
KNOWN_FAILING: List[str] = ["cut-in", "bicycle_fall_over", "follow_trajectory"]


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkEntry:
    name: str
    ok: bool
    error: Optional[str] = None
    stats: Dict[str, Any] = field(default_factory=dict)
    runs: List[Dict[str, float]] = field(default_factory=list)

    # ---- aggregates over repeated runs ----

    def _series(self, key: str) -> List[float]:
        return [r.get(key, 0.0) for r in self.runs if r]

    def median(self, key: str) -> float:
        series = self._series(key)
        return round(statistics.median(series), 4) if series else 0.0

    def spread(self, key: str) -> float:
        """Max − min, so run-to-run noise is visible in the report."""
        series = self._series(key)
        return round(max(series) - min(series), 4) if len(series) > 1 else 0.0

    @property
    def overhead_pct(self) -> float:
        base = self.median("cr_conversion")
        if base <= 0:
            return 0.0
        return round(100.0 * self.median("trigger_preservation") / base, 1)

    def to_dict(self) -> Dict[str, Any]:
        keys = [
            "total", "cr_conversion", "trigger_preservation", "write_embed",
            "storyboard_parse", "strategy_transcription",
            "strategy_translation", "strategy_interpretation",
            "interpretation_replay", "trigger_merge", "timeline",
        ]
        return {
            "name": self.name,
            "ok": self.ok,
            "error": self.error,
            "stats": self.stats,
            "n_runs": len(self.runs),
            "median_s": {k: self.median(k) for k in keys},
            "spread_s": {k: self.spread(k) for k in keys},
            "overhead_pct": self.overhead_pct,
            "runs_s": self.runs,
        }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def warmup() -> float:
    """Import every heavy dependency once.  Returns the seconds spent."""
    return ensure_imports()




def run_benchmark(
    scenarios: Optional[List[str]] = None,
    repeat: int = 1,
    dt: float = 0.1,
    output_dir: Optional[Path] = None,
    verbose: bool = True,
    isolate: bool = True,
) -> Dict[str, Any]:
    """
    Convert each scenario ``repeat`` times and collect timings.

    ``isolate`` runs every conversion in its own interpreter so a native esmini
    crash costs one scenario instead of the whole report.  Timings are measured
    inside the child around the same code, with imports warmed up first, so
    they are comparable to the in-process numbers.
    """
    names = scenarios or DEFAULT_SUITE
    out_root = output_dir or paths.OUTPUT_DIR

    if verbose:
        print("warming up imports …", flush=True)
    warm_s = warmup()
    if verbose:
        print(f"  {warm_s:.2f}s\n")
        print(f"Benchmarking {len(names)} scenario(s), {repeat} run(s) each"
              f"{' — isolated subprocesses' if isolate else ''}\n", flush=True)

    converter = TriggerPreservingConverter(dt=dt)
    entries: List[BenchmarkEntry] = []

    for name in names:
        entry = BenchmarkEntry(name=name, ok=False)

        try:
            paths.resolve_xosc(name)
        except FileNotFoundError as exc:
            entry.error = str(exc)
            entries.append(entry)
            if verbose:
                print(f"  ✗ {name}: not found", flush=True)
            continue

        last_stats: Optional[Dict[str, Any]] = None
        for _run_idx in range(repeat):
            if isolate:
                payload = convert_isolated(name, out_root, dt)
                ok = bool(payload.get("ok"))
                error = payload.get("error")
                timings = payload.get("timings_s") or {}
                stats = payload.get("stats") or {}
            else:
                result: ConversionResult = converter.convert(name, out_root / name)
                ok, error = result.ok, result.error
                timings = result.timings.to_dict()
                stats = result.stats

            if not ok:
                entry.error = error
                break
            entry.runs.append(timings)
            last_stats = stats

        if last_stats is not None:
            entry.ok = True
            entry.stats = last_stats

        entries.append(entry)

        if verbose:
            if entry.ok:
                s = entry.stats
                cov = s.get("coverage", {})
                uncond = s.get("interpretation_fired_unconditional", 0)
                print(
                    f"  ✓ {name:28s} {entry.median('total'):6.2f}s  "
                    f"(convert {entry.median('cr_conversion'):5.2f}s + "
                    f"triggers {entry.median('trigger_preservation'):5.3f}s)  "
                    f"{s.get('events', 0)} events / {s.get('conditions', 0)} conds, "
                    f"{s.get('interpretation_fired', 0)} fires"
                    + (f" ({uncond} unconditional)" if uncond else "")
                    + (f", coverage {cov.get('preserved_pct')}%" if cov else ""),
                    flush=True,
                )
            else:
                print(f"  ✗ {name:28s} {entry.error}", flush=True)

    ok_entries = [e for e in entries if e.ok]
    report = {
        "schema": "osc2cr-benchmark/1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
            "dt": dt,
            "repeat": repeat,
            "warmup_s": round(warm_s, 3),
            "isolated_subprocesses": isolate,
        },
        "totals": {
            "scenarios": len(entries),
            "succeeded": len(ok_entries),
            "failed": len(entries) - len(ok_entries),
            "wall_clock_s": round(sum(e.median("total") for e in ok_entries), 2),
            "cr_conversion_s": round(sum(e.median("cr_conversion") for e in ok_entries), 2),
            "trigger_preservation_s": round(
                sum(e.median("trigger_preservation") for e in ok_entries), 3),
            "events": sum(e.stats.get("events", 0) for e in ok_entries),
            "conditions": sum(e.stats.get("conditions", 0) for e in ok_entries),
            "interpretation_fired": sum(e.stats.get("interpretation_fired", 0) for e in ok_entries),
            "interpretation_fired_unconditional": sum(
                e.stats.get("interpretation_fired_unconditional", 0) for e in ok_entries),
            "source_conditions": sum(
                e.stats.get("coverage", {}).get("source_conditions", 0)
                for e in ok_entries),
            "parsed_conditions": sum(
                e.stats.get("coverage", {}).get("parsed_conditions", 0)
                for e in ok_entries),
        },
        "scenarios": [e.to_dict() for e in entries],
    }
    return report


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _md_table(headers: List[str], rows: List[List[str]], align: str = "") -> str:
    sep = []
    for i in range(len(headers)):
        sep.append("---:" if align[i:i + 1] == "r" else "---")
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def render_markdown(report: Dict[str, Any]) -> str:
    env = report["environment"]
    tot = report["totals"]
    ok = [s for s in report["scenarios"] if s["ok"]]
    failed = [s for s in report["scenarios"] if not s["ok"]]

    out: List[str] = []
    out.append("# OpenSCENARIO → CommonRoad conversion benchmark\n")
    out.append(
        f"Generated {report['generated_at']} · Python {env['python']} · "
        f"{env['platform']}\n"
    )
    out.append(
        f"`dt = {env['dt']} s` · {env['repeat']} run(s) per scenario "
        f"(median reported) · imports warmed up in {env['warmup_s']} s"
        + (" · each conversion in its own interpreter"
           if env.get("isolated_subprocesses") else "") + "\n"
    )

    out.append("\n## Summary\n")
    real_fires = tot["interpretation_fired"] - tot["interpretation_fired_unconditional"]
    out.append(
        f"- **{tot['succeeded']}/{tot['scenarios']}** scenarios converted "
        f"({tot['failed']} failed)\n"
        f"- **{tot['wall_clock_s']} s** total, of which "
        f"**{tot['cr_conversion_s']} s** is the existing converter "
        f"(esmini simulation + CommonRoad construction)\n"
        f"- **{tot['trigger_preservation_s']} s** total spent preserving triggers — "
        f"the contribution of this tool\n"
        f"- **{tot['events']} events / {tot['conditions']} conditions** recovered "
        f"({tot['parsed_conditions']}/{tot['source_conditions']} of the "
        f"`<Condition>` elements in the source files)\n"
        f"- **{real_fires}** event fires "
        f"reconstructed from real predicates"
        + (f", plus {tot['interpretation_fired_unconditional']} unconditional "
           f"fires from events whose conditions could not be parsed"
           if tot["interpretation_fired_unconditional"] else "") + "\n"
    )

    out.append("\n## Per-scenario timing\n")
    rows = []
    for s in ok:
        m, sp = s["median_s"], s["spread_s"]
        spread = f" ±{sp['total']:.2f}" if sp["total"] else ""
        rows.append([
            f"`{s['name']}`",
            f"{m['total']:.2f}{spread}",
            f"{m['cr_conversion']:.2f}",
            f"{m['trigger_preservation']:.3f}",
            f"{m['write_embed']:.3f}",
            f"{s['overhead_pct']:.1f}%",
        ])
    out.append(_md_table(
        ["Scenario", "Total [s]", "Converter [s]", "Triggers [s]",
         "Write [s]", "Trigger overhead"],
        rows, align="_rrrrr",
    ))

    out.append("\n\n## What was recovered\n")
    rows = []
    for s in ok:
        st = s["stats"]
        fired = st.get("interpretation_fired", 0)
        uncond = st.get("interpretation_fired_unconditional", 0)
        fires = f"{fired}" + (f" ({uncond} uncond.)" if uncond else "")
        rows.append([
            f"`{s['name']}`",
            st.get("obstacles", 0),
            st.get("lanelets", 0),
            st.get("time_steps", 0),
            f"{st.get('duration_s', 0):.1f}",
            st.get("events", 0),
            st.get("conditions", 0),
            st.get("translation_mapped", 0),
            st.get("translation_skipped", 0),
            fires,
        ])
    out.append(_md_table(
        ["Scenario", "Actors", "Lanelets", "Steps", "Duration [s]",
         "Events", "Conditions", "Translation mapped", "Translation skipped",
         "Interpretation fires"],
        rows, align="_rrrrrrrrr",
    ))

    out.append("\n\n## Trigger coverage\n")
    out.append(
        "How much of each source file's trigger logic the condition model "
        "actually represents. `Preserved` is parsed conditions over "
        "`<Condition>` elements in the `.xosc`.\n\n"
        "This column matters for reading the one above: a dropped condition "
        "leaves its event with an empty start trigger, and an empty trigger is "
        "unconditionally true in OpenSCENARIO — so the event fires on the first "
        "step and looks like a reconstructed trigger. Those fires are counted "
        "separately as *uncond.* and are not evidence of recovered "
        "reactivity.\n"
    )
    rows = []
    for s in ok:
        cov = s["stats"].get("coverage", {})
        unsupported = cov.get("unsupported") or {}
        rows.append([
            f"`{s['name']}`",
            f"{cov.get('parsed_conditions', 0)}/{cov.get('source_conditions', 0)}",
            f"{cov.get('preserved_pct', 0)}%",
            ", ".join(f"`{t}`×{n}" for t, n in sorted(unsupported.items())) or "—",
        ])
    out.append(_md_table(
        ["Scenario", "Preserved", "%", "Unsupported condition types"],
        rows, align="_rr_",
    ))

    all_unsupported: Dict[str, int] = {}
    for s in ok:
        for t, n in (s["stats"].get("coverage", {}).get("unsupported") or {}).items():
            all_unsupported[t] = all_unsupported.get(t, 0) + n
    if all_unsupported:
        out.append("\n\nCondition types encountered but not modelled, across the suite:\n")
        rows = [[f"`{t}`", n] for t, n in sorted(
            all_unsupported.items(), key=lambda kv: -kv[1])]
        out.append(_md_table(["Condition type", "Occurrences"], rows, align="_r"))

    out.append("\n\n## Trigger-preservation breakdown\n")
    out.append("Sub-stages of the trigger-preservation cost, in milliseconds.\n\n")
    rows = []
    for s in ok:
        m = s["median_s"]
        rows.append([
            f"`{s['name']}`",
            f"{m['storyboard_parse'] * 1000:.1f}",
            f"{m['strategy_transcription'] * 1000:.1f}",
            f"{m['strategy_translation'] * 1000:.1f}",
            f"{m['strategy_interpretation'] * 1000:.1f}",
            f"{m['interpretation_replay'] * 1000:.1f}",
            f"{m['timeline'] * 1000:.1f}",
            f"{m['trigger_merge'] * 1000:.1f}",
        ])
    out.append(_md_table(
        ["Scenario", "Parse", "Transcr.", "Transl.", "Interp. build",
         "Interp. replay", "Timeline", "Merge"],
        rows, align="_rrrrrrr",
    ))

    if failed:
        out.append("\n\n## Failures\n")
        rows = [[f"`{s['name']}`", s["error"] or "unknown"] for s in failed]
        out.append(_md_table(["Scenario", "Reason"], rows))

    out.append("\n\n## Reading the numbers\n")
    out.append(
        "`Converter [s]` is the cost that already exists today — esmini replays "
        "the storyboard and the CommonRoad scenario is built from the resulting "
        "states.  `Triggers [s]` is everything this tool adds to keep the "
        "conditional structure: parsing the storyboard, running strategies "
        "Transcription/Translation/Interpretation, replaying the conditions "
        "against the converted trajectories, and "
        "building the per-step condition timeline the viewer draws.\n\n"
        "The overhead column is `Triggers / Converter`.  It stays small because "
        "the expensive part of conversion is the simulation, while trigger "
        "preservation is XML parsing and arithmetic over an already-computed "
        "trajectory.\n"
    )

    return "\n".join(out)


def write_report(
    report: Dict[str, Any], out_dir: Optional[Path] = None,
) -> Dict[str, Path]:
    """
    Write ``benchmark.json`` and ``benchmark.md``; returns the paths.

    Defaults to a directory under :data:`paths.OUTPUT_DIR`, not to
    ``paths.BENCHMARK_DIR``: the latter holds the reference results shipped with
    the package, which a local run must not overwrite — and which live inside an
    install that may be read-only.
    """
    target = out_dir or (paths.OUTPUT_DIR / "benchmarks")
    target.mkdir(parents=True, exist_ok=True)

    json_path = target / "benchmark.json"
    json_path.write_text(json.dumps(report, indent=2))

    md_path = target / "benchmark.md"
    md_path.write_text(render_markdown(report))

    return {"json": json_path, "markdown": md_path}


def print_console_table(report: Dict[str, Any]) -> None:
    ok = [s for s in report["scenarios"] if s["ok"]]
    if not ok:
        print("\nNo scenario converted successfully.")
        return

    width = 104
    print("\n" + "=" * width)
    print(f"{'scenario':24s} {'total':>8s} {'converter':>10s} {'triggers':>9s} "
          f"{'overhead':>9s} {'events':>7s} {'conds':>6s} {'fires':>7s} {'coverage':>9s}")
    print("-" * width)
    for s in ok:
        m, st = s["median_s"], s["stats"]
        cov = st.get("coverage", {})
        uncond = st.get("interpretation_fired_unconditional", 0)
        fires = f"{st.get('interpretation_fired', 0)}" + (f"({uncond}u)" if uncond else "")
        print(
            f"{s['name'][:24]:24s} {m['total']:7.2f}s {m['cr_conversion']:9.2f}s "
            f"{m['trigger_preservation']:8.3f}s {s['overhead_pct']:8.1f}% "
            f"{st.get('events', 0):7d} {st.get('conditions', 0):6d} "
            f"{fires:>7s} {cov.get('preserved_pct', 0):8.0f}%"
        )
    print("-" * width)
    tot = report["totals"]
    total_cov = (100.0 * tot["parsed_conditions"] / tot["source_conditions"]
                 if tot["source_conditions"] else 0.0)
    uncond = tot["interpretation_fired_unconditional"]
    fires = f"{tot['interpretation_fired']}" + (f"({uncond}u)" if uncond else "")
    print(
        f"{'TOTAL':24s} {tot['wall_clock_s']:7.2f}s {tot['cr_conversion_s']:9.2f}s "
        f"{tot['trigger_preservation_s']:8.3f}s "
        f"{100 * tot['trigger_preservation_s'] / max(tot['cr_conversion_s'], 1e-9):8.1f}% "
        f"{tot['events']:7d} {tot['conditions']:6d} {fires:>7s} {total_cov:8.0f}%"
    )
    print("=" * width)
    if uncond:
        print(f"  u = unconditional fires (event's conditions were not parsed; "
              f"an empty trigger is always true)")
