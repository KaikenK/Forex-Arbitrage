"""FX event-impact research workspace.

Marks `research` as a package so `research.tests` is namespaced distinctly from the
top-level `tests` package; without it pytest resolves both to the module name `tests`
and aborts collection with an import-file mismatch.
"""
