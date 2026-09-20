"""infrx gateway internals.

Lifted out of the single-file `gateway.py` (F1) with behavior unchanged. Nothing
here runs at import: no os.environ read, no client, no app. Everything stateful
belongs to the objects `infrx.gateway.app.create_app()` builds, so two apps in
one process share nothing.
"""
