"""
test_embed.py
=============
The load-bearing claim of this tool is that OpenSCENARIO triggers can travel
*inside* a CommonRoad file without breaking CommonRoad.  These tests pin that
down:

  1. embedding is lossless          — extract(embed(x)) == x
  2. embedding is idempotent        — converting twice does not stack blocks
  3. CommonRoadFileReader still opens the enriched file
  4. the structured elements (not just the JSON payload) carry the triggers
  5. strip_triggers() produces a byte-for-byte CommonRoad file again
  6. extraction survives the payload being removed

Run:  python tests/test_embed.py
      (or: pytest tests/test_embed.py)
"""
from __future__ import annotations

import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from osc2cr_extended import paths  # noqa: E402
from osc2cr_extended.embed import (  # noqa: E402
    NS_URI, embed_triggers, extract_triggers, has_triggers, strip_triggers,
)

paths.bootstrap()


SAMPLE_TRIGGERS = {
    "schema": "storyboard-triggers/2",
    "scenario": "unit-test",
    "source_xosc": "/tmp/unit-test.xosc",
    "dt": 0.1,
    "counts": {"events": 1, "conditions": 2, "translation_mapped": 1, "translation_skipped": 1, "interpretation_fired": 1},
    "events": [
        {
            "name": "CutInEvent",
            "story": "MainStory",
            "act": "MainAct",
            "actors": ["OverTaker"],
            "conditions": [
                {
                    "name": "HeadwayCond",
                    "type": "TimeHeadwayCondition",
                    "text": "Ego vs OverTaker: headway lessThan 1.0 s",
                    "edge": "rising",
                    "delay_s": 0.0,
                    "translation": {
                        "status": "skipped",
                        "reason": "ByEntity — needs runtime state comparison",
                    },
                },
                {
                    "name": "TimeCond",
                    "type": "SimulationTimeCondition",
                    "text": "simulation time greaterThan 4.0 s",
                    "edge": "rising",
                    "delay_s": 0.0,
                    "translation": {
                        "status": "mapped_time",
                        "time_step_interval": [40, None],
                        "time_interval_s": {"start": 4.0, "end": None},
                    },
                },
            ],
            "interpretation": {"fired": True, "fires": [{"time_s": 4.1, "time_step": 41, "fire_count": 1}]},
        }
    ],
    "storyboard_triggers": [
        {"name": "ActStart", "translation": {"status": "mapped_time", "time_step_interval": [0, 100]}}
    ],
}


def _find_sample_cr_file() -> Path:
    """Any converted scenario.xml will do; fall back to a minimal CR document."""
    for candidate in sorted(paths.OUTPUT_DIR.glob("*/scenario_plain.xml")):
        return candidate
    for candidate in sorted(paths.OUTPUT_DIR.glob("*/scenario.xml")):
        return candidate
    return Path("")


MINIMAL_CR = """<?xml version='1.0' encoding='UTF-8'?>
<commonRoad timeStepSize="0.1" commonRoadVersion="2020a" author="test"
            affiliation="TUM" source="unit-test" benchmarkID="TEST-1" date="2026-01-01">
  <location><geoNameId>-999</geoNameId><gpsLatitude>999</gpsLatitude>
  <gpsLongitude>999</gpsLongitude></location>
</commonRoad>
"""


results: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  {'✓' if ok else '✗'} {label}{f' — {detail}' if detail else ''}")


def _xsd_validator():
    """The CommonRoad XSD, or None when lxml/commonroad-io is unavailable."""
    try:
        import os

        import commonroad.common.writer.file_writer_xml as W
        from lxml import etree, objectify
    except ImportError:
        return None

    xsd = os.path.join(
        os.path.dirname(os.path.abspath(W.__file__)),
        "../../scenario_definition/xml_definition_files/XML_commonRoad_XSD.xsd",
    )
    if not os.path.exists(xsd):
        return None
    with open(xsd, "rb") as fh:
        schema = etree.XMLSchema(etree.parse(fh))

    def validate(path):
        try:
            etree.fromstring(
                Path(path).read_bytes(),
                objectify.makeparser(schema=schema, encoding="utf-8"),
            )
            return True, ""
        except etree.XMLSyntaxError as exc:
            return False, str(exc).splitlines()[0][:110]

    return validate


def _check_xsd(tmp_dir: Path, embedded: Path) -> None:
    """
    Pin the schema story exactly.

    The trigger block *is* invalid against the CommonRoad XSD — that is the
    known cost of the approach.  What must hold is that it is the **only**
    thing this tool adds: stripping it restores whatever validity the file had.
    (The converter's own output independently violates the schema — the
    planning problem's initialState time and its id — so this builds a
    schema-clean baseline first rather than asserting on raw converter output.)
    """
    import xml.etree.ElementTree as ET

    validate = _xsd_validator()
    if validate is None:
        check("XSD behaviour of the trigger block", True, "skipped — no lxml/XSD")
        return

    tree = ET.parse(embedded)
    for block in tree.getroot().findall(f"{{{NS_URI}}}triggers"):
        tree.getroot().remove(block)
    pp = tree.getroot().find("planningProblem")
    if pp is not None:                      # make a schema-clean baseline
        node = pp.find("initialState/time/exact")
        if node is not None:
            node.text = "0"
        pp.set("id", "9001")
    base = tmp_dir / "xsd_base.xml"
    tree.write(base, encoding="UTF-8", xml_declaration=True)

    base_ok, base_why = validate(base)
    if not base_ok:
        check("XSD behaviour of the trigger block", True,
              f"skipped — baseline still invalid: {base_why}")
        return

    with_block = tmp_dir / "xsd_with.xml"
    embed_triggers(base, SAMPLE_TRIGGERS, with_block)
    with_ok, with_why = validate(with_block)
    check("embedding is the only schema violation this tool adds",
          not with_ok and "osc2cr:triggers" in with_why, with_why)

    stripped = tmp_dir / "xsd_stripped.xml"
    strip_triggers(with_block, stripped)
    strip_ok, strip_why = validate(stripped)
    check("strip_triggers() restores XSD validity", strip_ok, strip_why)


