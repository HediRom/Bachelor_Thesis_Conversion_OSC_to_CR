"""
embed.py
========
Carries OpenSCENARIO triggers *inside* the CommonRoad file.

Motivation
----------
The existing commonroad-openscenario-converter flattens a scenario: esmini
evaluates every trigger and only the resulting trajectories survive.  The
conditional structure — the part that says *why* the cut-in happens — is gone
by the time a ``.xml`` is written, and CommonRoad's schema has nowhere to put
it.

This module attaches the trigger model to the CommonRoad XML as a single
element in a **private namespace**::

    <commonRoad ...>
      <lanelet .../>
      <dynamicObstacle .../>
      <osc:triggers xmlns:osc="urn:osc2cr:triggers:1" schema="osc-triggers/1">
        <osc:event name="CutInEvent" story="..." act="..." actors="Ego">
          <osc:condition name="..." type="TimeHeadwayCondition"
                         mapping="mapped_time" fired="true" firedAt="4.10">
            Ego vs Target: headway lessThan 1.0 s
          </osc:condition>
        </osc:event>
        <osc:payload encoding="json">{ ...full triggers.json... }</osc:payload>
      </osc:triggers>
    </commonRoad>

Two representations sit side by side on purpose:

* the ``<osc:event>`` / ``<osc:condition>`` elements are human-readable and
  greppable — you can open the CommonRoad file and see the triggers;
* ``<osc:payload>`` holds the exact ``triggers.json`` document so extraction is
  lossless.

Ignored, not supported
----------------------
commonroad-io has no extension mechanism: its object model is closed.  What
makes this work is mechanical — ``commonroad/common/reader/file_reader_xml.py``
looks elements up by *unqualified* tag name (``xml_node.findall("lanelet")``),
and an unqualified name never matches a namespaced element.  So the reader
opens the enriched file and returns the correct scenario, but:

* the ``Scenario`` object does not carry the triggers, and
* a read→write round trip through commonroad-io **drops the block**, because
  the writer serialises the object model.

Both are asserted in ``tests/test_embed.py``.  The CommonRoad file is therefore
a faithful carrier for consumers that read the XML; workflows that re-serialise
through commonroad-io must use the ``triggers.json`` sidecar written alongside.

Strict XSD validation rejects the foreign element — that cost is real and
isolated in the tests.  :func:`strip_triggers` removes it again, restoring
whatever validity the file had.  (The converter's own output is already
XSD-invalid for unrelated reasons, so "stripped" does not mean "valid".)
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

NS_URI = "urn:osc2cr:triggers:1"
NS_PREFIX = "osc"
SCHEMA = "osc-triggers/1"

_TRIGGERS_TAG = f"{{{NS_URI}}}triggers"
_EVENT_TAG = f"{{{NS_URI}}}event"
_CONDITION_TAG = f"{{{NS_URI}}}condition"
_STORYBOARD_TAG = f"{{{NS_URI}}}storyboardTrigger"
_PAYLOAD_TAG = f"{{{NS_URI}}}payload"

ET.register_namespace(NS_PREFIX, NS_URI)


# ---------------------------------------------------------------------------
# Building the XML subtree
# ---------------------------------------------------------------------------

def _fmt_interval(interval: Any) -> Optional[str]:
    """
    Render an interval as ``"start..end"``, with an open end left blank.

    Accepts both shapes the C report emits: a two-element list
    (``time_step_interval``) and a ``{"start": ..., "end": ...}`` dict
    (``time_interval_s``, ``velocity_interval_ms``).
    """
    if isinstance(interval, dict):
        start, end = interval.get("start"), interval.get("end")
    elif isinstance(interval, (list, tuple)) and len(interval) == 2:
        start, end = interval
    else:
        return None
    if start is None and end is None:
        return None
    return f"{'' if start is None else start}..{'' if end is None else end}"


def _condition_element(parent: ET.Element, cond: Dict[str, Any]) -> ET.Element:
    el = ET.SubElement(parent, _CONDITION_TAG)
    el.set("name", str(cond.get("name", "")))
    el.set("type", str(cond.get("type", "")))

    edge = cond.get("edge")
    if edge:
        el.set("edge", str(edge))
    delay = cond.get("delay_s")
    if delay:
        el.set("delay", str(delay))

    # Translation outcome — did this condition map onto a native CR construct?
    translation_out = cond.get("translation")
    if isinstance(translation_out, dict):
        status = translation_out.get("status")
        if status:
            el.set("mapping", str(status))
        window = _fmt_interval(translation_out.get("time_step_interval"))
        if window:
            el.set("timeStepInterval", window)
        seconds = _fmt_interval(translation_out.get("time_interval_s"))
        if seconds:
            el.set("timeIntervalSeconds", seconds)
        vel = _fmt_interval(translation_out.get("velocity_interval_ms"))
        if vel:
            el.set("velocityIntervalMs", vel)
        reason = translation_out.get("reason")
        if reason:
            el.set("skipReason", str(reason))

    el.text = str(cond.get("text", ""))
    return el


def build_triggers_element(triggers: Dict[str, Any]) -> ET.Element:
    """Turn a ``triggers.json`` dict into the ``<osc:triggers>`` subtree."""
    root = ET.Element(_TRIGGERS_TAG)
    root.set("schema", SCHEMA)
    if triggers.get("source_xosc"):
        root.set("sourceXosc", str(triggers["source_xosc"]))
    if triggers.get("dt") is not None:
        root.set("dt", str(triggers["dt"]))

    counts = triggers.get("counts", {})
    for key, attr in (
        ("events", "events"),
        ("conditions", "conditions"),
        ("translation_mapped", "mapped"),
        ("translation_skipped", "skipped"),
        ("interpretation_fired", "fired"),
    ):
        if key in counts:
            root.set(attr, str(counts[key]))

    # Coverage: how much of the source trigger logic survived parsing.  A
    # consumer needs this to tell a faithful trigger set from a partial one.
    cov = triggers.get("coverage")
    if isinstance(cov, dict):
        cov_el = ET.SubElement(root, f"{{{NS_URI}}}coverage")
        cov_el.set("sourceConditions", str(cov.get("source_conditions", 0)))
        cov_el.set("parsedConditions", str(cov.get("parsed_conditions", 0)))
        cov_el.set("preservedPct", str(cov.get("preserved_pct", 0)))
        for ctype, count in sorted((cov.get("unsupported") or {}).items()):
            un = ET.SubElement(cov_el, f"{{{NS_URI}}}unsupported")
            un.set("type", ctype)
            un.set("count", str(count))

    for ev in triggers.get("events", []):
        ev_el = ET.SubElement(root, _EVENT_TAG)
        ev_el.set("name", str(ev.get("name", "")))
        if ev.get("story"):
            ev_el.set("story", str(ev["story"]))
        if ev.get("act"):
            ev_el.set("act", str(ev["act"]))
        actors = ev.get("actors") or []
        if actors:
            ev_el.set("actors", " ".join(str(a) for a in actors))

        interpretation_out = ev.get("interpretation") or {}
        fires = interpretation_out.get("fires") or []
        ev_el.set("fired", "true" if interpretation_out.get("fired") else "false")
        if fires:
            ev_el.set("firedAt", " ".join(f"{f.get('time_s')}" for f in fires))
            ev_el.set(
                "firedAtStep", " ".join(str(f.get("time_step")) for f in fires)
            )

        for cond in ev.get("conditions", []):
            _condition_element(ev_el, cond)

    for st in triggers.get("storyboard_triggers", []):
        st_el = ET.SubElement(root, _STORYBOARD_TAG)
        st_el.set("name", str(st.get("name", "")))
        translation_out = st.get("translation")
        if isinstance(translation_out, dict):
            if translation_out.get("status"):
                st_el.set("mapping", str(translation_out["status"]))
            window = _fmt_interval(translation_out.get("time_step_interval"))
            if window:
                st_el.set("timeStepInterval", window)
            if translation_out.get("reason"):
                st_el.set("skipReason", str(translation_out["reason"]))

    payload = ET.SubElement(root, _PAYLOAD_TAG)
    payload.set("encoding", "json")
    payload.text = json.dumps(triggers, separators=(",", ":"))

    return root


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def embed_triggers(
    cr_xml: Path | str,
    triggers: Dict[str, Any],
    out_path: Optional[Path | str] = None,
) -> Path:
    """
    Append the trigger subtree to a CommonRoad XML file.

    Parameters
    ----------
    cr_xml
        Path of a CommonRoad file written by ``CommonRoadFileWriter``.
    triggers
        A ``triggers.json`` dict (schema ``storyboard-triggers/2``).
    out_path
        Where to write.  Defaults to overwriting ``cr_xml`` in place.

    Returns the path written.
    """
    src = Path(cr_xml)
    dst = Path(out_path) if out_path is not None else src

    tree = ET.parse(src)
    root = tree.getroot()

    # Replace any previously embedded block so re-runs stay idempotent
    for existing in root.findall(_TRIGGERS_TAG):
        root.remove(existing)

    root.append(build_triggers_element(triggers))

    # ET.indent needs Python 3.9+, which paths.require_python() guarantees.
    ET.indent(tree, space="  ")
    tree.write(dst, encoding="UTF-8", xml_declaration=True)
    return dst


def extract_triggers(cr_xml: Path | str) -> Optional[Dict[str, Any]]:
    """
    Read the triggers back out of a CommonRoad file.

    Returns the original ``triggers.json`` dict, or ``None`` if the file
    carries no embedded triggers.
    """
    root = ET.parse(Path(cr_xml)).getroot()
    block = root.find(_TRIGGERS_TAG)
    if block is None:
        return None

    payload = block.find(_PAYLOAD_TAG)
    if payload is not None and payload.text:
        return json.loads(payload.text)

    # Payload missing — reconstruct what we can from the structured elements
    events = []
    for ev_el in block.findall(_EVENT_TAG):
        events.append({
            "name": ev_el.get("name", ""),
            "story": ev_el.get("story"),
            "act": ev_el.get("act"),
            "actors": (ev_el.get("actors") or "").split(),
            "conditions": [
                {
                    "name": c.get("name", ""),
                    "type": c.get("type", ""),
                    "text": (c.text or "").strip(),
                    "edge": c.get("edge"),
                }
                for c in ev_el.findall(_CONDITION_TAG)
            ],
            "interpretation": {"fired": ev_el.get("fired") == "true", "fires": []},
        })
    return {"schema": SCHEMA, "events": events, "storyboard_triggers": []}


def strip_triggers(cr_xml: Path | str, out_path: Path | str) -> Path:
    """
    Write a copy of ``cr_xml`` with the trigger block removed.

    Use this when a downstream tool insists on strict CommonRoad XSD validity.
    """
    tree = ET.parse(Path(cr_xml))
    root = tree.getroot()
    for existing in root.findall(_TRIGGERS_TAG):
        root.remove(existing)
    dst = Path(out_path)
    tree.write(dst, encoding="UTF-8", xml_declaration=True)
    return dst


def has_triggers(cr_xml: Path | str) -> bool:
    """True if the CommonRoad file carries an embedded trigger block."""
    try:
        return ET.parse(Path(cr_xml)).getroot().find(_TRIGGERS_TAG) is not None
    except ET.ParseError:
        return False
