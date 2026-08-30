"""
cli.py
======
Command-line entry point.

    python -m osc2cr_extended list                          what can be converted
    python -m osc2cr_extended convert cut-in_simple ...     convert scenarios
    python -m osc2cr_extended benchmark [names] [--repeat N]
    python -m osc2cr_extended serve [--port 8000]
    python -m osc2cr_extended inspect output/cut-in_simple/scenario.xml
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import paths


def _quiet_logging(verbose: bool) -> None:
    """The converter and crdesigner are chatty; keep the console readable."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.ERROR)
    if not verbose:
        for noisy in ("osc_cr_converter", "crdesigner", "commonroad", "matplotlib"):
            logging.getLogger(noisy).setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    corpus = paths.available_xosc()
    if not corpus:
        print("No .xosc files found. Checked:")
        print(f"  {paths.BUNDLED_XOSC}")
        print(f"  {paths.ESMINI_XOSC}")
        print(f"  {paths.LOCAL_XOSC}")
        return 1

    clashes = paths.colliding_stems()
    print(f"{len(corpus)} OpenSCENARIO file(s) available:\n")
    for name, path in corpus.items():
        if paths.BUNDLED_XOSC in path.parents:
            origin = "converter"
        elif paths.LOCAL_XOSC in path.parents:
            origin = "bundled"
        else:
            origin = "esmini"
        flag = "  ⚠ also in esmini, different content" if name in clashes else ""
        print(f"  {name:36s} [{origin}]{flag}")

    if clashes:
        print(f"\n⚠ {len(clashes)} name(s) exist in both corpora with different "
              f"content. A bare name resolves to the converter's copy; these are "
              f"not the same scenarios — pass a full path to pick deliberately:")
        for stem, (bundled, esmini) in clashes.items():
            print(f"    {stem}")
            print(f"      converter: {bundled}")
            print(f"      esmini   : {esmini}")

    from .server import list_bundles
    bundles = list_bundles()
    if bundles:
        print(f"\n{len(bundles)} converted bundle(s) in {paths.OUTPUT_DIR}:\n")
        for b in bundles:
            st = b.get("stats", {})
            print(f"  {b['name']:36s} {st.get('events', 0)} events, "
                  f"{st.get('conditions', 0)} conditions, "
                  f"{st.get('interpretation_fired', 0)} fires")
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    clashes = paths.colliding_stems()
    for name in args.scenarios:
        stem = Path(name).stem
        if stem in clashes and not Path(name).exists():
            bundled, esmini = clashes[stem]
            chosen, other = ((esmini, bundled) if getattr(args, "prefer", "bundled") == "esmini"
                             else (bundled, esmini))
            print(f"⚠ '{stem}' exists in both corpora with different content.")
            print(f"    converting: {chosen}")
            print(f"    ignoring  : {other}")
            print(f"    they are not the same scenario — use --prefer or a full path")

    from .pipeline import (
        ConversionResult, StageTimings, TriggerPreservingConverter,
        convert_isolated,
    )

    # esmini keeps process-wide state that is not reset between runs, so
    # converting several scenarios in one interpreter corrupts the later ones:
    # `drive_when_close` followed by `acc-test` truncates acc-test from 600 to
    # 333 time steps. Batches therefore get one interpreter per scenario, the
    # same way the benchmark and the server already do. A single conversion is
    # unaffected and stays in-process, which is faster.
    isolate = len(args.scenarios) > 1 and not args.no_isolate

    converter = TriggerPreservingConverter(
        dt=args.dt,
        keep_plain_copy=not args.no_plain_copy,
        compute_timeline=not args.no_timeline,
        fix_xodr=getattr(args, "fix_xodr", False),
    )

    failures = 0
    results = []
    for name in args.scenarios:
        out_dir = Path(args.output) / Path(name).stem if args.output else None

        if isolate:
            payload = convert_isolated(
                name,
                Path(args.output) if args.output else None,
                dt=args.dt,
                fix_xodr=getattr(args, "fix_xodr", False),
            )
            result = ConversionResult(
                name=payload.get("name", name),
                xosc_path=payload.get("xosc_path", name),
                ok=bool(payload.get("ok")),
                timings=StageTimings(**{
                    k: v for k, v in (payload.get("timings_s") or {}).items()
                    if k in StageTimings.__dataclass_fields__
                }),
                stats=payload.get("stats") or {},
                files=payload.get("files") or {},
                error=payload.get("error"),
                bundle_dir=payload.get("bundle_dir"),
            )
        else:
            result = converter.convert(
                name, out_dir, prefer=getattr(args, 'prefer', 'bundled'),
            )

        results.append(result)
        print(result.summary())
        if getattr(result, "discarded_cosim", None):
            print(f"    ⚠ discarded closed-loop artifacts from the previous "
                  f"conversion: {', '.join(result.discarded_cosim)}")
            print(f"      they described the old geometry — re-run: "
                  f"python -m osc2cr_extended cosim {result.name} --driver planner")
        if result.ok:
            print(f"    → {result.bundle_dir}")
        else:
            failures += 1

    # Machine-readable result for the benchmark's subprocess isolation.  It
    # goes to a file rather than stdout because the converter writes progress
    # lines there that would corrupt the document.
    if args.json_out:
        payload = (results[0].to_dict() if len(results) == 1
                   else [r.to_dict() for r in results])
        Path(args.json_out).write_text(json.dumps(payload, indent=2))

    return 1 if failures else 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    from .benchmark import (
        print_console_table, run_benchmark, write_report,
    )

    report = run_benchmark(
        scenarios=args.scenarios or None,
        repeat=args.repeat,
        dt=args.dt,
        isolate=not args.no_isolate,
    )
    print_console_table(report)

    written = write_report(
        report, Path(args.output) if args.output else None,
    )
    print(f"\nreport → {written['markdown']}")
    print(f"raw    → {written['json']}")
    return 0 if report["totals"]["succeeded"] else 1


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    serve(host=args.host, port=args.port)
    return 0


