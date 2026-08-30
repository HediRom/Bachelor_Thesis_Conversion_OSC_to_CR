"""
params.py
=========
Resolves OpenSCENARIO parameter references in **entity names**.

``shared/storyboard_parser.py`` already substitutes ``$name`` references in
numeric attributes (``value="$HeadwayTime_LaneChange"`` → ``0.4``), but entity
references are read verbatim.  Real scenarios lean on this heavily — esmini's
``cut-in_simple.xosc`` declares

    <Story name="CutInAndBrakeStory">
      <ParameterDeclarations>
        <ParameterDeclaration parameterType="string" name="owner" value="OverTaker"/>

and then writes ``<TimeHeadwayCondition entityRef="$owner" .../>``.

Left unresolved, ``$owner`` matches no entity in the converted scenario, so
every ByEntity condition referring to it silently evaluates to False — the
Interpretation replay and the viewer's activity strips would show a trigger that
never arms.  This pass rewrites those references onto real entity names.

It runs as a post-processing step over the already-parsed storyboard so the
shared parser (and the existing thesis outputs that depend on it) stay
untouched.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Tuple

_PARAM_RE = re.compile(r"\$\{?(\w+)\}?")

# Fields on parsed Condition dataclasses that name an entity
_ENTITY_FIELDS = ("triggering_entity", "reference_entity", "entity_ref")


def load_parameters(xosc_path: str | Path) -> Dict[str, str]:
    """
    Collect every ``<ParameterDeclaration>`` as ``name → value`` strings.

    Declarations are gathered document-wide.  Story-scoped declarations are
    applied after the global ones, so a story-level ``owner`` wins over a
    global parameter of the same name — which matches how the enclosing scope
    resolves in practice for the single-story files in the corpora.
    """
    try:
        root = ET.parse(Path(xosc_path)).getroot()
    except (ET.ParseError, OSError):
        return {}

    params: Dict[str, str] = {}

    # Global first
    for decls in root.findall("ParameterDeclarations"):
        for pd in decls.findall("ParameterDeclaration"):
            name, value = pd.get("name"), pd.get("value")
            if name is not None and value is not None:
                params[name] = value

    # Then story-scoped (override)
    for story in root.iter("Story"):
        for decls in story.findall("ParameterDeclarations"):
            for pd in decls.findall("ParameterDeclaration"):
                name, value = pd.get("name"), pd.get("value")
                if name is not None and value is not None:
                    params[name] = value

    return params


def resolve_text(raw: str, params: Dict[str, str], known: set) -> str:
    """
    Substitute ``$name`` / ``${name}`` in ``raw``.

    A substitution is only applied when it resolves to a declared parameter.
    Unresolvable references are left as-is rather than blanked, so a failure
    stays visible in the output instead of turning into a silent empty name.
    """
    if not raw or "$" not in raw:
        return raw

    def _sub(m: re.Match) -> str:
        value = params.get(m.group(1))
        return str(value) if value is not None else m.group(0)

    resolved = _PARAM_RE.sub(_sub, raw)
    # If the parameter pointed at something that is not an entity we still
    # return it — the caller reports resolution counts, not correctness.
    return resolved


def resolve_entity_references(
    storyboard: Any,
    xosc_path: str | Path,
) -> Tuple[int, List[str]]:
    """
    Rewrite parameterised entity names in a ``ParsedStoryboard`` in place.

    Returns ``(n_substitutions, unresolved)`` where ``unresolved`` lists the
    ``$references`` that had no matching declaration.
    """
    params = load_parameters(xosc_path)
    known_entities = set()
    try:
        root = ET.parse(Path(xosc_path)).getroot()
        known_entities = {
            o.get("name") for o in root.iter("ScenarioObject") if o.get("name")
        }
    except (ET.ParseError, OSError):
        pass

    n_subs = 0
    unresolved: List[str] = []

    def _fix(value: str) -> str:
        nonlocal n_subs
        if not value or "$" not in value:
            return value
        new = resolve_text(value, params, known_entities)
        if new != value:
            n_subs += 1
        else:
            unresolved.append(value)
        return new

    def _fix_conditions(trigger: Any) -> None:
        for group in (trigger or []):
            for cond in group:
                for fname in _ENTITY_FIELDS:
                    if hasattr(cond, fname):
                        setattr(cond, fname, _fix(getattr(cond, fname) or ""))
                if hasattr(cond, "element_ref"):
                    cond.element_ref = _fix(cond.element_ref or "")

    for story in storyboard.stories:
        for act in story.acts:
            _fix_conditions(act.start_trigger)
            _fix_conditions(act.stop_trigger)
            for mg in act.maneuver_groups:
                mg.actor_refs = [_fix(a) for a in mg.actor_refs]
                for maneuver in mg.maneuvers:
                    for event in maneuver.events:
                        _fix_conditions(event.start_trigger)

    _fix_conditions(getattr(storyboard, "stop_trigger", None))

    return n_subs, sorted(set(unresolved))
