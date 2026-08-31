"""
Front-end surfaces for the package.

``overlay/``          Tampermonkey userscript that overlays a converted
                      scenario's triggers on crdesigner.cps.cit.tum.de.
:mod:`~.run_pipeline` one-shot CLI running the Transcription/Translation/
                      Interpretation pipeline on a single .xosc, plus a JSON summary:
                      ``python -m osc2cr_extended.web.run_pipeline <file.xosc>``

The interactive viewer is separate, in ``osc2cr_extended/viewer/``, because the
package's own HTTP server hosts it (``osc2cr-ext serve``).
"""
