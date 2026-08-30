# OpenSCENARIO → CommonRoad conversion benchmark

Generated 2026-08-18T08:07:05+00:00 · Python 3.11.6 · Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.31

`dt = 0.1 s` · 1 run(s) per scenario (median reported) · imports warmed up in 5.215 s · each conversion in its own interpreter


## Summary

- **54/72** scenarios converted (18 failed)
- **292.28 s** total, of which **247.52 s** is the existing converter (esmini simulation + CommonRoad construction)
- **1.627 s** total spent preserving triggers — the contribution of this tool
- **135 events / 132 conditions** recovered (239/240 of the `<Condition>` elements in the source files)
- **74** event fires reconstructed from real predicates, plus 3 unconditional fires from events whose conditions could not be parsed


## Per-scenario timing

| Scenario | Total [s] | Converter [s] | Triggers [s] | Write [s] | Trigger overhead |
| --- | ---: | ---: | ---: | ---: | ---: |
| `acc-test` | 50.24 | 49.81 | 0.026 | 0.400 | 0.1% |
| `acc-toggle` | 3.15 | 2.48 | 0.024 | 0.641 | 1.0% |
| `alks-test` | 1.54 | 1.15 | 0.015 | 0.375 | 1.3% |
| `alks_cut-in` | 1.43 | 1.07 | 0.004 | 0.359 | 0.4% |
| `alks_cut-out` | 1.50 | 1.09 | 0.023 | 0.380 | 2.1% |
| `alks_decelerate` | 1.43 | 1.05 | 0.004 | 0.369 | 0.4% |
| `alks_pedestrian` | 1.48 | 1.09 | 0.004 | 0.382 | 0.4% |
| `alks_r157_cut_in_quick_brake` | 1.45 | 1.07 | 0.005 | 0.373 | 0.5% |
| `controller_test` | 1.77 | 1.35 | 0.039 | 0.380 | 2.9% |
| `cut-in_cr` | 3.31 | 2.63 | 0.020 | 0.658 | 0.8% |
| `cut-in_external` | 7.13 | 5.47 | 0.023 | 1.631 | 0.4% |
| `cut-in_interactive` | 7.05 | 5.48 | 0.023 | 1.547 | 0.4% |
| `cut-in_simple` | 0.86 | 0.66 | 0.006 | 0.195 | 0.9% |
| `cut-in_sloppy` | 5.83 | 4.37 | 0.012 | 1.449 | 0.3% |
| `cut-in_sumo` | 16.29 | 14.09 | 0.141 | 2.055 | 1.0% |
| `cut-in_visibility` | 5.91 | 4.33 | 0.015 | 1.566 | 0.3% |
| `distance_test` | 1.71 | 1.39 | 0.035 | 0.281 | 2.5% |
| `drive_when_close` | 2.11 | 1.69 | 0.014 | 0.404 | 0.8% |
| `drop-bike` | 0.99 | 0.74 | 0.007 | 0.243 | 0.9% |
| `follow_ghost` | 5.79 | 4.27 | 0.009 | 1.519 | 0.2% |
| `follow_reference` | 2.33 | 1.92 | 0.024 | 0.379 | 1.3% |
| `follow_reference_interactive` | 1.44 | 1.16 | 0.015 | 0.261 | 1.3% |
| `highway_merge` | 0.86 | 0.54 | 0.088 | 0.227 | 16.3% |
| `highway_merge_advanced` | 4.47 | 3.20 | 0.118 | 1.149 | 3.7% |
| `keep_lateral_distance` | 7.44 | 5.74 | 0.034 | 1.660 | 0.6% |
| `keep_lateral_distance_external` | 5.46 | 3.96 | 0.007 | 1.489 | 0.2% |
| `lane-change_clothoid_based_trajectory` | 1.41 | 1.02 | 0.005 | 0.383 | 0.5% |
| `lane_change` | 1.42 | 1.11 | 0.010 | 0.297 | 0.9% |
| `lane_change_crest` | 1.02 | 0.75 | 0.018 | 0.253 | 2.5% |
| `lane_change_simple` | 1.87 | 1.40 | 0.036 | 0.433 | 2.6% |
| `left-hand-traffic_by_heading` | 6.74 | 5.08 | 0.016 | 1.649 | 0.3% |
| `left-hand-traffic_using_road_rule` | 5.69 | 3.84 | 0.005 | 1.851 | 0.1% |
| `long_dist_action_with_jerk` | 1.82 | 1.36 | 0.009 | 0.449 | 0.7% |
| `ltap-od-relative-speed` | 3.38 | 2.64 | 0.040 | 0.700 | 1.5% |
| `ltap-od` | 4.00 | 3.32 | 0.018 | 0.655 | 0.6% |
| `override_bb` | 2.08 | 1.61 | 0.008 | 0.458 | 0.5% |
| `parking_lot` | 0.95 | 0.76 | 0.008 | 0.179 | 1.1% |
| `pedestrian` | 50.85 | 50.25 | 0.008 | 0.587 | 0.0% |
| `pedestrian_collision` | 2.10 | 1.49 | 0.008 | 0.593 | 0.5% |
| `pedestrian_traj_synch` | 1.89 | 1.43 | 0.007 | 0.449 | 0.5% |
| `routing-test` | 8.87 | 6.65 | 0.150 | 2.073 | 2.3% |
| `slow-lead-vehicle` | 0.37 | 0.32 | 0.004 | 0.037 | 1.3% |
| `speed-profile` | 1.55 | 1.14 | 0.007 | 0.403 | 0.6% |
| `straight_500m` | 2.51 | 1.88 | 0.009 | 0.616 | 0.5% |
| `straight_500m_pedestrian` | 2.29 | 1.59 | 0.106 | 0.598 | 6.7% |
| `synch_with_steady_state` | 8.33 | 6.31 | 0.146 | 1.874 | 2.3% |
| `synchronize` | 15.44 | 12.98 | 0.034 | 2.421 | 0.3% |
| `traffic_lights` | 2.88 | 2.30 | 0.043 | 0.535 | 1.9% |
| `trailers` | 8.65 | 6.51 | 0.009 | 2.131 | 0.1% |
| `trajectory-test` | 2.18 | 1.69 | 0.014 | 0.473 | 0.8% |
| `truck_with_rotating_axle` | 0.53 | 0.47 | 0.006 | 0.057 | 1.3% |
| `velodrome` | 5.56 | 4.08 | 0.056 | 1.422 | 1.4% |
| `pedestrian_collision_udp` | 3.04 | 2.35 | 0.105 | 0.586 | 4.5% |
| `pedestrian_udp` | 1.87 | 1.42 | 0.006 | 0.434 | 0.4% |


