"""Tick-by-tick co-simulation: esmini <-> Frenetix Motion Planner (`ReactivePlannerCpp`).

Mirrors `cosim/cosimulation.py`, but driven by the Frenetix Motion Planner
(https://github.com/TUM-AVS/Frenetix-Motion-Planner) instead of `commonroad_rp`.

Each simulation tick:
  1. `esmini.step()` -> esmini advances the world by one dt and reports object states.
  2. The non-ego traffic's live state is constant-velocity-extrapolated over the planning
     horizon and packed into the `predictions` dict consumed by
     `ReactivePlannerCpp.set_predictions()`. A `DynamicObstacle` with the same prediction
     is also added to `scenario` purely for visualization.
  3. `planner.plan()` (replanned every `replanning_frequency` ticks, otherwise continuing
     along the previously planned trajectory).
  4. The ego's next state is written back into `esmini` via `set_ego_state(...)`.
  5. Steps 1-4 repeat until esmini ends the scenario, the goal is reached, the planner
     fails, or `max_steps` is reached.
  6. The recorded states are assembled into a CommonRoad `Trajectory` / `Solution`.

This module only reads `scenario_init.xml` (written once by `convert_scenario.py` in the
`cr-osc-converter` env) via `CommonRoadFileReader` -- it never imports `osc_cr_converter`/
`crdesigner`, so it has no dependency conflicts with Frenetix's pinned `commonroad-io`/
`commonroad-drivability-checker`/`commonroad-route-planner` versions.

Example
-------
>>> from cosimulation import CoSimulation
>>> cosim = CoSimulation(desired_velocity=8.0)
>>> result = cosim.run()
>>> result.write_solution()
>>> result.write_combined_scenario()
"""

import argparse
import copy
import logging
import os
import sys
from dataclasses import dataclass, field

import numpy as np

from commonroad.common.file_writer import CommonRoadFileWriter
from commonroad.common.file_reader import CommonRoadFileReader
from commonroad.common.writer.file_writer_interface import OverwriteExistingFile
from commonroad.common.solution import (
    CommonRoadSolutionWriter, PlanningProblemSolution, Solution, VehicleType, VehicleModel, CostFunction,
)
from commonroad.geometry.shape import Rectangle
from commonroad.planning.planning_problem import PlanningProblem, PlanningProblemSet
from commonroad.prediction.prediction import TrajectoryPrediction
from commonroad.scenario.obstacle import DynamicObstacle, ObstacleType
from commonroad.scenario.scenario import Scenario
from commonroad.scenario.state import CustomState, InitialState
from commonroad.scenario.trajectory import Trajectory

from commonroad_route_planner.route_planner import RoutePlanner

from cr_scenario_handler.utils.configuration_builder import ConfigurationBuilder
from frenetix_motion_planner.reactive_planner_cpp import ReactivePlannerCpp
from frenetix_motion_planner.state import ReactivePlannerState

from osc2cr_extended.cosim.esmini_interface import EsminiSimulation

THIS_DIR = os.path.dirname(os.path.realpath(__file__))

from osc2cr_extended import paths
OUTPUT_DIR = str(paths.OUTPUT_DIR / "cosim")
SCENARIO_XML = str(paths.OUTPUT_DIR / "scenario_init.xml")
#: Frenetix's own repository root — it resolves its logging//results paths
#: relative to this.  Located through the installed ``frenetix_motion_planner``
#: package, overridable with OSC2CR_FRENETIX_HOME for a source checkout that is
#: not pip-installed.
WORK_DIR = paths.frenetix_home() or ""
XOSC_PATH = str(paths.ESMINI_XOSC / "cut-in_external.xosc")
EGO_NAME = "Ego"
OVERTAKER_NAME = "OverTaker"
MAX_STEPS = 150  # safety cap: 150 * dt(0.1s) = 15s, matches the converted scenario duration


