"""
server.py
=========
Backend for the interactive simulator.

Serves the viewer (a self-contained canvas app modelled on the TUM CommonRoad
Scenario Designer at https://crdesigner.cps.cit.tum.de/) plus a small JSON API
that the static site cannot provide on its own:

  GET  /                              the viewer
  GET  /api/scenarios                 converted bundles + their stats
  GET  /api/corpus                    .xosc files available for conversion
  GET  /api/bundle/<name>/<file>      a file from a bundle (scenario.xml, ...)
  POST /api/convert   {"xosc": ...}   convert a scenario on demand
  POST /api/cosim     {"scenario":…}  run a converted bundle closed-loop
  POST /api/evaluate  {...}           re-evaluate conditions on a what-if state

``/api/evaluate`` is the part that separates this from a replay viewer: it
feeds a caller-supplied world state through the Interpretation condition evaluator
and returns which predicates hold.  Drag an actor in the browser and the
triggers respond, which is impossible once esmini has flattened the storyboard.

Conversions and closed-loop runs are serialised behind one lock — both drive
esmini through a process-wide library handle and are not re-entrant, and each
runs in a child process so a scenario that crashes esmini cannot take the
viewer down with it.
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

from . import paths

paths.bootstrap()

logger = logging.getLogger(__name__)

_CONVERT_LOCK = threading.Lock()
_SESSIONS: Dict[str, Any] = {}          # bundle name → LiveSession
_SESSION_LOCK = threading.Lock()

# Files a client may pull out of a bundle
_ALLOWED_BUNDLE_FILES = {
    "scenario.xml", "scenario_plain.xml", "triggers.json", "timeline.json",
    "conditions_transcription.json", "conditions_translation.json",
    "trace_interpretation.json", "report_translation.txt",
    "bundle.json",
    # closed-loop runs (cosim.py); the viewer offers whichever exist
    "cosim_trace_esmini.json", "cosim_trace_planner.json",
    # …and the same runs as CommonRoad scenarios, triggers embedded
    "cosim_esmini.xml", "cosim_planner.xml",
}

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
}


# ---------------------------------------------------------------------------
# Bundle discovery
# ---------------------------------------------------------------------------

def list_bundles(output_dir: Optional[Path] = None) -> list:
    """Every converted bundle under ``output/``, newest manifest data included."""
    root = output_dir or paths.OUTPUT_DIR
    bundles = []
    if not root.is_dir():
        return bundles

    for manifest_path in sorted(root.glob("*/bundle.json")):
        try:
            manifest = json.loads(manifest_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        bundles.append({
            "name": manifest.get("name", manifest_path.parent.name),
            "xosc_path": manifest.get("xosc_path"),
            "stats": manifest.get("stats", {}),
            "timings_s": manifest.get("timings_s", {}),
            "files": sorted(
                f.name for f in manifest_path.parent.iterdir()
                if f.name in _ALLOWED_BUNDLE_FILES
            ),
            # which closed-loop runs this bundle has, so the viewer can enable
            # its run buttons without probing for 404s
            "cosim_runs": _cosim_runs(manifest_path.parent),
        })
    return bundles


def _cosim_runs(bundle_dir: Path) -> dict:
    """``driver → {ok, status, events}`` for each closed-loop run on disk."""
    runs = {}
    for driver in ("esmini", "planner"):
        path = bundle_dir / f"cosim_trace_{driver}.json"
        if not path.exists():
            continue
        try:
            trace = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        planner = trace.get("planner") or {}
        status = planner.get("status", "n/a")
        runs[driver] = {
            # a planner run that never planned has nothing to show
            "ok": driver == "esmini" or status in (
                "completed", "goal-reached",
            ),
            "status": status,
            "reason": planner.get("reason"),
            "events": len(trace.get("events") or []),
            "steps": trace.get("steps", 0),
        }
    return runs


def get_session(name: str):
    """Cached :class:`LiveSession` for a bundle."""
    from .live import LiveSession

    with _SESSION_LOCK:
        session = _SESSIONS.get(name)
        if session is None:
            session = LiveSession(paths.OUTPUT_DIR / name)
            _SESSIONS[name] = session
        return session


def invalidate_session(name: str) -> None:
    with _SESSION_LOCK:
        _SESSIONS.pop(name, None)


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class ViewerHandler(SimpleHTTPRequestHandler):
    """Static viewer + JSON API.  Paths outside the tool root are refused."""

    server_version = "osc2cr-viewer/1.0"

    # ---- helpers -----------------------------------------------------

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_json({"error": f"not found: {path.name}"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            _CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc

    @staticmethod
    def _safe_bundle_file(name: str, filename: str) -> Optional[Path]:
        """Resolve a bundle file, refusing traversal and unlisted filenames."""
        if filename not in _ALLOWED_BUNDLE_FILES:
            return None
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            return None
        candidate = (paths.OUTPUT_DIR / name / filename).resolve()
        try:
            candidate.relative_to(paths.OUTPUT_DIR.resolve())
        except ValueError:
            return None
        return candidate

    # ---- GET ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 — stdlib naming
        route = unquote(urlparse(self.path).path)

        try:
            if route in ("/", "/index.html"):
                self._send_file(paths.VIEWER_DIR / "index.html")
                return

            if route.startswith("/viewer/"):
                rel = route[len("/viewer/"):]
                target = (paths.VIEWER_DIR / rel).resolve()
                try:
                    target.relative_to(paths.VIEWER_DIR.resolve())
                except ValueError:
                    self._send_json({"error": "forbidden"}, 403)
                    return
                self._send_file(target)
                return

            if route == "/api/scenarios":
                self._send_json({"bundles": list_bundles()})
                return

            if route == "/api/corpus":
                corpus = [
                    {"name": name, "path": str(path)}
                    for name, path in sorted(paths.available_xosc().items())
                ]
                self._send_json({"scenarios": corpus})
                return

            if route.startswith("/api/bundle/"):
                parts = route[len("/api/bundle/"):].split("/")
                if len(parts) != 2:
                    self._send_json({"error": "expected /api/bundle/<name>/<file>"}, 400)
                    return
                target = self._safe_bundle_file(parts[0], parts[1])
                if target is None:
                    self._send_json({"error": "forbidden or unknown file"}, 403)
                    return
                self._send_file(target)
                return

            self._send_json({"error": f"no route for {route}"}, 404)

        except Exception as exc:  # noqa: BLE001 — never kill the server thread
            logger.exception("GET %s failed", route)
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    # ---- POST --------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        route = unquote(urlparse(self.path).path)

        try:
            body = self._read_json_body()

            if route == "/api/convert":
                self._handle_convert(body)
                return

            if route == "/api/cosim":
                self._handle_cosim(body)
                return

            if route == "/api/evaluate":
                self._handle_evaluate(body)
                return

            self._send_json({"error": f"no route for {route}"}, 404)

        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            logger.exception("POST %s failed", route)
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    # ---- handlers ----------------------------------------------------

    def _handle_convert(self, body: Dict[str, Any]) -> None:
        xosc = body.get("xosc")
        if not xosc:
            self._send_json({"error": "body must contain 'xosc'"}, 400)
            return

        from .pipeline import convert_isolated

        dt = float(body.get("dt", 0.1))
        # Converting in a child process keeps a scenario that segfaults esmini
        # from taking the viewer's server down with it.  esmini is also driven
        # through a process-wide handle, so serialise the conversions.
        with _CONVERT_LOCK:
            result = convert_isolated(str(xosc), dt=dt)

        if result.get("ok"):
            invalidate_session(result.get("name", str(xosc)))

        self._send_json(result, 200 if result.get("ok") else 422)

    def _handle_cosim(self, body: Dict[str, Any]) -> None:
        """
        Run a converted bundle closed-loop and return the resulting trace.

        Without this the viewer could only *display* a closed-loop run someone
        had already produced from the command line — the co-sim button was a
        view switch over an artifact the UI had no way to create.

        Synchronous, like ``/api/convert``: a planner run over a converted
        bundle takes a few seconds, so the request completes well inside a
        browser's patience and the client can simply reload the bundle
        afterwards.  Long runs are bounded by ``timeout`` rather than left to
        hang the connection.
        """
        name = body.get("scenario")
        if not name:
            self._send_json({"error": "body must contain 'scenario'"}, 400)
            return

        driver = str(body.get("driver", "planner"))
        if driver not in ("esmini", "planner"):
            self._send_json(
                {"error": "'driver' must be 'esmini' or 'planner'"}, 400)
            return

        # Reuse the same traversal guard the bundle-file route uses: `name` is
        # a bundle directory name, never a path.
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            self._send_json({"error": "invalid scenario name"}, 400)
            return

        bundle_dir = paths.OUTPUT_DIR / name
        if not bundle_dir.is_dir():
            self._send_json({"error": f"no converted bundle named '{name}'"}, 404)
            return

        from .cosim import cosim_isolated

        extra: list = []
        if body.get("desired_velocity") is not None:
            extra += ["--desired-velocity", str(float(body["desired_velocity"]))]
        if body.get("max_steps") is not None:
            extra += ["--max-steps", str(int(body["max_steps"]))]

        # Same lock as conversion: esmini is reached through a process-wide
        # handle, so two runs must not overlap even though each is a subprocess.
        with _CONVERT_LOCK:
            result = cosim_isolated(
                bundle_dir,
                driver=driver,
                timeout=float(body.get("timeout", 900.0)),
                extra_args=extra or None,
            )

        # The run rewrites the bundle, so a cached what-if session for it now
        # describes the previous geometry.
        if result.get("ok"):
            invalidate_session(name)

        self._send_json(result, 200 if result.get("ok") else 422)

    def _handle_evaluate(self, body: Dict[str, Any]) -> None:
        name = body.get("scenario")
        if not name:
            self._send_json({"error": "body must contain 'scenario'"}, 400)
            return

        entities = body.get("entities") or {}
        if not isinstance(entities, dict):
            self._send_json({"error": "'entities' must be an object"}, 400)
            return

        try:
            session = get_session(name)
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, 404)
            return

        result = session.evaluate_state(
            entities=entities,
            time_s=float(body.get("time_s", 0.0)),
            traveled=body.get("traveled"),
        )
        self._send_json(result)

    # ---- logging -----------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("%s - %s", self.address_string(), fmt % args)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the viewer server until interrupted."""
    paths.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), ViewerHandler)

    n_bundles = len(list_bundles())
    print(f"osc2cr interactive simulator → http://{host}:{port}/")
    print(f"  {n_bundles} converted bundle(s) in {paths.OUTPUT_DIR}")
    print("  Ctrl-C to stop")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping …")
    finally:
        httpd.server_close()
