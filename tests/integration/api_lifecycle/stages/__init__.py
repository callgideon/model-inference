"""AP-11b: the stage contracts of verification.md's 18 steps.

Each stage names the routes it drives - the mounted ones (`owner=None`) and the target ones
of contracts.md §3-§8 with the AP package that delivers them - its predecessors, and its
implementation. A stage whose target route is absent is BLOCKED naming that package and the
route, never a green skip. A stage with an implementation still runs its mounted half first
(stages 01, 08, 10, 11), so a defect there is a FAIL even while the rest waits. A later lane
plugs in by setting the route's owner to None and giving the stage its `run`.

`accepted_operation` is R270's long-operation contract as a stage reads it (202 + Location +
`infrx.contracts.api.OperationDoc`), for the stages AP-04/05/06 unblock.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from infrx.contracts import api


class Blocked(Exception):
    """A prerequisite is absent: the stage is BLOCKED with this reason."""


class Failed(Exception):
    """A required assertion failed (already recorded): the stage stops, FAIL."""


class ContractBroken(Exception):
    """A response outside the R270 wire contract."""


def accepted_operation(response) -> api.OperationDoc:
    """A long operation as contracts.md §2 states it: 202, a Location, an operation doc."""
    if response.status_code != 202:
        raise ContractBroken(f"a long operation answers 202, got {response.status_code}")
    if not response.headers.get("Location"):
        raise ContractBroken("a 202 operation carries a Location")
    try:
        return api.OperationDoc.model_validate(response.json())
    except ValueError as broken:
        raise ContractBroken(f"not an R270 operation document: {type(broken).__name__}") \
            from None


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    owner: str | None = None          # the AP package delivering it; None = mounted on the base
    origin: str = "gateway"           # gateway (consumer/operator data plane) | lab (control)

    def __str__(self) -> str:
        return f"{self.method} {self.path}"


@dataclass(frozen=True)
class Stage:
    sid: str
    title: str                        # verification.md's "API-driven step", verbatim
    proves: str                       # its "what must be proved", verbatim
    routes: tuple[Route, ...]
    run: Callable | None = None
    needs: tuple[str, ...] = ()       # earlier stages whose outputs this one reads
    reads_only: bool = False          # selected by `inspect`
    prerequisites: tuple[str, ...] = ()   # beyond routes: "<ID> what" (P-10, a GPU target)

    def missing(self) -> dict[str, list[str]]:
        """The absent routes by owning package."""
        found: dict[str, list[str]] = {}
        for route in self.routes:
            if route.owner is not None:
                found.setdefault(route.owner, []).append(str(route))
        return found


def _r(method: str, path: str, owner: str | None = None, origin: str | None = None) -> Route:
    lab = path.startswith("/lab/") if origin is None else origin == "lab"
    return Route(method, path, owner, "lab" if lab else "gateway")


from . import consumer  # noqa: E402  (the implementations read Blocked/Failed above)

GPU = "GPU-TARGET an isolated GPU target and resource budget (verification.md prerequisite 3)"
JUDGE = "P-10 an approved media-capable judge, its secret reference, payer and spend limit"
TRACES = "AP-07 trace storage composed (TRACE_PUMPS, ClickHouse, LAB_TRACES) for the run"

STAGES: tuple[Stage, ...] = (
    Stage("01", "Auth/session APIs for each actor; workspace/account/capability reads",
          "Correct roles and distinct audiences; fresh feature availability; no unauthorized "
          "membership/grants",
          (_r("GET", "/v1/jobs/{handle}"), _r("GET", "/lab/v1/control/models"),
           _r("GET", "/auth/v1/availability", "AP-01"), _r("GET", "/console/v1/me", "AP-01"),
           _r("GET", "/console/v1/capabilities", "AP-01"),
           _r("GET", "/lab/v1/workspaces", "AP-01"), _r("GET", "/lab/v1/capabilities", "AP-01"),
           _r("GET", "/lab/v1/workspaces/{id}/members", "AP-01")),
          run=consumer.s01, reads_only=True),
    Stage("02", "POST model project; start pinned import or upload parts/complete",
          "Empty workspace succeeds; manifest hashes cover all actual model files; operation "
          "completes once",
          (_r("POST", "/lab/v1/control/model-projects", "AP-04"),
           _r("POST", "/lab/v1/artifacts/uploads", "AP-04"),
           _r("POST", "/lab/v1/artifacts/uploads/{id}/complete", "AP-04"),
           _r("POST", "/lab/v1/artifacts/imports", "AP-04"),
           _r("GET", "/lab/v1/operations/{id}", "AP-04"))),
    Stage("03", "GET verified artifact; create immutable serving revision",
          "Actual source commit, model/card/schema/harness/processor/runtime pins, supported "
          "hardware profile",
          (_r("GET", "/lab/v1/artifacts/{id}", "AP-04"),
           _r("POST", "/lab/v1/control/model-projects/{id}/revisions", "AP-04")),
          needs=("02",)),
    Stage("04", "POST private deployment; poll its operation and detail",
          "Isolated resource allocated; real engine reports matching identity; state is not "
          "inferred from record presence",
          (_r("GET", "/lab/v1/hosting-profiles", "AP-05"),
           _r("POST", "/lab/v1/control/deployments", "AP-05"),
           _r("GET", "/lab/v1/control/deployments/{id}", "AP-05"),
           _r("GET", "/lab/v1/control/deployments/{id}/readiness", "AP-05")),
          needs=("03",), prerequisites=(GPU,)),
    Stage("05", "Operator private rate/funding API; bounded smoke; issue provider-dev key",
          "Approved private CREDIT meter; exact readiness receipt; key audience/endpoint scope "
          "correct; no public exposure",
          (_r("POST", "/operator/v1/deployments/{id}/dev-rate", "AP-06"),
           _r("POST", "/operator/v1/dev-wallet-grants", "AP-06"),
           _r("GET", "/lab/v1/control/dev-wallet", "AP-06"),
           # mounted, but a synchronous stand-in over NoEngine (503) until AP-05 05d
           _r("POST", "/lab/v1/control/deployments/{id}/smoke", "AP-05"),
           _r("POST", "/lab/v1/control/endpoints/{id}/keys", "AP-06")),
          needs=("04",), prerequisites=(GPU,)),
    Stage("06", "Call private endpoint with finite-video fixture",
          "Real media processing and response; charge/usage receipt; no text-only substitute "
          "for modality smoke",
          (_r("POST", "/v1/chat/completions"),),
          needs=("05",), prerequisites=(
              "AP-06 a provider-dev key scoped to the private endpoint (stage 05)", GPU)),
    Stage("07", "Provider administrator proposes; operator approves through API",
          "Expected listing version, approved rates/limits and candidate receipts validated; "
          "decision audited",
          (_r("POST", "/lab/v1/control/proposals"),
           _r("GET", "/operator/v1/publication-proposals", "AP-06"),
           _r("POST", "/operator/v1/publication-proposals/{id}/approve", "AP-06")),
          needs=("05",)),
    Stage("08", "GET public catalog; GET account; claim grant; create consumer A key",
          "Newly published test listing appears with truthful contract; one individual grant; "
          "one key despite retried creation",
          (_r("GET", "/v1/models"), _r("GET", "/console/v1/me", "AP-01"),
           _r("POST", "/console/v1/signup-grant/claim", "AP-03"),
           _r("POST", "/console/v1/keys", "AP-03")),
          run=consumer.s08),
    Stage("09", "Allocate consumer upload; PUT bytes; complete; submit async video job",
          "Upload not mistaken for model artifact; stable idempotency; result references the "
          "requested published serving version",
          (_r("POST", "/v1/uploads"), _r("PUT", "/v1/uploads/{handle}"),
           _r("POST", "/v1/uploads/{handle}/complete"), _r("POST", "/v1/jobs")),
          run=consumer.s09),
    Stage("10", "Poll status/result and console request/ledger APIs",
          "Terminal result and one exact CREDIT settlement; reserve released correctly; "
          "request/deployment/listing/rate pins agree",
          (_r("GET", "/v1/jobs/{handle}"), _r("GET", "/v1/jobs/{handle}/result"),
           _r("GET", "/console/v1/requests/{id}", "AP-02"),
           _r("GET", "/console/v1/credit-ledger", "AP-02")),
          run=consumer.s10, needs=("09",)),
    Stage("11", "Repeat bounded sync and SSE calls to cover supported modes",
          "Correct sync behavior, stream journal/disconnect rules; no implicit async "
          "conversion; exact usage/settlement per request",
          (_r("POST", "/v1/chat/completions"), _r("GET", "/console/v1/requests/{id}", "AP-02")),
          run=consumer.s11),
    Stage("12", "Enable capture and scoped sharing/external_judging grants for test key/data; "
                "submit one captured request",
          "Permission persisted through API; prior uncaptured request stays uncaptured; use "
          "remaining request budget",
          (_r("PUT", "/console/v1/keys/{id}/capture", "AP-07"),
           _r("POST", "/console/v1/data-grants", "AP-07"),
           _r("GET", "/console/v1/data-grants", "AP-07"), _r("POST", "/v1/chat/completions")),
          needs=("08",), prerequisites=(TRACES,)),
    Stage("13", "Poll Lab trace list/detail for that request; inspect authorized media",
          "Same request ID/pins; real capture content/timing/usage; metadata-only and "
          "unavailable content distinguished",
          (_r("GET", "/lab/v1/traces"), _r("GET", "/lab/v1/traces/{id}")),
          needs=("12",), prerequisites=(TRACES,)),
    Stage("14", "Create rubric/config; estimate; set budget; POST judge run; poll results",
          "Approved live adapter invoked once; video/SOP evidence available; real judge result "
          "or honest abstention; exact provider spend",
          (_r("GET", "/lab/v1/judge/models", "AP-08"), _r("POST", "/lab/v1/judge/rubrics", "AP-08"),
           _r("POST", "/lab/v1/judge/configs", "AP-08"),
           _r("POST", "/lab/v1/judge/estimates", "AP-08"),
           _r("PUT", "/lab/v1/judge/budgets/{payer_id}", "AP-08"),
           _r("POST", "/lab/v1/judge/runs", "AP-08"), _r("GET", "/lab/v1/judge/runs/{id}", "AP-08"),
           _r("GET", "/lab/v1/judge/runs/{id}/results", "AP-08")),
          needs=("13",), prerequisites=(JUDGE,)),
    Stage("15", "Read consumer credits and provider budget after judge",
          "Consumer not charged for provider judging; run replay does not duplicate spend; "
          "estimates separate from settled cost",
          (_r("GET", "/console/v1/credits", "AP-02"), _r("GET", "/lab/v1/judge/budgets", "AP-08")),
          needs=("14",)),
    Stage("16", "Submit human review; read review/calibration",
          "Immutable reviewed provenance; inadequate reference sample shows insufficient "
          "calibration",
          (_r("POST", "/lab/v1/traces/{id}/reviews", "AP-08"),
           _r("GET", "/lab/v1/traces/{id}/feedback", "AP-08"),
           _r("GET", "/lab/v1/judge/calibration", "AP-08")),
          needs=("13",)),
    Stage("17", "Revoke sharing/judging grant; retry reads and enqueue a follow-up judge",
          "Future prohibited content read/egress refused; no permanent content bypass; "
          "aggregate access follows its separate policy",
          (_r("DELETE", "/console/v1/data-grants/{id}", "AP-07"), _r("GET", "/lab/v1/traces/{id}"),
           _r("POST", "/lab/v1/judge/runs", "AP-08")),
          needs=("12", "14"), prerequisites=(TRACES,)),
    Stage("18", "Roll back/retire test listing and candidate through operator/control APIs",
          "Prior jobs keep original pins; new admissions use correct listing or refuse; "
          "task-owned resources drained and cleaned",
          (_r("POST", "/operator/v1/listings/{model_id}/rollback", "AP-06"),
           _r("POST", "/lab/v1/control/deployments/{id}/retire", "AP-05"),
           _r("POST", "/lab/v1/operations/{id}/cancel", "AP-05")),
          needs=("07",)),
)
