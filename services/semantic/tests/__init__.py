"""Makes `tests` a regular package so `from tests.api ...` resolves to this directory.

Without it, `tests` here is a namespace package, and any regular package called `tests`
installed in site-packages (some third-party wheels ship one) takes precedence over a
namespace package regardless of sys.path order, breaking those imports at collection.
"""