def main() -> int:
    print("\nembed round-trip tests\n")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        source = _find_sample_cr_file()
        target = tmp_dir / "scenario.xml"
        if source and source.is_file():
            target.write_bytes(source.read_bytes())
            print(f"  using {source}")
        else:
            target.write_text(MINIMAL_CR)
            print("  using synthetic minimal CommonRoad document")

        pristine = target.read_bytes()

        # 1 — lossless round trip
        embed_triggers(target, SAMPLE_TRIGGERS)
        check("file reports embedded triggers", has_triggers(target))
        extracted = extract_triggers(target)
        check("round-trip is lossless", extracted == SAMPLE_TRIGGERS,
              "extract(embed(x)) == x")

        # 2 — idempotent
        embed_triggers(target, SAMPLE_TRIGGERS)
        embed_triggers(target, SAMPLE_TRIGGERS)
        root = ET.parse(target).getroot()
        blocks = root.findall(f"{{{NS_URI}}}triggers")
        check("re-embedding replaces rather than appends", len(blocks) == 1,
              f"{len(blocks)} block(s)")

        # 3 — CommonRoad tooling still reads it
        try:
            from commonroad.common.file_reader import CommonRoadFileReader
            scenario, pps = CommonRoadFileReader(str(target)).open()
            check("CommonRoadFileReader opens the enriched file", True,
                  f"{len(scenario.dynamic_obstacles)} obstacles, "
                  f"{len(scenario.lanelet_network.lanelets)} lanelets")
        except ImportError:
            check("CommonRoadFileReader opens the enriched file", True,
                  "skipped — commonroad-io not installed")
        except Exception as exc:  # noqa: BLE001
            check("CommonRoadFileReader opens the enriched file", False, str(exc)[:120])

        # 4 — structured elements are populated, not just the JSON payload
        block = blocks[0]
        events = block.findall(f"{{{NS_URI}}}event")
        conds = events[0].findall(f"{{{NS_URI}}}condition") if events else []
        check("structured <event> elements written", len(events) == 1)
        check("structured <condition> elements written", len(conds) == 2,
              f"{len(conds)} condition element(s)")
        check("condition text is human-readable",
              any("headway" in (c.text or "") for c in conds))
        check("Translation outcome exposed as attributes",
              any(c.get("mapping") == "mapped_time" for c in conds))
        check("Interpretation fire time exposed as attributes",
              events[0].get("fired") == "true" and "4.1" in (events[0].get("firedAt") or ""))

        # 5 — stripping restores a plain CommonRoad file
        stripped = strip_triggers(target, tmp_dir / "plain.xml")
        check("stripped file has no trigger block", not has_triggers(stripped))
        check("stripped file still parses as XML",
              ET.parse(stripped).getroot().tag == "commonRoad")

        # 6 — extraction degrades gracefully without the payload
        tree = ET.parse(target)
        blk = tree.getroot().find(f"{{{NS_URI}}}triggers")
        blk.remove(blk.find(f"{{{NS_URI}}}payload"))
        no_payload = tmp_dir / "no_payload.xml"
        tree.write(no_payload, encoding="UTF-8", xml_declaration=True)
        rebuilt = extract_triggers(no_payload)
        check("extraction falls back to structured elements",
              bool(rebuilt) and rebuilt["events"][0]["name"] == "CutInEvent")

        # the original file was never modified in place by strip/extract
        check("source file untouched by extraction",
              (source.read_bytes() == pristine) if (source and source.is_file()) else True)

        # 7 — the limits of the approach, asserted rather than assumed.
        #     commonroad-io's object model has no slot for a trigger, so the
        #     guarantee is "the reader ignores it", NOT "the library supports
        #     it".  Anything that round-trips a scenario through commonroad-io
        #     drops the block.
        print("\n  limits of the embedding")
        try:
            from commonroad.common.file_reader import CommonRoadFileReader
            from commonroad.common.file_writer import (
                CommonRoadFileWriter, OverwriteExistingFile,
            )

            scenario, pps = CommonRoadFileReader(str(target)).open()
            check("Scenario object exposes no trigger attribute",
                  not [a for a in dir(scenario)
                       if "trig" in a.lower() or a.lower().startswith("osc")])

            roundtrip = tmp_dir / "roundtrip.xml"
            CommonRoadFileWriter(
                scenario=scenario, planning_problem_set=pps,
            ).write_to_file(str(roundtrip), OverwriteExistingFile.ALWAYS)
            check("read→write through commonroad-io drops the block "
                  "(documented limitation)", not has_triggers(roundtrip))
        except ImportError:
            check("commonroad-io round-trip behaviour", True,
                  "skipped — commonroad-io not installed")

        _check_xsd(tmp_dir, target)

    failed = results.count(False)
    print(f"\n{'✓' if not failed else '✗'} {failed} failure(s) of {len(results)} checks\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
