#!/usr/bin/env python3
"""r1 R32 for M1's adapter: one single-edit mutant per guard M's tests claim.

Same rule as the contracts list, same verdicts: the mutant is applied to a **copy** of
the package in a temporary directory, the tests that claim the invariant are run there,
and only "pytest failed, and only the named tests failed" counts as a kill. A syntax
error, an import error or a defect with wider reach than the declaration is a broken
runner, which fails the suite exactly as a survivor does. Nothing is written inside the
worktree.

The runner is M's own because the target is different: `tests/m`, not the shared
conformance suite. `Mutant`, `Outcome` and `Result` are the shared declarations, so a
verdict here means what it means there.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

from ..contracts.mutants import Mutant, Outcome, Result

API_DIR = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = "infrx"
SUITE = ("tests/m/test_fetch.py", "tests/m/test_store.py")

F = "media/fetch.py"
S = "media/store.py"
V = "media/video.py"          # F1's address policy, reused by the new path


def _m(name, invariant, file, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- the address policy ---------------------------------------------------
    _m("reserved_addresses_allowed",
       "reserved space is refused, which is what stops NAT64 and v4-compatible forms",
       V, "or a.is_reserved or a.is_unspecified", "or a.is_unspecified",
       "test_no_internal_address_form_is_reachable"),
    _m("cgnat_allowed", "only globally routable space is allowed (so not CGNAT)",
       V, "    return a.is_global", "    return True",
       "test_no_internal_address_form_is_reachable"),
    _m("unparseable_address_allowed", "text that is not an address is not a destination",
       V, "    except ValueError:\n        return False", "    except ValueError:\n        return True",
       "test_no_internal_address_form_is_reachable"),

    # --- resolution and pinning -----------------------------------------------
    _m("one_answer_is_enough", "every answer for a name must be public (rebinding)",
       F, "if not all(address_allowed(address) for address in addresses):",
       "if not any(address_allowed(address) for address in addresses):",
       "test_one_private_answer_among_many_is_refused",
       "test_a_rebinding_resolver_cannot_reach_an_internal_address"),
    _m("empty_answer_accepted", "an empty DNS answer is a refusal, not an empty pin",
       F, "        if not addresses:\n            raise refused(\"dns\", host=host)", "        pass",
       "test_a_dns_failure_or_an_empty_answer_is_a_refusal_not_a_crash"),
    _m("resolution_failure_swallowed", "a resolver error is a typed refusal",
       F, "            raise refused(\"dns\", host=host) from None", "            return [host]",
       "test_a_dns_failure_or_an_empty_answer_is_a_refusal_not_a_crash"),
    _m("connects_to_the_name_not_the_address",
       "the connection goes to the validated address, not to the name again",
       F, "\"GET\", target.copy_with(host=address),", "\"GET\", target,",
       "test_the_connection_is_pinned_to_the_validated_address",
       "test_an_ipv6_answer_is_pinned_as_a_bracketed_literal"),
    _m("no_sni_for_the_original_host",
       "TLS still verifies the certificate against the host the caller named",
       F, "extensions={\"sni_hostname\": target.host}", "extensions={}",
       "test_the_connection_is_pinned_to_the_validated_address"),
    _m("host_header_from_the_pinned_url", "the origin still sees the host it was asked for",
       F, "headers={\"Host\": target.netloc.decode(\"ascii\"),",
       "headers={\"X-Original-Host\": target.netloc.decode(\"ascii\"),",
       "test_the_connection_is_pinned_to_the_validated_address"),
    _m("httpx_follows_redirects_itself",
       "no unpinned hop: httpx following a redirect resolves the name again",
       F, "            follow_redirects=False,", "            follow_redirects=True,",
       "test_the_client_follows_nothing_and_trusts_no_environment"),
    _m("proxy_environment_trusted", "no proxy from the environment may replace the pin",
       F, "            trust_env=False,", "            trust_env=True,",
       "test_the_client_follows_nothing_and_trusts_no_environment"),
    _m("the_connect_budget_is_the_whole_budget", "connect and total budgets are separate",
       F, "connect=self.limits.media_fetch_connect_timeout_s)",
       "connect=self.limits.media_fetch_timeout_s)",
       "test_the_client_follows_nothing_and_trusts_no_environment"),

    # --- the URL and its hops -------------------------------------------------
    _m("any_scheme_fetched", "only http(s) URLs are fetched",
       F, "    if target.scheme not in (\"http\", \"https\"):", "    if False:",
       "test_only_http_urls_with_a_host_are_fetched",
       "test_a_redirect_to_another_scheme_or_to_credentials_is_refused"),
    _m("credentials_in_the_url_accepted", "a URL carrying credentials is refused",
       F, "        raise refused(\"credentials-in-url\", host=target.host)", "        pass",
       "test_credentials_in_the_url_are_refused"),
    _m("hostless_url_accepted", "a URL with no host has no destination to validate",
       F, "    if not target.host:\n        raise refused(\"no-host\")", "    pass",
       "test_only_http_urls_with_a_host_are_fetched"),
    _m("later_hops_unvalidated", "every hop is validated, not only the first",
       F, "                target = parse_source(url)\n"
          "                if self.monotonic() >= expires_at:",
       "                target = parse_source(url) if _hop == 0 else httpx.URL(url)\n"
       "                if self.monotonic() >= expires_at:",
       "test_a_redirect_to_another_scheme_or_to_credentials_is_refused"),
    _m("later_hops_unresolved", "every hop is resolved and re-pinned",
       F, "                address = await self._pin(target.host)",
       "                address = await self._pin(target.host) if _hop == 0 else target.host",
       "test_every_redirect_hop_is_validated_and_repinned",
       "test_a_rebinding_resolver_cannot_reach_an_internal_address"),
    _m("redirect_budget_ignored", "the redirect budget is MEDIA_FETCH_MAX_REDIRECTS",
       F, "            for _hop in range(limits.media_fetch_max_redirects + 1):",
       "            for _hop in range(100):",
       "test_a_redirect_chain_over_the_budget_is_refused"),
    _m("a_redirect_without_a_destination_is_content",
       "a 3xx with no Location is refused, not read as a body",
       F, "                        if not location:\n"
          "                            raise refused(\"bad-redirect\", host=target.host)",
       "                        if not location:\n                            location = \"/\"",
       "test_a_redirect_without_a_location_is_refused"),

    # --- what came back -------------------------------------------------------
    _m("any_status_is_content", "only 200 carries the media",
       F, "                    if response.status_code != 200:", "                    if False:",
       "test_a_non_200_response_is_refused_without_its_body"),
    _m("content_coding_accepted", "a compressed body is refused (the gzip bomb)",
       F, "                    if coding not in (\"\", \"identity\"):", "                    if False:",
       "test_a_compressed_body_is_refused_outright"),
    _m("any_type_is_a_video", "the response type is on an allow-list",
       F, "                    if mime is None:\n"
          "                        raise refused(\"unsupported-type\", host=target.host,\n"
          "                                      exc=errors.UnsupportedMedia)",
       "                    mime = mime or \"video/mp4\"",
       "test_a_non_video_response_is_refused",
       "test_an_undeclared_type_falls_back_to_the_extension_only"),
    _m("declared_length_ignored", "a declared oversize body is refused before it is read",
       F, "                    if declared.isdigit() and int(declared) > cap:", "                    if False:",
       "test_a_declared_oversize_body_is_refused_before_it_is_read"),
    _m("streaming_cap_removed", "the byte cap is enforced while the body streams",
       F, "                        if len(body) > cap:", "                        if False:",
       "test_an_oversize_body_is_aborted_mid_stream"),
    _m("aggregate_deadline_not_checked_between_chunks",
       "the aggregate time limit stops a slow body",
       F, "                        if self.monotonic() >= expires_at:\n"
          "                            raise refused(\"timeout\", host=target.host)\n"
          "                    if not body:",
       "                    if not body:",
       "test_a_slow_body_stops_at_the_aggregate_deadline"),
    _m("an_empty_body_is_a_video", "an empty body is not media",
       F, "                    if not body:\n"
          "                        raise refused(\"empty-body\", host=target.host)", "                    pass",
       "test_an_empty_body_is_not_a_video"),
    _m("upstream_text_echoed", "no upstream exception text reaches the caller or the log",
       F, "            self.log.warning(\"media fetch failed: type=%s\", type(exc).__name__)\n"
          "            raise refused(\"fetch-failed\") from None",
       "            self.log.warning(\"media fetch failed: %s\", exc)\n"
       "            raise refused(\"fetch-failed\") from exc",
       "test_a_refusal_tells_the_caller_and_the_log_nothing_about_the_url"),
    _m("a_timeout_is_an_unknown_failure", "a transport timeout is reported as a timeout",
       F, "        except httpx.TimeoutException:\n"
          "            self.log.warning(\"media fetch refused: reason=%s\", \"timeout\")\n"
          "            raise refused(\"timeout\") from None",
       "        except httpx.TimeoutException:\n            raise",
       "test_a_transport_timeout_is_a_timeout_refusal"),

    # --- data: URLs -----------------------------------------------------------
    _m("base64_not_validated", "strict base64: no whitespace or stray characters",
       F, "        data = base64.b64decode(payload, validate=True)",
       "        data = base64.b64decode(payload + \"===\", validate=False)",
       "test_a_data_url_is_decoded_strictly_and_within_a_bound"),
    _m("data_url_bound_after_decode", "the bound is applied before anything is decoded",
       F, "    if len(payload) > (cap + 2) // 3 * 4:\n"
          "        raise refused(\"too-large\", exc=errors.RequestTooLarge)", "    pass",
       "test_an_oversize_data_url_is_refused_before_it_is_decoded"),
    _m("any_data_url_type_accepted", "a data: URL's type is on the same allow-list",
       F, "    if mime not in ALLOWED_MIME:\n"
          "        raise refused(\"unsupported-type\", exc=errors.UnsupportedMedia)", "    pass",
       "test_a_data_url_must_be_base64_and_an_allowed_video_type"),
    _m("a_plain_data_url_is_decoded_as_base64", "only base64 is a bounded encoding",
       F, "    if \"base64\" not in parameters[1:]:\n        raise refused(\"bad-data-url\")",
       "    pass", "test_a_data_url_must_be_base64_and_an_allowed_video_type"),

    # --- staging --------------------------------------------------------------
    _m("stage_accepts_another_orgs_request", "a request is staged only for its own org",
       S, "            raise errors.Forbidden(\"a request may only be staged for its own org\")",
       "            pass", "test_a_request_may_only_be_staged_for_its_own_org"),
    _m("stage_accepts_a_foreign_reference", "a body cannot name another tenant's object",
       S, "                raise errors.NotFound(\"media reference does not belong to this org\")",
       "                pass", "test_a_foreign_or_oversize_reference_is_not_staged"),
    _m("stage_ignores_the_byte_cap", "a source over MAX_MEDIA_BYTES is refused",
       S, "            if ref.bytes > self.limits.max_media_bytes:", "            if False:",
       "test_a_foreign_or_oversize_reference_is_not_staged"),
    _m("stage_trusts_an_upload_handle", "an upload reference is resolved, not trusted",
       S, "                owned = await self.resolve_owned(org_id, ref.handle)\n"
          "                if owned.digest != ref.digest:",
       "                owned = ref\n                if False:",
       "test_an_upload_reference_is_resolved_not_trusted"),
    _m("stage_replaces_an_existing_object", "a staged handle keeps the content it has",
       S, "                    raise errors.Conflict(\n"
          "                        f\"media handle {ref.handle} already holds different content\")",
       "                    existing = ref", "test_a_staged_handle_keeps_the_content_it_has"),
    _m("stage_takes_the_last_of_two_handles", "one handle cannot carry two objects",
       S, "                raise errors.InvalidRequest(\n"
          "                    f\"media handle {ref.handle} appears twice with different content\")",
       "                pass", "test_one_handle_cannot_carry_two_different_objects_in_one_request"),
    _m("stage_indexes_as_it_goes", "staging is all or nothing",
       S, "            pending[(org_id, staged.handle)] = staged",
       "            pending[(org_id, staged.handle)] = staged\n"
       "            self.refs[(org_id, staged.handle)] = staged",
       "test_a_refused_request_stages_nothing_at_all"),
    _m("payload_key_from_the_request", "the caller never names the payload's path",
       S, "        key = f\"payloads/{org_id}/{request.request_id}.json\"",
       "        key = request.payload_ref",
       "test_staging_makes_the_canonical_payload_durable_with_a_digest_and_a_size"),
    _m("payload_not_stored", "the canonical payload is durable before acceptance",
       S, "        await self._write_once(key, payload, \"application/json\")", "        pass",
       "test_staging_makes_the_canonical_payload_durable_with_a_digest_and_a_size"),
    _m("payload_size_from_the_reference", "the durable payload's digest and size are measured",
       S, "        self.payloads[request.request_id] = StagedPayload(ref=key, digest=digest_of(payload),\n"
          "                                                          bytes=len(payload))",
       "        self.payloads[request.request_id] = StagedPayload(ref=key,\n"
       "                                                          digest=request.payload_digest,\n"
       "                                                          bytes=0)",
       "test_staging_makes_the_canonical_payload_durable_with_a_digest_and_a_size"),
    _m("an_object_can_be_replaced", "content already at a key is immutable",
       S, "        if stored != digest_of(data):\n"
          "            raise errors.Conflict(f\"an object already exists at {key} with different content\")",
       "        await self.objects.put(key, data, content_type)",
       "test_an_object_is_never_replaced_by_different_content",
       "test_a_second_payload_for_one_request_id_cannot_replace_the_first"),

    # --- keys, materialization and attach -------------------------------------
    _m("key_without_the_tenant", "two orgs never share an object",
       S, "        return f\"media/{org_id}/{profile_version}/{digest.split(':')[1][:16]}/{part}\"",
       "        return f\"media/{profile_version}/{digest.split(':')[1][:16]}/{part}\"",
       "test_two_organizations_never_share_an_object"),
    _m("key_without_the_profile_version",
       "the profile version namespaces the cache key (01)",
       S, "        return f\"media/{org_id}/{profile_version}/{digest.split(':')[1][:16]}/{part}\"",
       "        return f\"media/{org_id}/{digest.split(':')[1][:16]}/{part}\"",
       "test_a_fetched_source_becomes_a_tenant_scoped_content_addressed_object"),
    _m("any_source_materialized", "a media source is an http(s) or data: URL",
       S, "            raise errors.InvalidRequest(\"a media source must be an http(s) or data: URL\")",
       "            fetched, kind = await self.fetcher.fetch(source), MediaKind.url",
       "test_an_unsupported_source_is_refused_before_anything_is_stored"),
    _m("materialized_bytes_unbounded",
       "a materialized source is bounded whatever the fetcher returned",
       S, "        if len(fetched.data) > self.limits.max_media_bytes:", "        if False:",
       "test_an_oversize_source_never_reaches_the_object_store"),
    _m("attach_trusts_the_refs_tenant", "every attached ref belongs to the job's org (R52)",
       S, "                raise errors.NotFound(\"media attached to a job must belong to its org\")",
       "                pass", "test_attach_takes_the_tenant_from_the_job_row"),
    _m("attach_without_a_job_row", "the org comes from the job row, and there must be one",
       S, "        org_id = self.job_org(job_id)\n        for ref in refs:",
       "        org_id = refs[0].org_id if refs else None\n        for ref in refs:",
       "test_attach_takes_the_tenant_from_the_job_row"),
    _m("resolve_ignores_the_tenant", "a handle is resolved inside its own tenant only",
       S, "        media = self.refs.get((org_id, ref))",
       "        media = next((m for (_o, h), m in self.refs.items() if h == ref), None)",
       "test_two_organizations_never_share_an_object"),
)

# pytest exit codes: 0 all passed, 1 tests failed; 2-5 mean the runner broke.
PYTEST_TESTS_FAILED = 1
PYTEST_ALL_PASSED = 0
_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) ([^\s:]+(?:::[^\s]+)?)")


def _failing_ids(stdout: str) -> tuple[list[str], list[str]]:
    failed, errored = [], []
    for line in stdout.splitlines():
        match = _FAILED_LINE.match(line.strip())
        if match:
            (errored if line.strip().startswith("ERROR") else failed).append(match.group(1))
    return failed, errored


def run_mutant(mutant: Mutant) -> Result:
    """Apply one mutant to a throwaway copy and run the tests it names."""
    if not mutant.cases:
        return Result(Outcome.misdeclared, "declares no case")
    with tempfile.TemporaryDirectory(prefix=f"m1-mutant-{mutant.name}-") as tmp:
        root = pathlib.Path(tmp)
        for tree in (PACKAGE, "tests"):
            shutil.copytree(API_DIR / tree, root / tree,
                            ignore=shutil.ignore_patterns("__pycache__"))
        # the pinned pytest configuration travels too, or the copy collects under
        # different import rules than the suite was written for
        shutil.copy(API_DIR / "pyproject.toml", root / "pyproject.toml")
        target = root / PACKAGE / mutant.file
        source = target.read_text()
        if mutant.old not in source:
            return Result(Outcome.misdeclared,
                          f"anchor not found in {mutant.file}: {mutant.old[:60]!r}")
        target.write_text(source.replace(mutant.old, mutant.new, 1))
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
             "-rf", "--tb=no", *SUITE, "-k", " or ".join(mutant.cases)],
            cwd=root, capture_output=True, text=True,
            env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin"})
        stdout = done.stdout or ""
        lines = (stdout or done.stderr).strip().splitlines()
        summary = lines[-1] if lines else "no output"
        if done.returncode not in (PYTEST_ALL_PASSED, PYTEST_TESTS_FAILED):
            return Result(Outcome.broken_runner, f"pytest exit {done.returncode}: {summary}")
        if not re.search(r"(\d+) (?:passed|failed|skipped)", summary) or "no tests ran" in summary:
            return Result(Outcome.misdeclared, f"no test matched {mutant.cases}: {summary}")
        failed, errored = _failing_ids(stdout)
        if errored:
            return Result(Outcome.broken_runner, f"errors outside the named tests: {errored[:3]}")
        if done.returncode == PYTEST_ALL_PASSED or not failed:
            return Result(Outcome.survived, summary)
        stray = [test_id for test_id in failed
                 if not any(case in test_id for case in mutant.cases)]
        if stray:
            return Result(Outcome.broken_runner, f"failures outside the named tests: {stray[:3]}")
        return Result(Outcome.killed, summary)
