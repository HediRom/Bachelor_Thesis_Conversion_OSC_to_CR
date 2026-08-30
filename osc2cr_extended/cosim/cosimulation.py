"""Class-based wrapper around the esmini <-> CommonRoad reactive planner co-simulation.

This provides the same tick-by-tick co-simulation as `cosim_loop.py`, but as a reusable
`CoSimulation` class instead of a standalone script. `cosim_loop.py` is left untouched
as the original CLI entry point; this module is an additional, more ergonomic API for
using the co-simulation from other Python code (e.g. notebooks, batch experiments).

Example
-------
>>> from cosimulation import CoSimulation
>>> cosim = CoSimulation(desired_velocity=8.0)
>>> while cosim.step() is not None:
...     pass
>>> result = cosim.result()
>>> result.write_solution()
>>> result.write_combined_scenario("output/cosim_result.xml")
"""

import copy
import os
from dataclasses import dataclass, field

import numpy as np

from commonroad.common.file_writer import CommonRoadFileWriter
from commonroad.common.writer.file_writer_interface import OverwriteExistingFile
from commonroad.common.solution import CommonRoadSolutionWriter
from commonroad.geometry.shape import Rectangle
from commonroad.planning.planning_problem import PlanningProblem, PlanningProblemSet
from commonroad.prediction.prediction import TrajectoryPrediction
from commonroad.scenario.obstacle import DynamicObstacle, ObstacleType
from commonroad.scenario.scenario import Scenario
from commonroad.scenario.state import CustomState, InitialState
from commonroad.scenario.trajectory import Trajectory

from commonroad_dc.boundary.boundary import create_road_boundary_obstacle
from commonroad_route_planner.route_planner import RoutePlanner

from commonroad_rp.reactive_planner import ReactivePlanner
from commonroad_rp.utility.config import ReactivePlannerConfiguration
from commonroad_rp.utility.evaluation import create_full_solution_trajectory, create_planning_problem_solution
from commonroad_rp.utility.logger import initialize_logger

from osc2cr_extended.cosim import scenario_setup
from osc2cr_extended.cosim.reactive_loop import MAX_STEPS, OUTPUT_DIR, THIS_DIR, build_live_obstacle
from osc2cr_extended.cosim.esmini_interface import EsminiSimulation

from osc2cr_extended import paths

DEFAULT_CONFIG_PATH = str(paths.CONFIG_DIR / "cosim.yaml")


