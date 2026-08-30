"""
Shared test setup.

``test_semantic_engine.py`` installs a stub ``redis`` module into ``sys.modules``
at import time. Import fakeredis here first so it binds against the real
``redis`` package before any stub can shadow it (collection order is
alphabetical, so this must happen in conftest, not a test module).
"""

try:  # optional dep — only the stream-consumer tests need it
    import fakeredis  # noqa: F401
    import fakeredis.aioredis  # noqa: F401
except Exception:
    pass
