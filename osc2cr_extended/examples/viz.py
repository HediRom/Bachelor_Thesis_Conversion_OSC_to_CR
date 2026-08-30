"""
viz.py
======
Renders a converted CR Scenario for the VS Code extension's results panel.
Same MPRenderer/Agg approach as cosim/visualize.py, but operating
on an in-memory scenario (no intermediate file) and split into two views:

  render_preview(scenario, path)  -> single frame at the scenario's first
                                      time step (entities at their initial
                                      positions, before anything has moved)
  render_replay(scenario, path)   -> full tick-by-tick GIF across every
                                      time step (the "during simulation" view)

Both are best-effort: failures are caught by the caller (vscode_bridge.py)
so a rendering problem never blocks the actual Transcription/Translation/Interpretation pipeline output.
"""
from __future__ import annotations

from typing import Any, Tuple

import matplotlib

matplotlib.use("Agg")

import numpy as np
import matplotlib.pyplot as plt
from commonroad.visualization.draw_params import MPDrawParams
from commonroad.visualization.mp_renderer import MPRenderer


def _plot_limits(scenario: Any, margin: float = 15.0) -> Tuple[float, float, float, float]:
    positions = []
    for obstacle in scenario.dynamic_obstacles:
        states = [obstacle.initial_state]
        if obstacle.prediction and obstacle.prediction.trajectory:
            states += obstacle.prediction.trajectory.state_list
        for state in states:
            positions.append(state.position)
    for lanelet in scenario.lanelet_network.lanelets:
        positions.extend(lanelet.center_vertices.tolist())
    positions = np.array(positions)
    x_min, y_min = positions.min(axis=0) - margin
    x_max, y_max = positions.max(axis=0) + margin
    return x_min, x_max, y_min, y_max


def render_preview(scenario: Any, output_path: str) -> None:
    """Single frame at the scenario's first time step."""
    time_begin = min(obs.initial_state.time_step for obs in scenario.dynamic_obstacles)
    rnd = MPRenderer(figsize=(8, 12), plot_limits=list(_plot_limits(scenario)))
    scenario.draw(rnd, draw_params=MPDrawParams(time_begin=time_begin, time_end=time_begin))
    rnd.render(filename=output_path)
    plt.close(rnd.f)


def render_replay(scenario: Any, output_path: str) -> int:
    """Full tick-by-tick GIF. Returns the number of frames written."""
    time_steps = [obs.initial_state.time_step for obs in scenario.dynamic_obstacles]
    for obs in scenario.dynamic_obstacles:
        if obs.prediction and obs.prediction.trajectory:
            time_steps += [s.time_step for s in obs.prediction.trajectory.state_list]
    time_begin, time_end = min(time_steps), max(time_steps)

    rnd = MPRenderer(figsize=(8, 12), plot_limits=list(_plot_limits(scenario)))
    rnd.create_video(
        [scenario],
        output_path,
        draw_params=MPDrawParams(time_begin=time_begin, time_end=time_end),
    )
    return time_end - time_begin + 1
