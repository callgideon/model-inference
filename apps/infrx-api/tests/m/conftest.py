"""M6's world fixtures (`make_world`, `make_d10_world`; tests/m/worlds.py), registered here
rather than imported into tests/m/test_retention.py, where every case naming one as a
parameter read as a redefinition of the import (ruff F811)."""
from .worlds import make_d10_world, make_world  # noqa: F401 (pytest fixtures)
