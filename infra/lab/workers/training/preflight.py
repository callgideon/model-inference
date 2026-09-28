#!/usr/bin/env python3
"""I6/I7: a Lab annotation, training or rollout role's env file, checked before its unit starts.

    /usr/bin/python3 infra/lab/workers/training/preflight.py --role training \
        --env-file /etc/infrx-lab/training.env

`ExecStartPre` of `infrx-lab-{annotation,training,rollout}.service`: exit 1 (the unit does not
start) naming each refusal by setting and never printing a value. Stdlib only (the host's
python3 runs it from the deployed checkout, so the approvals are the deployed commit's).

* **Names.** Every setting is on the role's own list: `INFRX_IMAGE`, the database, object store,
  egress allowlist and concurrency, plus - for the annotation (teacher, P-10) and training
  (connector, P-11) roles only - that role's adapter, endpoint, token, USD budget and payer.
  Anything else (a consumer secret, another purpose's token, cloud credentials, a proxy
  override in any letter case, a `DOCKER_*`, `PYTHON*` or `LD_*` setting: EnvironmentFile=
  reaches every Exec line of the unit) is refused. A bare `NAME` line is refused: docker's
  `--env-file` would copy it unchecked from the calling environment; a line whose name is
  not an identifier (a pasted DSN) is refused by line number, never printed. `INFRX_IMAGE`
  (one argv word of the unit) must be a local image id, `sha256:<64 hex>`: never a docker
  flag, never absent (docker would run the public `python`), never a registry reference
  (the daemon's pull is egress the container's deny proxy never sees).
* **The file.** Readable by its owner only (0600 or 0400): it holds the token and password.
  docker's `--env-file` fills the container and systemd's `EnvironmentFile=` fills
  `${INFRX_IMAGE}`/`${LAB_EGRESS_ALLOW}` in the unit's argv, and the two read a file
  differently (systemd joins a line ending in `\\` to the next, unquotes, strips spaces; the
  last duplicate wins in both). So a line with a backslash, a value with a quote, a control
  character or surrounding space, and a name set twice are refused, and every checked name
  must equal what this process received from `EnvironmentFile=` (the unit's `ExecStartPre`
  gets systemd's view; `-I` keeps `os.environ`): the values checked are the values run.
* **Adapters.** The default (`dry-run` teacher, `manual-bundle` training; rollout has none)
  needs no approval and carries no endpoint, token, budget or payer (a stray one is refused,
  so nothing turns on by editing one line). Any other adapter needs its role's entry in
  `egress.json` (P-10 or P-11, empty until approved), an `https://<approved host>` endpoint,
  a non-empty token (a failed secret lookup refuses; it never falls back to another
  adapter), a finite USD budget above 0 and within the approval's, and exactly the
  approval's named payer.
* **Egress.** The unit sends every HTTP client through a dead proxy except `NO_PROXY` =
  `LAB_EGRESS_ALLOW`; each of its entries must be exactly the object store's host
  (`LAB_S3_ENDPOINT`), the instance metadata address `169.254.169.254` (botocore's
  instance-role credentials; the object store has no other credential path, `AWS_*` being
  refused) or the enabled adapter's approved host (no `*`, suffix, port or scheme).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

APPROVALS = Path(__file__).resolve().with_name("egress.json")
COMMON = ("INFRX_IMAGE", "LAB_DATABASE_URL", "LAB_S3_BUCKET", "LAB_S3_ENDPOINT",
          "LAB_EGRESS_ALLOW")
# role -> (adapter setting, its default, endpoint/token prefix, approval id)
ADAPTERS = {"annotation": ("LAB_ANNOTATION_TEACHER", "dry-run", "LAB_ANNOTATION_TEACHER", "P-10"),
            "training": ("LAB_TRAINING_CONNECTOR", "manual-bundle", "LAB_TRAINING_CONNECTOR",
                         "P-11")}
ROLES = (*ADAPTERS, "rollout")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
IMDS = "169.254.169.254"                  # botocore's instance-role credentials (IMDSv2)
UNSAFE = re.compile(r"[\\'\"\x00-\x1f\x7f]")   # read differently by systemd and docker
ARGV = ("INFRX_IMAGE", "LAB_EGRESS_ALLOW")  # expanded by systemd into the unit's argv


def paid_names(role: str) -> tuple[str, ...]:
    _, _, prefix, _ = ADAPTERS[role]
    up = role.upper()
    return (f"LAB_{up}_BUDGET_USD", f"{prefix}_URL", f"{prefix}_TOKEN", f"LAB_{up}_PAYER_REF")


def allowed_names(role: str) -> set[str]:
    names = {*COMMON, f"LAB_{role.upper()}_CONCURRENCY"}
    if role in ADAPTERS:
        names |= {ADAPTERS[role][0], *paid_names(role)}
    return names


def parse(text: str) -> tuple[dict[str, str], list[str]]:
    """docker's --env-file: `NAME=VALUE` taken literally; `#` comments and blank lines. Any
    line systemd's EnvironmentFile= could read otherwise is refused, never printed."""
    env, refusals = {}, []
    for n, line in enumerate(text.split("\n"), 1):   # both split on \n only
        if "\\" in line:                                # even a comment's joins the next
            refusals.append(f"line {n}: a backslash (systemd joins the next line)")
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, eq, value = line.partition("=")
        if not eq:
            refusals.append(f"line {n}: a bare name copies the caller's environment")
        elif not NAME.fullmatch(name):
            refusals.append(f"line {n}: not a setting name (not printed)")
        elif name in env:
            refusals.append(f"line {n}: {name} is set twice")
        elif UNSAFE.search(value) or value != value.strip():
            refusals.append(f"line {n}: {name} has a quote, control character or surrounding "
                            f"space (systemd reads it differently)")
        else:
            env[name] = value
    return env, refusals


