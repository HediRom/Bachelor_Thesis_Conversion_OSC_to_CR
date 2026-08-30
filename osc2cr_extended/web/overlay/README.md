# crdesigner web overlay — trigger panel (Transcription/Translation/Interpretation)

A Tampermonkey userscript that overlays a **trigger panel** on the TUM
[CommonRoad Scenario Viewer](https://crdesigner.cps.cit.tum.de/). It shows the
OpenSCENARIO trigger information that the `Storyboard_parsing` pipeline
extracts (Transcription annotations, Translation time windows, Interpretation replay
trace) and syncs it live with the viewer's playback timeline:

- **Translation** time-window goals switch `pending → ACTIVE → passed` as you
  scrub the timeline; skipped conditions show their skip reason.
- **Interpretation** replay events flash **FIRED** the moment playback crosses
  the step at which they fired.
- **Transcription** condition details (type, entities, rule/threshold, edge,
  delay) are listed per event.

The panel consumes one file: `triggers.json`, which the pipeline now writes
into every output folder (merged from `conditions_transcription.json`, `conditions_translation.json`
and `trace_interpretation.json` by `shared/triggers_export.py`).

## Install (once)

1. Install the [Tampermonkey](https://www.tampermonkey.net/) (or Violentmonkey)
   browser extension.
2. In Tampermonkey: *Create a new script* → paste the contents of
   `crdesigner_triggers.user.js` → save. (Or drag the file into the
   Tampermonkey dashboard.)
3. Open <https://crdesigner.cps.cit.tum.de/>. If the TLS certificate is still
   expired you must click through the browser warning once — the userscript
   runs fine after that. The "Triggers Transcription/Translation/Interpretation" panel appears top-right.

## Use

1. Run the pipeline as usual, e.g.

   ```bash
   python vscode_bridge.py <scenario>.xosc
   ```

   This writes `output/<scenario>/triggers.json` along with the other files.
   (For folders generated before this feature existed, regenerate with
   `python shared/triggers_export.py` — no esmini needed.)

2. In the viewer, load the converted CommonRoad scenario: drag
   `output/<scenario>/scenario_transcription.xml` (or `scenario_translation.xml` / `scenario_interpretation.xml`)
   into the page.

3. Feed the panel, either way:
   - **Drag & drop** `output/<scenario>/triggers.json` onto the panel's drop
     zone (zero setup), or
   - **Fetch from localhost**: serve the output folder

     ```bash
     python -m http.server 8765 --directory output/<scenario>
     ```

     and press **Fetch** in the panel (default URL
     `http://127.0.0.1:8765/triggers.json`). The panel also tries this URL
     automatically when the page loads.

4. Press **Fit view** in the panel. The viewer has no auto-zoom and starts
   at a fixed camera near the origin — scenarios on a straight road
   (y ≈ 0, only a few metres tall) render as an invisible sliver on the top
   edge of the canvas until you fit or zoom manually.

5. Press play / scrub the timeline in the viewer and watch the trigger states
   update.

## Notes & limits

- The panel reads the viewer's timeline through its slider element
  (`aria-valuenow`). If TUM redesigns the app, the "slider = steps/seconds"
  selector lets you correct the unit; worst case the time sync stops working
  but the static trigger view still does.
- Alerts are panel-based (temporal/textual). Pinning markers to map
  coordinates inside the viewer's canvas is intentionally out of scope — it
  would require reverse-engineering the minified renderer.
- Nothing is sent anywhere: the userscript only reads local files or
  `127.0.0.1`, and touches nothing on TUM's servers.
