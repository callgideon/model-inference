"""AP-08: the judge and review session doors, run as the verified session user.

Replaces the Lab's direct PostgREST transport for judge and review (`apps/lab/lib/services/
judge/{core,runs}.ts`, `review/index.ts`): the same `public.lab_judge_*` / review doors, now
called by FastAPI on the backend's own login. One transaction sets the PostgREST claim GUCs
(both forms, as `tests/d/checks._jwt` documents) to the user id the `ActorSource` verified, then
calls the door - so `auth.uid()` and every role/payer/purpose rule stay in SQL (contracts.md §1:
no unrestricted backend client replaces them). The claims are transaction-local
(`set_config(..., true)`): nothing leaks to the next statement on the connection. No role is
SET (the Lab login is a member of no role, LDP-F7); 0064 grants it EXECUTE on these doors.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ...contracts import errors
from ...state import rpc
from ...state.jobstore import domain_error

if TYPE_CHECKING:
    from ...state.jobstore import Connect

#: door -> its argument casts (0037, 0038, 0064). Only these names are ever formatted into SQL.
SIGNATURES: dict[str, tuple[str, ...]] = {
    "lab_judge_configure_keyed": ("uuid", "uuid", "uuid", "uuid", "text", "int", "int"),
    "lab_judge_config_list": ("uuid", "uuid", "uuid", "int"),
    "lab_judge_set_budget_keyed": ("uuid", "text", "jsonb", "text"),
    "lab_judge_budget_list": ("uuid",),
    "lab_judge_request_run": ("uuid", "uuid", "uuid", "text"),
    "lab_judge_run_list": ("uuid", "uuid", "uuid", "int"),
    "lab_judge_run_results": ("uuid", "uuid", "uuid", "int"),
    "lab_judge_cancel": ("uuid", "uuid"),
    "lab_review_feedback": ("jsonb",),
    "lab_trace_review": ("jsonb",),
    "lab_trace_reviews": ("jsonb",),
    "lab_judge_rubric_create": ("jsonb",),       # SR-AP08-1
    "lab_judge_rubric_list": ("uuid",),          # SR-AP08-1
    "lab_judge_runs": ("uuid", "uuid"),          # 0043 (EXECUTE for the Lab login: 0067)
}
CLAIMS = ("select set_config('request.jwt.claims', %s, true), "
          "set_config('request.jwt.claim.sub', %s, true), "
          "set_config('request.jwt.claim.role', 'authenticated', true)")


def refusal(exc: Exception) -> Exception:
    """A door's refusal as the domain error R270 renders: 42501 (the door's role/grant rule)
    is 403; the rest is `jobstore.domain_error`'s mapping (P0001 codes, P0002, 22023, 55000)."""
    if getattr(exc, "sqlstate", None) == "42501":
        return errors.Forbidden("the session's provider role or grant does not hold this action")
    mapped = domain_error(exc)
    if isinstance(mapped, errors.BudgetExceeded):
        return errors.RateLimitError(mapped.detail, code="budget_exceeded")
    return mapped


class SessionDoors:
    """`call(user_id, door, *args)` -> the door's jsonb answer, or a typed refusal."""

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def call(self, user_id: str, door: str, *args: Any) -> Any:
        from psycopg import Error, OperationalError
        from psycopg.errors import ObjectNotInPrerequisiteState
        from psycopg.types.json import Jsonb
        casts = SIGNATURES[door]
        if len(args) != len(casts):
            raise TypeError(f"{door} takes {len(casts)} arguments")
        sql = f"select public.{door}({', '.join(f'%s::{c}' for c in casts)})"
        claims = json.dumps({"sub": user_id, "role": "authenticated"})
        try:
            async with rpc.connection(self._connect) as conn, conn.transaction():
                await conn.execute(CLAIMS, (claims, user_id))
                cursor = await conn.execute(sql, [Jsonb(a) if isinstance(a, dict) else a
                                                  for a in args])
                (answer,) = await cursor.fetchone()
                return answer
        except ObjectNotInPrerequisiteState as off:   # 55000 (require_feature): the 30 s
            raise domain_error(off) from None          # maintenance 503, not an outage
        except OperationalError:
            raise errors.DependencyUnavailable("the judge store is unreachable",
                                               retry_after_s=5) from None
        except Error as failed:
            raise refusal(failed) from None