## What was recovered

| Scenario | Actors | Lanelets | Steps | Duration [s] | Events | Conditions | C mapped | C skipped | D fires |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `acc-test` | 2 | 2 | 600 | 59.9 | 5 | 5 | 5 | 0 | 5 |
| `acc-toggle` | 2 | 2 | 600 | 59.9 | 4 | 4 | 2 | 3 | 3 |
| `alks-test` | 2 | 2 | 283 | 28.2 | 5 | 5 | 5 | 1 | 5 |
| `alks_cut-in` | 2 | 4 | 101 | 10.0 | 1 | 1 | 1 | 1 | 0 |
| `alks_cut-out` | 3 | 4 | 101 | 10.0 | 1 | 1 | 1 | 1 | 1 |
| `alks_decelerate` | 2 | 4 | 101 | 10.0 | 1 | 1 | 2 | 0 | 1 |
| `alks_pedestrian` | 2 | 4 | 101 | 10.0 | 1 | 1 | 2 | 0 | 1 |
| `alks_r157_cut_in_quick_brake` | 2 | 4 | 100 | 9.9 | 2 | 2 | 2 | 3 | 1 |
| `controller_test` | 1 | 2 | 600 | 59.9 | 5 | 5 | 7 | 1 | 5 |
| `cut-in_cr` | 2 | 2 | 600 | 59.9 | 3 | 2 | 1 | 3 | 3 (1 uncond.) |
| `cut-in_external` | 2 | 6 | 600 | 59.9 | 3 | 3 | 2 | 4 | 0 |
| `cut-in_interactive` | 2 | 6 | 600 | 59.9 | 3 | 3 | 2 | 4 | 0 |
| `cut-in_simple` | 2 | 2 | 102 | 10.1 | 2 | 2 | 2 | 3 | 2 |
| `cut-in_sloppy` | 2 | 6 | 276 | 27.5 | 3 | 3 | 1 | 4 | 2 |
| `cut-in_sumo` | 8 | 6 | 401 | 40.0 | 3 | 3 | 2 | 1 | 3 |
| `cut-in_visibility` | 2 | 6 | 226 | 22.5 | 6 | 6 | 4 | 4 | 5 |
| `distance_test` | 2 | 0 | 600 | 59.9 | 2 | 1 | 1 | 2 | 1 (1 uncond.) |
| `drive_when_close` | 2 | 4 | 401 | 40.0 | 2 | 2 | 0 | 2 | 1 |
| `drop-bike` | 4 | 2 | 101 | 10.0 | 1 | 1 | 3 | 0 | 1 |
| `follow_ghost` | 2 | 6 | 196 | 19.5 | 5 | 5 | 3 | 3 | 0 |
| `follow_reference` | 1 | 4 | 600 | 59.9 | 6 | 5 | 1 | 6 | 1 |
| `follow_reference_interactive` | 2 | 0 | 600 | 59.9 | 1 | 1 | 2 | 0 | 1 |
| `highway_merge` | 6 | 0 | 143 | 14.2 | 3 | 3 | 2 | 3 | 1 |
| `highway_merge_advanced` | 6 | 0 | 600 | 59.9 | 3 | 5 | 2 | 5 | 3 |
| `keep_lateral_distance` | 2 | 6 | 600 | 59.9 | 6 | 6 | 3 | 5 | 3 |
| `keep_lateral_distance_external` | 2 | 6 | 131 | 13.0 | 3 | 3 | 5 | 1 | 3 |
| `lane-change_clothoid_based_trajectory` | 1 | 4 | 81 | 8.0 | 3 | 3 | 3 | 2 | 1 |
| `lane_change` | 2 | 2 | 171 | 17.0 | 5 | 5 | 2 | 4 | 2 |
| `lane_change_crest` | 3 | 2 | 121 | 12.0 | 1 | 1 | 1 | 2 | 1 |
| `lane_change_simple` | 1 | 2 | 600 | 59.9 | 6 | 5 | 1 | 3 | 2 |
| `left-hand-traffic_by_heading` | 2 | 6 | 600 | 59.9 | 1 | 1 | 1 | 2 | 1 |
| `left-hand-traffic_using_road_rule` | 2 | 6 | 98 | 9.7 | 1 | 1 | 1 | 2 | 1 |
| `long_dist_action_with_jerk` | 3 | 4 | 201 | 20.0 | 2 | 2 | 2 | 2 | 2 |
| `ltap-od-relative-speed` | 2 | 28 | 600 | 59.9 | 1 | 1 | 2 | 2 | 0 |
| `ltap-od` | 2 | 28 | 600 | 59.9 | 1 | 1 | 2 | 2 | 0 |
| `override_bb` | 1 | 4 | 600 | 59.9 | 0 | 0 | 1 | 0 | 0 |
| `parking_lot` | 1 | 0 | 600 | 59.9 | 0 | 0 | 2 | 0 | 0 |
| `pedestrian` | 1 | 28 | 600 | 59.9 | 0 | 0 | 0 | 0 | 0 |
| `pedestrian_collision` | 2 | 28 | 93 | 9.2 | 3 | 3 | 1 | 4 | 2 |
| `pedestrian_traj_synch` | 2 | 28 | 107 | 10.6 | 3 | 2 | 3 | 1 | 3 (1 uncond.) |
| `routing-test` | 1 | 112 | 474 | 47.3 | 2 | 2 | 1 | 3 | 2 |
| `slow-lead-vehicle` | 2 | 0 | 114 | 11.3 | 1 | 1 | 1 | 2 | 0 |
| `speed-profile` | 2 | 4 | 141 | 14.0 | 2 | 2 | 3 | 0 | 2 |
| `straight_500m` | 2 | 4 | 301 | 30.0 | 0 | 0 | 2 | 0 | 0 |
| `straight_500m_pedestrian` | 4 | 4 | 301 | 30.0 | 0 | 0 | 2 | 0 | 0 |
| `synch_with_steady_state` | 2 | 112 | 148 | 14.7 | 2 | 2 | 1 | 2 | 0 |
| `synchronize` | 7 | 6 | 600 | 59.9 | 0 | 0 | 2 | 2 | 0 |
| `traffic_lights` | 3 | 28 | 301 | 30.0 | 9 | 9 | 2 | 6 | 1 |
| `trailers` | 7 | 112 | 151 | 15.0 | 0 | 0 | 1 | 0 | 0 |
| `trajectory-test` | 2 | 2 | 293 | 29.2 | 4 | 4 | 1 | 4 | 1 |
| `truck_with_rotating_axle` | 1 | 0 | 381 | 38.0 | 0 | 0 | 1 | 0 | 0 |
| `velodrome` | 3 | 3 | 466 | 46.5 | 2 | 2 | 0 | 3 | 2 |
| `pedestrian_collision_udp` | 2 | 28 | 600 | 59.9 | 3 | 3 | 1 | 4 | 0 |
| `pedestrian_udp` | 2 | 28 | 93 | 9.2 | 3 | 3 | 1 | 4 | 2 |


