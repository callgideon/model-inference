#!/usr/bin/env python3
"""R32/R83 for I7: one single-edit defect per decision `test_units.py` and `test_exercise.py`
claim - the rollout unit (outside `infrx`) and R2's controller under the exercise. I5's
layout and private runner copy, reused.

    uv run --frozen pytest -q tests/i/lab_rollout/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/lab_rollout/test_mutants.py   # all
"""
from __future__ import annotations

from ..lab_eval import mutants as i5
from ..lab_pipeline import mutants as i6

shared, Mutant, Result, Runner = i5.shared, i5.Mutant, i5.Result, i5.Runner

SUITES = ("tests/i/lab_rollout/test_units.py", "tests/i/lab_rollout/test_exercise.py")
RO = "deploy/lab/rollout/infrx-lab-rollout.service"
R2 = "infrx/rollouts/control/__init__.py"

READY = "test_i7_the_controller_is_bounded_off_by_default_gated_and_independently_ready"
CAPACITY = "test_i7_the_controller_cannot_buy_capacity_or_reach_past_the_object_store"
EXERCISE = "test_i7_the_rollback_exercise_never_expands_and_every_restart_converges"
m = i5.m

MUTANTS: tuple[Mutant, ...] = (
    # --- the unit
    m("i7_rollout_on_by_default", "the controller starts only with its env file", RO,
      "ConditionPathExists=/etc/infrx-lab/rollout.env\n", "", READY),
    m("i7_rollout_preflight_as_training", "the preflight checks the rollout role", RO,
      "--role rollout --env-file", "--role training --env-file", READY),
    m("i7_rollout_shares_training_port", "its own readiness port", RO,
      "LAB_WORKER_HEALTH_PORT=8016", "LAB_WORKER_HEALTH_PORT=8015", READY),
    m("i7_rollout_part_of_the_gateway", "a gateway restart never takes the controller along",
      RO, "Requires=docker.service\n",
      "Requires=docker.service\nPartOf=marlin2b-gateway.service\n", READY),
    m("i7_rollout_egress_open", "egress denied as I6's", RO,
      " -e no_proxy=${LAB_EGRESS_ALLOW}", "", READY),
    m("i7_rollout_docker_socket", "no docker socket: it cannot start capacity", RO,
      "--network host \\\n", "--network host -v /var/run/docker.sock:/var/run/docker.sock \\\n",
      CAPACITY),
    m("i7_rollout_privileged", "no added privilege", RO,
      "--cap-drop ALL ", "--cap-drop ALL --cap-add NET_ADMIN ", CAPACITY),
    m("i7_rollout_host_pid", "no host PID namespace", RO,
      "--pids-limit 64 ", "--pids-limit 64 --pid=host ", CAPACITY),
    m("i7_rollout_no_enable_marker", "the Lab-wide enable marker gates the controller", RO,
      "ConditionPathExists=/etc/infrx-lab/enabled\n", "", READY),
    m("i7_rollout_pulls", "the daemon never pulls", RO, "--pull never ", "", CAPACITY),
    m("i7_rollout_consumer_group", "not the consumer runtime's group", RO,
      "--user 10003:10003", "--user 10003:10000", READY, CAPACITY),
    m("i7_rollout_preflight_not_isolated", "PYTHON* settings never steer the preflight", RO,
      "python3 -I /home", "python3 /home", READY),
    # --- R2 under the exercise
    m("i7_stale_metrics_expand", "stale telemetry holds", R2,
      '        hold.append("metrics_stale")\n', "        pass\n", EXERCISE),
    m("i7_restart_does_not_converge", "a restart converges a rolled-back alias", R2,
      '        if release.state == "rolled_back":\n            await self._converge(policy, '
      'policy_ref)\n', '        if release.state == "rolled_back":\n', EXERCISE),
    m("i7_converge_never_tries", "a pass tries the alias CAS", R2,
      "CONVERGE_TRIES = 3", "CONVERGE_TRIES = 0", EXERCISE),
)



def case_names() -> set[str]:
    return i6.case_names(SUITES)


RUNNER = Runner(name="i7", targets=SUITES, package="", layout=i5._layout,
                require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the I7 rollout controller mutation list"))
