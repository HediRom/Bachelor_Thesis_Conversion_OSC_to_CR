"""
ui_test.py
==========
Browser tests for the viewer, covering the two things that are easy to break
and impossible to check from Python alone:

  1. **Layout reachability.** Every control must be usable at any window size.
     The app-shell layout once let the trigger panel slide 255 px below the
     fold with ``body { overflow: hidden }`` hiding the evidence, so the panel
     silently vanished on smaller windows.

  2. **The run switch.** The viewer shows one scenario two ways — the converted
     esmini run and the closed-loop run where the CommonRoad planner drove the
     ego. Switching between them has to change the motion on the canvas, the
     activity strips, the fire markers and the divergence readout together; a
     button that flips its own state while the canvas keeps drawing the old run
     is exactly the kind of failure that looks fine in a screenshot.

Run (server must be running, or pass a port to an existing one):

    python -m osc2cr serve --port 8800 &
    python tests/ui_test.py 8800

Needs playwright:  pip install playwright && playwright install chromium
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_PORT = 8800
SIZES = [(1600, 900), (1280, 800), (1000, 700), (900, 600), (700, 600), (1400, 420)]
CONTROLS = {
    "play": "#play-btn",
    "step-fwd": "#step-fwd",
    "scrub": "#scrub",
    "triggers": "#triggers",
    "panel": ".panel",
    # The two toolbar actions that start work on the server. They are the
    # controls that keep the viewer from being read-only, so a layout that
    # pushes them off screen defeats the point.
    "convert": "#convert-btn",
    "run-cosim": "#cosim-btn",
}

results: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  {'✓' if ok else '✗'} {label}{f' — {detail}' if detail else ''}")


VISIBLE_JS = """(sel) => {
  const e = document.querySelector(sel);
  if (!e) return {found: false, visible: false};
  const r = e.getBoundingClientRect();
  return {found: true,
          visible: r.bottom > 0 && r.top < innerHeight && r.right > 0
                   && r.left < innerWidth && r.width > 0 && r.height > 0};
}"""


def wait_for_server(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/scenarios", timeout=2):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


def test_layout(browser, port: int, scenario: str) -> None:
    print("\nlayout reachability")
    for vw, vh in SIZES:
        page = browser.new_page(viewport={"width": vw, "height": vh})
        page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
        page.select_option("#scenario-select", scenario)
        page.wait_for_timeout(500)

        unreachable = []
        for name, sel in CONTROLS.items():
            page.evaluate("() => scrollTo(0, 0)")
            page.wait_for_timeout(50)
            if page.evaluate(VISIBLE_JS, sel)["visible"]:
                continue
            try:
                page.locator(sel).scroll_into_view_if_needed(timeout=2000)
                page.wait_for_timeout(100)
                if not page.evaluate(VISIBLE_JS, sel)["visible"]:
                    unreachable.append(name)
            except Exception:  # noqa: BLE001
                unreachable.append(name)

        # the transport must survive scrolling to the bottom
        page.evaluate("() => scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(100)
        if not page.evaluate(VISIBLE_JS, "#play-btn")["visible"]:
            unreachable.append("play-after-scroll")

        check(f"{vw}x{vh}: every control reachable", not unreachable,
              f"unreachable: {unreachable}" if unreachable else "")
        page.close()


def test_run_switch(browser, port: int, scenario: str) -> None:
    print("\nrun switch — esmini vs co-sim")
    page = browser.new_page(viewport={"width": 1500, "height": 860})
    errors: list = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
    page.select_option("#scenario-select", scenario)
    page.wait_for_timeout(900)

    check("the what-if button is gone", page.locator("#whatif-btn").count() == 0)
    check("both run buttons are present",
          page.locator("#run-replay").count() == 1
          and page.locator("#run-cosim").count() == 1)
    check("esmini is the default run",
          page.get_attribute("#run-replay", "aria-pressed") == "true"
          and page.get_attribute("#run-cosim", "aria-pressed") == "false")

    # Producing a run is always offered once a bundle is loaded — that is what
    # keeps the viewer from requiring a trip back to the terminal.
    check("the run-co-sim action is offered",
          not page.locator("#cosim-btn").is_disabled())

    has_cosim = page.evaluate("() => !!window.__osc2cr.state.cosim")
    if not has_cosim:
        # A bundle without a planner run must say so rather than offer a view
        # switch that does nothing.
        check("co-sim view switch is disabled without a closed-loop run",
              page.locator("#run-cosim").is_disabled())
        check("its tooltip points at the button that produces one",
              "Run co-sim" in (page.get_attribute("#run-cosim", "title") or ""))
        check("no console errors", not errors, str(errors[:2]))
        page.close()
        return

    check("co-sim button is enabled when a run exists",
          not page.locator("#run-cosim").is_disabled())
    check("the action offers to redo an existing run",
          (page.locator("#cosim-btn").text_content() or "").strip() == "Re-run co-sim")
    check("divergence bar is shown once both runs are loaded",
          page.locator("#diffbar").is_visible())

    # park on a step both runs cover, so the comparison is meaningful
    page.evaluate("""() => { const A = window.__osc2cr;
      const s = document.querySelector('#scrub');
      s.value = String(Math.floor(A.runMaxStep() / 2));
      s.dispatchEvent(new Event('input')); }""")
    page.wait_for_timeout(300)

    replay_pos = page.evaluate("""() => {
      const A = window.__osc2cr;
      return A.state.scenario.obstacles.map(o => {
        const st = A.replayStateAt(o, A.state.step);
        return st ? {id: o.id, x: st.x, y: st.y} : null;
      }).filter(Boolean);
    }""")

    page.click("#run-cosim")
    page.wait_for_timeout(400)

    check("pressing co-sim flips both buttons",
          page.get_attribute("#run-cosim", "aria-pressed") == "true"
          and page.get_attribute("#run-replay", "aria-pressed") == "false")
    check("the active run is now the closed loop",
          page.evaluate("() => window.__osc2cr.state.run") == "cosim")

    # the canvas must actually be drawing different motion, not just relabelling
    cosim_pos = page.evaluate("""() => {
      const A = window.__osc2cr;
      return A.state.scenario.obstacles.map(o => {
        const st = A.cosimStateAt(o, A.state.step);
        return st ? {id: o.id, x: st.x, y: st.y} : null;
      }).filter(Boolean);
    }""")
    check("the closed-loop run supplies its own positions",
          len(cosim_pos) > 0, f"{len(cosim_pos)} actor(s)")

    by_id = {p["id"]: p for p in replay_pos}
    gaps = [abs(c["x"] - by_id[c["id"]]["x"]) + abs(c["y"] - by_id[c["id"]]["y"])
            for c in cosim_pos if c["id"] in by_id]
    check("at least one actor sits somewhere different in the two runs",
          any(g > 0.25 for g in gaps),
          f"max separation {max(gaps):.2f} m" if gaps else "no overlap")

    check("the strips follow the run on screen",
          page.evaluate("""() => {
            const A = window.__osc2cr;
            return A.state.timeline === null
              || A.state.timeline !== A.state.replayTimeline;
          }"""))

    check("the divergence bar names both runs",
          "esmini" in (page.text_content("#diffbar") or "")
          and "co-sim" in (page.text_content("#diffbar") or ""),
          page.text_content("#diffbar"))

    # a scenario whose triggers re-timed should say so somewhere
    shift = page.evaluate("""() => {
      const A = window.__osc2cr;
      return ((A.state.triggers && A.state.triggers.events) || []).map(ev =>
        A.eventShift(ev.name, ((ev.interpretation && ev.interpretation.fires) || []).map(f => f.time_s))
      ).filter(Boolean).map(s => s.kind);
    }""")
    check("every event is classified against the other run",
          all(k in ("same", "moved", "only-cosim", "only-replay") for k in shift),
          f"kinds: {sorted(set(shift))}")

    page.keyboard.press("KeyR")
    page.wait_for_timeout(300)
    check("R switches back to the esmini run",
          page.evaluate("() => window.__osc2cr.state.run") == "replay")

    page.click("#run-replay")
    page.wait_for_timeout(250)
    check("the step stays within the active run's range",
          page.evaluate("""() => { const A = window.__osc2cr;
            return A.state.step <= A.runMaxStep(); }"""))

    check("no console errors", not errors, str(errors[:2]))
    page.close()


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT

    server = None
    if not wait_for_server(port, timeout=2):
        print(f"starting a viewer on port {port} …")
        server = subprocess.Popen(
            [sys.executable, "-m", "osc2cr_extended", "serve", "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not wait_for_server(port):
            print("could not start the viewer server")
            return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed:\n"
              "  pip install playwright && playwright install chromium")
        return 2

    try:
        import json
        import urllib.request as req
        with req.urlopen(f"http://127.0.0.1:{port}/api/scenarios") as fh:
            bundles = json.load(fh)["bundles"]
        if not bundles:
            print("no converted bundles — run: python -m osc2cr convert cut-in_simple")
            return 2
        # prefer a bundle that has a usable closed-loop run, so the run-switch
        # test exercises the comparison rather than the disabled-button path
        def has_cosim(b):
            runs = (b.get("cosim_runs") or {}).get("planner") or {}
            return bool(runs.get("ok"))

        preferred = next(
            (b["name"] for b in bundles if b["name"] == "cut-in_simple" and has_cosim(b)),
            next((b["name"] for b in bundles if has_cosim(b)),
                 next((b["name"] for b in bundles if b["name"] == "cut-in_simple"),
                      bundles[0]["name"])),
        )
        print(f"\nviewer UI tests — scenario \"{preferred}\"")

        with sync_playwright() as p:
            browser = p.chromium.launch()
            test_layout(browser, port, preferred)
            test_run_switch(browser, port, preferred)
            browser.close()
    finally:
        if server is not None:
            server.terminate()

    failed = results.count(False)
    print(f"\n{'✓' if not failed else '✗'} {failed} failure(s) of {len(results)} checks\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
