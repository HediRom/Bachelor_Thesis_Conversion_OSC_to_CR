"""Tick-by-tick co-simulation: esmini <-> CommonRoad reactive planner.

Each tick:
  1. esmini.step()                         -> esmini advances the world by one dt
  2. read all object states from esmini    -> live positions/speeds of ego + traffic
  3. build a short-horizon (constant-velocity) prediction for the non-ego traffic and
     feed it to the reactive planner as the current world state
  4. planner.plan() / continue along the previously planned trajectory
  5. write the planner's next ego state back into esmini (SE_ReportObjectPosXYH/Speed)
  6. record the planner's state -> later assembled into a CommonRoad Trajectory

Simplification: the reactive planner needs a short-horizon *prediction* of other
traffic for collision checking, but esmini only reports the *current* state each tick.
We extrapolate each non-ego object at constant velocity/heading over
`config.planning.time_steps_computation` steps. This is a standard simplification for
coupling an online planner to a simulator that does not expose other agents' plans.
"""

import argparse
import os

import numpy as np

from commonroad.common.file_writer import CommonRoadFileWriter
from commonroad.common.writer.file_writer_interface import OverwriteExistingFile
from commonroad.geometry.shape import Rectangle
from commonroad.planning.planning_problem import PlanningProblemSet
from commonroad.scenario.obstacle import ObstacleType, DynamicObstacle
from commonroad.scenario.state import InitialState, CustomState
from commonroad.scenario.trajectory import Trajectory
from commonroad.prediction.prediction import TrajectoryPrediction
from commonroad.common.solution import CommonRoadSolutionWriter

from commonroad_dc.boundary.boundary import create_road_boundary_obstacle

from commonroad_route_planner.route_planner import RoutePlanner

from commonroad_rp.reactive_planner import ReactivePlanner
from commonroad_rp.utility.config import ReactivePlannerConfiguration
from commonroad_rp.utility.logger import initialize_logger
from commonroad_rp.utility.evaluation import create_full_solution_trajectory, create_planning_problem_solution

from osc2cr_extended.cosim import scenario_setup
from osc2cr_extended.cosim.esmini_interface import EsminiSimulation

THIS_DIR = os.path.dirname(os.path.realpath(__file__))

from osc2cr_extended import paths
OUTPUT_DIR = str(paths.OUTPUT_DIR / "cosim")
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


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(paths.CONFIG_DIR / "cosim.yaml"),
                        help="path to the ReactivePlannerConfiguration yaml")
    parser.add_argument("--desired-velocity", type=float, default=None,
                        help="override the planner's desired velocity in m/s "
                             "(default: derived from the planning problem's goal)")
    parser.add_argument("--viewer", action="store_true",
                        help="open esmini's 3D viewer window and render each tick live")
    return parser.parse_args()


