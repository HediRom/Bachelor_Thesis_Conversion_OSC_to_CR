# Co-simulation evaluation

Generated 2026-08-18 · 54 converted bundles (every scenario that survived the
[conversion benchmark](benchmark.md)) · both legs, isolated subprocesses per
bundle · `python -m osc2cr_extended cosim osc2cr_output/* --driver {esmini,planner}`

## Method, briefly

Every bundle is replayed closed-loop twice:

| leg | who drives the ego | answers |
|---|---|---|
| **`esmini`** (validation) | esmini, scenario as authored | is *our* condition implementation right? |
| **`planner`** (evaluation) | `commonroad-rp`, ego externalised | what does this scenario do to this planner? |

Both legs run esmini's own native condition-firing callback alongside our
`EdgeAwareExecutor`, on the *same* stepped world, and diff the two streams —
esmini is a **differential oracle**, not a second implementation to trust
blindly. That is true in the planner leg too: esmini still drives every other
actor and still observes the ego (now planner-controlled), so "agreement %"
there checks whether our model stays correct once the trajectory it is
evaluating is no longer the authored one. The validation leg is the
precondition for trusting the evaluation leg — a planner-driven fire time is
meaningless if the condition model producing it disagrees with the reference
player.

Each condition gets one verdict per bundle:

| verdict | meaning |
|---|---|
| `agree` | same fire count, every time matched within 1.5·dt |
| `time_mismatch` | both fired, at least one time outside tolerance |
| `count_mismatch` | both fired, different number of times |
| `esmini_only` / `shadow_only` | one side fired, the other never did |
| `not_modelled` | esmini fired a condition we do not carry at all |
| `inconclusive_at_end` | fires confined to the run's final tick (can't be corroborated — the corroborating step never ran) |

A divergence on a condition declaring a `delay` is tagged `declares_delay` —
tagged, not excused (delay is a documented unmodelled feature) — and still
counts as a mismatch in the totals below. "Agreement %" is `agree / conclusive`,
i.e. `inconclusive_at_end` rows are removed from both numerator and denominator
rather than counted as failures.

Collision counts below are **steps during which the ego's bounding box
overlapped another entity's**, from esmini's own geometric collision check —
not a count of distinct crash events. Several scenarios (`alks_decelerate`,
`follow_reference_interactive`) run the ego in sustained contact with another
actor by design (ALKS/close-following regulatory tests), which is why some
counts run into the hundreds.

## Scope and top-line numbers

54/54 bundles produced an esmini-leg trace. 53/54 produced a planner-leg trace
— `left-hand-traffic_by_heading` crashed its child process with a nanobind
reference-counting fault before writing one (see *Notable findings* below).

| | esmini leg (validation) | planner leg (evaluation) |
|---|---:|---:|
| Bundles with a trace | 54 / 54 | 53 / 54 |
| Conditions compared | 169 | 88 |
| Agree | 104 | 63 |
| Conclusive | 155 | 85 |
| **Corpus agreement** | **67.1%** | **74.1%** |
| Delay-tagged divergences (documented, not a defect) | 26 | 9 |
| Genuinely open findings (below) | 37 | 15 |

Planner-leg outcomes, 53 attempted runs:

| status | count | meaning |
|---|---:|---|
| `goal-reached` | 17 | planner satisfied the planning problem mid-run |
| `completed` | 11 | ran the full length without a goal region to reach (no failure either) |
| `goal-already-satisfied` | 7 | goal region contained the initial state — **null result, nothing tested** |
| `infeasible` | 7 | planner found no feasible trajectory at some step |
| `failed` | 11 | route rejected, no lanelet network, or the run errored |

28/53 (53%) are genuine planner-driven completions. The rest are either null
results or non-completions, all reported as such rather than silently
dropped — consistent with §10.7 of the main report, which predicted several
of these exact failure classes from a smaller corpus.

## Esmini leg — validating the condition model, all 54 bundles