def cmd_cosim(args: argparse.Namespace) -> int:
    """Run a bundle closed-loop and report the two players' condition streams."""
    from .cosim import cosim_isolated, run_cosim

    # esmini leaks state between scenarios in one interpreter, so a batch runs
    # each bundle in its own child unless told otherwise.
    isolate = len(args.bundles) > 1 and not args.no_isolate

    failed = 0
    for bundle in args.bundles:
        bundle_dir = Path(bundle)
        if not bundle_dir.is_dir():
            bundle_dir = paths.OUTPUT_DIR / bundle
        if not (bundle_dir / "bundle.json").exists():
            print(f"{bundle}: not a bundle (no bundle.json)")
            failed += 1
            continue

        if isolate:
            extra = []
            if args.max_steps:
                extra += ["--max-steps", str(args.max_steps)]
            if args.ego:
                extra += ["--ego", args.ego]
            if args.config:
                extra += ["--config", args.config]
            result = cosim_isolated(bundle_dir, driver=args.driver, extra_args=extra)
            if not result.get("ok"):
                print(f"\n{bundle_dir.name}  [{args.driver}]")
                print(f"  failed    : {result.get('error')}")
                failed += 1
                continue
        else:
            result = run_cosim(
                bundle_dir, driver=args.driver, max_steps=args.max_steps,
                config_path=args.config, desired_velocity=args.desired_velocity,
                viewer=args.viewer, ego=args.ego, write=not args.no_write,
            )

        planner = result["planner"]
        print(f"\n{result['scenario']}  [{args.driver}]")
        if planner.get("status") in ("failed", "infeasible"):
            print(f"  planner   : {planner['status']} — {planner.get('reason')}")
            failed += 1
        elif planner.get("status") == "goal-already-satisfied":
            print("  ⚠ planner : the goal region already contains the initial "
                  "state — the planner never moved, so this run tests nothing")
            failed += 1
        elif args.driver == "planner":
            print(f"  planner   : {planner.get('status')} "
                  f"after {planner.get('steps', result.get('steps'))} steps")

        ext = result.get("externalization")
        if ext:
            if ext["controller_replaced"]:
                print(f"  ego       : {ext['ego']} — replaced "
                      f"{ext['controller_replaced']} with the external controller")
            for mg in ext["voided_maneuver_groups"]:
                print(f"  ⚠ voided  : ManeuverGroup '{mg}' commands the ego; "
                      f"its actions do not apply while the planner drives")

        for ev in result["events"]:
            print(f"  fired     : {ev['event']} at {ev['time_s']}s")
        for col in result["collisions"]:
            print(f"  ⚠ collision: {', '.join(col['with'])} at {col['time_s']}s")

        summary = result["differential"]["summary"]
        pct = summary.get("agreement_pct")
        print(f"  agreement : {'n/a' if pct is None else f'{pct}%'} "
              f"({summary.get('agree', 0)}/{summary.get('conclusive', 0)} "
              f"conclusive of {summary.get('compared', 0)} conditions)")
        for row in result["differential"]["conditions"]:
            if row["verdict"] == "agree":
                continue
            note = " [declares delay, unmodelled]" if row["declares_delay"] else ""
            print(f"    · {row['verdict']}: {row['name']} "
                  f"esmini={row['esmini_fires']} ours={row['shadow_fires']}{note}")

        if result.get("written_to"):
            print(f"  trace     → {result['written_to']}")

    return 1 if failed else 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Show the triggers carried inside a CommonRoad file."""
    from .embed import extract_triggers, has_triggers

    path = Path(args.cr_xml)
    if not path.is_file():
        print(f"not a file: {path}")
        return 1

    if not has_triggers(path):
        print(f"{path.name}: no embedded triggers")
        return 1

    triggers = extract_triggers(path) or {}
    counts = triggers.get("counts", {})
    print(f"{path.name}")
    print(f"  schema      : {triggers.get('schema')}")
    print(f"  source      : {triggers.get('source_xosc')}")
    print(f"  dt          : {triggers.get('dt')}")
    print(f"  counts      : {counts}")

    cov = triggers.get("coverage")
    if isinstance(cov, dict):
        from .coverage import summary as coverage_summary
        print(f"  coverage    : {coverage_summary(cov)}")
        uncond = counts.get("interpretation_fired_unconditional", 0)
        if uncond:
            print(f"  ⚠ {uncond} of {counts.get('interpretation_fired', 0)} fires are "
                  f"unconditional (event had no parsed start condition)")
    print()

    for ev in triggers.get("events", []):
        fires = (ev.get("interpretation") or {}).get("fires") or []
        when = ", ".join(f"{f['time_s']}s" for f in fires) or "never"
        print(f"  event {ev['name']}  [{ev.get('story')}/{ev.get('act')}]  fires: {when}")
        for cond in ev.get("conditions", []):
            translation_out = cond.get("translation") or {}
            status = translation_out.get("status", "—")
            print(f"      · {cond['text']}")
            print(f"        {cond['type']} · edge={cond.get('edge')} · translation={status}")

    sb = triggers.get("storyboard_triggers", [])
    if sb:
        print("\n  storyboard/act triggers:")
        for t in sb:
            print(f"      · {t['name']}  translation={(t.get('translation') or {}).get('status')}")

    if args.json:
        print("\n" + json.dumps(triggers, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="osc2cr-ext",
        description="OpenSCENARIO → CommonRoad conversion that keeps the triggers.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list available .xosc files and bundles")
    p_list.set_defaults(func=cmd_list)

    p_conv = sub.add_parser("convert", help="convert scenarios into bundles")
    p_conv.add_argument("scenarios", nargs="+", help="scenario names or .xosc paths")
    p_conv.add_argument("--dt", type=float, default=0.1, help="CommonRoad time step [s]")
    p_conv.add_argument("--fix-xodr", action="store_true",
                        help="repair OpenDRIVE constructs crdesigner's parser "
                             "rejects (currently: 1.7 direct junctions, which "
                             "use linkedRoad instead of connectingRoad). Off by "
                             "default — a repair approximates the source "
                             "topology and is recorded in bundle.json")
    p_conv.add_argument("--prefer", choices=("bundled", "esmini"), default="bundled",
                        help="which corpus wins when a bare name exists in both "
                             "with different content (default: bundled, which is "
                             "what every existing bundle was built with)")
    p_conv.add_argument("--output", help="output root (default: ./osc2cr_output, or $OSC2CR_OUTPUT_DIR)")
    p_conv.add_argument("--no-plain-copy", action="store_true",
                        help="skip the trigger-free scenario_plain.xml copy")
    p_conv.add_argument("--no-timeline", action="store_true",
                        help="skip the per-step condition timeline")
    p_conv.add_argument("--json-out", help="write the conversion result as JSON")
    p_conv.add_argument("--no-isolate", action="store_true",
                        help="convert a batch in one interpreter; faster, but "
                             "esmini state leaks between scenarios and can "
                             "truncate later ones")
    p_conv.set_defaults(func=cmd_convert)

    p_bench = sub.add_parser("benchmark", help="timed batch conversion + report")
    p_bench.add_argument("scenarios", nargs="*", help="default: the built-in suite")
    p_bench.add_argument("--repeat", type=int, default=1, help="runs per scenario")
    p_bench.add_argument("--dt", type=float, default=0.1)
    p_bench.add_argument("--output", help="report directory (default: benchmarks/)")
    p_bench.add_argument("--no-isolate", action="store_true",
                        help="convert in-process; a native esmini crash then "
                             "aborts the whole benchmark")
    p_bench.set_defaults(func=cmd_benchmark)

    p_serve = sub.add_parser("serve", help="run the interactive viewer")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

    p_cosim = sub.add_parser(
        "cosim",
        help="run a bundle closed-loop with the conditions observed from both "
             "esmini and our own executor",
    )
    p_cosim.add_argument("bundles", nargs="+", help="bundle directories or names")
    p_cosim.add_argument(
        "--driver", choices=("esmini", "planner"), default="esmini",
        help="esmini: the scenario as authored, used to validate our condition "
             "implementation against the reference player. planner: externalise "
             "the ego and let commonroad-rp drive it (default: esmini)",
    )
    p_cosim.add_argument("--max-steps", type=int,
                         help="step cap (default: the bundle's length + 20)")
    p_cosim.add_argument("--config", help="ReactivePlannerConfiguration yaml")
    p_cosim.add_argument("--desired-velocity", type=float,
                         help="planner target speed [m/s]")
    p_cosim.add_argument("--ego", help="override the detected ego entity name")
    p_cosim.add_argument("--viewer", action="store_true",
                         help="open esmini's 3D window")
    p_cosim.add_argument("--no-write", action="store_true",
                         help="do not write cosim_trace.json into the bundle")
    p_cosim.add_argument("--no-isolate", action="store_true",
                         help="run a batch in one interpreter; faster, but "
                              "esmini state leaks between scenarios and a "
                              "native crash takes the whole run down")
    p_cosim.set_defaults(func=cmd_cosim)

    p_insp = sub.add_parser("inspect", help="print triggers embedded in a CR file")
    p_insp.add_argument("cr_xml", help="path to a converted scenario.xml")
    p_insp.add_argument("--json", action="store_true", help="also dump the raw JSON")
    p_insp.set_defaults(func=cmd_inspect)

    return parser


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    _quiet_logging(args.verbose)
    paths.bootstrap()
    # A degraded environment silently produces map-less scenarios; say it up
    # front rather than letting the user discover it in the viewer.
    paths.warn_if_degraded()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
