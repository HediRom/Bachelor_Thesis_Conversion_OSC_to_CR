# osc2cr_extended

**An extension package for [commonroad-openscenario-converter](https://commonroad.in.tum.de/tools/openscenario-converter) that keeps the storyboard's triggers.**

The stock converter flattens a scenario: esmini executes the storyboard,
evaluates every trigger, and the converter records the resulting positions.
What lands in the CommonRoad file is a set of trajectories — correct motion, but
no answer to *why* the car changed lane at t = 6.6 s. The conditional structure
that made it a scenario *specification* rather than a *recording* is gone.

That matters because the triggers are the reusable part. A cut-in that fires at
"headway < 1.0 s" is a *family* of scenarios; a cut-in that happens at
t = 6.6 s is one recording of one run.

This package runs the same conversion, parses the storyboard a second time for
its conditions, writes them **into** the CommonRoad XML, replays them, and
co-simulates the result against a motion planner so the triggers fire against
what the planner actually does.

> **[REPORT.md](REPORT.md)** is the full technical report — motivation,
> architecture, correctness findings, benchmark analysis, limitations.
> **[COMMANDS.md](COMMANDS.md)** is the complete command reference — every
> entry point, flag and script. This file is the operating manual.

---

## Install

The package has no dependency on any particular checkout layout. Pick whichever
of these fits.

### 1. Inside the converter's repository (what this folder is designed for)

Drop the folder into the converter's repository root, next to
`osc_cr_converter/`:

```bash
cp -r osc2cr_extended /path/to/commonroad-openscenario-converter/
cd /path/to/commonroad-openscenario-converter
python -m osc2cr_extended list
```

No install step. `import osc2cr_extended` works from the converter root, and
the converter's own `setup.py` (`find_packages()`) picks the subpackages up if
you later `pip install -e .` the converter.

### 2. Standalone, alongside an installed converter

```bash
pip install ./osc2cr_extended            # brings the converter in as a dependency
osc2cr-ext list
```

### Optional extras

| Extra | Brings | Needed for |
|---|---|---|
| `cosim` | reactive planner, route planner, drivability checker | `cosim --driver planner` |
| `frenetix` | Frenetix-Motion-Planner | `cosim/frenetix/` |
| `viz` | matplotlib, imageio | GIF rendering of replays and co-simulations |
| `dev` | pytest | the test suite |

`cosim` and `frenetix` pin **incompatible** versions of `commonroad-io` and the
drivability checker. Install one or the other, not both — see
[cosim/frenetix/convert_scenario.py](cosim/frenetix/convert_scenario.py), which
exists precisely so the conversion step can run in the converter's environment
and hand Frenetix a finished CommonRoad file.

### Requirements

**Python ≥ 3.9** — the floor the converter itself sets. Developed and tested on
**3.11**. Older interpreters are refused with an actionable message rather than
allowed to half-work: on Python 3.8 the converter, commonroad and crdesigner all
import, so it runs far enough to *look* correct while producing **0 lanelets for
every scenario** — that crdesigner predates `crdesigner.common.config`, so the
geo-reprojection guard cannot be applied. Failing loudly beats hours of empty
maps.

esmini is found automatically: the copy the converter already bundles
(`osc_cr_converter/wrapper/esmini/esmini_v2.29.3/`) supplies the shared
libraries, which pins the co-simulation to the same engine build the converter
runs. A full esmini checkout, when present, additionally supplies its `.xosc`
corpus.

### Environment overrides

| Variable | Effect |
|---|---|
| `OSC2CR_ESMINI_HOME` | esmini installation (the dir holding `bin/`, `resources/`) |
| `OSC2CR_OUTPUT_DIR` | where bundles are written. Default `./osc2cr_output` |
| `OSC2CR_FRENETIX_HOME` | Frenetix repository root, for a non-installed checkout |

Nothing is ever written inside the package — an install may be read-only, and
results should not depend on where pip put it.

---

## Quick start

```bash
osc2cr-ext list                             # what can be converted
osc2cr-ext convert cut-in_simple acc-test   # convert into ./osc2cr_output/
osc2cr-ext inspect osc2cr_output/cut-in_simple/scenario.xml
osc2cr-ext cosim  osc2cr_output/cut-in_simple --driver planner
osc2cr-ext serve                            # → http://127.0.0.1:8000/
osc2cr-ext benchmark --repeat 3
```

`python -m osc2cr_extended <command>` is identical and needs no install.

---

## Patches to the upstream repositories

`patches/` carries the changes this work required in its dependencies. They are
**not applied automatically** — apply them against your own checkouts:

```bash
git -C /path/to/commonroad-openscenario-converter apply \
    osc2cr_extended/patches/0001-converter-ego-filter-union-type.patch
```

| Patch | What it fixes |
|---|---|
| `0001-converter-ego-filter-union-type` | `Osc2CrConverter.ego_filter` is annotated `Optional[re.Pattern, str]`, which is not a valid `Optional`. Genuine upstream type bug; belongs in a PR. |
| `0002-converter-examples-and-scenarios` | Example scripts and two `.xosc` fixtures used by this package's tests. |
| `0003-reactive-planner-api-and-dataclass-fixes` | Mutable dataclass defaults in `config.py`, the `commonroad_dc` → `commonroad_clcs` import migration, and the `RoutePlanner` signature change. Dependency-version drift, not a defect. |

Only 0001 is needed for conversion; 0003 is needed for `--driver planner`.

---

## Layout

```
osc2cr_extended/
  paths.py            dependency + asset discovery (converter, esmini, output)
  pipeline.py         the timed conversion: stock path + trigger path + bundle writer
  embed.py            triggers ↔ CommonRoad XML (embed / extract / strip)
  params.py           OpenSCENARIO parameter resolution for entity names
  conditions_ext.py   extended condition taxonomy (parse + evaluate)
  coverage.py         which source conditions survived parsing, and which did not
  roadmanager.py      esmini RoadManager binding: lane position → world (x, y)
  live.py             condition timeline, Interpretation replay, evaluate-state session
  xodr_repair.py      salvages OpenDRIVE files crdesigner cannot read
  server.py           viewer host + JSON API
  benchmark.py        timed batch + report generation
  cli.py              the osc2cr-ext command

  strategies/         the three representation strategies
    transcription.py    triggers as metadata on flat trajectories
    translation.py      conditions mapped onto native CR constructs
    interpretation.py   re-evaluable condition layer
    condition_evaluator.py
    merge.py            runs all three and reunites them with the trajectories
    shared/             the condition taxonomy they share
      storyboard_parser.py · condition_model.py
      triggers_export.py   · road_network.py

  cosim/              closed loop: esmini ↔ motion planner
    esmini_interface.py   ctypes wrapper: step / read / write, one tick at a time
    loop.py               ego externalisation, observation, differential oracle
    reactive_loop.py      standalone reactive-planner co-simulation
    cosimulation.py · scenario_sweep.py · scenario_setup.py · visualize.py
    frenetix/             the same loop driven by Frenetix-Motion-Planner

  viewer/             index.html · app.js · style.css — no build step, no CDN
  web/
    overlay/            Tampermonkey userscript overlaying triggers on
                        crdesigner.cps.cit.tum.de
    vscode-extension/   VS Code extension source (run `npm install` to build)
    vscode_bridge.py    the extension's Python side

  data/
    configurations/     co-simulation + converter YAML
    frenetix/           Frenetix's configuration set
    scenarios/          this package's own .xosc test corpus
  benchmarks/         reference results shipped with the package
  patches/            changes required in the upstream repositories
  examples/           runnable demos and scenario viewers
  tests/
```

### Tests

```bash
python tests/test_embed.py       # embedding round trip + CommonRoad compatibility
python tests/test_pipeline.py    # end-to-end bundle checks
python tests/test_cosim.py       # externalisation transform + differential oracle

npm install jsdom && node tests/viewer_smoke.mjs   # jsdom ≤ 24 on Node 20
pip install playwright && playwright install chromium
python tests/ui_test.py                            # starts its own viewer
```

---

## HTTP API

| Route | Purpose |
|---|---|
| `GET /api/scenarios` | converted bundles with stats and timings |
| `GET /api/corpus` | `.xosc` files available for conversion |
| `GET /api/bundle/<name>/<file>` | a file from a bundle |
| `POST /api/convert` | `{"xosc": "cut-in_simple"}` → convert on demand |
| `POST /api/cosim` | `{"scenario", "driver"}` → run a bundle closed-loop |
| `POST /api/evaluate` | `{"scenario", "time_s", "entities"}` → which conditions hold |

Both `convert` and `cosim` run in a child process behind one lock — esmini is
reached through a process-wide handle and some scenarios crash it outright, so
neither can be re-entrant and neither may take the server down.

```bash
curl -X POST http://127.0.0.1:8000/api/evaluate -H 'Content-Type: application/json' \
  -d '{"scenario":"cut-in_simple","time_s":1.0,
       "entities":{"Ego":{"x":0,"y":0,"speed":20,"heading":0},
                   "OverTaker":{"x":14,"y":0,"speed":20,"heading":0}}}'
```

---

## Python API

```python
from osc2cr_extended import convert, extract_triggers, has_triggers
from osc2cr_extended.cosim import run_cosim

result = convert("cut-in_simple")            # → ConversionResult
assert result.ok and has_triggers(result.files["scenario_xml"])

triggers = extract_triggers(result.files["scenario_xml"])
report = run_cosim(result.bundle_dir, driver="planner")
```

`ConversionResult` carries `name`, `ok`, `error`, `bundle_dir`, `stats`,
per-stage `timings`, and `files` — the bundle's artifacts keyed by name:
`scenario_xml` (enriched), `scenario_plain_xml` (triggers stripped, XSD-valid),
`triggers_json`, `conditions_transcription_json`, `conditions_translation_json`, `report_translation_txt`,
`trace_interpretation_json`, `timeline_json`, `bundle_json`.

---

## Known limits

The `<osc:triggers>` block is a namespaced extension element in a schema that
does not declare one, so an enriched file fails XSD validation until
`strip_triggers()` is called — and a read/write round trip through
`commonroad-io` drops the block, because `Scenario` has nowhere to hold it.
Both are exercised as explicit tests in `tests/test_embed.py` rather than left
as surprises. See [REPORT.md](REPORT.md) for the rest.