def build_live_obstacle(template: DynamicObstacle, esmini_state, current_time_step: int,
                        dt: float, num_future_steps: int) -> DynamicObstacle:
    """Build a CommonRoad DynamicObstacle for `template`'s id from esmini's current state,
    with a constant-velocity/heading extrapolation as its short-horizon prediction."""
    x, y, h, v = esmini_state.x, esmini_state.y, esmini_state.h, esmini_state.speed
    shape = Rectangle(width=float(esmini_state.width), length=float(esmini_state.length))

    initial_state = InitialState(
        time_step=current_time_step,
        position=np.array([x, y]),
        orientation=h,
        velocity=v,
        acceleration=0.0,
        yaw_rate=0.0,
    )

    future_states = []
    for i in range(1, num_future_steps + 1):
        ds = v * dt * i
        future_states.append(
            CustomState(
                time_step=current_time_step + i,
                position=np.array([x + ds * np.cos(h), y + ds * np.sin(h)]),
                orientation=h,
                velocity=v,
            )
        )
    prediction = TrajectoryPrediction(
        Trajectory(initial_time_step=current_time_step + 1, state_list=future_states), shape
    )

    return DynamicObstacle(
        obstacle_id=template.obstacle_id,
        obstacle_type=ObstacleType.CAR,
        obstacle_shape=shape,
        initial_state=initial_state,
        prediction=prediction,
    )


def retrieve_desired_velocity_from_pp(planning_problem: PlanningProblem) -> float:
    """Derive a target speed from the planning problem's goal velocity interval (or fall
    back to the initial state's velocity if the goal has none)."""
    goal = planning_problem.goal
    if goal.state_list and hasattr(goal.state_list[0], "velocity"):
        velocity = goal.state_list[0].velocity
        if velocity.start > 0:
            return (velocity.start + velocity.end) / 2
        return velocity.end / 2
    return planning_problem.initial_state.velocity


