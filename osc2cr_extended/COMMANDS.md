# Command reference

Every runnable entry point in `osc2cr_extended/`. See [README.md](README.md)
for what the tool does and why.

## Setting up the shell

`./setup.sh` (see the top-level README) pip-installs this package, so
`osc2cr-ext <command>` works from any directory and no `PYTHONPATH` is needed.

Without that install, Python must still be able to find the package: run from
the directory *containing* `osc2cr_extended/` — `python -m` puts the working
directory on the path — or set the path once:

```bash
cd ~/Bachelor_Conversion
# or
export PYTHONPATH=~/Bachelor_Conversion
```

Output lands in `./osc2cr_output/` — relative to wherever you run from. Pin it
if you want one fixed location regardless of shell:

```bash
export OSC2CR_OUTPUT_DIR=~/Bachelor_Conversion/osc2cr_output
```

All commands below are written as `python3 -m osc2cr_extended <command>`;
`osc2cr-ext <command>` is the identical shorter form once installed. `python3`
must be the environment `setup.sh` installed into — activate it first.

---

## 1. The CLI

### list — what can be converted

```bash
python3 -m osc2cr_extended list
```

Shows the 75 discoverable `.xosc` files tagged by origin — `[converter]`,
`[esmini]`, `[bundled]` (this package's own corpus) — flags the 5 names that
exist in two corpora with **different content**, then lists converted bundles.

### convert — .xosc → enriched CommonRoad

```bash
# one scenario
python3 -m osc2cr_extended convert cut-in_simple

# several (one interpreter each — esmini leaks state between scenarios)
python3 -m osc2cr_extended convert cut-in_simple acc-test drop-bike

# a file anywhere on disk
python3 -m osc2cr_extended convert /path/to/my_scenario.xosc

# resolve a colliding name to esmini's copy instead of the converter's
python3 -m osc2cr_extended convert acc-test --prefer esmini

# every discoverable scenario
python3 -m osc2cr_extended convert $(python3 -m osc2cr_extended list \
    | awk '/^  [a-zA-Z]/ {print $1}')
```

| Flag | Effect |
|---|---|
| `--dt 0.1` | CommonRoad time step [s] |
| `--output DIR` | output root (default `./osc2cr_output`) |
| `--prefer {bundled,esmini}` | which corpus wins a name collision |
| `--fix-xodr` | repair OpenDRIVE 1.7 direct junctions crdesigner rejects; the repair approximates the source topology and is recorded in `bundle.json` |
| `--no-plain-copy` | skip the trigger-free, XSD-valid `scenario_plain.xml` |
| `--no-timeline` | skip the per-step condition timeline |
| `--json-out FILE` | write the result as JSON |
| `--no-isolate` | convert a batch in one interpreter — faster, but esmini state leaks and can truncate later scenarios |
| `-v` | debug logging (before the subcommand: `-m osc2cr_extended -v convert …`) |

### inspect — read the triggers back out of a CommonRoad file

```bash
python3 -m osc2cr_extended inspect osc2cr_output/cut-in_simple/scenario.xml
python3 -m osc2cr_extended inspect osc2cr_output/cut-in_simple/scenario.xml --json
```

### serve — the interactive viewer

```bash
python3 -m osc2cr_extended serve                          # → http://127.0.0.1:8000/
python3 -m osc2cr_extended serve --port 8800
python3 -m osc2cr_extended serve --host 0.0.0.0 --port 8000
```

The viewer runs work on the server, so you do not need a second terminal:
**Convert** turns any `.xosc` from the corpus into a bundle, and **Run co-sim**
runs the loaded bundle closed-loop against the reactive planner and switches
the view to it. The `esmini` / `co-sim` pair next to them only *chooses which
existing run to draw* — `co-sim` stays disabled until a run exists.

The same two actions over HTTP:

```bash
curl -X POST http://127.0.0.1:8000/api/convert -H 'Content-Type: application/json' \
  -d '{"xosc":"cut-in_simple"}'

curl -X POST http://127.0.0.1:8000/api/cosim -H 'Content-Type: application/json' \
  -d '{"scenario":"cut-in_simple","driver":"planner"}'
```

`/api/cosim` also accepts `desired_velocity`, `max_steps` and `timeout`.

### cosim — closed loop against a planner

```bash
# validation leg: esmini drives, both condition streams are compared
python3 -m osc2cr_extended cosim osc2cr_output/cut-in_simple

# the real test: externalise the ego and let commonroad-rp drive it
python3 -m osc2cr_extended cosim osc2cr_output/cut-in_simple --driver planner

# batch, with esmini's 3D window open
python3 -m osc2cr_extended cosim osc2cr_output/* --driver esmini --viewer
```

| Flag | Effect |
|---|---|
| `--driver {esmini,planner}` | who drives the ego (default `esmini`) |
| `--max-steps N` | step cap (default: bundle length + 20) |
| `--config FILE` | `ReactivePlannerConfiguration` yaml |
| `--desired-velocity V` | planner target speed [m/s] |
| `--ego NAME` | override the detected ego entity |
| `--viewer` | open esmini's 3D window |
| `--no-write` | don't write `cosim_trace.json` into the bundle |
| `--no-isolate` | run a batch in one interpreter |

`--driver planner` needs the `cosim` extra and patch `0003`.

### benchmark — timed batch + report

```bash
python3 -m osc2cr_extended benchmark                       # built-in 13-scenario suite
python3 -m osc2cr_extended benchmark --repeat 3
python3 -m osc2cr_extended benchmark cut-in_simple acc-test
python3 -m osc2cr_extended benchmark --output ./my_report
```

Writes `benchmark.md` + `benchmark.json` to `osc2cr_output/benchmarks/`.
The reference results shipped in `benchmarks/` are never overwritten.

---

## 2. Tests

```bash
cd ~/Bachelor_Conversion
python3 osc2cr_extended/tests/test_embed.py      # 17 checks — embedding round trip
python3 osc2cr_extended/tests/test_pipeline.py   # 28 checks — end-to-end bundles
python3 osc2cr_extended/tests/test_cosim.py      # 28 checks — externalisation + oracle
```

Browser and viewer tests need extra tooling:

```bash
# viewer smoke test — jsdom ≤ 24 (jsdom 27 is ESM-only and wants Node 22+)
npm install jsdom@24
JSDOM_DIR=$PWD node osc2cr_extended/tests/viewer_smoke.mjs

# real browser
python3 -m pip install playwright && python3 -m playwright install chromium
python3 osc2cr_extended/tests/ui_test.py         # starts its own viewer
```

---

## 3. Standalone co-simulation (reactive planner)

Needs the `cosim` extra and patch `0003`. These are the chapter's standalone
experiments, separate from the CLI's `cosim` command.

```bash
# one closed-loop run
python3 -m osc2cr_extended.cosim.reactive_loop
python3 -m osc2cr_extended.cosim.reactive_loop --desired-velocity 8.0 --viewer
python3 -m osc2cr_extended.cosim.reactive_loop --config path/to/cosim.yaml

# convert a .xosc into the CommonRoad scenario the loop consumes
python3 -m osc2cr_extended.cosim.scenario_setup

# parameter sweep over a scenario family → sweep_results.csv
python3 -m osc2cr_extended.cosim.scenario_sweep
python3 -m osc2cr_extended.cosim.scenario_sweep \
    --xosc "$OSC2CR_ESMINI_HOME/resources/xosc/keep_lateral_distance_external.xosc" \
    --desired-velocity 8.0 --output ./sweep

# render a finished run to GIF (needs the `viz` extra)
python3 -m osc2cr_extended.cosim.visualize
python3 -m osc2cr_extended.cosim.visualize \
    --input osc2cr_output/cosim/cosim_result.xml \
    --output osc2cr_output/cosim/animation.gif
```

---

## 4. Frenetix backend

Needs the `frenetix` extra, which **conflicts** with `cosim` — use a separate
environment (`frenetix-cosim` on this machine). Conversion happens in the
converter's environment first, because Frenetix pins an incompatible
`commonroad-io`:

```bash
# step 1 — in the cr-osc-converter environment
python3 -m osc2cr_extended.cosim.frenetix.convert_scenario \
    --output osc2cr_output/scenario_init.xml

# step 2 — in the frenetix environment
python3 -m osc2cr_extended.cosim.frenetix.cosimulation
python3 -m osc2cr_extended.cosim.frenetix.cosimulation --desired-velocity 8.0

# sweeps
python3 -m osc2cr_extended.cosim.frenetix.generate_sweep_scenarios
python3 -m osc2cr_extended.cosim.frenetix.scenario_sweep
python3 -m osc2cr_extended.cosim.frenetix.visualize
```

---

## 5. Examples and viewers

```bash
# run the Transcription/Translation/Interpretation pipeline over the bundled sample scenarios
python3 -m osc2cr_extended.examples.run_examples

# open a raw .xosc in esmini's native viewer
python3 -m osc2cr_extended.examples.view_osc path/to/scenario.xosc
python3 -m osc2cr_extended.examples.view_osc scenario.xosc --window 1920x1080

# open a CommonRoad .xml in the CommonRoad Scenario Designer GUI
python3 -m osc2cr_extended.examples.view_cr osc2cr_output/cut-in_simple/scenario.xml
```

---

## 6. Web surfaces

### One-shot pipeline run

Runs all three strategies on a single `.xosc` and writes `summary.json`,
`preview.png` and `replay.gif` alongside the per-strategy files:

```bash
python3 -m osc2cr_extended.web.run_pipeline path/to/scenario.xosc
python3 -m osc2cr_extended.web.run_pipeline path/to/scenario.xosc ./out
```

### crdesigner overlay

Install `web/overlay/crdesigner_triggers.user.js` in Tampermonkey, then visit
<https://crdesigner.cps.cit.tum.de>. See [web/overlay/README.md](web/overlay/README.md).

---

## 7. Patches and packaging

```bash
# apply the upstream fixes (0001 needed to convert, 0003 to use --driver planner)
git -C ../commonroad-openscenario-converter apply \
    osc2cr_extended/patches/0001-converter-ego-filter-union-type.patch
git -C ../commonroad-openscenario-converter apply \
    osc2cr_extended/patches/0002-converter-examples-and-scenarios.patch
git -C ../reactive-planner apply \
    osc2cr_extended/patches/0003-reactive-planner-api-and-dataclass-fixes.patch

# install
pip install ./osc2cr_extended
pip install './osc2cr_extended[cosim,viz,dev]'
pip install -e ./osc2cr_extended

# drop into the converter's repository instead — no install
cp -r osc2cr_extended /path/to/commonroad-openscenario-converter/

# build a wheel
cd osc2cr_extended && python3 -m build --wheel
```

---

## 8. A full run from scratch

```bash
cd ~/Bachelor_Conversion

python3 -m osc2cr_extended list
python3 -m osc2cr_extended convert cut-in_simple acc-test drop-bike
python3 -m osc2cr_extended inspect osc2cr_output/cut-in_simple/scenario.xml
python3 -m osc2cr_extended cosim   osc2cr_output/cut-in_simple --driver esmini
python3 -m osc2cr_extended cosim   osc2cr_output/cut-in_simple --driver planner
python3 -m osc2cr_extended benchmark --repeat 3
python3 -m osc2cr_extended serve
```
