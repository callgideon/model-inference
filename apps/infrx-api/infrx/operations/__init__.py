"""G6B: headless provisioning and operations for the Marlin endpoint.

`service.Operations` is the narrow adapter an operator (or `cli.py`) drives with no
Next.js process running: verified-identity lookup, personal wallet binding, scoped
key issue/revoke/rotate, suspension, the A1 signup grant and D5 adjustments, catalog
publication, cancellation and reconciliation - each operator write audited with the
D1 `AuditAction` vocabulary - plus a tenant's read of its own usage, holds and jobs.

It owns no storage. `ports.py` names the D/A1/D5 shapes it needs; until D1R/D5/A1
land, only the in-memory fakes in `tests/g/ops/fakes.py` implement them, so passing
here is *implemented*, never integrated.
"""
