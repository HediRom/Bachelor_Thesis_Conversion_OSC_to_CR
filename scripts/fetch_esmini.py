#!/usr/bin/env python3
"""
Download esmini v2.29.3 into the converter, ahead of the first conversion.

The converter fetches esmini's binaries from GitHub the first time it converts
anything, and unpacks them into its own package directory.  osc2cr_extended
resolves the esmini shared libraries (``libesminiLib.so`` for the co-simulation,
``libesminiRMLib.so`` for lane-position queries) at *import* time, so a session
that runs ``cosim`` before it has ever run ``convert`` finds nothing.

Running this once after installation removes that ordering constraint.  It is
idempotent: if the binaries are already there, it does nothing but report them.
"""
import sys


def main() -> int:
    try:
        from osc_cr_converter.utility.configuration import ConverterParams
        from osc_cr_converter.wrapper.esmini.esmini_wrapper_provider import (
            EsminiWrapperProvider,
        )
    except ImportError as exc:
        print(f"commonroad-openscenario-converter is not importable: {exc}")
        print("Run ./setup.sh first.")
        return 1

    print("Resolving esmini v2.29.3 — downloads 33 MB once, unpacks to ~90 MB …")
    wrapper = EsminiWrapperProvider(ConverterParams()).provide_esmini_wrapper()
    if wrapper is None:
        print("Failed — no esmini binaries and no network. "
              "Point OSC2CR_ESMINI_HOME at an esmini installation instead.")
        return 1

    from osc2cr_extended import paths
    import importlib
    importlib.reload(paths)          # re-resolve now that the binaries exist

    print(f"esmini binaries : {paths.ESMINI_HOME}")
    print(f"esmini scenarios: {paths.ESMINI_XOSC}")
    print(f"esminiLib.so    : {paths.esmini_lib('libesminiLib.so')}")
    print(f"esminiRMLib.so  : {paths.esmini_lib('libesminiRMLib.so')}")
    return 0 if paths.esmini_lib("libesminiLib.so") else 1


if __name__ == "__main__":
    sys.exit(main())
