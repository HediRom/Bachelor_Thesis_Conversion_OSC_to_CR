"""
run_pipeline.py
===============
Single-file entry point that wraps merge.run_pipeline() (see merge.py) so the
full Transcription/Translation/Interpretation pipeline can be run on one .xosc
file from the command line, and hands back a compact JSON summary.

Usage
-----
    python -m osc2cr_extended.web.run_pipeline <path/to/scenario.xosc> [output_dir]

Writes the usual per-strategy files (scenario_transcription.xml, conditions_transcription.json, ...)
into output_dir (default: output/<scenario-name>/), plus summary.json
describing what was produced. Progress lines go to stdout; on success the last
stdout line is the path to the summary JSON, prefixed with "SUMMARY_JSON:".
As soon as the scenario is converted (before Transcription/Translation/Interpretation
and before the replay GIF, which takes the longest), a preview.png is rendered
and announced via a "PREVIEW_PNG:" line, so a caller can show the initial layout
while the rest keeps running.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.ERROR)

from osc2cr_extended import paths

OUTPUT_DIR = paths.OUTPUT_DIR


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m osc2cr_extended.web.run_pipeline "
              "<scenario.xosc> [output_dir]", file=sys.stderr)
        return 2

    xosc_path = Path(sys.argv[1]).resolve()
    if not xosc_path.exists():
        print(f"File not found: {xosc_path}", file=sys.stderr)
        return 2

    out_dir = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else OUTPUT_DIR / xosc_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] Running Osc2CrConverter (esmini) on {xosc_path.name} ...", flush=True)
    from osc2cr_extended.strategies.merge import run_pipeline

    try:
        enriched = run_pipeline(str(xosc_path), strategies=("transcription", "translation", "interpretation"))
    except Exception as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1

    preview_png = out_dir / "preview.png"
    try:
        from osc2cr_extended.examples import viz
        viz.render_preview(enriched.scenario, str(preview_png))
        print(f"PREVIEW_PNG:{preview_png}", flush=True)
    except Exception as exc:
        print(f"[preview] rendering failed (non-fatal): {exc}", file=sys.stderr, flush=True)
        preview_png = None

    print(f"[2/4] Parsed {enriched.n_events} events, {enriched.n_conditions} conditions", flush=True)

    print("[3/4] Writing outputs ...", flush=True)
    written = enriched.save(str(out_dir))

    summary_path = out_dir / "summary.txt"
    summary_path.write_text(enriched.summary())
    written["summary_txt"] = str(summary_path)

    mapped = skipped = 0
    if enriched.mapping_report is not None:
        mapped = len(enriched.mapping_report.mapped_time) + len(enriched.mapping_report.mapped_velocity)
        skipped = len(enriched.mapping_report.skipped)

    trace_events = 0
    if "interpretation_trace_json" in written:
        trace_events = len(json.loads(Path(written["interpretation_trace_json"]).read_text()))

    print("[4/4] Rendering replay GIF ...", flush=True)
    replay_gif = out_dir / "replay.gif"
    try:
        from osc2cr_extended.examples import viz
        viz.render_replay(enriched.scenario, str(replay_gif))
    except Exception as exc:
        print(f"[replay] rendering failed (non-fatal): {exc}", file=sys.stderr, flush=True)
        replay_gif = None

    result = {
        "xosc": str(xosc_path),
        "output_dir": str(out_dir),
        "n_obstacles": enriched.n_obstacles,
        "n_events": enriched.n_events,
        "n_conditions": enriched.n_conditions,
        "summary_text": enriched.summary(),
        "preview_png": str(preview_png) if preview_png else None,
        "replay_gif": str(replay_gif) if replay_gif else None,
        "files": written,
        "strategies": {
            "transcription": {
                "scenario_file": written.get("transcription_scenario_xml"),
                "conditions_file": written.get("transcription_conditions_json"),
            },
            "translation": {
                "mapped": mapped,
                "skipped": skipped,
                "report_file": written.get("translation_report_txt"),
                "scenario_file": written.get("translation_scenario_xml"),
            },
            "interpretation": {
                "trace_events": trace_events,
                "trace_file": written.get("interpretation_trace_json"),
                "scenario_file": written.get("interpretation_scenario_xml"),
            },
        },
    }

    summary_json = out_dir / "summary.json"
    summary_json.write_text(json.dumps(result, indent=2))

    print(f"Done. Outputs in {out_dir}", flush=True)
    print(f"SUMMARY_JSON:{summary_json}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
