#!/usr/bin/env python3
"""R32/R83 for I6: one single-edit defect per decision `test_units.py`, `test_preflight.py`
and `test_egress.py` claim. The mutated files are outside `infrx` (the units, the preflight
and its approvals), so the copy is I5's layout and I5's private runner copy (reused, not
repeated). `test_protocol_drills.py` (P3 over TCP) is outside the runner.

    uv run --frozen pytest -q tests/i/lab_pipeline/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/lab_pipeline/test_mutants.py   # all
"""
from __future__ import annotations

import ast

from ..lab_eval import mutants as i5

shared, Mutant, Result, Runner = i5.shared, i5.Mutant, i5.Result, i5.Runner
API_DIR = i5.API_DIR

SUITES = ("tests/i/lab_pipeline/test_units.py", "tests/i/lab_pipeline/test_preflight.py",
          "tests/i/lab_pipeline/test_egress.py")
TR, AN = "deploy/lab/pipelines/infrx-lab-training.service", \
    "deploy/lab/pipelines/infrx-lab-annotation.service"
PF = "../../infra/lab/workers/training/preflight.py"
APPROVALS = "../../infra/lab/workers/training/egress.json"

BOUNDED = "test_i6_each_role_is_bounded_off_by_default_and_gated_by_the_preflight"
FLAGS = "test_i6_egress_is_denied_by_default_through_a_dead_proxy_the_env_file_cannot_override"
SHIPPED = "test_i6_the_shipped_approvals_are_empty_so_every_role_is_local_or_manual_only"
PAID = "test_i6_an_approved_paid_adapter_needs_its_budget_payer_host_and_secret"
SILENT = "test_i6_an_adapter_is_approved_per_role_and_nothing_is_enabled_silently"
NAMES = "test_i6_every_setting_is_named_for_its_role_and_purpose"
ALLOW = "test_i6_the_egress_allowlist_is_exact_hosts_of_the_object_store_and_the_approval"
PARSE = "test_i6_the_env_file_is_parsed_like_docker_and_refuses_a_bare_name"
CLI = "test_i6_the_cli_exits_1_on_a_refusal_and_never_prints_a_value"
WIDEN = "test_i6_only_the_allowlisted_host_is_reachable_and_the_env_file_cannot_widen_it"
EMPTY = "test_i6_an_empty_allowlist_denies_every_http_host"
PRE = ("ExecStartPre=/usr/bin/python3 /home/ubuntu/model-inference/infra/lab/workers/training/"
       "preflight.py --role training --env-file /etc/infrx-lab/training.env\n")
m = i5.m

