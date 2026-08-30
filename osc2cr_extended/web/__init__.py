"""
Front-end surfaces for the package.

``overlay/``          Tampermonkey userscript that overlays a converted
                      scenario's triggers on crdesigner.cps.cit.tum.de.
``vscode-extension/`` VS Code extension: run the Transcription/Translation/Interpretation pipeline on the active
                      .xosc and render the result in a webview.
:mod:`~.vscode_bridge`  the extension's Python side — also runnable directly:
                      ``python -m osc2cr_extended.web.vscode_bridge <file.xosc>``

The interactive viewer is separate, in ``osc2cr_extended/viewer/``, because the
package's own HTTP server hosts it (``osc2cr-ext serve``).
"""