## Trigger coverage

How much of each source file's trigger logic the condition model actually represents. `Preserved` is parsed conditions over `<Condition>` elements in the `.xosc`.

This column matters for reading the one above: a dropped condition leaves its event with an empty start trigger, and an empty trigger is unconditionally true in OpenSCENARIO — so the event fires on the first step and looks like a reconstructed trigger. Those fires are counted separately as *uncond.* and are not evidence of recovered reactivity.

| Scenario | Preserved | % | Unsupported condition types |
| --- | ---: | ---: | --- |
| `acc-test` | 6/6 | 100.0% | — |
| `acc-toggle` | 5/5 | 100.0% | — |
| `alks-test` | 7/7 | 100.0% | — |
| `alks_cut-in` | 2/2 | 100.0% | — |
| `alks_cut-out` | 2/2 | 100.0% | — |
| `alks_decelerate` | 2/2 | 100.0% | — |
| `alks_pedestrian` | 2/2 | 100.0% | — |
| `alks_r157_cut_in_quick_brake` | 5/5 | 100.0% | — |
| `controller_test` | 8/8 | 100.0% | — |
| `cut-in_cr` | 4/4 | 100.0% | — |
| `cut-in_external` | 6/6 | 100.0% | — |
| `cut-in_interactive` | 6/6 | 100.0% | — |
| `cut-in_simple` | 5/5 | 100.0% | — |
| `cut-in_sloppy` | 5/5 | 100.0% | — |
| `cut-in_sumo` | 5/5 | 100.0% | — |
| `cut-in_visibility` | 8/8 | 100.0% | — |
| `distance_test` | 3/3 | 100.0% | — |
| `drive_when_close` | 3/3 | 100.0% | — |
| `drop-bike` | 3/3 | 100.0% | — |
| `follow_ghost` | 7/7 | 100.0% | — |
| `follow_reference` | 8/8 | 100.0% | — |
| `follow_reference_interactive` | 2/2 | 100.0% | — |
| `highway_merge` | 6/6 | 100.0% | — |
| `highway_merge_advanced` | 8/8 | 100.0% | — |
| `keep_lateral_distance` | 8/8 | 100.0% | — |
| `keep_lateral_distance_external` | 6/6 | 100.0% | — |
| `lane-change_clothoid_based_trajectory` | 5/5 | 100.0% | — |
| `lane_change` | 7/7 | 100.0% | — |
| `lane_change_crest` | 3/3 | 100.0% | — |
| `lane_change_simple` | 8/8 | 100.0% | — |
| `left-hand-traffic_by_heading` | 3/3 | 100.0% | — |
| `left-hand-traffic_using_road_rule` | 3/3 | 100.0% | — |
| `long_dist_action_with_jerk` | 4/4 | 100.0% | — |
| `ltap-od-relative-speed` | 4/4 | 100.0% | — |
| `ltap-od` | 4/5 | 80.0% | — |
| `override_bb` | 1/1 | 100.0% | — |
| `parking_lot` | 2/2 | 100.0% | — |
| `pedestrian` | 0/0 | 100.0% | — |
| `pedestrian_collision` | 5/5 | 100.0% | — |
| `pedestrian_traj_synch` | 4/4 | 100.0% | — |
| `routing-test` | 4/4 | 100.0% | — |
| `slow-lead-vehicle` | 3/3 | 100.0% | — |
| `speed-profile` | 3/3 | 100.0% | — |
| `straight_500m` | 2/2 | 100.0% | — |
| `straight_500m_pedestrian` | 2/2 | 100.0% | — |
| `synch_with_steady_state` | 4/4 | 100.0% | — |
| `synchronize` | 4/4 | 100.0% | — |
| `traffic_lights` | 11/11 | 100.0% | — |
| `trailers` | 1/1 | 100.0% | — |
| `trajectory-test` | 6/6 | 100.0% | — |
| `truck_with_rotating_axle` | 1/1 | 100.0% | — |
| `velodrome` | 3/3 | 100.0% | — |
| `pedestrian_collision_udp` | 5/5 | 100.0% | — |
| `pedestrian_udp` | 5/5 | 100.0% | — |


