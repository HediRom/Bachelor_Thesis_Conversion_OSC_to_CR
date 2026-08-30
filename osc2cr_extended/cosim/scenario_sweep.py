"""Parameter sweep over an OpenSCENARIO scenario family.

For each combination of ParameterDeclaration values in PARAM_GRID, runs a full
co-simulation, scores the resulting ego trajectory, and writes a CSV summary.

Designed to be run in the `cr-osc-converter` conda env (same as cosimulation.py).

Usage:
    conda run -n cr-osc-converter python scenario_sweep.py
    conda run -n cr-osc-converter python scenario_sweep.py \\
        --xosc ../esmini/resources/xosc/keep_lateral_distance_external.xosc \\
        --desired-velocity 8.0 --output sweep_results/
"""

import argparse
import csv
import os
import re
import sys
import tempfile
from itertools import product

import numpy as np

THIS_DIR = os.path.dirname(os.path.realpath(__file__))

from osc2cr_extended import paths

from osc2cr_extended.cosim.cosimulation import CoSimulation

DEFAULT_XOSC = str(paths.ESMINI_XOSC / "keep_lateral_distance_external.xosc")
DEFAULT_OUTPUT = str(paths.OUTPUT_DIR / "sweep_results")

# Axes of the parameter grid.  Keys must match ParameterDeclaration names in the xosc.
PARAM_GRID = {
    "EgoSpeed":  [72, 90, 108],   # km/h  (20 / 25 / 30 m/s)
    "EgoStartS": [30, 50, 70],    # m along road
}


def apply_params(xosc_path: str, params: dict) -> str:
    """Return a path to a temporary xosc file with ParameterDeclaration values overridden.

    Uses text-level replacement so that XML comments, the declaration header, and
    attribute ordering are preserved verbatim.
    """
    with open(xosc_path, "r", encoding="utf-8") as f:
        text = f.read()

    for name, value in params.items():
        # Matches:  name="EgoSpeed"  ...  value="108"
        # and replaces the value="..." of the ParameterDeclaration with that name.
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


def score_result(result, planning_problem, dt: float) -> dict:
    """Compute scalar benchmark metrics from a CoSimulationResult."""
    states = result.ego_trajectory.state_list
    n = len(states)

    last = states[-1]
    goal_reached = int(planning_problem.goal.is_reached(last))

    velocities = np.array([s.velocity for s in states])

    accels = np.array(
        [s.acceleration for s in states
         if hasattr(s, "acceleration") and s.acceleration is not None]
    )
    if len(accels) > 1:
        jerk = np.diff(accels) / dt
        rms_jerk = float(np.sqrt(np.mean(jerk ** 2)))
    else:
        rms_jerk = float("nan")

    if result.overtaker_states:
        pairs = min(n, len(result.overtaker_states))
        dists = [
            float(np.linalg.norm(states[i].position - result.overtaker_states[i].position))
            for i in range(pairs)
        ]
        min_dist = min(dists)
    else:
        min_dist = float("nan")

    return {
        "n_steps":               n,
        "duration_s":            round(n * dt, 2),
        "goal_reached":          goal_reached,
        "mean_velocity_ms":      round(float(np.mean(velocities)), 3),
        "rms_jerk":              round(rms_jerk, 4),
        "min_dist_to_overtaker": round(min_dist, 3),
    }


def params_slug(params: dict) -> str:
    return "_".join(f"{k}{v}" for k, v in params.items())


def run_sweep(xosc_path: str, param_grid: dict,
              desired_velocity: float, output_dir: str) -> list[dict]:
    os.makedirs(output_dir, exist_ok=True)

    keys   = list(param_grid.keys())
    combos = [dict(zip(keys, vals)) for vals in product(*param_grid.values())]
    records = []

    for i, params in enumerate(combos, 1):
        print(f"\n[sweep] {i}/{len(combos)}  params={params}")
        tmp_xosc = apply_params(xosc_path, params)
        try:
            cosim  = CoSimulation(xosc_path=tmp_xosc, desired_velocity=desired_velocity)
            result = cosim.run()
            dt     = cosim.config.planning.dt
            pp     = cosim.planning_problem

            out_dir = os.path.join(output_dir, params_slug(params))
            result.write_solution(output_dir=out_dir)
            result.write_combined_scenario(path=os.path.join(out_dir, "cosim_result.xml"))

            metrics = score_result(result, pp, dt)
        except Exception as exc:
            print(f"[sweep]   FAILED: {exc}")
            metrics = {
                "n_steps": -1, "duration_s": -1, "goal_reached": 0,
                "mean_velocity_ms": float("nan"),
                "rms_jerk": float("nan"),
                "min_dist_to_overtaker": float("nan"),
            }
        finally:
            os.unlink(tmp_xosc)

        record = {**params, **metrics}
        records.append(record)
        print(f"[sweep]   → {metrics}")

    csv_path = os.path.join(output_dir, "sweep_results.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)

    print(f"\n[sweep] {len(records)} runs written to {csv_path}")
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xosc", default=DEFAULT_XOSC,
                        help="OpenSCENARIO file (default: keep_lateral_distance_external.xosc)")
    parser.add_argument("--desired-velocity", type=float, default=8.0,
                        help="planner target speed m/s (default: 8.0)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help="output directory for CSV and per-run results")
    args = parser.parse_args()
    run_sweep(args.xosc, PARAM_GRID, args.desired_velocity, args.output)


if __name__ == "__main__":
    main()
