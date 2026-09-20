"""Road design engine (pure geometry and rules; no web or database imports).

Modules
-------
standards       parameter tables (Nepal Road Standard etc.) with provenance and project deviations
horizontal      standards checks on a HorizontalAlignment (radius, transition, superelevation rate)
vertical        vertical alignment: PVIs, parabolic curves, grades, K values, checks
superelevation  superelevation rate and its development along the alignment
template        typical cross-sections (components, side slopes, ditches) and assignments by chainage
corridor        sweep templates along the alignment over the terrain: daylight, areas, volumes
earthworks      end-area and prismoidal volumes, mass haul
structures      retaining walls, culverts and drains: data model and placement suggestions

The terrain (TIN) is read-only here: every module receives a `plm.engine.tin.TIN` and an alignment
and never mutates them. Module engines for canal and building site design are meant to reuse the
same vertical / template / corridor / earthworks parts.
"""
