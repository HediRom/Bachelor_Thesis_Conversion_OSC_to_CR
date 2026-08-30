"""Step 1 of the Frenetix parameter sweep (run in `cr-osc-converter` env).

For each parameter combination in PARAM_GRID, patches the xosc and converts it
to a CommonRoad XML file using the same OSC->CR conversion as convert_scenario.py.
Results land in `sweep_scenarios/` alongside this script.

PARAM_GRID must be kept in sync with scenario_sweep.py (step 2).

Usage:
    conda run -n cr-osc-converter python generate_sweep_scenarios.py
    conda run -n cr-osc-converter python generate_sweep_scenarios.py \\
        --xosc ../esmini/resources/xosc/keep_lateral_distance_external.xosc \\
        --output sweep_scenarios/
"""

import argparse
import os
import re
import sys
import tempfile
from itertools import product

from commonroad.common.file_writer import CommonRoadFileWriter
from commonroad.common.writer.file_writer_interface import OverwriteExistingFile

THIS_DIR = os.path.dirname(os.path.realpath(__file__))

from osc2cr_extended import paths

from osc2cr_extended.cosim import scenario_setup

DEFAULT_XOSC = str(paths.ESMINI_XOSC / "keep_lateral_distance_external.xosc")
DEFAULT_OUTPUT = str(paths.OUTPUT_DIR / "sweep_scenarios")

PARAM_GRID = {
    "EgoSpeed":  [72, 90, 108],
    "EgoStartS": [30, 50, 70],
}


def apply_params(xosc_path: str, params: dict) -> str:
    with open(xosc_path, "r", encoding="utf-8") as f:
        text = f.read()
    for name, value in params.items():
        text = re.sub(
            r'(name="' + re.escape(name) + r'"[^/\n]*?)value="[^"]*"',
            r'\g<1>value="' + str(value) + '"',
            text,
        )
    xosc_dir = os.path.dirname(os.path.abspath(xosc_path))
    tmp = tempfile.NamedTemporaryFile(
        suffix=".xosc", delete=False, mode="w", encoding="utf-8", dir=xosc_dir
    )
    tmp.write(text)
    tmp.close()
    return tmp.name


def params_slug(params: dict) -> str:
    return "_".join(f"{k}{v}" for k, v in params.items())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xosc", default=DEFAULT_XOSC)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    keys   = list(PARAM_GRID.keys())
    combos = [dict(zip(keys, vals)) for vals in product(*PARAM_GRID.values())]

    for i, params in enumerate(combos, 1):
        slug     = params_slug(params)
        out_path = os.path.join(args.output, f"scenario_init_{slug}.xml")
        print(f"\n[generate] {i}/{len(combos)}  params={params}  -> {out_path}")

        tmp_xosc = apply_params(args.xosc, params)
        try:
            scenario, planning_problem, pps, ego_name = scenario_setup.setup(tmp_xosc)
        finally:
            os.unlink(tmp_xosc)

        print(f"           lanelets={len(scenario.lanelet_network.lanelets)}  "
              f"obstacles={[o.obstacle_id for o in scenario.dynamic_obstacles]}  "
              f"initial_state={planning_problem.initial_state.position}")

        CommonRoadFileWriter(scenario, pps).write_to_file(
            filename=out_path,
            overwrite_existing_file=OverwriteExistingFile.ALWAYS,
        )

    print(f"\n[generate] wrote {len(combos)} scenario XMLs to {args.output}/")


if __name__ == "__main__":
    main()
