from infrx.gateway import lab_auth
_r = lab_auth.refusal
def refusal(exc):
    print("DIAG", type(exc).__name__, str(exc)[:300])
    return _r(exc)
lab_auth.refusal = refusal