@dataclass
class CoSimulationResult:
    """The outcome of a finished `CoSimulation` run."""

    ego_trajectory: Trajectory
    solution: Solution
    overtaker_states: list = field(default_factory=list)
    scenario: Scenario = None
    planning_problem: PlanningProblem = None
    obstacle_templates: list = field(default_factory=list)
    vehicle_width: float = None
    vehicle_length: float = None

    def write_solution(self, output_dir: str = OUTPUT_DIR, overwrite: bool = True) -> str:
        """Write the ego's driven trajectory as a CommonRoad `Solution` XML.

        :return: the output directory the solution was written to.
        """
        os.makedirs(output_dir, exist_ok=True)
        CommonRoadSolutionWriter(self.solution).write_to_file(output_path=output_dir, overwrite=overwrite)
        return output_dir

    def build_combined_scenario(self) -> Scenario:
        """Return a copy of the scenario containing both vehicles' full driven
        trajectories (ego from the planner, OverTaker as recorded from esmini),
        for visualization with `visualize.py`."""
        scenario = copy.deepcopy(self.scenario)
        for obs in list(scenario.dynamic_obstacles):
            scenario.remove_obstacle(obs)

        ego_initial = self.ego_trajectory.state_list[0]
        ego_obstacle = DynamicObstacle(
            obstacle_id=self.planning_problem.planning_problem_id,
            obstacle_type=ObstacleType.CAR,
            obstacle_shape=Rectangle(width=self.vehicle_width, length=self.vehicle_length),
            initial_state=InitialState(
                time_step=ego_initial.time_step,
                position=ego_initial.position,
                orientation=ego_initial.orientation,
                velocity=ego_initial.velocity,
                acceleration=ego_initial.acceleration,
                yaw_rate=ego_initial.yaw_rate,
                slip_angle=0.0,
            ),
            prediction=TrajectoryPrediction(
                Trajectory(initial_time_step=self.ego_trajectory.state_list[1].time_step,
                           state_list=self.ego_trajectory.state_list[1:]),
                Rectangle(width=self.vehicle_width, length=self.vehicle_length),
            ),
        )
        scenario.add_objects(ego_obstacle)

        if self.overtaker_states:
            overtaker_template = self.obstacle_templates[0]
            overtaker_obstacle = DynamicObstacle(
                obstacle_id=overtaker_template.obstacle_id,
                obstacle_type=ObstacleType.CAR,
                obstacle_shape=overtaker_template.obstacle_shape,
                initial_state=InitialState(
                    time_step=self.overtaker_states[0].time_step,
                    position=self.overtaker_states[0].position,
                    orientation=self.overtaker_states[0].orientation,
                    velocity=self.overtaker_states[0].velocity,
                    acceleration=0.0,
                    yaw_rate=0.0,
                ),
                prediction=TrajectoryPrediction(
                    Trajectory(initial_time_step=self.overtaker_states[1].time_step,
                               state_list=self.overtaker_states[1:]),
                    overtaker_template.obstacle_shape,
                ),
            )
            scenario.add_objects(overtaker_obstacle)

        return scenario

    def write_combined_scenario(self, path: str = os.path.join(OUTPUT_DIR, "cosim_result.xml")) -> str:
        """Build and write the combined visualization scenario to `path`."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        scenario = self.build_combined_scenario()
        CommonRoadFileWriter(scenario, PlanningProblemSet()).write_to_file(
            filename=path,
            overwrite_existing_file=OverwriteExistingFile.ALWAYS,
        )
        return path


class CoSimulation:
    """Tick-by-tick esmini <-> Frenetix Motion Planner co-simulation.

    :param xosc_path: OpenSCENARIO file to simulate (default: `XOSC_PATH`)
    :param scenario_xml: CommonRoad XML written by `convert_scenario.py` for `xosc_path`
        (default: `SCENARIO_XML`)
    :param config_root: root path containing `configurations/` (default: the
        Frenetix configuration set bundled with this package, `data/`)
    :param desired_velocity: overrides the planner's target speed in m/s
        (default: derived from the planning problem's goal)
    :param max_steps: safety cap on the number of simulation ticks
    """

    def __init__(self, xosc_path: str = None, scenario_xml: str = None, config_root: str = None,
                 desired_velocity: float = None, max_steps: int = MAX_STEPS):
        self.xosc_path = xosc_path or XOSC_PATH
        self.scenario_xml = scenario_xml or SCENARIO_XML
        self.config_root = config_root or str(paths.DATA_DIR / "frenetix")
        self.max_steps = max_steps

        # one-shot read: lanelet network, ego shape/initial state/goal, traffic template
        self.scenario, pps = CommonRoadFileReader(self.scenario_xml).open()
        self.planning_problem = list(pps.planning_problem_dict.values())[0]
        self.obstacle_templates = list(self.scenario.dynamic_obstacles)
        for obs in self.obstacle_templates:
            self.scenario.remove_obstacle(obs)

        # Frenetix configuration (avoid OmegaConf.from_cli() picking up our own argv)
        argv_backup, sys.argv = sys.argv, sys.argv[:1]
        try:
            self.config_plan = ConfigurationBuilder.build_frenetplanner_configuration(
                scenario_name="cosim", root_path=self.config_root)
            self.config_sim = ConfigurationBuilder.build_sim_configuration(
                scenario_name="cosim", scenario_folder=self.config_root, root_path=self.config_root)
        finally:
            sys.argv = argv_backup

        self.config_sim.simulation.ego_agent_id = self.planning_problem.planning_problem_id

        self.desired_velocity = desired_velocity if desired_velocity is not None \
            else retrieve_desired_velocity_from_pp(self.planning_problem)

        self.msg_logger = logging.getLogger("frenetix_cosim")
        if not self.msg_logger.handlers:
            self.msg_logger.addHandler(logging.StreamHandler())
        self.msg_logger.setLevel(logging.WARNING)

        os.makedirs(OUTPUT_DIR, exist_ok=True)

        # planner setup
        self.planner = ReactivePlannerCpp(self.config_plan, self.config_sim, self.scenario,
                                          self.planning_problem, OUTPUT_DIR, WORK_DIR, self.msg_logger)

        route_planner = RoutePlanner(self.scenario.lanelet_network, self.planning_problem, scenario=self.scenario)
        route = route_planner.plan_routes().retrieve_first_route()

        x_0 = ReactivePlannerState.create_from_initial_state(
            copy.deepcopy(self.planning_problem.initial_state),
            self.config_sim.vehicle.wheelbase, self.config_sim.vehicle.wb_rear_axle,
        )

        self.planner.update_externals(
            x_0=x_0, reference_path=route.reference_path, goal_area=self.planning_problem.goal,
        )
        # set_cost_function() (called via update_externals(cost_weights=...)) needs
        # self.desired_velocity to already be set, so set it first.
        self.planner.set_desired_velocity(self.desired_velocity, x_0.velocity)
        self.planner.set_cost_function(self.config_plan.cost.cost_weights)
        self.x_0 = self.planner.x_0
        self.x_cl = self.planner.x_cl

        self.planner.record_state_and_input(self.x_0)

        # live esmini simulation
        self.sim = EsminiSimulation(self.xosc_path, dt=self.config_plan.planning.dt)
        self.ego_id = self.sim.get_object_id_by_name(EGO_NAME)

        # the live OverTaker trajectory, recorded tick-by-tick for visualization
        self.overtaker_states = []
        self._trajectory_pair = None
        self._closed = False

    @property
    def finished(self) -> bool:
        return self._closed

    def step(self):
        """Advance the co-simulation by one tick.

        :return: a dict with the tick's `time_step` and ego/overtaker states, or `None`
            once the simulation has finished (esmini ended, goal reached, planner failed,
            or `max_steps` was reached). `sim.close()` is called automatically on finish.
        """
        if self._closed:
            return None

        current_count = len(self.planner.record_state_list) - 1
        ego_center = self.x_0.shift_positions_to_center(self.config_sim.vehicle.wb_rear_axle)
        if self.sim.is_finished() or self.planning_problem.goal.is_reached(ego_center) \
                or current_count >= self.max_steps:
            self._close()
            return None

        self.sim.step()
        live_states = self.sim.get_object_states()

        # rebuild the live (non-ego) traffic with constant-velocity prediction
        for obs in list(self.scenario.dynamic_obstacles):
            self.scenario.remove_obstacle(obs)

        predictions = {}
        tick_overtaker_state = None
        for template in self.obstacle_templates:
            esmini_id = self.sim.get_object_id_by_name(OVERTAKER_NAME)
            if esmini_id in live_states:
                state = live_states[esmini_id]
                live_obstacle = build_live_obstacle(
                    template, state, current_count + 1, self.config_plan.planning.dt, self.planner.N
                )
                self.scenario.add_objects(live_obstacle)

                x, y, h, v = state.x, state.y, state.h, state.speed
                steps = np.arange(1, self.planner.N + 1)
                ds = v * self.config_plan.planning.dt * steps
                pos_list = np.stack([x + ds * np.cos(h), y + ds * np.sin(h)], axis=1)
                predictions[template.obstacle_id] = {
                    "pos_list": pos_list,
                    "orientation_list": np.full(self.planner.N, h),
                    "v_list": np.full(self.planner.N, v),
                    "cov_list": np.tile(np.eye(2) * 0.1, (self.planner.N, 1, 1)),
                    "shape": {"length": float(state.length), "width": float(state.width)},
                }

                tick_overtaker_state = CustomState(
                    time_step=current_count + 1,
                    position=np.array([x, y]),
                    orientation=h,
                    velocity=v,
                )
                self.overtaker_states.append(tick_overtaker_state)

        self.planner.update_externals(scenario=self.scenario, x_0=self.x_0, x_cl=self.x_cl,
                                      desired_velocity=self.desired_velocity, predictions=predictions)

        plan_new_trajectory = current_count % self.config_plan.planning.replanning_frequency == 0
        if plan_new_trajectory:
            trajectory_pair = self.planner.plan()
            if not trajectory_pair:
                self._close()
                return None
            self._trajectory_pair = trajectory_pair
            next_state = trajectory_pair[0].state_list[1]
            next_curv = (trajectory_pair[2][1], trajectory_pair[3][1])
        else:
            temp = current_count % self.config_plan.planning.replanning_frequency
            next_state = self._trajectory_pair[0].state_list[1 + temp]
            next_curv = (self._trajectory_pair[2][1 + temp], self._trajectory_pair[3][1 + temp])

        self.planner.record_state_and_input(next_state)
        self.planner.update_externals(x_0=next_state, x_cl=next_curv)
        self.x_0 = self.planner.x_0
        self.x_cl = self.planner.x_cl

        # push the ego's new state back into esmini
        ego_center = next_state.shift_positions_to_center(self.config_sim.vehicle.wb_rear_axle)
        self.sim.set_ego_state(self.ego_id, ego_center.position[0], ego_center.position[1],
                                ego_center.orientation, next_state.velocity)

        return {
            "time_step": current_count,
            "time": current_count * self.config_plan.planning.dt,
            "ego_state": next_state,
            "overtaker_state": tick_overtaker_state,
        }

    def run(self) -> "CoSimulationResult":
        """Run until the simulation finishes, then return the result."""
        while self.step() is not None:
            pass
        return self.result()

    def result(self) -> CoSimulationResult:
        """Assemble the planner's recorded states into a `CoSimulationResult`.

        Can be called after `run()`, or after manually stepping with `step()` until it
        returns `None`.
        """
        wb_rear_axle = self.config_sim.vehicle.wb_rear_axle
        centered_states = [state.shift_positions_to_center(wb_rear_axle)
                            for state in self.planner.record_state_list]
        ego_trajectory = Trajectory(initial_time_step=centered_states[0].time_step, state_list=centered_states)

        pp_solution = PlanningProblemSolution(
            planning_problem_id=self.planning_problem.planning_problem_id,
            vehicle_type=VehicleType(self.config_sim.vehicle.cr_vehicle_id),
            vehicle_model=VehicleModel.KS,
            cost_function=CostFunction.JB1,
            trajectory=ego_trajectory,
        )
        solution = Solution(self.scenario.scenario_id, [pp_solution])

        return CoSimulationResult(
            ego_trajectory=ego_trajectory,
            solution=solution,
            overtaker_states=self.overtaker_states,
            scenario=self.scenario,
            planning_problem=self.planning_problem,
            obstacle_templates=self.obstacle_templates,
            vehicle_width=self.config_sim.vehicle.width,
            vehicle_length=self.config_sim.vehicle.length,
        )

    def _close(self):
        if not self._closed:
            self.sim.close()
            self._closed = True


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desired-velocity", type=float, default=None,
                        help="override the planner's desired velocity in m/s "
                             "(default: derived from the planning problem's goal)")
    parser.add_argument("--xosc", default=None, help="OpenSCENARIO file to simulate (default: cut-in_external.xosc)")
    parser.add_argument("--scenario-xml", default=None,
                         help="CommonRoad XML written by convert_scenario.py for --xosc (default: scenario_init.xml)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    cosim = CoSimulation(xosc_path=args.xosc, scenario_xml=args.scenario_xml,
                          desired_velocity=args.desired_velocity)
    while True:
        info = cosim.step()
        if info is None:
            break
        print(f"[cosim] t={info['time']:5.2f}s  "
              f"ego=({info['ego_state'].position[0]:6.2f},{info['ego_state'].position[1]:6.2f})  "
              f"v={info['ego_state'].velocity:5.2f}")

    result = cosim.result()
    output_dir = result.write_solution()
    result_path = result.write_combined_scenario()
    print(f"[cosim] done: {len(result.ego_trajectory.state_list)} states, "
          f"solution written to {output_dir}")
    print(f"[cosim] combined scenario written to {result_path}")
