"""
triggers_export.py
==================
Builds one compact ``triggers.json`` per scenario by merging the per-strategy
sidecar files that the pipeline already writes:

  conditions_transcription.json  — every event with its trigger conditions   (Transcription)
  conditions_translation.json  — mapped time windows / skip reasons         (Translation)
  trace_interpretation.json       — events that fired during trajectory replay (Interpretation)

The merged file is what the crdesigner web overlay (web_overlay/
crdesigner_triggers.user.js) consumes: drop it onto the trigger panel in the
browser and scrub the timeline.

Schema ("storyboard-triggers/2")
--------------------------------
  scenario, source_xosc, generated_at, dt, counts
  events[]                — Transcription events; each condition carries its
                            Translation outcome under "translation", each event
                            its Interpretation replay result under "interpretation"
  storyboard_triggers[]   — Translation entries not attached to any Transcription
                            event condition (act start triggers, stop triggers, ...)

Usage
-----
  From the pipeline: EnrichedScenario.save() calls export_triggers_json().

  Standalone (regenerate from existing output folders, no esmini needed):
      python shared/triggers_export.py                 # all dirs under output/
      python shared/triggers_export.py output/acc-test [--dt 0.1]
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = "storyboard-triggers/2"

# time_step upper bounds at or above this are treated as "unbounded"
_INF_STEP = 999_999


# ---------------------------------------------------------------------------
# Human-readable condition text
# ---------------------------------------------------------------------------

def _condition_text(cond: Dict[str, Any]) -> str:
    """One-line description of a condition dict from conditions_transcription.json."""
    # A serialiser that already knows how to describe itself wins — this lets
    # condition types outside the taxonomy below supply their own wording
    # instead of falling through to the generic field dump.
    preset = cond.get("text")
    if preset:
        return str(preset)

    ctype = cond.get("type", "")
    trig = cond.get("triggering_entity")
    ref = cond.get("reference_entity")
    rule = cond.get("rule", "")

    if ctype == "TimeHeadwayCondition":
        fs = " (freespace)" if cond.get("freespace") else ""
        return f"{trig} vs {ref}: headway {rule} {cond.get('value')} s{fs}"
    if ctype == "TimeToCollisionCondition":
        fs = " (freespace)" if cond.get("freespace") else ""
        return f"{trig} vs {ref}: TTC {rule} {cond.get('value')} s{fs}"
    if ctype == "RelativeDistanceCondition":
        dt_ = f" ({cond['distance_type']})" if cond.get("distance_type") else ""
        fs = " (freespace)" if cond.get("freespace") else ""
        # transcription.py serialises this threshold as "value", not "value_m";
        # reading only "value_m" rendered every relative-distance condition as
        # "distance lessThan None m" even though the value parsed correctly.
        value = cond.get("value_m", cond.get("value"))
        return f"{trig} vs {ref}: distance {rule} {value} m{dt_}{fs}"
    if ctype == "RelativeSpeedCondition":
        return f"{trig} vs {ref}: rel. speed {rule} {cond.get('value_ms')} m/s"
    if ctype == "SimulationTimeCondition":
        return f"simulation time {rule} {cond.get('value_s')} s"
    if ctype == "EntityTraveledDistanceCondition":
        return f"{cond.get('entity_ref')} traveled distance {rule} {cond.get('value_m')} m"
    if ctype == "StoryboardElementStateCondition":
        el_type = cond.get("element_type")
        el = cond.get("element_ref")
        return f"{el_type} '{el}' reaches state {cond.get('state')}"

    # Generic fallback: dump the informative fields
    skip = {"type", "name", "delay_s", "edge"}
    parts = [f"{k}={v}" for k, v in cond.items() if k not in skip and v is not None]
    return ", ".join(parts) if parts else ctype


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def _time_step_from_seconds(time_s: float, dt: float) -> int:
    return int(round(time_s / dt)) if dt > 0 else 0


def _normalise_translation_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Pass through a conditions_translation.json entry, flagging unbounded windows."""
    out = dict(entry)
    interval = out.get("time_step_interval")
    if isinstance(interval, (list, tuple)) and len(interval) == 2:
        start, end = interval
        out["time_step_interval"] = [start, None if end >= _INF_STEP else end]
    return out


