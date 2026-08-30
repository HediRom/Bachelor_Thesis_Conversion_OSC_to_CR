"""Minimal ctypes wrapper around esmini's libesminiLib.so for tick-by-tick co-simulation.

Unlike osc_cr_converter's EsminiWrapper (which runs a scenario to completion in one go),
this wrapper exposes the raw step / read / write primitives needed to drive esmini one
tick at a time: read the current state of every scenario object, and write the ego
vehicle's next state back into esmini before stepping again.
"""

import ctypes as ct
import os

# Reuse the esmini v2.29.3 binaries bundled with commonroad-openscenario-converter so
# that resource/catalog paths referenced by the .xosc scenarios resolve correctly.
_ESMINI_BIN_DIR = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "commonroad-openscenario-converter",
        "osc_cr_converter",
        "wrapper",
        "esmini",
        "esmini_v2.29.3",
        "esmini",
        "bin",
    )
)
ESMINI_LIB_PATH = os.path.join(_ESMINI_BIN_DIR, "libesminiLib.so")


class SEScenarioObjectState(ct.Structure):
    """Mirrors esmini v2.29.3's SE_ScenarioObjectState layout (see esminiLib.hpp)."""

    _fields_ = [
        ("id", ct.c_int),
        ("model_id", ct.c_int),
        ("control", ct.c_int),
        ("timestamp", ct.c_float),
        ("x", ct.c_float),
        ("y", ct.c_float),
        ("z", ct.c_float),
        ("h", ct.c_float),
        ("p", ct.c_float),
        ("r", ct.c_float),
        ("roadId", ct.c_int),
        ("junctionId", ct.c_int),
        ("t", ct.c_float),
        ("laneId", ct.c_int),
        ("laneOffset", ct.c_float),
        ("s", ct.c_float),
        ("speed", ct.c_float),
        ("centerOffsetX", ct.c_float),
        ("centerOffsetY", ct.c_float),
        ("centerOffsetZ", ct.c_float),
        ("width", ct.c_float),
        ("length", ct.c_float),
        ("height", ct.c_float),
        ("objectType", ct.c_int),
        ("objectCategory", ct.c_int),
        ("wheel_angle", ct.c_float),
        ("wheel_rotation", ct.c_float),
    ]


class EsminiSimulation:
    """Tick-by-tick controllable esmini scenario.

    Usage:
        sim = EsminiSimulation("scenario.xosc", dt=0.1)
        while not sim.is_finished():
            sim.step()
            states = sim.get_object_states()
            ...
            sim.set_ego_state(ego_id, x, y, heading, speed)
        sim.close()
    """

    def __init__(self, xosc_path: str, dt: float, use_viewer: bool = False, random_seed: int = 0):
        self.dt = dt
        self._lib = ct.CDLL(ESMINI_LIB_PATH)
        self._configure_signatures()

        self._lib.SE_LogToConsole(False)
        self._lib.SE_SetLogFilePath("".encode("ascii"))

        viewer_mode = 1 if use_viewer else 0
        ret = self._lib.SE_Init(
            os.path.abspath(xosc_path).encode("ascii"), 0, viewer_mode, 0, 0
        )
        if ret != 0:
            raise RuntimeError(f"esmini SE_Init failed for scenario '{xosc_path}'")

        self._lib.SE_SetSeed(random_seed)

    def _configure_signatures(self):
        lib = self._lib
        lib.SE_Init.argtypes = [ct.c_char_p, ct.c_int, ct.c_int, ct.c_int, ct.c_int]
        lib.SE_Init.restype = ct.c_int

        lib.SE_StepDT.argtypes = [ct.c_float]
        lib.SE_StepDT.restype = ct.c_int

        lib.SE_GetSimulationTime.restype = ct.c_float
        lib.SE_GetQuitFlag.restype = ct.c_int

        lib.SE_GetNumberOfObjects.restype = ct.c_int
        lib.SE_GetId.argtypes = [ct.c_int]
        lib.SE_GetId.restype = ct.c_int

        lib.SE_GetIdByName.argtypes = [ct.c_char_p]
        lib.SE_GetIdByName.restype = ct.c_int

        lib.SE_GetObjectName.argtypes = [ct.c_int]
        lib.SE_GetObjectName.restype = ct.c_char_p

        lib.SE_GetObjectState.argtypes = [ct.c_int, ct.POINTER(SEScenarioObjectState)]
        lib.SE_GetObjectState.restype = ct.c_int

        # NOTE: in esmini v2.29.3 these take `float` (not `double`), and
        # SE_ReportObjectPosXYH additionally takes a leading `timestamp` argument.
        lib.SE_ReportObjectPosXYH.argtypes = [ct.c_int, ct.c_float, ct.c_float, ct.c_float, ct.c_float]
        lib.SE_ReportObjectPosXYH.restype = ct.c_int

        lib.SE_ReportObjectSpeed.argtypes = [ct.c_int, ct.c_float]
        lib.SE_ReportObjectSpeed.restype = ct.c_int

        lib.SE_SetSeed.argtypes = [ct.c_uint]

    def step(self):
        """Advance the simulation by one tick of size `self.dt`."""
        if self._lib.SE_StepDT(self.dt) != 0:
            raise RuntimeError("esmini SE_StepDT failed")

    def sim_time(self) -> float:
        return float(self._lib.SE_GetSimulationTime())

    def is_finished(self) -> bool:
        return self._lib.SE_GetQuitFlag() == 1

    def get_object_id_by_name(self, name: str) -> int:
        return self._lib.SE_GetIdByName(name.encode("ascii"))

    def get_object_name(self, object_id: int) -> str:
        raw = self._lib.SE_GetObjectName(object_id)
        return raw.decode("utf-8") if raw is not None else ""

    def get_object_states(self) -> dict:
        """Return {object_id: SEScenarioObjectState} for every object in the scenario."""
        states = {}
        for j in range(self._lib.SE_GetNumberOfObjects()):
            object_id = self._lib.SE_GetId(j)
            state = SEScenarioObjectState()
            if self._lib.SE_GetObjectState(object_id, ct.byref(state)) == 0:
                states[object_id] = state
        return states

    def set_ego_state(self, object_id: int, x: float, y: float, heading: float, speed: float):
        """Write the ego's next cartesian position/heading/speed back into esmini."""
        if self._lib.SE_ReportObjectPosXYH(object_id, 0.0, x, y, heading) != 0:
            raise RuntimeError("esmini SE_ReportObjectPosXYH failed")
        if self._lib.SE_ReportObjectSpeed(object_id, speed) != 0:
            raise RuntimeError("esmini SE_ReportObjectSpeed failed")

    def close(self):
        self._lib.SE_Close()