## Trigger-preservation breakdown

Sub-stages of the trigger-preservation cost, in milliseconds.


| Scenario | Parse | Transcr. | Transl. | Interp. build | Interp. replay | Timeline | Merge |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `acc-test` | 1.6 | 0.0 | 0.0 | 0.0 | 14.2 | 9.5 | 0.5 |
| `acc-toggle` | 1.7 | 0.0 | 0.0 | 0.0 | 13.2 | 8.9 | 0.5 |
| `alks-test` | 1.8 | 0.0 | 0.0 | 0.0 | 7.0 | 5.4 | 0.6 |
| `alks_cut-in` | 1.2 | 0.0 | 0.0 | 0.0 | 1.3 | 1.2 | 0.4 |
| `alks_cut-out` | 1.4 | 0.0 | 0.0 | 0.0 | 19.4 | 1.4 | 0.4 |
| `alks_decelerate` | 1.2 | 0.0 | 0.0 | 0.0 | 1.3 | 1.1 | 0.2 |
| `alks_pedestrian` | 1.2 | 0.0 | 0.0 | 0.0 | 1.4 | 1.2 | 0.4 |
| `alks_r157_cut_in_quick_brake` | 1.4 | 0.0 | 0.0 | 0.0 | 1.6 | 1.7 | 0.3 |
| `controller_test` | 1.6 | 0.0 | 0.0 | 0.0 | 12.8 | 24.0 | 0.5 |
| `cut-in_cr` | 1.3 | 0.0 | 0.0 | 0.0 | 9.8 | 8.2 | 0.4 |
| `cut-in_external` | 1.5 | 0.0 | 0.0 | 0.0 | 10.8 | 9.7 | 0.8 |
| `cut-in_interactive` | 1.6 | 0.0 | 0.0 | 0.0 | 10.7 | 10.3 | 0.5 |
| `cut-in_simple` | 1.5 | 0.0 | 0.0 | 0.0 | 1.9 | 1.9 | 0.4 |
| `cut-in_sloppy` | 1.8 | 0.0 | 0.0 | 0.0 | 5.4 | 4.4 | 0.4 |
| `cut-in_sumo` | 1.7 | 0.0 | 0.1 | 0.1 | 124.7 | 13.3 | 0.6 |
| `cut-in_visibility` | 1.9 | 0.0 | 0.0 | 0.0 | 7.4 | 5.2 | 0.5 |
| `distance_test` | 3.0 | 0.0 | 0.1 | 0.0 | 21.7 | 9.6 | 0.8 |
| `drive_when_close` | 1.4 | 0.0 | 0.0 | 0.0 | 6.7 | 5.5 | 0.4 |
| `drop-bike` | 1.9 | 0.0 | 0.0 | 0.0 | 2.0 | 2.4 | 0.3 |
| `follow_ghost` | 1.6 | 0.0 | 0.0 | 0.0 | 2.8 | 3.5 | 0.5 |
| `follow_reference` | 1.5 | 0.0 | 0.0 | 0.0 | 12.8 | 9.3 | 0.5 |
| `follow_reference_interactive` | 1.2 | 0.0 | 0.0 | 0.0 | 7.5 | 6.3 | 0.4 |
| `highway_merge` | 2.0 | 0.0 | 0.0 | 0.0 | 80.7 | 5.0 | 0.7 |
| `highway_merge_advanced` | 2.4 | 0.0 | 0.0 | 0.0 | 95.3 | 20.1 | 0.7 |
| `keep_lateral_distance` | 2.7 | 0.1 | 0.1 | 0.0 | 18.0 | 12.6 | 0.6 |
| `keep_lateral_distance_external` | 1.5 | 0.0 | 0.0 | 0.0 | 2.5 | 2.3 | 0.4 |
| `lane-change_clothoid_based_trajectory` | 1.6 | 0.0 | 0.0 | 0.0 | 1.9 | 1.2 | 0.5 |
| `lane_change` | 1.7 | 0.0 | 0.0 | 0.0 | 4.3 | 3.2 | 0.6 |
| `lane_change_crest` | 1.5 | 0.0 | 0.0 | 0.0 | 2.0 | 14.3 | 0.4 |
| `lane_change_simple` | 1.9 | 0.0 | 0.0 | 0.0 | 23.9 | 9.3 | 0.7 |
| `left-hand-traffic_by_heading` | 1.2 | 0.0 | 0.0 | 0.0 | 7.2 | 7.4 | 0.4 |
| `left-hand-traffic_using_road_rule` | 1.8 | 0.0 | 0.0 | 0.0 | 1.7 | 1.4 | 0.4 |
| `long_dist_action_with_jerk` | 1.8 | 0.0 | 0.0 | 0.0 | 3.7 | 3.2 | 0.4 |
| `ltap-od-relative-speed` | 1.7 | 0.0 | 0.0 | 0.0 | 6.9 | 30.1 | 0.6 |
| `ltap-od` | 1.6 | 0.0 | 0.0 | 0.0 | 7.8 | 8.5 | 0.6 |
| `override_bb` | 1.1 | 0.0 | 0.0 | 0.0 | 2.8 | 3.4 | 0.3 |
| `parking_lot` | 0.8 | 0.0 | 0.0 | 0.0 | 2.5 | 4.7 | 0.3 |
| `pedestrian` | 1.3 | 0.0 | 0.0 | 0.1 | 4.3 | 2.1 | 0.4 |
| `pedestrian_collision` | 2.8 | 0.0 | 0.1 | 0.0 | 2.5 | 2.0 | 0.6 |
| `pedestrian_traj_synch` | 2.1 | 0.0 | 0.0 | 0.0 | 2.1 | 2.1 | 0.7 |
| `routing-test` | 1.5 | 0.0 | 0.0 | 0.0 | 142.0 | 5.7 | 0.6 |
| `slow-lead-vehicle` | 1.1 | 0.0 | 0.0 | 0.0 | 1.4 | 1.4 | 0.2 |
| `speed-profile` | 1.4 | 0.0 | 0.0 | 0.0 | 2.1 | 2.8 | 0.4 |
| `straight_500m` | 1.4 | 0.0 | 0.0 | 0.0 | 2.5 | 4.3 | 0.4 |
| `straight_500m_pedestrian` | 1.4 | 0.0 | 0.0 | 0.0 | 3.5 | 100.3 | 0.4 |
| `synch_with_steady_state` | 1.6 | 0.0 | 0.0 | 0.0 | 2.3 | 141.4 | 0.4 |
| `synchronize` | 2.0 | 0.0 | 0.0 | 0.0 | 14.0 | 17.4 | 0.7 |
| `traffic_lights` | 2.9 | 0.0 | 0.0 | 0.0 | 32.4 | 7.3 | 0.8 |
| `trailers` | 1.4 | 0.0 | 0.0 | 0.0 | 3.3 | 3.6 | 0.4 |
| `trajectory-test` | 2.1 | 0.0 | 0.0 | 0.0 | 6.3 | 4.9 | 0.7 |
| `truck_with_rotating_axle` | 0.8 | 0.0 | 0.0 | 0.0 | 2.1 | 2.5 | 0.4 |
| `velodrome` | 1.3 | 0.0 | 0.0 | 0.0 | 46.4 | 7.3 | 0.4 |
| `pedestrian_collision_udp` | 1.8 | 0.0 | 0.0 | 0.0 | 10.6 | 92.1 | 0.6 |
| `pedestrian_udp` | 1.9 | 0.0 | 0.0 | 0.0 | 1.9 | 1.9 | 0.5 |


