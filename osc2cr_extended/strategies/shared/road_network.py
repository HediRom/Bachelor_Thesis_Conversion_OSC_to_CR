"""
road_network.py
================
Fixes a coordinate-frame mismatch between esmini's simulated obstacle
trajectories and the lanelet network that Osc2CrConverter builds from the
same scenario's .xodr file (via crdesigner's opendrive_to_commonroad).

Osc2CrConverter._create_basic_scenario() already calls
opendrive_to_commonroad() using crdesigner's default OpenDriveConfig, which
re-projects lanelet geometry from the .xodr's <geoReference> CDATA PROJ
string (e.g. "+proj=utm ...") into crdesigner's own default projection
(pseudo-Mercator). esmini, however, reports obstacle states in the road's
raw local reference-line frame and never applies that re-projection. The
two therefore land ~500 km apart in absolute coordinates, so the lanelet
network silently falls outside any plot limited to the obstacles' extent
even though it was converted correctly.

Call disable_lanelet_geo_reprojection() once, before the first
Osc2CrConverter/opendrive_to_commonroad call in the process, to keep
lanelet coordinates in the same local frame the .xodr defines (and esmini
reports positions in) so the two line up again.
"""
from __future__ import annotations


def disable_lanelet_geo_reprojection() -> None:
    from crdesigner.common.config.opendrive_config import open_drive_config
    open_drive_config.proj_string_odr = None
