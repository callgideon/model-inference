from .test_upgrade_d10 import _split


def test_a_later_migration_is_neither_the_0018_world_nor_d10() -> None:
    """LW0 item 5 (R151): a Lab migration (0027 on) must not be applied before 0019 as part of
    the "0018 world", nor counted as D10. Oracle: the old split put every non-D10 file,
    0027 included, in the base."""
    files = (("shim.sql", ""), ("0018_x.sql", ""), ("0019_x.sql", ""), ("0026_x.sql", ""),
             ("0027_lab.sql", ""), ("clock.sql", ""))
    base, ours = _split(files)
    assert [label for label, _ in base] == ["shim.sql", "0018_x.sql", "clock.sql"]
    assert [label for label, _ in ours] == ["0019_x.sql", "0026_x.sql"]
