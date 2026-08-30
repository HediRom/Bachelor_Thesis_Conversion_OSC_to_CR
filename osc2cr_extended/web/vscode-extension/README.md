# Storyboard Parsing (VS Code extension)

Runs the thesis's OpenSCENARIO → CommonRoad storyboard-parsing pipeline (see
[../README.md](../README.md)) on a single `.xosc` file from inside the editor,
instead of editing and re-running `run_examples.py` by hand.

## What it does

For the active or right-clicked `.xosc` file, it runs the full pipeline —
esmini conversion, storyboard parsing, and all three representation
strategies (Transcription, Translation, Interpretation) — via
[`../vscode_bridge.py`](../vscode_bridge.py).

As soon as esmini conversion finishes (before Transcription/Translation/Interpretation, before the replay GIF —
the slowest step), a results panel opens showing the scenario's initial
layout (entities at their starting positions on the road network, rendered
via [`../viz.py`](../viz.py)) while the rest keeps running in the
background. Once everything finishes, that same panel updates with a
tick-by-tick replay GIF of the full simulation plus the Transcription/Translation/Interpretation comparison
(conditions mapped/skipped, events fired on replay) and links to the
generated `scenario_transcription.xml` / `scenario_translation.xml` / `scenario_interpretation.xml`, `conditions_transcription.json`, `report_translation.txt`, and
`trace_interpretation.json` files.

Note: the "initial layout" preview is the first frame of the *converted*
scenario, not a render of the raw `.xosc` prior to simulation — esmini has
to run before any positions/trajectories exist at all. In practice the
conversion itself is fast (single-digit seconds), so this still arrives well
before Transcription/Translation/Interpretation + the replay GIF are ready.

## Requirements

- The `cr-osc-converter` conda environment (same one `run_examples.py` uses)
  must exist and be reachable at the path set in `storyboard.pythonPath`.
- esmini must be available to that environment, since the converter shells
  out to it.

## Usage

1. Open a `.xosc` file in the editor (or right-click one in the Explorer).
2. Run **"Storyboard: Run Pipeline on Active File"** — via the command
   palette, the editor title bar, or the Explorer context menu.
3. Watch progress in the **"Storyboard Parsing"** output channel. On success,
   a results panel opens beside the editor.

## Settings

| Setting | Default | Description |
|---|---|---|
| `storyboard.pythonPath` | `python3` | Interpreter that can import `osc2cr_extended` and the converter. |
| `storyboard.storyboardParsingPath` | *(empty)* | Absolute path to `osc2cr_extended/web` (holds `vscode_bridge.py`). Needed once the extension is installed, since it then runs from `~/.vscode-server/extensions/...` with no relation to this checkout. Leave empty when `osc2cr_extended` is installed into the interpreter above. |
| `storyboard.timeoutSeconds` | `120` | Kills the pipeline if esmini hasn't returned by then (some scenarios make esmini hang indefinitely). |

## Known limitations

- Scenarios with `.xosc`-relative `../xodr/` map references only resolve
  correctly when run from their original location (see the note in
  `run_examples.py`) — copies under `Storyboard_parsing/input/` will fail
  with `SIMULATION_FAILED_CREATING_OUTPUT`.
- A few esmini scenarios (observed: `pedestrian.xosc`) hang rather than
  erroring; the extension cancels them after `storyboard.timeoutSeconds`
  rather than waiting forever.

## Development

```bash
npm install
npm run compile   # or: npm run watch
```

Press **F5** with this folder open as the workspace root to launch an
Extension Development Host for manual testing.
