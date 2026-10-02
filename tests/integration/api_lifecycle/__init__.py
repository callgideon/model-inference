"""AP-11: the API-only lifecycle runner (research/plan/api-lifecycle/verification.md). A
package, so its modules never share a bare name with the sibling gates' runner/state/mutants
(WR-E7L-5). It reads the shared R270 wire models from `infrx`, put on the path only when
nothing else provides it (a mutation copy brings its own)."""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if importlib.util.find_spec("infrx") is None:
    sys.path.append(str(REPO / "apps" / "infrx-api"))
