"""
test_cosim.py
=============
Checks on the closed-loop co-simulation.

Two things are worth testing here, and they are not the same thing:

  * the **externalisation transform** — a mechanical rewrite of the .xosc that
    must add exactly one controller, activate it, keep the ego's initial state,
    and account for anything it invalidates.  Getting this wrong produces a
    scenario esmini still happily runs, with the planner's commands silently
    ignored, which is the failure mode this project exists to avoid;

  * the **differential oracle** itself — the comparison logic must call a real
    disagreement a disagreement, and must not manufacture one out of a run that
    simply ended early.  A scoreboard that always reads 100% measures nothing.

The esmini-driven leg is also run end to end when a bundle is available, since
that is the mode whose whole purpose is to be checked against a reference.

Run:  python tests/test_cosim.py [scenario]
"""
from __future__ import annotations

import json
import logging
import sys
import xml.etree.ElementTree as ET
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


def test_externalization(tmp: Path) -> None:
    from osc2cr_extended.cosim import ego_maneuver_groups, externalize_ego

    src = paths.resolve_xosc(SCENARIO)
    out = tmp / "external.xosc"
    report = externalize_ego(src, "Ego", out)

    print("\nego externalisation")
    check("rewritten scenario written", out.exists())

    root = ET.parse(out).getroot()
    ego = next(o for o in root.iter("ScenarioObject") if o.get("name") == "Ego")

    controllers = ego.findall("ObjectController")
    check("ego has exactly one ObjectController", len(controllers) == 1,
          f"found {len(controllers)}")

    props = {p.get("name"): p.get("value")
             for p in controllers[0].iter("Property")} if controllers else {}
    check("controller is esmini's ExternalController",
          props.get("esminiController") == "ExternalController")
    check("controller overrides rather than adds to esmini's own control",
          props.get("mode") == "override")

    activates = list(root.iter("ActivateControllerAction"))
    check("the controller is activated in Init", len(activates) == 1,
          "without this esmini keeps driving the ego and drops what we report")

    # the ego's initial state is the planning problem's initial state; losing it
    # would move the planner's start silently
    private = next(p for p in root.iter("Private") if p.get("entityRef") == "Ego")
    check("ego keeps its Init TeleportAction",
          any(True for _ in private.iter("TeleportAction")))

    # relative references must survive being written somewhere else
    logic = next(root.iter("LogicFile")).get("filepath")
    check("road network reference is absolute", Path(logic).is_absolute(), logic)
    check("referenced road network exists", Path(logic).exists())

    check("voided maneuver groups are reported, not dropped",
          report.voided_maneuver_groups == ego_maneuver_groups(
              ET.parse(src).getroot(), "Ego"),
          f"{report.voided_maneuver_groups or 'none for this scenario'}")

    # idempotence: re-externalising an already-external scenario must not stack
    again = tmp / "external2.xosc"
    externalize_ego(out, "Ego", again)
    root2 = ET.parse(again).getroot()
    ego2 = next(o for o in root2.iter("ScenarioObject") if o.get("name") == "Ego")
    check("re-externalising does not stack controllers",
          len(ego2.findall("ObjectController")) == 1)
    check("re-externalising does not stack activations",
          len(list(root2.iter("ActivateControllerAction"))) == 1)