| Scenario | Compared | Agree | Conclusive | Agreement | Collisions |
| --- | ---: | ---: | ---: | ---: | --- |
| `acc-test` | 5 | 5 | 5 | 100.0% | — |
| `acc-toggle` | 4 | 2 | 4 | 50.0% | — |
| `alks-test` | 6 | 5 | 5 | 100.0% | — |
| `alks_cut-in` | 2 | 1 | 2 | 50.0% | 8 |
| `alks_cut-out` | 2 | 2 | 2 | 100.0% | — |
| `alks_decelerate` | 2 | 2 | 2 | 100.0% | 47 |
| `alks_pedestrian` | 2 | 2 | 2 | 100.0% | 7 |
| `alks_r157_cut_in_quick_brake` | 4 | 2 | 3 | 66.7% | — |
| `controller_test` | 7 | 6 | 6 | 100.0% | — |
| `cut-in_cr` | 3 | 2 | 3 | 66.7% | — |
| `cut-in_external` | 2 | 1 | 2 | 50.0% | — |
| `cut-in_interactive` | 2 | 1 | 2 | 50.0% | — |
| `cut-in_simple` | 5 | 4 | 4 | 100.0% | — |
| `cut-in_sloppy` | 5 | 3 | 4 | 75.0% | 3 |
| `cut-in_sumo` | 3 | 2 | 3 | 66.7% | — |
| `cut-in_visibility` | 8 | 6 | 7 | 85.7% | 7 |
| `distance_test` | 0 | 0 | 0 | n/a | — |
| `drive_when_close` | 1 | 1 | 1 | 100.0% | — |
| `drop-bike` | 3 | 3 | 3 | 100.0% | 2 |
| `follow_ghost` | 4 | 0 | 4 | 0.0% | — |
| `follow_reference` | 2 | 0 | 2 | 0.0% | — |
| `follow_reference_interactive` | 1 | 0 | 1 | 0.0% | 620 |
| `highway_merge` | 4 | 1 | 4 | 25.0% | — |
| `highway_merge_advanced` | 4 | 1 | 4 | 25.0% | — |
| `keep_lateral_distance` | 5 | 3 | 5 | 60.0% | — |
| `keep_lateral_distance_external` | 5 | 4 | 4 | 100.0% | — |
| `lane-change_clothoid_based_trajectory` | 5 | 2 | 5 | 40.0% | — |
| `lane_change` | 6 | 2 | 5 | 40.0% | — |
| `lane_change_crest` | 3 | 1 | 2 | 50.0% | — |
| `lane_change_simple` | 3 | 1 | 3 | 33.3% | — |
| `left-hand-traffic_by_heading` | 1 | 1 | 1 | 100.0% | — |
| `left-hand-traffic_using_road_rule` | 3 | 2 | 2 | 100.0% | — |
| `long_dist_action_with_jerk` | 4 | 2 | 4 | 50.0% | 201 |
| `ltap-od` | 2 | 1 | 2 | 50.0% | — |
| `ltap-od-relative-speed` | 2 | 1 | 2 | 50.0% | — |
| `override_bb` | 0 | 0 | 0 | n/a | — |
| `parking_lot` | 1 | 0 | 1 | 0.0% | — |
| `pedestrian` | 0 | 0 | 0 | n/a | — |
| `pedestrian_collision` | 5 | 3 | 4 | 75.0% | 6 |
| `pedestrian_collision_udp` | 1 | 1 | 1 | 100.0% | — |
| `pedestrian_traj_synch` | 4 | 4 | 4 | 100.0% | 6 |
| `pedestrian_udp` | 5 | 3 | 4 | 75.0% | 6 |
| `routing-test` | 4 | 3 | 3 | 100.0% | — |
| `slow-lead-vehicle` | 3 | 1 | 2 | 50.0% | — |
| `speed-profile` | 3 | 3 | 3 | 100.0% | — |
| `straight_500m` | 2 | 2 | 2 | 100.0% | 5 |
| `straight_500m_pedestrian` | 2 | 2 | 2 | 100.0% | 16 |
| `synch_with_steady_state` | 3 | 2 | 3 | 66.7% | 5 |
| `synchronize` | 3 | 1 | 3 | 33.3% | — |
| `traffic_lights` | 3 | 2 | 3 | 66.7% | — |
| `trailers` | 1 | 1 | 1 | 100.0% | — |
| `trajectory-test` | 5 | 2 | 5 | 40.0% | — |
| `truck_with_rotating_axle` | 1 | 1 | 1 | 100.0% | — |
| `velodrome` | 3 | 1 | 3 | 33.3% | — |

## Planner leg — evaluating commonroad-rp, all 54 bundles

