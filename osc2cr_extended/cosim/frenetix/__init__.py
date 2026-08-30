"""
Frenetix-Motion-Planner as the ego driver for the co-simulation.

Same loop as :mod:`osc2cr_extended.cosim.cosimulation`, different planner.  Kept
apart because Frenetix and the reactive planner pin incompatible versions of
commonroad-io and the drivability checker — converting the scenario is therefore
a separate step (:mod:`~.convert_scenario`) run in the converter's environment,
and this package only reads the CommonRoad XML it produced.

Requires the ``frenetix`` extra.
"""
