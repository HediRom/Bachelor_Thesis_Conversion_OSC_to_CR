"""
view_osc.py
===========
Open a raw .xosc scenario in esmini's native viewer, before any
CommonRoad conversion happens. Useful for sanity-checking the storyboard,
entities, and triggers that Transcription/Translation/Interpretation will later parse.

Run with the cr-osc-converter conda environment:
    python view_osc.py path/to/scenario.xosc
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.ERROR)

# Runnable straight from a checkout, without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def view_osc(xosc_path: Path, width: int, height: int) -> None:
    from osc_cr_converter.utility.configuration import ConverterParams, EsminiParams
    from osc_cr_converter.wrapper.esmini.esmini_wrapper_provider import (
        EsminiWrapperProvider,
    )

    config = ConverterParams()
    wrapper = EsminiWrapperProvider(config).provide_esmini_wrapper()
    if wrapper is None:
        print("✗ Could not obtain an esmini wrapper (binary not found/downloadable).")
        sys.exit(1)

    window_size = EsminiParams.WindowSize(x=0, y=0, width=width, height=height)
    print(f"Opening esmini viewer for {xosc_path} …  (close the window to continue)")
    wrapper.view_scenario(str(xosc_path), window_size=window_size)


def parse_window(value: str) -> tuple[int, int]:
    try:
        w, h = value.lower().split("x")
        return int(w), int(h)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected WxH, e.g. 1280x720, got {value!r}") from exc


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xosc", type=Path, help="Path to the .xosc file to view")
    parser.add_argument(
        "--window",
        type=parse_window,
        default=(1280, 720),
        metavar="WxH",
        help="Viewer window size, default 1280x720",
    )
    args = parser.parse_args()

    if not args.xosc.exists():
        print(f"✗ File not found: {args.xosc}")
        sys.exit(1)

    view_osc(args.xosc.resolve(), *args.window)
