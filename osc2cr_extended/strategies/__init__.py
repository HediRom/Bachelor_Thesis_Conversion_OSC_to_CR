"""
Representation strategies for an OpenSCENARIO storyboard in CommonRoad.

The converter this package extends flattens a scenario: esmini evaluates every
trigger and only trajectories survive.  These three strategies each keep the
conditional structure in a different way, trading fidelity against
compatibility with existing CommonRoad tooling.

======================  ==========================================  ==========
Module                  What it does                                Fidelity
======================  ==========================================  ==========
:mod:`~.transcription`   Keeps flat trajectories, attaches the       low
                        triggers and events as metadata.  Every
                        condition type can be tagged; existing CR
                        tools ignore the annotations.
:mod:`~.translation`    Maps conditions onto native CommonRoad      partial
                        constructs — goal regions, planning
                        problems.  Faithful where an analogue
                        exists, narrow where none does.
:mod:`~.interpretation` A re-evaluable condition layer that         high
                        recomputes predicates at run time.
                        Reactivity survives, but consuming it
                        needs the evaluator in this package.
======================  ==========================================  ==========

:mod:`~.merge` runs all three over one scenario and reunites their output with
the converter's trajectories into a single enriched scenario.

The condition taxonomy they share — the parser, the typed condition model and
the JSON export — lives in :mod:`~.shared`.
"""