@dataclass
class CoSimulationResult:
    """The outcome of a finished `CoSimulation` run."""

    ego_trajectory: Trajectory
    solution: object
    overtaker_states: list = field(default_factory=list)
    scenario: Scenario = None
    planning_problem: PlanningProblem = None
    obstacle_templates: list = field(default_factory=list)
    config: ReactivePlannerConfiguration = None

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
            obstacle_shape=Rectangle(width=self.config.vehicle.width, length=self.config.vehicle.length),
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
                Rectangle(width=self.config.vehicle.width, length=self.config.vehicle.length),
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
    """Tick-by-tick esmini <-> CommonRoad reactive planner co-simulation.

    :param xosc_path: OpenSCENARIO file to simulate (default: `scenario_setup.XOSC_PATH`)
    :param config_path: `ReactivePlannerConfiguration` yaml (default: `configurations/cosim.yaml`)
    :param desired_velocity: overrides the planner's target speed in m/s
        (default: derived from the planning problem's goal)
    :param max_steps: safety cap on the number of simulation ticks
    :param use_viewer: open esmini's 3D viewer window and render each tick live
    """

    def __init__(self, xosc_path: str = None, config_path: str = None,
                 desired_velocity: float = None, max_steps: int = MAX_STEPS,
                 use_viewer: bool = False):
        self.xosc_path = xosc_path or scenario_setup.XOSC_PATH
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self.desired_velocity = desired_velocity
        self.max_steps = max_steps

        # one-shot conversion: lanelet network, ego shape/initial state/goal, traffic template
        self.scenario, self.planning_problem, self.planning_problem_set, self.ego_name = scenario_setup.setup(
            self.xosc_path
        )
        self.obstacle_templates = list(self.scenario.dynamic_obstacles)
        for obs in self.obstacle_templates:
            self.scenario.remove_obstacle(obs)

        # reactive planner configuration & route
        self.config = ReactivePlannerConfiguration.load(self.config_path)
        self.config.update(scenario=self.scenario, planning_problem=self.planning_problem)
        self.config.planning_problem_set = self.planning_problem_set
        initialize_logger(self.config)

        route_planner = RoutePlanner(self.config.scenario, self.config.planning_problem)
        route = route_planner.plan_routes().retrieve_first_route()

        self.planner = ReactivePlanner(self.config)
        self.planner.set_reference_path(route.reference_path)

        # precompute the static road boundary once, reused for every collision-checker rebuild
        _, self.road_boundary = create_road_boundary_obstacle(self.scenario)

        # live esmini simulation
        self.sim = EsminiSimulation(self.xosc_path, dt=self.config.planning.dt, use_viewer=use_viewer)
        self.ego_id = self.sim.get_object_id_by_name(self.ego_name)

        self.planner.record_state_and_input(self.planner.x_0)

        # the live OverTaker trajectory, recorded tick-by-tick for visualization
        self.overtaker_states = []
        self._optimal = None
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
        if self.sim.is_finished() or self.planner.goal_reached() or current_count >= self.max_steps:
            self._close()
            return None

        self.sim.step()
        live_states = self.sim.get_object_states()

        # rebuild the live (non-ego) traffic with constant-velocity prediction
        for obs in list(self.scenario.dynamic_obstacles):
            self.scenario.remove_obstacle(obs)

        tick_overtaker_state = None
        for template in self.obstacle_templates:
            esmini_id = self.sim.get_object_id_by_name(scenario_setup.obstacle_name(template))
            if esmini_id in live_states:
                state = live_states[esmini_id]
                live_obstacle = build_live_obstacle(
                    template, state, current_count + 1, self.config.planning.dt,
                    self.config.planning.time_steps_computation
                )
                self.scenario.add_objects(live_obstacle)
                tick_overtaker_state = CustomState(
                    time_step=current_count + 1,
                    position=np.array([state.x, state.y]),
                    orientation=state.h,
                    velocity=state.speed,
                )
                self.overtaker_states.append(tick_overtaker_state)

        self.planner.set_collision_checker(scenario=self.scenario, road_boundary_obstacle=self.road_boundary)

        plan_new_trajectory = current_count % self.config.planning.replanning_frequency == 0
        if plan_new_trajectory:
            self.planner.set_desired_velocity(desired_velocity=self.desired_velocity,
                                               current_speed=self.planner.x_0.velocity)
            self._optimal = self.planner.plan()
            if not self._optimal:
                self._close()
                return None
            next_state = self._optimal[0].state_list[1]
            next_curv = (self._optimal[2][1], self._optimal[3][1])
        else:
            temp = current_count % self.config.planning.replanning_frequency
            next_state = self._optimal[0].state_list[1 + temp]
            next_curv = (self._optimal[2][1 + temp], self._optimal[3][1 + temp])

        self.planner.record_state_and_input(next_state)
        self.planner.reset(initial_state_cart=next_state, initial_state_curv=next_curv,
                            collision_checker=self.planner.collision_checker,
                            coordinate_system=self.planner.coordinate_system)

        # push the ego's new state back into esmini
        ego_center = next_state.shift_positions_to_center(self.config.vehicle.wb_rear_axle)
        self.sim.set_ego_state(self.ego_id, ego_center.position[0], ego_center.position[1],
                                ego_center.orientation, next_state.velocity)

        return {
            "time_step": current_count,
            "time": current_count * self.config.planning.dt,
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
        ego_trajectory = create_full_solution_trajectory(self.config, self.planner.record_state_list)
        solution = create_planning_problem_solution(self.config, ego_trajectory, self.config.scenario,
                                                      self.config.planning_problem)
        return CoSimulationResult(
            ego_trajectory=ego_trajectory,
            solution=solution,
            overtaker_states=self.overtaker_states,
            scenario=self.scenario,
            planning_problem=self.planning_problem,
            obstacle_templates=self.obstacle_templates,
            config=self.config,
        )

    def _close(self):
        if not self._closed:
            self.sim.close()
            self._closed = True


if __name__ == "__main__":
    cosim = CoSimulation(desired_velocity=8.0)
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