| Scenario | Planner status | Agreement | Collisions | Voided groups |
| --- | --- | --- | ---: | ---: |
| `acc-test` | goal-already-satisfied | n/a | — | — |
| `acc-toggle` | goal-reached | 50.0% | — | 1 |
| `alks-test` | completed | 100.0% | — | — |
| `alks_cut-in` | completed | 100.0% | — | — |
| `alks_cut-out` | completed | 100.0% | — | — |
| `alks_decelerate` | completed | 100.0% | — | — |
| `alks_pedestrian` | completed | 100.0% | — | — |
| `alks_r157_cut_in_quick_brake` | completed | 66.7% | — | — |
| `controller_test` | completed | 100.0% | — | 1 |
| `cut-in_cr` | infeasible | 50.0% | — | — |
| `cut-in_external` | goal-already-satisfied | n/a | — | — |
| `cut-in_interactive` | goal-already-satisfied | n/a | — | — |
| `cut-in_simple` | goal-reached | 100.0% | — | — |
| `cut-in_sloppy` | goal-reached | 75.0% | 1 | — |
| `cut-in_sumo` | goal-reached | 50.0% | — | 1 |
| `cut-in_visibility` | goal-reached | 85.7% | — | — |
| `distance_test` | failed | 0.0% | — | — |
| `drive_when_close` | completed | 100.0% | — | — |
| `drop-bike` | goal-reached | 100.0% | 2 | — |
| `follow_ghost` | completed | 0.0% | — | 1 |
| `follow_reference` | goal-reached | 0.0% | — | 2 |
| `follow_reference_interactive` | failed | 0.0% | — | — |
| `highway_merge` | failed | 0.0% | — | — |
| `highway_merge_advanced` | failed | 0.0% | — | — |
| `keep_lateral_distance` | goal-reached | 75.0% | — | 1 |
| `keep_lateral_distance_external` | goal-already-satisfied | n/a | — | — |
| `lane-change_clothoid_based_trajectory` | failed | 0.0% | — | — |
| `lane_change` | failed | 0.0% | — | — |
| `lane_change_crest` | goal-reached | 50.0% | — | — |
| `lane_change_simple` | failed | 0.0% | — | — |
| `left-hand-traffic_by_heading` | crashed (nanobind) | n/a | — | — |
| `left-hand-traffic_using_road_rule` | goal-reached | 100.0% | — | — |
| `long_dist_action_with_jerk` | infeasible | n/a | 1 | 1 |
| `ltap-od` | goal-already-satisfied | n/a | — | — |
| `ltap-od-relative-speed` | goal-already-satisfied | n/a | — | — |
| `override_bb` | infeasible | n/a | — | — |
| `parking_lot` | failed | 0.0% | — | — |
| `pedestrian` | infeasible | n/a | — | — |
| `pedestrian_collision` | goal-reached | 66.7% | — | — |
| `pedestrian_collision_udp` | failed | 0.0% | — | — |
| `pedestrian_traj_synch` | goal-reached | 100.0% | — | 1 |
| `pedestrian_udp` | goal-reached | 66.7% | — | — |
| `routing-test` | infeasible | 100.0% | — | 1 |
| `slow-lead-vehicle` | failed | 0.0% | — | — |
| `speed-profile` | goal-reached | 100.0% | — | 1 |
| `straight_500m` | goal-reached | 100.0% | 2 | 1 |
| `straight_500m_pedestrian` | goal-reached | 100.0% | 5 | 1 |
| `synch_with_steady_state` | goal-reached | 50.0% | 2 | — |
| `synchronize` | goal-already-satisfied | n/a | — | — |
| `traffic_lights` | infeasible | 100.0% | — | 1 |
| `trailers` | completed | 100.0% | — | — |
| `trajectory-test` | completed | 40.0% | — | — |
| `truck_with_rotating_axle` | failed | 0.0% | — | — |
| `velodrome` | infeasible | n/a | — | — |

## Does the planner re-time the scenario?

46 events fire under both legs with a matching name. 10 shift by more than
0.05 s; the other 36 land at the same tick under both drivers. This is the
same conclusion §10.6 of the main report drew from 11 events in the curated
corpus — extended to 46, it holds: most of this corpus keeps the ego on a
predictable path, so a velocity-keeping planner reproduces esmini's authored
motion closely enough that triggers barely move. Where it does move, the
shift is 0.1–0.6 s, not a qualitative change in outcome.

| Scenario | Event | esmini [s] | planner [s] | shift |
| --- | --- | ---: | ---: | ---: |
| `cut-in_cr` | LaneChangeEvent | 3.2 | 3.8 | +0.6 |
| `cut-in_visibility` | CutInEvent | 7.7 | 8.3 | +0.6 |
| `cut-in_visibility` | OvertakerBrakeEvent | 9.2 | 9.8 | +0.6 |
| `cut-in_simple` | BrakeEvent | 9.1 | 8.6 | -0.5 |
| `left-hand-traffic_using_road_rule` | Lane change | 3.8 | 4.2 | +0.4 |
| `cut-in_simple` | CutInEvent | 6.6 | 6.4 | -0.2 |
| `pedestrian_collision` | ped_collide_event | 5.5 | 5.7 | +0.2 |
| `pedestrian_udp` | ped_collide_event | 5.5 | 5.7 | +0.2 |
| `cut-in_sloppy` | CutInEvent | 7.8 | 7.9 | +0.1 |
| `cut-in_sloppy` | OvertakerBrakeEvent | 13.5 | 13.6 | +0.1 |

