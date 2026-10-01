# The repo-root pytest.ini runs --import-mode=importlib, which does not put this directory on sys.path;
# the plan tests import progress / validate_plan as top-level modules (as `python3 -m unittest` here does).
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