def test_differential_logic() -> None:
    from osc2cr_extended.cosim import differential

    print("\ndifferential comparison")
    dt = 0.1

    same = differential(
        [{"name": "A", "time_s": 6.6}], [{"name": "A", "time_s": 6.6}], dt
    )
    check("identical streams agree", same["summary"]["agreement_pct"] == 100.0)

    near = differential(
        [{"name": "A", "time_s": 6.6}], [{"name": "A", "time_s": 6.7}], dt
    )
    check("one tick of slack still agrees",
          near["conditions"][0]["verdict"] == "agree")

    far = differential(
        [{"name": "A", "time_s": 6.6}], [{"name": "A", "time_s": 8.2}], dt
    )
    check("a real time difference is a mismatch",
          far["conditions"][0]["verdict"] == "time_mismatch")

    counts = differential(
        [{"name": "A", "time_s": 1.0}],
        [{"name": "A", "time_s": 1.0}, {"name": "A", "time_s": 2.0}], dt
    )
    check("re-firing that esmini did not do is a mismatch",
          counts["conditions"][0]["verdict"] == "count_mismatch")

    missing = differential([{"name": "A", "time_s": 1.0}], [], dt, modelled=set())
    check("a condition we do not model at all is marked as such",
          missing["conditions"][0]["verdict"] == "not_modelled")

    # a fire on the final tick cannot be corroborated: esmini would have
    # reported it on the step that never ran
    ending = differential(
        [], [{"name": "A", "time_s": 8.9}], dt, end_time=8.9
    )
    check("a fire at the run's end is inconclusive, not a disagreement",
          ending["conditions"][0]["verdict"] == "inconclusive_at_end")
    check("inconclusive rows leave the denominator",
          ending["summary"]["conclusive"] == 0)

    mid = differential([], [{"name": "A", "time_s": 0.1}], dt, end_time=8.9)
    check("a fire in the middle of the run is still a disagreement",
          mid["conditions"][0]["verdict"] == "shadow_only")

    delayed = differential(
        [{"name": "A", "time_s": 10.3}], [{"name": "A", "time_s": 0.1}], dt,
        delays={"A": 2.0},
    )
    check("divergence on a condition declaring a delay is tagged",
          delayed["conditions"][0]["declares_delay"])
    check("tagging a delay does not hide the mismatch",
          delayed["summary"].get("agree", 0) == 0)


def test_esmini_leg(bundle_dir: Path) -> None:
    from osc2cr_extended.cosim import run_cosim

    print("\nesmini-driven run (the validation leg)")
    result = run_cosim(bundle_dir, driver="esmini", write=False)

    check("the run advanced", result["steps"] > 0, f"{result['steps']} steps")
    check("esmini reported conditions", bool(result["esmini_conditions"]),
          f"{len(result['esmini_conditions'])} fires")
    check("our executor reported conditions", bool(result["shadow_conditions"]),
          f"{len(result['shadow_conditions'])} fires")

    summary = result["differential"]["summary"]
    check("both streams cover the same conditions", summary["compared"] > 0,
          f"{summary['compared']} compared")

    # the events list is what the viewer renders, so it must keep trace_interpretation's shape
    trace_d = json.loads((bundle_dir / "trace_interpretation.json").read_text())
    if trace_d and result["events"]:
        expected = set(trace_d[0]) - {"unconditional"}
        check("events match trace_interpretation.json's shape",
              expected <= set(result["events"][0]),
              f"missing {sorted(expected - set(result['events'][0]))}")

    fired = {e["event"] for e in result["events"]}
    check("the same events fire as in the offline replay",
          fired == {e["event"] for e in trace_d},
          f"cosim {sorted(fired)} vs replay {sorted({e['event'] for e in trace_d})}")

    # every condition esmini fired should be one we carry — anything else means
    # the taxonomy dropped something the reference player acts on
    unknown = [
        r["name"] for r in result["differential"]["conditions"]
        if r["verdict"] == "not_modelled"
    ]
    check("no condition esmini acts on is missing from our model",
          not unknown, ", ".join(unknown))


def main() -> int:
    import tempfile

    bundle_dir = paths.OUTPUT_DIR / SCENARIO
    print(f"\ncosim tests — {SCENARIO}")

    with tempfile.TemporaryDirectory() as tmp:
        test_externalization(Path(tmp))

    test_differential_logic()

    if (bundle_dir / "bundle.json").exists():
        test_esmini_leg(bundle_dir)
    else:
        print(f"\n(no bundle at {bundle_dir} — skipping the end-to-end leg; "
              f"run `python -m osc2cr convert {SCENARIO}` first)")

    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
