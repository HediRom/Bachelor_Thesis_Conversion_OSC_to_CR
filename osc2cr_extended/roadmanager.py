"""
roadmanager.py
==============
Resolves OpenSCENARIO lane positions to world coordinates.

Why this is needed
------------------
``ReachPositionCondition`` is the most common condition type the thesis's
condition model does not carry — 29 occurrences across 14 files in these
corpora, and **28 of them address the target with ``<LanePosition>``**:

    <ReachPositionCondition tolerance="1.0">
      <Position><LanePosition roadId="0" laneId="-1" s="50.0"/></Position>
    </ReachPositionCondition>

A lane position is meaningless without the road geometry: turning
``(roadId, laneId, s)`` into an ``(x, y)`` requires evaluating the OpenDRIVE
reference line and lane widths at that station.  Rather than reimplement that,
this module calls esmini's own RoadManager through its C API
(``libesminiRMLib.so``), which is the same code that produced the trajectories
in the first place — so the answers are consistent with the simulation by
construction.

The library is loaded lazily and every failure degrades to ``None``; a scenario
whose lane positions cannot be resolved reports the condition as unevaluable
instead of silently evaluating to false.
"""
from __future__ import annotations

import ctypes
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import paths

# esmini/EnvironmentSimulator/Libraries/esminiRMLib/esminiRMLib.hpp
_ID_UNDEFINED = 0xFFFFFFFF


def _position_data(real, ident):
    """
    Build the ``RM_PositionData`` mirror for one ABI.

    esmini changed this structure's field types: v2.29.3 declares the
    coordinates as ``float`` and the road/junction IDs as ``int``, while v3.x
    uses ``double`` and ``uint32_t``.  Mirroring the wrong one does not fail --
    ctypes reads whatever bytes are there, which yields a constant position and
    an uninitialised heading for every query.  Both layouts are therefore
    generated and the right one selected at load time.
    """
    class RMPositionData(ctypes.Structure):
        _fields_ = [
            ("x", real), ("y", real), ("z", real),
            ("h", real), ("p", real), ("r", real),
            ("hRelative", real),
            ("roadId", ident), ("junctionId", ident),
            ("laneId", ctypes.c_int),
            ("laneOffset", real), ("s", real),
        ]
    return RMPositionData


#: v2.29.3 -- the build the converter vendors, and the default target.
_RMPositionData_v2 = _position_data(ctypes.c_float, ctypes.c_int)
#: v3.x -- a standalone esmini checkout.
_RMPositionData_v3 = _position_data(ctypes.c_double, ctypes.c_uint32)

#: Set by :func:`_load` to whichever layout the loaded library uses.
RMPositionData = _RMPositionData_v2


_LIB_NAME = "libesminiRMLib.so"

_lock = threading.Lock()
_lib = None
_lib_error: Optional[str] = None


def _load() -> Optional[ctypes.CDLL]:
    """Load libesminiRMLib once; ``None`` when unavailable."""
    global _lib, _lib_error
    if _lib is not None or _lib_error is not None:
        return _lib

    # Resolved through paths.esmini_lib rather than a fixed checkout location,
    # so it works for the converter's bundled esmini, a standalone esmini
    # checkout, or one pointed at by OSC2CR_ESMINI_HOME.
    candidate = paths.esmini_lib(_LIB_NAME)
    if candidate is None:
        _lib_error = (
            f"{_LIB_NAME} not found. Set OSC2CR_ESMINI_HOME to an esmini "
            f"installation containing bin/{_LIB_NAME}."
        )
        return None

    try:
        lib = ctypes.CDLL(str(candidate))
    except OSError as exc:
        _lib_error = f"{candidate.name}: {exc}"
        return None

    # Which ABI is this?  RM_InitWithString exists only from v3.x, so its
    # presence distinguishes the two layouts without parsing a version string.
    global RMPositionData
    v3 = hasattr(lib, "RM_InitWithString")
    RMPositionData = _RMPositionData_v3 if v3 else _RMPositionData_v2
    real = ctypes.c_double if v3 else ctypes.c_float
    ident = ctypes.c_uint32 if v3 else ctypes.c_int

    lib.RM_Init.argtypes = [ctypes.c_char_p]
    lib.RM_Init.restype = ctypes.c_int
    lib.RM_Close.restype = ctypes.c_int
    lib.RM_CreatePosition.restype = ctypes.c_int
    lib.RM_SetLanePosition.argtypes = [
        ctypes.c_int, ident, ctypes.c_int, real, real, ctypes.c_bool,
    ]
    lib.RM_SetLanePosition.restype = ctypes.c_int
    lib.RM_GetPositionData.argtypes = [
        ctypes.c_int, ctypes.POINTER(RMPositionData),
    ]
    lib.RM_GetPositionData.restype = ctypes.c_int
    _lib = lib
    return _lib


def available() -> bool:
    return _load() is not None


def unavailable_reason() -> Optional[str]:
    _load()
    return _lib_error


class LanePositionResolver:
    """
    Resolves ``(roadId, laneId, s)`` to world ``(x, y, heading)`` for one map.

    The RoadManager keeps a single global map, so calls are serialised and the
    map is re-initialised whenever a different ``.xodr`` is requested.  Results
    are cached per (road, lane, offset, s).
    """

    def __init__(self, xodr_path: str | Path) -> None:
        self.xodr_path = Path(xodr_path)
        self._cache: Dict[Tuple[int, int, float, float], Optional[Tuple[float, float, float]]] = {}
        self._ok: Optional[bool] = None

    def _ensure_map(self, lib: ctypes.CDLL) -> bool:
        if self._ok is not None:
            return self._ok
        if not self.xodr_path.is_file():
            self._ok = False
            return False
        lib.RM_Close()
        self._ok = lib.RM_Init(str(self.xodr_path).encode()) == 0
        return self._ok

    def resolve(
        self, road_id: int, lane_id: int, s: float, lane_offset: float = 0.0,
    ) -> Optional[Tuple[float, float, float]]:
        """World ``(x, y, heading)`` of a lane position, or ``None``."""
        lib = _load()
        if lib is None:
            return None

        key = (road_id, lane_id, lane_offset, s)
        if key in self._cache:
            return self._cache[key]

        with _lock:
            if not self._ensure_map(lib):
                self._cache[key] = None
                return None

            handle = lib.RM_CreatePosition()
            if handle < 0:
                self._cache[key] = None
                return None

            rc = lib.RM_SetLanePosition(
                handle, road_id, lane_id, lane_offset, s, True,
            )
            if rc != 0:
                self._cache[key] = None
                return None

            data = RMPositionData()
            if lib.RM_GetPositionData(handle, ctypes.byref(data)) != 0:
                self._cache[key] = None
                return None

            result = (float(data.x), float(data.y), float(data.h))

        self._cache[key] = result
        return result
