"""
view_cr.py
==========
Open a CommonRoad .xml scenario in the CommonRoad Scenario Designer GUI —
the desktop version of the web viewer at https://crdesigner.cps.cit.tum.de
(same lanelet styling, traffic signs/lights, and time-step animation slider).

The installed `crdesigner` CLI entry point crashes with a click/typer
incompatibility (TypeError: Secondary flag is not valid for non-boolean
flag), so this launcher calls the GUI's start function directly.

Run with the cr-osc-converter conda environment:
    python view_cr.py output/acc-test/scenario_transcription.xml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", type=Path, nargs="?", default=None,
                        help="CommonRoad .xml to open at startup (optional)")
    args = parser.parse_args()

    if args.xml is not None and not args.xml.exists():
        print(f"✗ File not found: {args.xml}")
        sys.exit(1)

    from crdesigner.ui.gui.start_gui import start_gui
    start_gui(str(args.xml.resolve()) if args.xml else None)
