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
    # Review B1: the policy states the tunnel and special-purpose ranges itself, so the
    # decisive guards are these three plus `is_global`. The interpreter's `is_reserved`
    # stays as depth and has no mutant: on this build it also covers NAT64, so removing it
    # changes no answer (see the evidence report).
    _m("site_local_allowed", "deprecated site-local fec0::/10 is refused (is_global says True)",
       V, "    if a.version == 6 and a.is_site_local:\n        return False", "    pass",
       "test_the_address_policy_does_not_depend_on_the_interpreters_tables",
       "test_no_internal_address_form_is_reachable"),
    _m("denied_networks_not_checked",
       "the explicit deny-list is checked (6to4 relay anycast, RFC 9637 documentation)",
       V, "    if any(a in net for net in DENIED_NETWORKS if net.version == a.version):\n"
          "        return False", "    pass",
       "test_the_address_policy_does_not_depend_on_the_interpreters_tables",
       "test_no_internal_address_form_is_reachable"),
    _m("mapped_address_not_unwrapped", "a v4-mapped address is judged as the v4 it names",
       V, "    if a.version == 6 and a.ipv4_mapped:\n        a = a.ipv4_mapped", "    pass",
       "test_a_v4_mapped_public_address_is_judged_as_the_v4_it_names",
       "test_the_address_policy_does_not_depend_on_the_interpreters_tables"),
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
       F, "extensions={\"sni_hostname\": host, \"timeout\": self._budget(remaining)}",
       "extensions={\"timeout\": self._budget(remaining)}",
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

    _m("transport_logs_not_silenced",
       "httpx and httpcore never write a request line (the pinned IP and the query)",
       F, "silence_transport_logs()\n\nALLOWED_MIME", "ALLOWED_MIME",
       "test_no_logger_in_the_process_writes_a_url_an_ip_or_a_location"),
    _m("hop_deadline_not_checked",
       "the budget is checked at the start of every hop, before resolving or connecting",
       F, "                if remaining <= 0:\n                    raise refused(\"timeout\", host=host)",
       "                pass", "test_a_slow_redirect_chain_stops_at_the_aggregate_deadline"),
    _m("request_budget_is_the_whole_budget",
       "each request carries what is left of the aggregate budget, not all of it",
       F, "        connect = min(self.limits.media_fetch_connect_timeout_s, remaining)\n"
          "        return {\"connect\": connect, \"read\": remaining, \"write\": remaining, \"pool\": remaining}",
       "        whole = self.limits.media_fetch_timeout_s\n"
       "        return {\"connect\": self.limits.media_fetch_connect_timeout_s, \"read\": whole,\n"
       "                \"write\": whole, \"pool\": whole}",
       "test_each_request_carries_what_is_left_of_the_budget"),
    _m("resolution_not_bounded", "resolution happens inside the remaining budget",
       F, "            addresses = await asyncio.wait_for(self.resolve(host), max(remaining, 0.001))",
       "            addresses = await self.resolve(host)",
       "test_a_resolver_that_never_answers_is_bounded"),
    _m("no_real_clock_backstop",
       "the aggregate limit also holds on the real clock, for a phase the fetcher cannot see",
       F, "            async with asyncio.timeout(self.limits.media_fetch_timeout_s + BACKSTOP_GRACE_S):\n"
          "                return await self._fetch(url)",
       "            return await self._fetch(url)",
       "test_a_body_that_stalls_for_ever_is_bounded_by_the_backstop"),
    _m("asks_for_compression", "the request asks for no content coding",
       F, "\"Accept-Encoding\": \"identity\"", "\"Accept-Encoding\": \"gzip, br\"",
       "test_a_compressed_body_is_refused_outright"),
    _m("a_redirect_may_downgrade", "an https fetch never continues in plaintext",
       F, "                elif secure and target.scheme != \"https\":\n"
          "                    # A redirect must not downgrade: the signed query string of the\n"
          "                    # `Location` would then travel in plaintext.\n"
          "                    raise refused(\"insecure-redirect\", host=host)",
       "                elif False:\n                    pass",
       "test_a_redirect_may_not_downgrade_to_plaintext"),
    _m("cookies_carried_between_hops", "no cookie from one hop reaches the next",
       F, "                client.cookies.clear()", "                pass",
       "test_a_redirect_carries_no_cookie_from_the_hop_before"),
    _m("decoded_host_used_instead_of_the_wire_form",
       "one spelling of the host for the resolver, SNI and Host (IDNA2003 vs 2008)",
       F, "    return url.raw_host.decode(\"ascii\")", "    return url.host",
       "test_the_resolver_and_the_wire_agree_on_one_spelling_of_the_host"),

    # --- the URL and its hops -------------------------------------------------
    _m("any_scheme_fetched", "only http(s) URLs are fetched",
       F, "    if target.scheme not in (\"http\", \"https\"):", "    if False:",
       "test_only_http_urls_with_a_host_are_fetched",
       "test_a_redirect_to_another_scheme_or_to_credentials_is_refused"),
    _m("credentials_in_the_url_accepted", "a URL carrying credentials is refused",
       F, "        raise refused(\"credentials-in-url\", host=raw_host_of(target))", "        pass",
       "test_credentials_in_the_url_are_refused"),
    _m("hostless_url_accepted", "a URL with no host has no destination to validate",
       F, "    if not target.raw_host:\n        raise refused(\"no-host\")", "    pass",
       "test_only_http_urls_with_a_host_are_fetched"),
    _m("later_hops_unvalidated", "every hop is validated, not only the first",
       F, "                target = parse_source(url)\n                host = raw_host_of(target)",
       "                target = parse_source(url) if hop == 0 else httpx.URL(url)\n"
       "                host = raw_host_of(target)",
       "test_a_redirect_to_another_scheme_or_to_credentials_is_refused"),
    _m("later_hops_unresolved", "every hop is resolved and re-pinned",
       F, "                address = await self._pin(host, remaining)",
       "                address = await self._pin(host, remaining) if hop == 0 else host",
       "test_every_redirect_hop_is_validated_and_repinned",
       "test_a_rebinding_resolver_cannot_reach_an_internal_address"),
    _m("redirect_budget_ignored", "the redirect budget is MEDIA_FETCH_MAX_REDIRECTS",
       F, "            for hop in range(limits.media_fetch_max_redirects + 1):",
       "            for hop in range(100):",
       "test_a_redirect_chain_over_the_budget_is_refused"),
    _m("a_redirect_without_a_destination_is_content",
       "a 3xx with no Location is refused, not read as a body",
       F, "                        if not location:\n"
          "                            raise refused(\"bad-redirect\", host=host)",
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
          "                        raise refused(\"unsupported-type\", host=host,\n"
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
          "                            raise refused(\"timeout\", host=host)\n"
          "                    if not body:",
       "                    if not body:",
       "test_a_slow_body_stops_at_the_aggregate_deadline"),
    _m("an_empty_body_is_a_video", "an empty body is not media",
       F, "                    if not body:\n"
          "                        raise refused(\"empty-body\", host=host)", "                    pass",
       "test_an_empty_body_is_not_a_video"),
    _m("upstream_text_echoed", "no upstream exception text reaches the caller or the log",
       F, "            self.log.warning(\"media fetch failed: type=%s\", type(exc).__name__)\n"
          "            raise refused(\"fetch-failed\") from None",
       "            self.log.warning(\"media fetch failed: %s\", exc)\n"
       "            raise refused(\"fetch-failed\") from exc",
       "test_a_refusal_tells_the_caller_and_the_log_nothing_about_the_url"),
    _m("a_timeout_is_an_unknown_failure", "a transport timeout is reported as a timeout",
       F, "        except (TimeoutError, httpx.TimeoutException):\n"
          "            self.log.warning(\"media fetch refused: reason=%s\", \"timeout\")\n"
          "            raise refused(\"timeout\") from None",
       "        except (TimeoutError, httpx.TimeoutException):\n            raise",
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
       F, "    if mime not in allowed:\n"
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
    _m("stage_returns_the_callers_ref",
       "an owned object is staged as the store has it, not as the request describes it",
       S, "                resolved.append(existing)", "                resolved.append(ref)",
       "test_a_known_handle_is_staged_as_the_object_the_store_has"),
    _m("materialize_indexes_before_the_write",
       "no ref is indexed without an object behind it",
       S, "        await self._write_once(ref.storage_ref, fetched.data, fetched.mime)\n"
          "        self.refs[(org_id, ref.handle)] = ref",
       "        self.refs[(org_id, ref.handle)] = ref\n"
       "        await self._write_once(ref.storage_ref, fetched.data, fetched.mime)",
       "test_no_ref_is_indexed_without_an_object_behind_it"),
    _m("handle_clash_ignored", "a handle never comes to name different content",
       S, "            raise errors.Conflict(f\"handle {ref.handle} already names different content\")",
       "            pass", "test_a_handle_that_already_names_other_content_is_a_conflict"),
    _m("digest_taken_from_the_fetcher", "the stored object's digest is measured here",
       S, "        digest = digest_of(fetched.data)", "        digest = fetched.digest",
       "test_the_digest_is_measured_not_taken_from_the_fetcher"),
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
       S, "        key = f\"payloads/{valid_org(org_id)}/{request.request_id}.json\"",
       "        key = request.payload_ref",
       "test_staging_makes_the_canonical_payload_durable_with_a_digest_and_a_size"),
    _m("index_before_the_payload",
       "nothing is indexed until the durable payload write has succeeded",
       S, "        await self._write_once(key, payload, \"application/json\")\n"
          "        # One visible step: nothing above wrote to `self.refs`.\n"
          "        self.refs.update(pending)",
       "        self.refs.update(pending)\n"
       "        await self._write_once(key, payload, \"application/json\")",
       "test_a_fault_at_the_payload_write_stages_nothing_and_the_retry_completes_it"),
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
       S, "        if await self.objects.head(key) != digest_of(data):\n"
          "            raise errors.Conflict(f\"an object already exists at {key} with different content\")",
       "        pass",
       "test_an_object_is_never_replaced_by_different_content",
       "test_a_second_payload_for_one_request_id_cannot_replace_the_first"),

    # --- keys, materialization and attach -------------------------------------
    _m("key_without_the_tenant", "two orgs never share an object",
       S, "        return (f\"media/{valid_org(org_id)}/{valid_profile(profile_version)}\"",
       "        return (f\"media/{valid_profile(profile_version)}\"",
       "test_two_organizations_never_share_an_object"),
    _m("key_without_the_profile_version",
       "the profile version namespaces the cache key (01)",
       S, "        return (f\"media/{valid_org(org_id)}/{valid_profile(profile_version)}\"",
       "        return (f\"media/{valid_org(org_id)}\"",
       "test_a_fetched_source_becomes_a_tenant_scoped_content_addressed_object"),
    _m("profile_version_unvalidated",
       "a profile version is an identifier, not a path (cross-track hazard)",
       S, "    if not isinstance(version, str) or not PROFILE_VERSION_RE.fullmatch(version):",
       "    if False:", "test_a_profile_version_cannot_escape_the_tenants_prefix"),
    _m("org_unvalidated", "the tenant every key is namespaced by is validated first",
       S, "    if not isinstance(org_id, str) or not UUID_RE.fullmatch(org_id):",
       "    if False:", "test_a_malformed_tenant_is_refused_before_anything_is_fetched"),
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
       S, "        org_id = self.job_org(job_id)\n        owned: list[MediaRef] = []",
       "        org_id = refs[0].org_id if refs else None\n        owned: list[MediaRef] = []",
       "test_attach_takes_the_tenant_from_the_job_row"),
    _m("attach_accepts_an_unstaged_ref",
       "a job executes only on media this store staged, as the store described it",
       S, "            if indexed is None or indexed.digest != ref.digest:\n"
          "                raise errors.NotFound(f\"media {ref.handle} was not staged for org {org_id}\")\n"
          "            owned.append(indexed)",
       "            owned.append(ref)",
       "test_only_a_staged_ref_can_be_attached_to_a_job",
       "test_a_known_handle_is_staged_as_the_object_the_store_has"),
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
