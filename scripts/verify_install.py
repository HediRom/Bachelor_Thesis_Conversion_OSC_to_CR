#!/usr/bin/env python3
"""
Check that everything osc2cr_extended needs is present, and say what is missing.

Exit code 0 means conversion and replay will work.  Co-simulation and the test
suite are reported separately: they are optional, and their absence is not a
failure.
"""
from __future__ import annotations

import importlib.util
import sys

OK, BAD, MEH = "  ok ", " FAIL", " --  "


def probe(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def version(dist: str) -> str:
    try:
        from importlib.metadata import version as v
        return v(dist)
    except Exception:  # noqa: BLE001
        return "?"


def main() -> int:
    print("Python")
    print(f"[{OK if sys.version_info[:2] >= (3, 9) else BAD}] "
          f"{sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info[:2] != (3, 11):
        print("       note: developed and measured on 3.11; "
              "3.9/3.10 work, 3.8 and older are refused")
    if sys.version_info[:2] < (3, 9):
        print("       Python 3.9+ is required — see README.md")
        return 1

    print("\nCore — conversion, triggers, replay, viewer, HTTP API")
    core = {
        "osc2cr_extended": "osc2cr-extended",
        "osc_cr_converter": "commonroad-openscenario-converter",
        "commonroad": "commonroad-io",
        "crdesigner": "commonroad-scenario-designer",
        "numpy": "numpy",
        "lxml": "lxml",
    }
    missing_core = []
    for module, dist in core.items():
        found = probe(module)
        print(f"[{OK if found else BAD}] {dist:<38} {version(dist) if found else ''}")
        if not found:
            missing_core.append(dist)

    print("\nesmini")
    if not probe("osc2cr_extended"):
        print(f"[{BAD}] cannot check — osc2cr_extended is not importable")
    else:
        from osc2cr_extended import paths
        lib = paths.esmini_lib("libesminiLib.so")
        rm = paths.esmini_lib("libesminiRMLib.so")
        print(f"[{OK if lib else BAD}] libesminiLib.so      {lib or 'not found'}")
        print(f"[{OK if rm else BAD}] libesminiRMLib.so    {rm or 'not found'}")
        if not lib:
            print("       run:  python scripts/fetch_esmini.py")
        corpus = paths.available_xosc()
        print(f"[{OK if corpus else BAD}] scenario corpus      "
              f"{len(corpus)} .xosc file(s)")
        print(f"       converter : {paths.BUNDLED_XOSC}"
              f"{'' if paths.BUNDLED_XOSC.is_dir() else '   (absent)'}")
        print(f"       esmini    : {paths.ESMINI_XOSC}"
              f"{'' if paths.ESMINI_XOSC.is_dir() else '   (absent)'}")
        print(f"       output    : {paths.OUTPUT_DIR}")
        paths.bootstrap()
        paths.warn_if_degraded(lambda m: print(f"       {m}"))

    print("\nOptional — cosim --driver planner")
    for module, dist in {
        "commonroad_rp": "commonroad-reactive-planner",
        "commonroad_route_planner": "commonroad-route-planner",
        "commonroad_clcs": "commonroad-clcs",
        "commonroad_dc": "commonroad-drivability-checker",
    }.items():
        found = probe(module)
        print(f"[{OK if found else MEH}] {dist:<38} "
              f"{version(dist) if found else 'not installed'}")

    print("\nOptional — rendering and tests")
    for module, dist in {
        "matplotlib": "matplotlib",
        "imageio": "imageio",
        "pytest": "pytest",
        "playwright": "playwright",
    }.items():
        found = probe(module)
        print(f"[{OK if found else MEH}] {dist:<38} "
              f"{version(dist) if found else 'not installed'}")

    if missing_core:
        print(f"\nMissing core: {', '.join(missing_core)} — run ./setup.sh")
        return 1
    print("\nCore is complete.  Try:  osc2cr-ext convert cut-in_simple")
    return 0


if __name__ == "__main__":
    sys.exit(main())
