"""AP-08 P-10 seam: judge egress beyond the local fake only for an operator-approved provider
host, only in `JUDGE_MODE=live`, only over https. Default (no allowlist, or any other mode):
loopback http only - exactly J2's rule before this seam.
"""
from __future__ import annotations

import pytest

from infrx.contracts import errors
from infrx.judge.submit import HttpJudgeProvider, egress_hosts

HOST = "judge.provider.example"


def allowed(url: str, hosts: frozenset[str] = frozenset()) -> bool:
    try:
        HttpJudgeProvider(url, allowed_hosts=hosts)
    except errors.InvalidRequest:
        return False
    return True


def test_ap08_egress__loopback_only_by_default():
    """Failure oracle: any non-loopback host reachable without the allowlist."""
    assert allowed("http://127.0.0.1:57564") and allowed("http://localhost:1")
    assert not allowed(f"https://{HOST}")
    assert not allowed("https://127.0.0.1.example")


def test_ap08_egress__the_allowlist_applies_only_in_live_mode():
    """Failure oracle: a dry-run (or misspelt-mode) worker egressing to a listed host."""
    assert egress_hosts("dry_run", HOST) == frozenset()
    assert egress_hosts("Live", HOST) == frozenset()
    assert egress_hosts("live", f" {HOST.upper()} , ,") == frozenset({HOST})
    assert not allowed(f"https://{HOST}", egress_hosts("dry_run", HOST))


def test_ap08_egress__a_listed_host_only_over_https():
    """Failure oracle: plaintext egress, or a lookalike host, to a listed provider."""
    hosts = egress_hosts("live", HOST)
    assert allowed(f"https://{HOST}/v1", hosts)
    assert not allowed(f"http://{HOST}", hosts)
    assert not allowed(f"https://{HOST}.attacker.example", hosts)
    assert not allowed("https://other.example", hosts)


@pytest.mark.parametrize("entry", ["https://judge.provider.example", "host/path", "host:443"])
def test_ap08_egress__an_allowlist_entry_is_a_bare_host(entry):
    with pytest.raises(errors.InvalidRequest):
        egress_hosts("live", entry)