def main():
    args = parse_args()

    # 1) one-shot conversion: lanelet network, ego shape/initial state/goal, traffic template
    scenario, planning_problem, planning_problem_set, ego_name = scenario_setup.setup()
    obstacle_templates = list(scenario.dynamic_obstacles)
    for obs in obstacle_templates:
        scenario.remove_obstacle(obs)

    # 2) reactive planner configuration & route
    config = ReactivePlannerConfiguration.load(args.config)
    config.update(scenario=scenario, planning_problem=planning_problem)
    config.planning_problem_set = planning_problem_set
    initialize_logger(config)

    route_planner = RoutePlanner(config.scenario, config.planning_problem)
    route = route_planner.plan_routes().retrieve_first_route()

    planner = ReactivePlanner(config)
    planner.set_reference_path(route.reference_path)

    # precompute the static road boundary once, reused for every collision-checker rebuild
    _, road_boundary = create_road_boundary_obstacle(scenario)

    # 3) live esmini simulation
    sim = EsminiSimulation(scenario_setup.XOSC_PATH, dt=config.planning.dt, use_viewer=args.viewer)
    ego_id = sim.get_object_id_by_name(ego_name)

    planner.record_state_and_input(planner.x_0)

    # the live OverTaker trajectory, recorded tick-by-tick for visualization
    overtaker_states = []

    optimal = None
    while not sim.is_finished() and not planner.goal_reached():
        current_count = len(planner.record_state_list) - 1
        if current_count >= MAX_STEPS:
            break

        sim.step()
        live_states = sim.get_object_states()

        # rebuild the live (non-ego) traffic with constant-velocity prediction
        for obs in list(scenario.dynamic_obstacles):
            scenario.remove_obstacle(obs)
        for template in obstacle_templates:
            esmini_id = sim.get_object_id_by_name(scenario_setup.obstacle_name(template))
            if esmini_id in live_states:
                state = live_states[esmini_id]
                live_obstacle = build_live_obstacle(
                    template, state, current_count + 1, config.planning.dt,
                    config.planning.time_steps_computation
                )
                scenario.add_objects(live_obstacle)
                overtaker_states.append(CustomState(
                    time_step=current_count + 1,
                    position=np.array([state.x, state.y]),
                    orientation=state.h,
                    velocity=state.speed,
                ))

        planner.set_collision_checker(scenario=scenario, road_boundary_obstacle=road_boundary)

        plan_new_trajectory = current_count % config.planning.replanning_frequency == 0
        if plan_new_trajectory:
            planner.set_desired_velocity(desired_velocity=args.desired_velocity, current_speed=planner.x_0.velocity)
            optimal = planner.plan()
            if not optimal:
                print(f"[cosim] planner failed to find a feasible trajectory at step {current_count}")
                break
            next_state = optimal[0].state_list[1]
            next_curv = (optimal[2][1], optimal[3][1])
        else:
            temp = current_count % config.planning.replanning_frequency
            next_state = optimal[0].state_list[1 + temp]
            next_curv = (optimal[2][1 + temp], optimal[3][1 + temp])

        planner.record_state_and_input(next_state)
        planner.reset(initial_state_cart=next_state, initial_state_curv=next_curv,
                       collision_checker=planner.collision_checker, coordinate_system=planner.coordinate_system)

        # push the ego's new state back into esmini
        ego_center = next_state.shift_positions_to_center(config.vehicle.wb_rear_axle)
        sim.set_ego_state(ego_id, ego_center.position[0], ego_center.position[1],
                          ego_center.orientation, next_state.velocity)

        print(f"[cosim] t={current_count * config.planning.dt:5.2f}s  "
              f"ego=({next_state.position[0]:6.2f},{next_state.position[1]:6.2f})  "
              f"v={next_state.velocity:5.2f}")

    sim.close()

    # 4) assemble the ego's driven motion as a CommonRoad Trajectory and write a solution file
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ego_trajectory = create_full_solution_trajectory(config, planner.record_state_list)
    solution = create_planning_problem_solution(config, ego_trajectory, config.scenario, config.planning_problem)
    CommonRoadSolutionWriter(solution).write_to_file(output_path=OUTPUT_DIR, overwrite=True)
    print(f"[cosim] done: {len(ego_trajectory.state_list)} states, "
          f"solution written to {OUTPUT_DIR}")

    # 5) write a combined scenario (lanelet network + both vehicles' driven trajectories)
    # for visualization
    for obs in list(scenario.dynamic_obstacles):
        scenario.remove_obstacle(obs)

    ego_initial = ego_trajectory.state_list[0]
    ego_obstacle = DynamicObstacle(
        obstacle_id=planning_problem.planning_problem_id,
        obstacle_type=ObstacleType.CAR,
        obstacle_shape=Rectangle(width=config.vehicle.width, length=config.vehicle.length),
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
            Trajectory(initial_time_step=ego_trajectory.state_list[1].time_step,
                      state_list=ego_trajectory.state_list[1:]),
            Rectangle(width=config.vehicle.width, length=config.vehicle.length),
        ),
    )
    scenario.add_objects(ego_obstacle)

    if overtaker_states:
        overtaker_template = obstacle_templates[0]
        overtaker_obstacle = DynamicObstacle(
            obstacle_id=overtaker_template.obstacle_id,
            obstacle_type=ObstacleType.CAR,
            obstacle_shape=overtaker_template.obstacle_shape,
            initial_state=InitialState(
                time_step=overtaker_states[0].time_step,
                position=overtaker_states[0].position,
                orientation=overtaker_states[0].orientation,
                velocity=overtaker_states[0].velocity,
                acceleration=0.0,
                yaw_rate=0.0,
            ),
            prediction=TrajectoryPrediction(
                Trajectory(initial_time_step=overtaker_states[1].time_step,
                          state_list=overtaker_states[1:]),
                overtaker_template.obstacle_shape,
            ),
        )
        scenario.add_objects(overtaker_obstacle)

    CommonRoadFileWriter(scenario, PlanningProblemSet()).write_to_file(
        filename=os.path.join(OUTPUT_DIR, "cosim_result.xml"),
        overwrite_existing_file=OverwriteExistingFile.ALWAYS,
    )
    print(f"[cosim] combined scenario written to {os.path.join(OUTPUT_DIR, 'cosim_result.xml')}")


if __name__ == "__main__":
    main()
