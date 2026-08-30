"""One-shot setup: convert the esmini OpenSCENARIO scenario to a CommonRoad scenario.

This runs the esmini-based OSC->CommonRoad converter (`Osc2CrConverter`) once, offline,
on the `cut-in_external.xosc` scenario. The result provides:
  - a lanelet network (the static road) for the reactive planner's reference path,
  - the ego vehicle's shape/type and its initial state + goal region (PlanningProblem),
  - the non-ego traffic obstacle (OverTaker) with its full recorded trajectory, used as
    a template (shape/type) for the live, esmini-driven obstacle states in the co-sim loop.

The ego obstacle itself is excluded from `scenario.dynamic_obstacles`
(`scenario.keep_ego_vehicle: false` in configurations/converter.yaml) since its motion
is determined by the reactive planner, not by esmini's recorded trajectory.
"""

import functools
import os

from crdesigner.common.config.opendrive_config import OpenDriveConfig
from crdesigner.map_conversion.map_conversion_interface import opendrive_to_commonroad

import osc_cr_converter.converter.osc2cr as osc2cr
from osc_cr_converter.utility.configuration import ConverterParams
from osc_cr_converter.converter.osc2cr import Osc2CrConverter
from osc_cr_converter.converter.base import EFailureReason
import osc_cr_converter.utility.logger as util_logger

from commonroad.scenario.scenario import Scenario
from commonroad.scenario.obstacle import DynamicObstacle
from commonroad.planning.planning_problem import PlanningProblem, PlanningProblemSet

# The bundled .xodr files carry a UTM <geoReference>, which crdesigner uses by default
# to project lanelet vertices into UTM-ish coordinates (~5e5, ~1e3). The OpenSCENARIO
# entity positions (from esmini) stay in the xodr's raw local track frame (~tens of
# meters), so the two would otherwise end up ~500km apart. Disable the projection so the
# lanelet network stays in the same local frame as the obstacle/ego positions.
_LOCAL_ODR_CONFIG = OpenDriveConfig()
_LOCAL_ODR_CONFIG.proj_string_odr = None
osc2cr.opendrive_to_commonroad = functools.partial(opendrive_to_commonroad, odr_conf=_LOCAL_ODR_CONFIG)

THIS_DIR = os.path.dirname(os.path.realpath(__file__))

from osc2cr_extended import paths
XOSC_PATH = str(paths.ESMINI_XOSC / "cut-in_external.xosc")
CONVERTER_CONFIG_PATH = str(paths.CONFIG_DIR / "converter.yaml")


def setup(xosc_path: str = None) -> tuple[Scenario, PlanningProblem, PlanningProblemSet, str]:
    """Convert the esmini scenario once and return the initial CommonRoad setup.

    :param xosc_path: OpenSCENARIO file to convert (default: `XOSC_PATH`)
    :return: (scenario, planning_problem, planning_problem_set, ego_name)
    """
    config = ConverterParams.load(CONVERTER_CONFIG_PATH)
    util_logger.initialize_logger(config)

    converter = Osc2CrConverter(config)
    scenario = converter.run_conversion(xosc_path or XOSC_PATH)
    if isinstance(scenario, EFailureReason):
        raise RuntimeError(f"OSC->CommonRoad conversion failed: {scenario}")

    pps = converter.conversion_result.planning_problem_set
    planning_problem = list(pps.planning_problem_dict.values())[0]
    if planning_problem.initial_state.acceleration is None:
        planning_problem.initial_state.acceleration = 0.0

    return scenario, planning_problem, pps, "Ego"


# Names of the non-ego traffic objects in `cut-in_external.xosc`, in the order
# `scenario.dynamic_obstacles` returns them after conversion. This scenario has a
# single non-ego vehicle, so the mapping is trivial.
_OBSTACLE_NAMES = ["OverTaker"]


def obstacle_name(obstacle: DynamicObstacle) -> str:
    """Return the esmini object name corresponding to a converted DynamicObstacle template."""
    return _OBSTACLE_NAMES[0]


if __name__ == "__main__":
    scenario, planning_problem, pps, ego_name = setup()
    print(f"lanelets: {len(scenario.lanelet_network.lanelets)}")
    print(f"dynamic obstacles: {[o.obstacle_id for o in scenario.dynamic_obstacles]}")
    print(f"ego planning problem id: {planning_problem.planning_problem_id}")
    print(f"initial state: {planning_problem.initial_state}")