## Failures

| Scenario | Reason |
| --- | --- |
| `bicycle_fall_over` | esmini produced no time steps, so every obstacle got an empty trajectory. Cause: its storyboard declares no <Act> (Init-only scenario), and the bundled esmini v2.29.3 quits such scenarios at t=0 ("All acts are done, quit now") instead of honouring the storyboard StopTrigger. The converter vendors esmini v2.29.3; this scenario needs a newer one. Nothing is wrong with the .xosc — it runs under the esmini checkout in esmini/bin. |
| `car_walk` | converter failed: SIMULATION_FAILED_CREATING_OUTPUT |
| `cut-in` | esmini crashed the interpreter (SIGSEGV) |
| `cut-in_environment` | esmini crashed the interpreter (SIGSEGV) |
| `cut-in_parameter_set` | converter failed: SCENARIO_FILE_IS_PARAMETER_VALUE_DISTRIBUTION |
| `follow_trajectory_by_front_axle` | esmini produced no time steps, so every obstacle got an empty trajectory. Cause: its storyboard declares no <Act> (Init-only scenario), and the bundled esmini v2.29.3 quits such scenarios at t=0 ("All acts are done, quit now") instead of honouring the storyboard StopTrigger. The converter vendors esmini v2.29.3; this scenario needs a newer one. Nothing is wrong with the .xosc — it runs under the esmini checkout in esmini/bin. |
| `highway_driver` | esmini produced no time steps, so every obstacle got an empty trajectory. Cause: its storyboard declares no <Act> (Init-only scenario), and the bundled esmini v2.29.3 quits such scenarios at t=0 ("All acts are done, quit now") instead of honouring the storyboard StopTrigger; it uses controller(s) NaturalDriver, added to esmini after the bundled v2.29.3. The converter vendors esmini v2.29.3; this scenario needs a newer one. Nothing is wrong with the .xosc — it runs under the esmini checkout in esmini/bin. |
| `lane-change_clothoid_spline_based_trajectory` | converter failed: SIMULATION_FAILED_CREATING_OUTPUT |
| `lane-change_trajectory_wp` | esmini produced no time steps, so every obstacle got an empty trajectory. Cause: its storyboard declares no <Act> (Init-only scenario), and the bundled esmini v2.29.3 quits such scenarios at t=0 ("All acts are done, quit now") instead of honouring the storyboard StopTrigger. The converter vendors esmini v2.29.3; this scenario needs a newer one. Nothing is wrong with the .xosc — it runs under the esmini checkout in esmini/bin. |
| `light_state` | converter failed: SIMULATION_FAILED_CREATING_OUTPUT |
| `offroad_follower` | esmini produced no time steps, so every obstacle got an empty trajectory. Cause: its storyboard declares no <Act> (Init-only scenario), and the bundled esmini v2.29.3 quits such scenarios at t=0 ("All acts are done, quit now") instead of honouring the storyboard StopTrigger. The converter vendors esmini v2.29.3; this scenario needs a newer one. Nothing is wrong with the .xosc — it runs under the esmini checkout in esmini/bin. |
| `sumo-test` | esmini produced no time steps, so every obstacle got an empty trajectory. Cause: its storyboard declares no <Act> (Init-only scenario), and the bundled esmini v2.29.3 quits such scenarios at t=0 ("All acts are done, quit now") instead of honouring the storyboard StopTrigger. The converter vendors esmini v2.29.3; this scenario needs a newer one. Nothing is wrong with the .xosc — it runs under the esmini checkout in esmini/bin. |
| `swarm` | esmini crashed the interpreter (SIGSEGV) |
| `trailer_connect` | converter failed: SIMULATION_FAILED_CREATING_OUTPUT |
| `tunnels` | esmini produced no time steps, so every obstacle got an empty trajectory. Cause: its storyboard declares no <Act> (Init-only scenario), and the bundled esmini v2.29.3 quits such scenarios at t=0 ("All acts are done, quit now") instead of honouring the storyboard StopTrigger. The converter vendors esmini v2.29.3; this scenario needs a newer one. Nothing is wrong with the .xosc — it runs under the esmini checkout in esmini/bin. |
| `two_plus_one_road` | converter failed: SIMULATION_FAILED_CREATING_OUTPUT |
| `drop-bike-udp` | timed out after 900s |
| `follow_trajectory` | converter failed: SIMULATION_FAILED_CREATING_OUTPUT |


## Reading the numbers

`Converter [s]` is the cost that already exists today — esmini replays the storyboard and the CommonRoad scenario is built from the resulting states.  `Triggers [s]` is everything this tool adds to keep the conditional structure: parsing the storyboard, running Transcription/Translation/Interpretation, replaying the conditions against the converted trajectories, and building the per-step condition timeline the viewer draws.

The overhead column is `Triggers / Converter`.  It stays small because the expensive part of conversion is the simulation, while trigger preservation is XML parsing and arithmetic over an already-computed trajectory.
