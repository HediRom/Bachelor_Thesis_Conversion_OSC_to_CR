# Conversion from OpenSCENARIO to CommonRoad

**Bachelor's thesis.** Trigger-preserving conversion of **OpenSCENARIO**
scenarios to **CommonRoad** — the code, the results, and everything needed to
run both.

The stock [commonroad-openscenario-converter](https://commonroad.in.tum.de/tools/openscenario-converter)
flattens a scenario: esmini executes the storyboard, evaluates every trigger,
and the converter records the resulting positions. What lands in the CommonRoad
file is a set of trajectories — correct motion, but no answer to *why* the car
changed lane at t = 6.6 s. `osc2cr_extended` runs the same conversion, parses
the storyboard a second time for its conditions, writes them **into** the
CommonRoad XML, replays them, and co-simulates the result against a motion
planner so the triggers fire against what the planner actually does.

This folder is a self-contained export.

---

## What's in here

| Path | What it is |
|---|---|
| `osc2cr_extended/` | **the code** — the whole package, plus its own `README.md` and `COMMANDS.md` (full command reference) |
| `osc2cr_output/` | **the results** — 58 converted scenario bundles, 311 MB, exactly as measured |
| `osc2cr_extended/benchmarks/` | timed conversion + co-simulation reports over that corpus |
| `deps/` | the two dependencies that need source patches, **already patched**, plus esmini's scenario corpus |
| `scripts/` | `fetch_esmini.py`, `verify_install.py` |
| `setup.sh` | one-shot installer |
| `requirements*.txt`, `environment.yml` | pinned dependencies |

`deps/` is not vendoring for its own sake. Two upstream repositories need
changes before any of this runs, and both copies here already carry them —
see [Why `deps/` exists](#why-deps-exists).

---

## Requirements

* **Linux** (x86-64). Developed on WSL2 / Ubuntu. macOS and Windows are
  untested — esmini ships binaries for all three, but nothing here was run on
  the other two.
* **Python 3.11.** Not a preference — see [Why Python 3.11](#why-python-311).
* **~3 GB of disk**: 319 MB for this folder, ~90 MB for esmini, and 2–3 GB for
  the Python environment — crdesigner pulls in PyQt6 and SUMO.
* **Network access, once**, for `pip` and for esmini's binaries.
* **conda or venv.** `setup.sh` refuses to install into a system interpreter.

No compiler, no build step, no Docker. The viewer has no build step and no CDN
dependency — `serve` runs the HTML/JS in `osc2cr_extended/viewer/` directly.

---

## Install

```bash
cd ~/Bachelor_Conversion

conda env create -f environment.yml     # a bare Python 3.11 env named 'osc2cr'
conda activate osc2cr
./setup.sh
```

or, without conda:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
./setup.sh
```

`setup.sh` installs the pinned dependencies, installs the two patched
repositories from `deps/` and this package (all editable, so the code you read
is the code that runs), downloads esmini, and finishes by printing a checklist.
It takes a few minutes, mostly pip.

Options:

| Flag | Effect |
|---|---|
| `--no-cosim` | skip the reactive planner — conversion, replay, viewer and the `esmini` co-simulation driver still work |
| `--dev` | also install `pytest` and `playwright` for the test suite |
| `--lock` | install `requirements-lock.txt` (the exact 85-package environment) instead of the version ranges |

Re-run the checklist at any time:

```bash
python scripts/verify_install.py
```

---

## First run

```bash
osc2cr-ext list                              # 72 .xosc available, 58 bundles converted
osc2cr-ext convert cut-in_simple             # → ./osc2cr_output/cut-in_simple/
osc2cr-ext inspect osc2cr_output/cut-in_simple/scenario.xml
osc2cr-ext cosim osc2cr_output/cut-in_simple --driver planner
osc2cr-ext serve                             # → http://127.0.0.1:8000/
```

`python -m osc2cr_extended <command>` is the identical longer form and needs no
install, provided the working directory is this folder.

Expected output from the first two:

```
✓ cut-in_simple: 2 obstacles, 2 lanelets, 102 steps | 2 events / 2 conditions,
  translation: 2 mapped / 3 skipped, interpretation: 2 fires | 0.91s

cut-in_simple  [planner]
  planner   : goal-reached after 89 steps
  fired     : CutInEvent at 6.4s
  fired     : BrakeEvent at 8.6s
  agreement : 100.0% (3/3 conclusive of 3 conditions)
```

The second block is the point of the tool: the ego is now driven by the
reactive planner rather than replayed from the recording, the cut-in still
fires — half a second earlier, because the planner drives differently — and
every condition that could be decided agrees with esmini's own evaluation.

**[`osc2cr_extended/COMMANDS.md`](osc2cr_extended/COMMANDS.md) is the complete
command reference** — every entry point, flag and script.
[`osc2cr_extended/README.md`](osc2cr_extended/README.md) is the operating
manual.

---

## What a bundle contains

`osc2cr_output/<scenario>/` — one directory per converted scenario:

| File | Contents |
|---|---|
| `scenario.xml` | the CommonRoad scenario **with** an `<osc:triggers>` block |
| `scenario_plain.xml` | the same file with the block stripped — XSD-valid, for any stock CommonRoad tool |
| `triggers.json` | the parsed storyboard conditions |
| `conditions_transcription.json` | Transcription — triggers as metadata on the flat trajectories |
| `conditions_translation.json`, `report_translation.txt` | Translation — conditions mapped onto native CommonRoad constructs, and what would not map |
| `trace_interpretation.json` | Interpretation — the re-evaluable condition layer, and when each fired |
| `timeline.json` | condition state over time |
| `cosim_trace_*.json`, `cosim_*.xml` | co-simulation traces and the resulting scenarios, per driver |
| `bundle.json` | stats and per-stage timings |

Use `scenario_plain.xml` with anything that validates against the CommonRoad
schema; `scenario.xml` carries the triggers and deliberately does not validate.
That is a known, tested limitation — the block is a namespaced extension
element in a schema with no extension point. See `Known limits` in the package
README.

---

## Regenerating the results

Everything in `osc2cr_output/` and `osc2cr_extended/benchmarks/` is
reproducible:

```bash
osc2cr-ext convert $(osc2cr-ext list | awk '/\[/{print $1}')   # the whole corpus
osc2cr-ext benchmark --repeat 3                                # → benchmarks/

BUNDLES=$(ls -d osc2cr_output/*/)
osc2cr-ext cosim $BUNDLES --driver esmini
osc2cr-ext cosim $BUNDLES --driver planner
```

Each scenario converts in its own interpreter — esmini leaks state between
scenarios, and a handful of them crash it outright (see
[Troubleshooting](#troubleshooting)). The isolation is why a batch never dies
halfway.

---

## Tests

```bash
./setup.sh --dev            # or: pip install -r requirements-dev.txt
cd osc2cr_extended

python tests/test_embed.py       # 17 checks — embedding round trip, XSD behaviour
python tests/test_pipeline.py    # 32 checks — end-to-end bundle contents
python tests/test_cosim.py       # 28 checks — externalisation, differential oracle
```

All three pass on a fresh install of this folder. The two browser tests are
optional and need extra tooling:

```bash
npm install jsdom && node tests/viewer_smoke.mjs   # jsdom <= 24 on Node 20
playwright install chromium && python tests/ui_test.py
```

---

## Environment variables

| Variable | Effect |
|---|---|
| `OSC2CR_OUTPUT_DIR` | where bundles are written. Default `./osc2cr_output` — i.e. relative to your working directory, so pin it if you move around |
| `OSC2CR_ESMINI_HOME` | an esmini installation to use instead of the discovered one |
| `OSC2CR_FRENETIX_HOME` | a Frenetix-Motion-Planner checkout (see [Not included](#not-included)) |

Nothing is ever written inside the package itself.

---

## Why `deps/` exists

Neither of these works from PyPI as published, so both are shipped here as
source checkouts with the fixes already applied. `setup.sh` installs them
editable.

| `deps/` | Patch | What it fixes |
|---|---|---|
| `commonroad-openscenario-converter` | `0001` | `Osc2CrConverter.ego_filter` is annotated `Optional[re.Pattern, str]`, which is not a valid `Optional`. A genuine upstream type bug. |
| | `0002` | Example scripts and two `.xosc` fixtures the test suite needs. |
| `reactive-planner` | `0003` | Mutable dataclass defaults in `config.py`, the `commonroad_dc` → `commonroad_clcs` import migration, and the `RoutePlanner` signature change. Dependency-version drift, not a defect. |
| `esmini` | — | not a patch: esmini's `.xosc`/`.xodr` corpus and catalogs, **without** the 106 MB of 3D models, which headless conversion never loads. 63 of the 72 available scenarios come from here; the other 9 are the converter's own. |

The patches themselves are in
[`osc2cr_extended/patches/`](osc2cr_extended/patches/) if you would rather
apply them to your own checkouts. `deps/commonroad-openscenario-converter`
carries 0001 and 0002; `deps/reactive-planner` carries 0003.

esmini's *binaries* are not in `deps/` — the converter downloads them (v2.29.3,
a 33 MB download that unpacks to ~90 MB) into its own package directory.
`scripts/fetch_esmini.py` does that during setup, so a `cosim` run that happens
before the first `convert` still finds the shared libraries.

---

## Why Python 3.11

3.9 is the floor the converter itself sets; 3.11 is what every number in
`osc2cr_output/` and `benchmarks/` was measured on. The window is narrow at
both ends:

* **3.8 and older are refused at import**, deliberately. The converter,
  commonroad and crdesigner all import fine on 3.8, so it runs far enough to
  *look* correct while producing **0 lanelets for every scenario** — that
  crdesigner predates `crdesigner.common.config`, so the geo-reprojection guard
  cannot be applied. Failing loudly beats hours of empty maps.
* **3.12 and newer** are ruled out by `commonroad-reactive-planner`, which pins
  `python = ">=3.8,<3.12"`.

---

## Troubleshooting

**`osc2cr-ext: command not found`** — the environment is not activated, or
`setup.sh` has not run. `python -m osc2cr_extended` from this folder works
either way.

**"esmini binaries not found" / co-simulation fails immediately** —
`python scripts/fetch_esmini.py`. The libraries are resolved once at import, so
a shell that started before the download will not see them.

**A scenario "dumped core" during conversion** — esmini segfaults on a handful
of scenarios, `cut-in` among them, with the full corpus and the stripped one
alike. It is an engine crash, not a converter bug. Because each scenario runs
in its own interpreter, the rest of a batch is unaffected; there is simply no
bundle for that one.

**Every scenario converts with 0 lanelets** — the geo-reprojection guard is not
being applied. `python scripts/verify_install.py` prints the warning that says
why; the usual cause is the wrong interpreter.

**`scenario.xml` fails XSD validation** — expected, by design. Use
`scenario_plain.xml`, or call `strip_triggers()`.

**A converted scenario is not the one you expected** — five names exist in both
the converter's corpus and esmini's *with different content*, `acc-test` being
the worst case. `osc2cr-ext list` flags them, conversion warns, and
`--prefer esmini` or a full path settles it.

**`pip` resolves conflicting versions** — install with `./setup.sh --lock`,
which pins the exact 85-package environment these results came from.

---

## Not included

* **esmini's 3D models** (106 MB). Headless conversion never loads them. Clone
  [esmini](https://github.com/esmini/esmini) and point `OSC2CR_ESMINI_HOME` at
  it if you want the rendered viewer.
* **Frenetix-Motion-Planner.** `osc2cr_extended/cosim/frenetix/` drives the same
  co-simulation loop with it, but Frenetix and the reactive planner pin
  *incompatible* versions of `commonroad-io` and the drivability checker, so
  they cannot share an environment. Install Frenetix in a second environment,
  convert scenarios in this one, and hand the finished CommonRoad files over —
  `cosim/frenetix/convert_scenario.py` exists for exactly that.

---

## Licenses

`osc2cr_extended/` is BSD-3-Clause (`osc2cr_extended/LICENSE`).
`deps/commonroad-openscenario-converter` is BSD-3-Clause and
`deps/reactive-planner` is BSD-3-Clause, both © TUM Cyber-Physical Systems
Group, redistributed here with the patches described above.
`deps/esmini/resources` is redistributed under the Mozilla Public License 2.0
(`deps/esmini/LICENSE`), © esmini contributors.