## Safety: does the planner ever collide when the reference run didn't?

Planner-leg collisions occur in `cut-in_sloppy`, `drop-bike`,
`long_dist_action_with_jerk`, `straight_500m`, `straight_500m_pedestrian`,
`synch_with_steady_state` — **all six are a subset of the fourteen scenarios
that already show collisions in the esmini (as-authored) leg.** The planner
introduces no collision anywhere the reference run stayed clear. That is a
useful negative result: across this corpus, closing the loop with
`commonroad-rp` did not create a new safety failure the authored scenario
didn't already contain.

## Externalising the ego: what gets voided

13 of 54 bundles have a `ManeuverGroup` that commands the ego, which is voided
when the ego is externalised for the planner leg (`acc-toggle`,
`controller_test`, `cut-in_sumo`, `follow_ghost`, `follow_reference` (×2),
`keep_lateral_distance`, `long_dist_action_with_jerk`, `pedestrian_traj_synch`,
`routing-test`, `speed-profile`, `straight_500m`, `straight_500m_pedestrian`,
`traffic_lights`). That is a larger fraction (24%) than the 2/17 (12%) the
main report measured on the curated corpus — expected, since this corpus
pulls in more of esmini's own scenario library, which uses scripted ego
maneuvers more often than the converter's own test set does. Each voided
group is reported by name in the trace rather than silently dropped, per the
same declared-not-dropped discipline the condition model uses.

## Notable findings

**A repeated-refiring pattern outside the curated corpus.** Several
`count_mismatch` / `shadow_only` rows in the esmini-leg table show our
executor firing a condition on *every step* for tens to hundreds of
consecutive ticks where esmini reports it once or not at all:
`follow_reference_interactive`'s `start_trigger1` (esmini: 1 fire at 15.0s;
ours: ~470 fires, one per tick from 15.0 to 61.9s), `long_dist_action_with_jerk`'s
`triggerEgo` / `triggerego jerk` (esmini: 1 fire at 1.0s; ours: ~100 fires,
one per tick), `traffic_lights`'s `PositionCondition` (esmini: 1 fire at
3.5s; ours: ~72 fires from 3.6–10.7s), and `synchronize`'s `EndCondition2`
(shadow-only, ~400 fires). This is the same *class* of defect §10.3 of the
main report already fixed once (exhausted events kept reporting) — these
four scenarios sit outside the 13/17-scenario corpus that fix was validated
against, and the oracle shows the fix did not generalise to whatever these
conditions have in common. Worth a follow-up; not diagnosed here.

**A genuine no-coverage gap, tied back to the conversion benchmark.**
`synchronize` converts with **0 events / 0 conditions recovered** (see
[benchmark.md](benchmark.md)) — and the oracle confirms real trigger activity
exists that the taxonomy simply never sees: `Synchronize_NPC_Action_Condition`
fires four times in esmini and is `not_modelled` here, the one true
"we don't even attempt this" case in the whole run.

**The nanobind crash is a class, not a scenario-specific bug.**
`left-hand-traffic_by_heading` crashed its planner-leg child process with
"nanobind: this is likely caused by a reference counting issue in the binding
code" — the exact failure §10.7 of the main report documented for
`pedestrian` on the curated-13 corpus. In *this* run, `pedestrian` itself
completed (as `infeasible`, not a crash) while a different scenario hit the
nanobind fault instead. That is consistent with a flaky, memory-corruption-class
bug rather than something specific to either scenario.

**Two `esmini_only` items were already known-open.** `alks_cut-in`'s
`TimeToCollisionCondition` (esmini fires at 2.2s, we never do) and
`lane_change_crest`'s `CutInStartCondition` (4.2s vs our 4.5s) reproduce
exactly the two gaps §10.4 of the main report called "genuinely open" on the
curated-17 corpus — same scenarios, same numbers, now confirmed a second time
independently.

## Reproducing

```bash
cd ~/Bachelor_Conversion
BUNDLES=$(python3 -m osc2cr_extended list | awk '{print "osc2cr_output/"$1}')  # or a fixed list
python3 -m osc2cr_extended cosim $BUNDLES --driver esmini
python3 -m osc2cr_extended cosim $BUNDLES --driver planner
```

Each bundle gets `cosim_trace_esmini.json` / `cosim_trace_planner.json`
written next to `bundle.json`; this report is a hand-built aggregate over
those 107 trace files (`cosim_report.json` alongside this file has the raw
aggregate). Unlike `benchmark.py`, `cosim` has no built-in report generator —
the per-bundle console summary (`agreement : NN% (...)`, fired events,
collisions, voided groups) is the only structured output the tool itself
produces.