def load_approvals(path: Path) -> dict[str, list[dict]]:
    data = json.loads(path.read_text())
    return {role: list(data.get(role, [])) for role in ADAPTERS}


def _host(url: str) -> str | None:
    parts = urlsplit(url)
    return parts.hostname if parts.scheme == "https" else None


def check(role: str, env: dict[str, str], approvals: dict[str, list[dict]]) -> list[str]:
    """Every refusal, by setting name; [] = the unit may start. Pure."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    names = allowed_names(role)
    refusals = [f"{name}: not a {role} setting" for name in sorted(env) if name not in names]
    if not IMAGE.fullmatch(env.get("INFRX_IMAGE", "")):
        refusals.append("INFRX_IMAGE: not a local image id (sha256:<64 hex>)")
    hosts = {_host(env["LAB_S3_ENDPOINT"])} if env.get("LAB_S3_ENDPOINT") else set()
    hosts.add(IMDS)
    if role in ADAPTERS:
        setting, default, prefix, approval_id = ADAPTERS[role]
        budget, url, token, payer = paid_names(role)
        adapter = env.get(setting, default)
        if adapter == default:
            refusals += [f"{name}: set while {setting} is {default}"
                         for name in paid_names(role) if name in env]
        else:
            approved = [a for a in approvals.get(role, []) if a["adapter"] == adapter]
            if not approved:
                refusals.append(f"{setting}: this adapter has no {approval_id} approval")
            else:
                (approval,) = approved
                hosts.add(approval["host"])
                if _host(env.get(url, "")) != approval["host"]:
                    refusals.append(f"{url}: not https://{approval['host']}")
                if not env.get(token):
                    refusals.append(f"{token}: the secret lookup failed (absent or empty)")
                try:
                    # NaN compares as InvalidOperation; Infinity is above any approval.
                    cap = Decimal(approval["budget_usd"])
                    ok = Decimal(0) < Decimal(env.get(budget, "")) <= cap
                except InvalidOperation:
                    ok = False
                if not ok:
                    refusals.append(f"{budget}: a USD amount above 0 and within the "
                                    f"approval's {approval['budget_usd']}")
                if not approval["payer_ref"] or env.get(payer) != approval["payer_ref"]:
                    refusals.append(f"{payer}: not the approval's named payer")
    entries = [e.strip() for e in env.get("LAB_EGRESS_ALLOW", "").split(",")]
    refusals += [f"LAB_EGRESS_ALLOW: entry {n} is neither the object store nor an approved "
                 f"endpoint" for n, entry in enumerate(entries, 1)
                 if entry and entry not in hosts]
    return refusals


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--role", required=True, choices=ROLES)
    ap.add_argument("--env-file", required=True)
    ap.add_argument("--approvals", default=str(APPROVALS))
    a = ap.parse_args(argv)
    path = Path(a.env_file)
    env, refusals = parse(path.read_text())
    if path.stat().st_mode & 0o077:
        refusals.append("the env file is readable by another account (chmod 0600)")
    refusals += [f"{name}: EnvironmentFile= gives the unit another value (not printed)"
                 for name in sorted({*env, *ARGV}) if os.environ.get(name) != env.get(name)]
    refusals += check(a.role, env, load_approvals(Path(a.approvals)))
    for refusal in refusals:
        print(f"FAIL {a.role}: {refusal}")
    if not refusals:
        print(f"PASS {a.role}")
    return 1 if refusals else 0


if __name__ == "__main__":
    sys.exit(main())
