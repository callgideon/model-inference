"""Scratch plugin (not in the tree's suites): applies WR-LR7-GRANT's SQL to the task-local r2
world just before the unit-login case, through the module's own `world` connection."""
GRANT = ("grant execute on function infrx.lab_release_live(jsonb), infrx.lab_experiments(jsonb) "
         "to infrx_lab_control")


def pytest_runtest_call(item):
    if "unit_login" in item.name:
        item.funcargs["world"].execute(GRANT)
