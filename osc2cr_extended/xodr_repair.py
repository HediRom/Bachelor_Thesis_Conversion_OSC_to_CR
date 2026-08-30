"""
xodr_repair.py
==============
Opt-in repairs for OpenDRIVE files crdesigner's parser cannot read.

The problem
-----------
esmini and CommonRoad reach the road network by two entirely different routes.
esmini parses the ``.xodr`` itself, so a scenario can convert with correct
trajectories while the *CommonRoad* lanelet network is empty — the converter
records the failure as a warning and carries on (§8.5).

``highway_merge`` is the case in this corpus.  ``soderleden.xodr`` is
OpenDRIVE **1.7** and contains a *direct* junction::

    <junction name="" id="8" type="direct">
        <connection id="0" incomingRoad="2" linkedRoad="0" contactPoint="start">

A direct junction joins two roads with no intermediate connecting road, so it
carries ``linkedRoad`` in place of ``connectingRoad``.  crdesigner's parser
assumes ``connectingRoad`` is always present::

    parser.py:799    new_connection.connectingRoad = connection.get("connectingRoad")
    junction.py:130  self._connectingRoad = int(value)
    TypeError: int() argument must be ... not 'NoneType'

It fails *in the parser*, before any geometry is built, so one unsupported
attribute costs the whole map — 0 lanelets rather than one missing junction.

The repair
----------
Rewrite ``linkedRoad`` to ``connectingRoad`` on a **temporary copy**.  Nothing
in ``commonroad-openscenario-converter`` or ``crdesigner`` is modified, and the
source ``.xodr`` is never touched.

Measured on ``soderleden.xodr``:

===============================  =========  ==============  ========
approach                         lanelets   succ/pred links isolated
===============================  =========  ==============  ========
unrepaired                       0          —               —
drop ``type="direct"`` junctions 15         4 / 4           8
**linkedRoad → connectingRoad**  **15**     **9 / 9**       **2**
===============================  =========  ==============  ========

Dropping the junction recovers the geometry but leaves 8 of 15 lanelets
isolated — the map renders and is still unroutable, so a planner gets no
reference path.  Rewriting the attribute recovers the connectivity too.

Honest limitation
-----------------
``linkedRoad`` and ``connectingRoad`` do **not** mean the same thing.  In a
direct junction the linked road is one of the two roads being joined, not an
intermediate connector, so this tells crdesigner something that is not quite
true.  The resulting topology is an approximation — for a merge it is very
likely the right one, since the lanes do join, but it is a *repair* and is
recorded as such in ``bundle.json`` under ``road_network.repairs`` rather than
being passed off as a clean conversion.

This is why the whole thing is opt-in (``--fix-xodr``): a silent repair that
invents connectivity would be precisely the class of defect this project
exists to expose.
"""
from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: Repairs applied during the most recent conversion, for the manifest.
#: Conversions are serialised (one interpreter per scenario in a batch, and a
#: lock in the server), so a module-level record is safe here.
LAST_REPAIRS: List[Dict[str, Any]] = []

_PATCHED = False


def _repair_direct_junctions(root: ET.Element) -> List[Dict[str, Any]]:
    """Give every ``linkedRoad`` connection the ``connectingRoad`` crdesigner wants."""
    repairs: List[Dict[str, Any]] = []
    for junction in root.iter("junction"):
        for connection in junction.iter("connection"):
            linked = connection.get("linkedRoad")
            if linked is None or connection.get("connectingRoad") is not None:
                continue
            connection.set("connectingRoad", linked)
            repairs.append({
                "repair": "linkedRoad->connectingRoad",
                "junction": junction.get("id"),
                "junction_type": junction.get("type"),
                "connection": connection.get("id"),
                "incoming_road": connection.get("incomingRoad"),
                "linked_road": linked,
                "note": (
                    "OpenDRIVE 1.7 direct junction; crdesigner's parser requires "
                    "connectingRoad. The two attributes are not equivalent, so "
                    "the junction topology is approximated."
                ),
            })
    return repairs


def repair_xodr(xodr_path: str | Path) -> Tuple[Path, List[Dict[str, Any]]]:
    """
    Return ``(path_to_use, repairs)`` for an OpenDRIVE file.

    When nothing needs repairing the original path comes back untouched and no
    temporary file is created.
    """
    src = Path(xodr_path)
    try:
        tree = ET.parse(src)
    except (ET.ParseError, OSError):
        return src, []          # let crdesigner report its own parse failure

    repairs = _repair_direct_junctions(tree.getroot())
    if not repairs:
        return src, []

    handle = tempfile.NamedTemporaryFile(
        prefix=f"{src.stem}_repaired_", suffix=".xodr", delete=False,
    )
    handle.close()
    out = Path(handle.name)
    tree.write(out, encoding="UTF-8", xml_declaration=True)
    return out, repairs


def enable(patch_target: Optional[Any] = None) -> bool:
    """
    Route the converter's OpenDRIVE conversion through :func:`repair_xodr`.

    ``osc_cr_converter.converter.osc2cr`` imports ``opendrive_to_commonroad``
    into its own namespace, so rebinding that name redirects the call without
    editing the package — the same technique ``paths.bootstrap()`` already uses
    to disable crdesigner's geo re-projection.

    Idempotent.  Returns True when the hook is in place.
    """
    global _PATCHED
    if _PATCHED:
        return True

    module = patch_target
    if module is None:
        try:
            import osc_cr_converter.converter.osc2cr as module  # type: ignore
        except ImportError:
            return False

    original = getattr(module, "opendrive_to_commonroad", None)
    if original is None:
        return False

    def _repairing(odr_file, *args, **kwargs):
        path, repairs = repair_xodr(odr_file)
        if repairs:
            LAST_REPAIRS.extend(repairs)
        try:
            return original(str(path), *args, **kwargs)
        finally:
            if repairs and path != Path(odr_file):
                path.unlink(missing_ok=True)

    module.opendrive_to_commonroad = _repairing
    _PATCHED = True
    return True


def reset() -> None:
    """Forget the repairs recorded for the previous conversion."""
    LAST_REPAIRS.clear()