def build_triggers(
    transcription_events: Optional[Dict[str, Any]],
    translation_conditions: Optional[Dict[str, Any]],
    interpretation_trace: Optional[List[Dict[str, Any]]],
    dt: float = 0.1,
    scenario_name: str = "",
    source_xosc: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge the three strategy outputs into one triggers dict."""
    transcription_events = transcription_events or {}
    translation_conditions = translation_conditions or {}
    interpretation_trace = interpretation_trace or []

    # Group Interpretation trace entries by event name (an event may fire more than once)
    interpretation_by_event: Dict[str, List[Dict[str, Any]]] = {}
    for fired in interpretation_trace:
        interpretation_by_event.setdefault(fired.get("event", ""), []).append(fired)

    matched_translation_names: set = set()
    events: List[Dict[str, Any]] = []

    for event_name, ev in transcription_events.items():
        conditions = []
        for cond in ev.get("conditions", []):
            cname = cond.get("name", "")
            translation_outcome = translation_conditions.get(cname)
            if translation_outcome is not None:
                matched_translation_names.add(cname)
                translation_outcome = _normalise_translation_entry(translation_outcome)
            conditions.append({
                "name": cname,
                "type": cond.get("type", ""),
                "text": _condition_text(cond),
                "edge": cond.get("edge"),
                "delay_s": cond.get("delay_s", 0.0),
                "translation": translation_outcome,
            })

        fires = [
            {
                "time_s": f.get("time_s"),
                "time_step": _time_step_from_seconds(f.get("time_s", 0.0), dt),
                "fire_count": f.get("fire_count", 1),
            }
            for f in interpretation_by_event.get(event_name, [])
        ]
        events.append({
            "name": event_name,
            "story": ev.get("story"),
            "act": ev.get("act"),
            "actors": ev.get("actors", []),
            "conditions": conditions,
            "interpretation": {"fired": bool(fires), "fires": fires},
        })

    # Translation entries that belong to no Transcription event condition:
    # act/storyboard-level triggers
    storyboard_triggers = [
        {"name": name, "translation": _normalise_translation_entry(entry)}
        for name, entry in translation_conditions.items()
        if name not in matched_translation_names
    ]

    n_conditions = sum(len(e["conditions"]) for e in events)
    translation_mapped = sum(
        1 for v in translation_conditions.values()
        if str(v.get("status", "")).startswith("mapped")
    )
    translation_skipped = sum(
        1 for v in translation_conditions.values() if v.get("status") == "skipped"
    )

    return {
        "schema": SCHEMA,
        "scenario": scenario_name,
        "source_xosc": source_xosc,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dt": dt,
        "counts": {
            "events": len(events),
            "conditions": n_conditions,
            "translation_mapped": translation_mapped,
            "translation_skipped": translation_skipped,
            "interpretation_fired": len(interpretation_trace),
        },
        "events": events,
        "storyboard_triggers": storyboard_triggers,
    }


# ---------------------------------------------------------------------------
# File-based export
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def export_triggers_json(
    output_dir: Path | str,
    dt: float = 0.1,
    source_xosc: Optional[str] = None,
) -> Path:
    """
    Read conditions_transcription.json / conditions_translation.json /
    trace_interpretation.json from ``output_dir`` (any of them may be missing)
    and write triggers.json next to them. Returns the path of the written file.
    """
    out = Path(output_dir)
    triggers = build_triggers(
        transcription_events=_load_json(out / "conditions_transcription.json"),
        translation_conditions=_load_json(out / "conditions_translation.json"),
        interpretation_trace=_load_json(out / "trace_interpretation.json"),
        dt=dt,
        scenario_name=out.name,
        source_xosc=source_xosc,
    )
    path = out / "triggers.json"
    path.write_text(json.dumps(triggers, indent=2))
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Regenerate triggers.json from existing pipeline output folders."
    )
    parser.add_argument(
        "dirs", nargs="*",
        help="Output folders (default: every subfolder of output/ that has "
             "conditions_transcription.json)",
    )
    parser.add_argument("--dt", type=float, default=0.1, help="Scenario time step [s]")
    args = parser.parse_args()

    if args.dirs:
        dirs = [Path(d) for d in args.dirs]
    else:
        root = Path(__file__).resolve().parent.parent / "output"
        dirs = sorted(
            p.parent for p in root.glob("*/conditions_transcription.json")
        )

    if not dirs:
        print("No output folders found.")
        return 1

    for d in dirs:
        if not d.is_dir():
            print(f"skip {d} — not a directory")
            continue
        path = export_triggers_json(d, dt=args.dt)
        counts = json.loads(path.read_text())["counts"]
        print(f"wrote {path}  ({counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
