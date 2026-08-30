"""One-shot conversion: run the OSC->CommonRoad conversion (env `cr-osc-converter`) and
write the result to `scenario_init.xml`.

This is the only step of `cosim/frenetix` that needs `osc_cr_converter`/
`crdesigner`. `cosimulation.py` (env `frenetix-cosim`) only reads `scenario_init.xml` via
`CommonRoadFileReader`, avoiding any cross-environment dependency conflicts between
`osc_cr_converter`'s and Frenetix's pinned `commonroad-io`/`commonroad-drivability-checker`
versions.

Run once:
    conda run -n cr-osc-converter python convert_scenario.py
"""

import argparse
import os
import sys

from commonroad.common.file_writer import CommonRoadFileWriter
from commonroad.common.writer.file_writer_interface import OverwriteExistingFile

THIS_DIR = os.path.dirname(os.path.realpath(__file__))

from osc2cr_extended import paths

from osc2cr_extended.cosim import scenario_setup

OUTPUT_PATH = str(paths.OUTPUT_DIR / "scenario_init.xml")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xosc", default=None, help="OpenSCENARIO file to convert (default: cut-in_external.xosc)")
    parser.add_argument("--output", default=OUTPUT_PATH, help="output CommonRoad XML path")
    args = parser.parse_args()

    scenario, planning_problem, pps, ego_name = scenario_setup.setup(args.xosc)

    print(f"lanelets: {len(scenario.lanelet_network.lanelets)}")
    print(f"dynamic obstacles: {[o.obstacle_id for o in scenario.dynamic_obstacles]}")
    print(f"ego planning problem id: {planning_problem.planning_problem_id}, name: {ego_name}")
    print(f"initial state: {planning_problem.initial_state}")

    CommonRoadFileWriter(scenario, pps).write_to_file(
        filename=args.output,
        overwrite_existing_file=OverwriteExistingFile.ALWAYS,
    )
    print(f"[convert_scenario] wrote {args.output}")


if __name__ == "__main__":
    main()
