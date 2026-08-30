"""
test_pipeline.py
================
End-to-end checks on a converted bundle.  These cover the two correctness
fixes this tool makes over the existing pipeline, both of which fail silently
(they produce plausible-looking but wrong output) rather than raising:

  * ``$owner``-style parameter references are resolved to real entity names,
    without which every ByEntity condition evaluates to False forever;
  * obstacle IDs are mapped to entities by the converter's own assignment rule
    (ego first, then alphabetical) instead of the order actor names happen to
    appear in the parsed conditions — the positional guess swaps Ego and the
    other actor whenever the ego is not mentioned first, inverting every
    relative-distance and headway condition.

Run:  python tests/test_pipeline.py [scenario]
      (converts the scenario first if no bundle exists)
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logging.basicConfig(level=logging.ERROR)

from osc2cr_extended import paths  # noqa: E402

paths.bootstrap()

SCENARIO = sys.argv[1] if len(sys.argv) > 1 else "cut-in_simple"

results: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  {'✓' if ok else '✗'} {label}{f' — {detail}' if detail else ''}")


def main() -> int:
    from osc2cr_extended.embed import extract_triggers, has_triggers
    from osc2cr_extended.live import LiveSession, map_obstacles_to_entities
    from osc2cr_extended.params import load_parameters, resolve_entity_references
    from osc2cr_extended.pipeline import convert

    bundle_dir = paths.OUTPUT_DIR / SCENARIO
    if not (bundle_dir / "bundle.json").exists():
        print(f"converting {SCENARIO} …")
        result = convert(SCENARIO)
        if not result.ok:
            print(f"conversion failed: {result.error}")
            return 2

    manifest = json.loads((bundle_dir / "bundle.json").read_text())
    timeline = json.loads((bundle_dir / "timeline.json").read_text())
    trace = json.loads((bundle_dir / "trace_interpretation.json").read_text())
    triggers = json.loads((bundle_dir / "triggers.json").read_text())
    stats = manifest["stats"]

    print(f"\npipeline tests — {SCENARIO}\n")

    print("bundle contents")
    for fname in ("scenario.xml", "scenario_plain.xml", "triggers.json",
                  "timeline.json", "conditions_transcription.json", "conditions_translation.json",
                  "trace_interpretation.json", "report_translation.txt", "bundle.json"):
        check(f"{fname} written", (bundle_dir / fname).exists())

    print("\ntriggers inside the CommonRoad file")
    scenario_xml = bundle_dir / "scenario.xml"
    check("scenario.xml carries the trigger block", has_triggers(scenario_xml))
    check("scenario_plain.xml does not",
          not has_triggers(bundle_dir / "scenario_plain.xml"))
    embedded = extract_triggers(scenario_xml)
    check("embedded triggers match the sidecar", embedded == triggers)

    print("\nentity reference resolution")
    from osc2cr_extended.strategies.shared.storyboard_parser import StoryboardParser

    xosc = manifest["xosc_path"]
    params = load_parameters(xosc)
    raw = StoryboardParser(xosc).parse()

    def entity_names(storyboard) -> set:
        found = set()
        for story in storyboard.stories:
            for act in story.acts:
                for mg in act.maneuver_groups:
                    found.update(mg.actor_refs)
                    for man in mg.maneuvers:
                        for ev in man.events:
                            for grp in (ev.start_trigger or []):
                                for c in grp:
                                    for f in ("triggering_entity", "reference_entity",
                                              "entity_ref"):
                                        v = getattr(c, f, None)
                                        if v:
                                            found.add(v)
        return found

    before = entity_names(raw)
    resolve_entity_references(raw, xosc)
    after = entity_names(raw)

    unresolved_before = {n for n in before if n.startswith("$")}
    unresolved_after = {n for n in after if n.startswith("$")}

    if unresolved_before:
        check("parameterised entity refs are resolved", not unresolved_after,
              f"{sorted(unresolved_before)} → {sorted(after - before)}")
    else:
        print("  – this scenario uses no parameterised entity refs; skipping")

    check("resolved names are declared parameters or real entities",
          all(not n.startswith("$") for n in after) if unresolved_before else True)
    check("parameters were found in the .xosc", len(params) > 0,
          f"{len(params)} declaration(s)")

    print("\nobstacle → entity mapping")
    from commonroad.common.file_reader import CommonRoadFileReader

    cr_scenario, _pps = CommonRoadFileReader(str(scenario_xml)).open()
    mapping, confidence, ego = map_obstacles_to_entities(cr_scenario, xosc)

    check("mapping covers every obstacle",
          len(mapping) == len(cr_scenario.dynamic_obstacles),
          f"{len(mapping)} obstacle(s)")
    check("mapping is exact, not positional", confidence == "exact", confidence)
    check("ego identified", bool(ego), str(ego))
    check("ego has the lowest obstacle id",
          mapping[min(mapping)] == ego if ego and mapping else True)
    check("no entity name is a bare parameter",
          all(not n.startswith("$") for n in mapping.values()),
          ", ".join(mapping.values()))

    print("\ncondition timeline")
    n_steps = stats["time_steps"]
    check("timeline covers the scenario",
          len(timeline["time_steps"]) == n_steps,
          f"{len(timeline['time_steps'])} of {n_steps} steps")
    check("every condition has one value per step",
          all(len(c["values"]) == len(timeline["time_steps"])
              for c in timeline["conditions"]))
    check("entity names in the timeline match the mapping",
          set(timeline["entities"]) == set(mapping.values()),
          f"{timeline['entities']}")

    byentity = [c for c in timeline["conditions"]
                if c["type"] in ("TimeHeadwayCondition", "TimeToCollisionCondition",
                                 "RelativeDistanceCondition", "RelativeSpeedCondition")]
    if byentity:
        evaluable = [c for c in byentity if c["true_steps"] > 0]
        check("ByEntity conditions actually evaluate somewhere",
              bool(evaluable),
              f"{len(evaluable)}/{len(byentity)} hold at some step "
              "(all-false would mean entity names never matched)")
    else:
        print("  – no ByEntity conditions in this scenario; skipping")

    print("\nInterpretation replay")
    check("fire times agree with the timeline", _fires_match(trace, timeline),
          f"{len(trace)} fire(s)")
    check("fire count matches the trigger document",
          len(trace) == triggers["counts"]["interpretation_fired"])

    print("\nlane-position resolution")
    _check_roadmanager()

    print("\nlive what-if session")
    session = LiveSession(bundle_dir)
    entities = {
        name: {"x": 0.0 + 50 * i, "y": 0.0, "speed": 20.0, "heading": 0.0,
               "length": 4.5, "width": 1.8}
        for i, name in enumerate(sorted(mapping.values()))
    }
    out = session.evaluate_state(entities=entities, time_s=1.0)
    check("what-if evaluation returns every condition",
          len(out["conditions"]) == len(timeline["conditions"]),
          f"{len(out['conditions'])} condition(s)")
    check("what-if reports no evaluation errors",
          all(c["error"] is None for c in out["conditions"]))

    failed = results.count(False)
    print(f"\n{'✓' if not failed else '✗'} {failed} failure(s) of {len(results)} checks\n")
    return 1 if failed else 0


def _check_roadmanager() -> None:
    """
    The RoadManager binding must actually vary with ``s``.

    esmini changed RM_PositionData's field types between v2.29.3 (float) and
    v3.x (double).  Mirroring the wrong layout does not raise: ctypes reads
    whatever bytes are present and returns the *same* position for every query,
    with an uninitialised heading.  A regression of that kind is invisible to
    every other check in this file, so it is pinned here directly.
    """
    from osc2cr_extended import roadmanager

    if not roadmanager.available():
        print(f"  – RoadManager unavailable ({roadmanager.unavailable_reason()});"
              f" skipping")
        return

    xodr = paths.BUNDLED_XOSC.parent / "xodr" / "straight_500m.xodr"
    if not xodr.is_file():
        print("  – straight_500m.xodr not found; skipping")
        return

    resolver = roadmanager.LanePositionResolver(xodr)
    got = [resolver.resolve(1, -1, s) for s in (0.0, 96.0, 250.0)]
    check("every lane position resolves", all(p is not None for p in got),
          str(got))
    if any(p is None for p in got):
        return

    xs = [p[0] for p in got]
    check("distinct s values give distinct positions",
          len(set(round(x, 3) for x in xs)) == 3,
          f"x = {[round(x, 3) for x in xs]}")
    check("x tracks s on a straight road",
          all(abs(x - s) < 0.5 for x, s in zip(xs, (0.0, 96.0, 250.0))),
          f"x = {[round(x, 3) for x in xs]}")
    check("heading is a real number, not uninitialised memory",
          all(abs(p[2]) < 10.0 for p in got),
          f"h = {[round(p[2], 4) for p in got]}")


def _fires_match(trace: list, timeline: dict) -> bool:
    """
    Every event fire should coincide with activity in its own conditions.

    ``timeline.json`` records condition *values* per step, while the replay
    fires on *edges*.  A rising-edge fire therefore lands on a step whose value
    is 1; a falling-edge fire lands on the step the value dropped to 0.  Accept
    either — the point of the check is that a fire is not attributed to a step
    where the event's conditions are doing nothing at all, which is what a
    wrong obstacle→entity mapping produces.
    """
    if not trace:
        return True
    steps = timeline["time_steps"]
    by_event: dict = {}
    for cond in timeline["conditions"]:
        by_event.setdefault(cond["event"], []).append(cond)

    for fire in trace:
        conds = by_event.get(fire["event"])
        if not conds:
            continue
        try:
            idx = steps.index(fire["time_step"])
        except ValueError:
            return False
        holds = any(c["values"][idx] == 1 for c in conds)
        changed = idx > 0 and any(
            c["values"][idx] != c["values"][idx - 1] for c in conds
        )
        if not (holds or changed):
            return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