MUTANTS: tuple[Mutant, ...] = (
    # --- the units: the preflight gate, bounds, OFF by default, isolation
    m("i6_training_no_preflight", "the preflight gates the start", TR, PRE, "", BOUNDED),
    m("i6_training_preflight_ignored", "a refused preflight is a failed start", TR,
      "ExecStartPre=/usr/bin/python3", "ExecStartPre=-/usr/bin/python3", BOUNDED),
    m("i6_training_preflight_as_root", "root never runs a file ubuntu can edit", TR,
      "ExecStartPre=/usr/bin/python3", "ExecStartPre=+/usr/bin/python3", BOUNDED),
    m("i6_training_preflight_other_role", "the preflight checks this role", TR,
      "--role training --env-file", "--role annotation --env-file", BOUNDED),
    m("i6_annotation_on_by_default", "a role starts only with its env file", AN,
      "ConditionPathExists=/etc/infrx-lab/annotation.env\n", "", BOUNDED),
    m("i6_annotation_memory_unbounded", "memory is bounded", AN,
      "--memory 1g --memory-swap 1g ", "", BOUNDED),
    m("i6_training_consumer_uid", "not the consumer worker's uid", TR,
      "--user 10003:10000", "--user 10002:10000", BOUNDED),
    m("i6_training_shares_eval_port", "each Lab role its own health port", TR,
      "LAB_WORKER_HEALTH_PORT=8015", "LAB_WORKER_HEALTH_PORT=8012", BOUNDED),
    m("i6_training_part_of_inference", "a consumer restart never takes a Lab unit along", TR,
      "Requires=docker.service\n", "Requires=docker.service\nPartOf=marlin2b-vllm.service\n",
      BOUNDED),
    m("i6_training_reads_gateway_env", "never the consumer's env file", TR,
      "EnvironmentFile=/etc/infrx-lab/training.env",
      "EnvironmentFile=/etc/marlin2b-gateway.env", BOUNDED),
    # --- the egress deny: every letter case, a dead proxy, NO_PROXY = the checked allowlist
    m("i6_training_https_lowercase_dropped", "https_proxy is set in both cases", TR,
      " -e https_proxy=http://127.0.0.1:9", "", BOUNDED, FLAGS),
    m("i6_training_http_lowercase_dropped", "http_proxy is set in both cases", TR,
      " -e http_proxy=http://127.0.0.1:9", "", BOUNDED, FLAGS, WIDEN),
    m("i6_training_http_proxy_empty", "the proxy is a dead address, never empty", TR,
      "-e http_proxy=http://127.0.0.1:9", "-e http_proxy=", BOUNDED, WIDEN, EMPTY),
    m("i6_training_no_proxy_lowercase_dropped", "no_proxy is set in both cases", TR,
      " -e no_proxy=${LAB_EGRESS_ALLOW}", "", BOUNDED, FLAGS, WIDEN),
    m("i6_training_no_proxy_star", "NO_PROXY is the checked allowlist", TR,
      "-e no_proxy=${LAB_EGRESS_ALLOW}", "-e no_proxy=*", BOUNDED, WIDEN, EMPTY),
    # --- the preflight: names
    m("i6_pf_any_name", "every setting is on the role's list", PF,
      "for name in sorted(env) if name not in names]",
      "for name in sorted(env) if False]", NAMES),
    m("i6_pf_lowercase_names_pass", "a proxy override in any case is refused", PF,
      "for name in sorted(env) if name not in names]",
      "for name in sorted(env) if name not in names and name.isupper()]", NAMES),
    m("i6_pf_other_purpose_secret", "a role holds only its own adapter's secret", PF,
      "        names |= {ADAPTERS[role][0], *paid_names(role)}",
      "        names |= {a for r in ADAPTERS for a in (ADAPTERS[r][0], *paid_names(r))}", NAMES),
    m("i6_pf_other_role_knob", "another Lab role's knob is refused", PF,
      'names = {*COMMON, f"LAB_{role.upper()}_CONCURRENCY"}',
      'names = {*COMMON, f"LAB_{role.upper()}_CONCURRENCY", "LAB_EVAL_CONCURRENCY"}', NAMES),
    m("i6_pf_image_flag_allowed", "the image is not a docker flag", PF,
      'if env.get("INFRX_IMAGE", "").startswith("-"):', "if False:", NAMES),
    m("i6_pf_bare_name_allowed", "a bare name is refused", PF,
      "        if not eq:\n", "        if False:\n", PARSE),
    m("i6_pf_indented_comment", "an indented comment is a comment", PF,
      'line.lstrip().startswith("#")', 'line.startswith("#")', PARSE),
    # --- the preflight: adapters, budget, payer, secret
    m("i6_pf_shipped_approval", "nothing is approved until P-10/P-11", APPROVALS,
      '"training": []',
      '"training": [{"adapter": "trainer-x", "host": "api.trainer.example", "approval": "x", '
      '"payer_ref": "lab:payer:nemo:0001", "budget_usd": "500.00"}]', SHIPPED),
    m("i6_pf_default_moved", "the default is the manual bundle", PF,
      '"LAB_TRAINING_CONNECTOR", "manual-bundle"', '"LAB_TRAINING_CONNECTOR", "trainer-x"',
      SHIPPED, SILENT),
    m("i6_pf_stray_allowed", "the default carries no endpoint, token, budget or payer", PF,
      "for name in paid_names(role) if name in env]",
      "for name in paid_names(role) if False]", SILENT),
    m("i6_pf_approval_any_role", "an approval is per role", PF,
      'approved = [a for a in approvals.get(role, []) if a["adapter"] == adapter]',
      'approved = [a for r in approvals.values() for a in r if a["adapter"] == adapter]',
      SILENT),
    m("i6_pf_host_unchecked", "the endpoint is the approved host", PF,
      'if _host(env.get(url, "")) != approval["host"]:', "if False:", PAID),
    m("i6_pf_plaintext_allowed", "the endpoint is https", PF,
      'if parts.scheme == "https"', 'if parts.scheme in ("https", "http")', PAID),
    m("i6_pf_empty_token_passes", "an empty token is a failed lookup", PF,
      "if not env.get(token):", "if token not in env:", PAID),
    m("i6_pf_budget_over_cap", "the budget is within the approval", PF,
      "<= cap", "<= cap + 1", PAID),
    m("i6_pf_budget_zero", "a zero budget is no budget", PF,
      "ok = Decimal(0) < Decimal", "ok = Decimal(0) <= Decimal", PAID),
    m("i6_pf_payer_any", "exactly the approval's named payer", PF,
      'or env.get(payer) != approval["payer_ref"]:', "or not env.get(payer):", PAID),
    m("i6_pf_payer_unnamed_approval", "an approval without a payer pays nothing", PF,
      'if not approval["payer_ref"] or env.get', "if env.get", PAID),
    # --- the preflight: the egress allowlist
    m("i6_pf_egress_star", "no wildcard", PF, "if entry and entry not in hosts]",
      'if entry and entry not in hosts and entry != "*"]', ALLOW),
    m("i6_pf_egress_off_adapter_host", "an approved host only while its adapter is on", PF,
      "    hosts = {_host(env[\"LAB_S3_ENDPOINT\"])} if env.get(\"LAB_S3_ENDPOINT\") else set()",
      "    hosts = ({_host(env[\"LAB_S3_ENDPOINT\"])} if env.get(\"LAB_S3_ENDPOINT\") else set()"
      ") | {a[\"host\"] for r in approvals.values() for a in r}", ALLOW),
    m("i6_pf_egress_no_approved_host", "the enabled adapter's host is reachable", PF,
      '                hosts.add(approval["host"])\n', "", PAID, ALLOW),
    m("i6_pf_egress_no_object_store", "the object store is reachable", PF,
      "    hosts = {_host(env[\"LAB_S3_ENDPOINT\"])} if env.get(\"LAB_S3_ENDPOINT\") else set()",
      "    hosts = set()", SHIPPED, PAID, SILENT, NAMES, ALLOW, CLI),
    m("i6_pf_egress_unstripped", "entries are trimmed", PF,
      "entries = [e.strip() for e", "entries = [e for e", ALLOW),
    # --- the CLI
    m("i6_cli_exit_ignores_refusals", "a refusal exits 1", PF,
      "return 1 if refusals else 0", "return 0", CLI),
    m("i6_cli_prints_values", "no value is printed", PF,
      'print(f"FAIL {a.role}: {refusal}")', 'print(f"FAIL {a.role}: {refusal} {env}")', CLI),
)


def case_names(suites=SUITES) -> set[str]:
    names = set()
    for suite in suites:
        tree = ast.parse((API_DIR / suite).read_text())
        names |= {node.name for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
    return names


RUNNER = Runner(name="i6", targets=SUITES, package="", layout=i5._layout,
                require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the I6 Lab pipeline worker mutation list"))
