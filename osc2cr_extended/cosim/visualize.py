"""Render the co-simulation result (output/cosim_result.xml) as an animated GIF.

Run `cosim_loop.py` first to generate `output/cosim_result.xml`, which contains the
lanelet network plus the ego's and OverTaker's full driven trajectories. This script
loads that scenario and renders it tick-by-tick into `output/cosim_animation.gif`.
"""

import argparse
import os

import matplotlib
import numpy as np

matplotlib.use("Agg")

from commonroad.common.file_reader import CommonRoadFileReader
from commonroad.visualization.draw_params import MPDrawParams
from commonroad.visualization.mp_renderer import MPRenderer

THIS_DIR = os.path.dirname(os.path.realpath(__file__))

from osc2cr_extended import paths
OUTPUT_DIR = str(paths.OUTPUT_DIR / "cosim")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=os.path.join(OUTPUT_DIR, "cosim_result.xml"))
    parser.add_argument("--output", default=os.path.join(OUTPUT_DIR, "cosim_animation.gif"))
    args = parser.parse_args()

    scenario, _ = CommonRoadFileReader(args.input).open()

    time_steps = []
    positions = []
    for obstacle in scenario.dynamic_obstacles:
        for state in [obstacle.initial_state, *obstacle.prediction.trajectory.state_list]:
            time_steps.append(state.time_step)
            positions.append(state.position)
    time_begin, time_end = min(time_steps), max(time_steps)

    # zoom in on the area the vehicles actually drive through, with some margin
    positions = np.array(positions)
    margin = 15.0
    x_min, y_min = positions.min(axis=0) - margin
    x_max, y_max = positions.max(axis=0) + margin

    rnd = MPRenderer(figsize=(8, 12), plot_limits=[x_min, x_max, y_min, y_max])
    rnd.create_video(
        [scenario],
        args.output,
        draw_params=MPDrawParams(time_begin=time_begin, time_end=time_end),
    )
    print(f"[visualize] wrote {args.output} ({time_end - time_begin + 1} frames)")


if __name__ == "__main__":
    main()
