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
SUITE = ("tests/m/test_fetch.py", "tests/m/test_store.py",
         "tests/m/test_probe.py", "tests/m/test_prepare.py")

F = "media/fetch.py"
S = "media/store.py"
V = "media/video.py"          # F1's address policy, reused by the new path
P = "media/probe.py"          # M2: the container probe
R = "media/prepare.py"        # M2: preparation, the profile and the cache


def _m(name, invariant, file, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- the address policy ---------------------------------------------------
    # Review B1 (r2 correction): every check in `address_allowed` is load bearing, and the
    # round-2 claim that `is_reserved` "changes no answer" was wrong - IPv4-compatible forms
    # (`::127.0.0.1`, `::a9fe:a9fe`) are global and not private on every build, and reserved
    # space outside `::/96` (`4000::1`) is refused by `is_reserved` alone. The policy states
    # the tunnel, IPv4-compatible and special-purpose ranges itself so it does not depend on
    # the interpreter's tables either way. `::/96` in `DENIED_NETWORKS` has no mutant for
    # that reason: it is the same refusal `is_reserved` already gives *on this build*, and it
    # is stated so the policy still holds on one where `::/8` is not reserved.
    _m("reserved_addresses_allowed",
       "reserved space is refused (IPv4-compatible forms and 4000::/3 are caught by it alone)",
       V, "or a.is_reserved or a.is_unspecified", "or a.is_unspecified",
       "test_the_address_policy_does_not_depend_on_the_interpreters_tables",
       "test_no_internal_address_form_is_reachable"),
    _m("multicast_allowed", "multicast is refused (224.0.0.1, ff02::1: nothing to fetch there)",
       V, "or a.is_multicast or a.is_reserved", "or a.is_reserved",
       "test_the_address_policy_does_not_depend_on_the_interpreters_tables",
       "test_no_internal_address_form_is_reachable"),
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
    _m("budget_not_recomputed_after_dns",
       "resolution spends the budget: the request carries what is left after it (review r2)",
       F, "                remaining = expires_at - self.monotonic()\n"
          "                if remaining <= 0:\n"
          "                    raise refused(\"timeout\", host=host)\n"
          "                # No Set-Cookie from one hop reaches the next",
       "                # No Set-Cookie from one hop reaches the next",
       "test_the_budget_is_recomputed_after_the_name_is_resolved"),
    _m("secure_decided_at_the_first_hop",
       "a chain that became confidential stays confidential (review r2)",
       F, "                secure = secure or target.scheme == \"https\"",
       "                secure = target.scheme == \"https\" if hop == 0 else secure",
       "test_a_chain_that_became_confidential_stays_confidential"),
    _m("connect_budget_not_capped",
       "connecting gets the remainder when less is left than the connect budget",
       F, "        connect = min(self.limits.media_fetch_connect_timeout_s, remaining)",
       "        connect = self.limits.media_fetch_connect_timeout_s",
       "test_each_request_carries_what_is_left_of_the_budget"),
    _m("fetch_ignores_the_configured_allow_list",
       "the allow-list the fetcher was given is the one the response is checked against",
       F, "                    mime = video_mime(response.headers.get(\"content-type\"), target,\n"
          "                                      self.allowed_mime)",
       "                    mime = video_mime(response.headers.get(\"content-type\"), target)",
       "test_the_allow_list_the_fetcher_was_given_is_the_one_that_is_used"),
    _m("data_urls_ignore_the_configured_allow_list",
       "the same allow-list decides a data: URL",
       S, "            fetched, kind = decode_data_url(source, self.limits, self.fetcher.allowed_mime), \\\n"
          "                MediaKind.inline",
       "            fetched, kind = decode_data_url(source, self.limits), MediaKind.inline",
       "test_the_allow_list_the_fetcher_was_given_is_the_one_that_is_used"),
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
       F, "                if hop and secure and target.scheme != \"https\":",
       "                if False:",
       "test_a_redirect_may_not_downgrade_to_plaintext",
       "test_a_chain_that_became_confidential_stays_confidential"),
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
       S, "        await self._write_once(ref.storage_ref, fetched.data, ref.mime)\n"
          "        self.refs[(org_id, ref.handle)] = ref",
       "        self.refs[(org_id, ref.handle)] = ref\n"
       "        await self._write_once(ref.storage_ref, fetched.data, ref.mime)",
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
    _m("profile_version_may_start_with_a_dot",
       "a profile version starts with an alphanumeric, so `..` is not one (B-R2-1.4)",
       S, 'PROFILE_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")',
       'PROFILE_VERSION_RE = re.compile(r"^[a-z0-9.][a-z0-9._-]{0,63}$")',
       "test_a_profile_version_cannot_escape_the_tenants_prefix"),
    _m("digest_unvalidated_in_the_key",
       "the digest a key is built from is validated where the key is built (review r2)",
       S, "                f\"/{valid_digest(digest)}/{part}\")",
       "                f\"/{digest.split(':')[1][:16]}/{part}\")",
       "test_a_digest_cannot_carry_a_path_into_a_key"),
    _m("org_unvalidated", "the tenant every key is namespaced by is validated first",
       S, "    if not isinstance(org_id, str) or not UUID_RE.fullmatch(org_id):",
       "    if False:", "test_a_malformed_tenant_is_refused_before_anything_is_fetched"),
    _m("org_matched_as_a_prefix",
       "the tenant is the whole string, not a UUID with something after it (B-R2-1.5)",
       S, "    if not isinstance(org_id, str) or not UUID_RE.fullmatch(org_id):",
       "    if not isinstance(org_id, str) or not UUID_RE.match(org_id):",
       "test_a_malformed_tenant_is_refused_before_anything_is_fetched"),
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
    _m("attach_lookup_is_not_tenant_scoped",
       "the ref a job gets is looked up in the job's own org (B-R2-1.3)",
       S, "            indexed = self.refs.get((org_id, ref.handle))",
       "            indexed = next((m for (_o, h), m in self.refs.items() if h == ref.handle),\n"
       "                           None)",
       "test_the_attach_lookup_is_inside_the_jobs_tenant"),
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
    # ======================================================================
    # M2: the container probe (`media/probe.py`)
    #
    # Two guards here deliberately have **no** mutant, for the reason M1 gave for `::/96`:
    # every single-edit version of them either produces the same refusal by another route or
    # makes the walk stop advancing, i.e. hang. They are the lower half of the ISO box-length
    # check (`size < body - at`, a termination guard, proved by the bounded-time assertion in
    # `test_a_hostile_length_is_refused_not_followed`) and the EBML `first == 0` width check
    # (a width of nine is refused by the truncation guard on any file short enough to matter).
    # `ProcessingCache.path_for`'s cache-root check is the third of the same kind: see
    # `cache_root_check_rejects_valid_paths` for what is and is not claimed about it.
    # ----------------------------------------------------------------------
    _m("box_length_not_compared_with_the_file",
       "a box may not claim more bytes than the file has (the content being there is not the "
       "same as the length being right)",
       P, "        if size < body - at or at + size > end:", "        if size < body - at:",
       "test_a_box_longer_than_the_file_is_refused_although_its_content_is_there"),
    _m("extended_64_bit_length_ignored",
       "`size == 1` is a 64-bit length; reading the literal 1 walks into the header",
       P, "        if size == 1:\n            size = _u(data, body, 8)\n            body += 8",
       "        if False:\n            size = _u(data, body, 8)\n            body += 8",
       "test_a_64_bit_box_length_is_read"),
    _m("to_end_of_file_length_ignored",
       "`size == 0` is \"to the end of the file\"; an empty box instead parses the payload "
       "after it as boxes",
       P, "        elif size == 0:\n            size = end - at",
       "        elif size == 0:\n            size = BOX_HEADER",
       "test_a_box_that_runs_to_the_end_of_the_file_is_read"),
    _m("second_movie_header_ignored",
       "two movie headers stating the duration is refused, not resolved first-wins - the "
       "second copy is then a free rewrite of the number the frame budget uses (review R20)",
       P, '            if kind == "mvhd":\n                if timescale or duration:',
       '            if kind == "mvhd":\n                if False:',
       "test_a_duplicate_duration_header_is_refused"),
    _m("second_matroska_duration_ignored",
       "likewise for a second Duration inside one Info",
       P, "            elif element == DURATION:\n                if duration:",
       "            elif element == DURATION:\n                if False:",
       "test_a_duplicate_duration_header_is_refused"),
    _m("mvhd_version_1_offsets",
       "version 1 of the movie header has 64-bit times, so the timescale and duration move",
       P, "    if version == 1:\n        return _u(data, start + 20, 4), _u(data, start + 24, 8)",
       "    if version == 1:\n        return _u(data, start + 12, 4), _u(data, start + 16, 4)",
       "test_the_probe_reads_the_container_the_bytes_describe"),
    _m("mvhd_version_unchecked",
       "an undefined movie-header version is refused, not read as version 0",
       P, '    raise _refuse("mvhd-version")',
       "    return _u(data, start + 12, 4), _u(data, start + 16, 4)",
       "test_a_container_that_states_no_usable_duration_is_refused"),
    _m("tkhd_version_1_offsets",
       "version 1 of the track header moves the geometry by twelve bytes",
       P, "    at = start + (88 if version == 1 else 76)", "    at = start + 76",
       "test_the_probe_reads_the_container_the_bytes_describe"),
    _m("tkhd_fixed_point_not_converted",
       "a track header stores 16.16 fixed point, so 640 is 0x02800000",
       P, "    return _u(data, at, 4) >> 16, _u(data, at + 4, 4) >> 16",
       "    return _u(data, at, 4), _u(data, at + 4, 4)",
       "test_the_probe_reads_the_container_the_bytes_describe"),
    _m("zero_timescale_not_refused",
       "a zero timescale is refused (declared: without it the kill is an unhandled "
       "ZeroDivisionError in the named case, not a typed refusal)",
       P, "    if not timescale or not duration:", "    if not duration:",
       "test_a_container_that_states_no_usable_duration_is_refused"),
    _m("video_track_read_by_position",
       "the video track is the one whose sample entry is a video codec, not track 1",
       P, "    video = next((t for t in tracks if t.get(\"format\") in MP4_CODECS), None)",
       "    video = tracks[0] if tracks else None",
       "test_the_video_track_is_chosen_by_its_codec_not_its_position"),
    _m("mp4_codec_allowlist_widened",
       "the MP4 codec table is the serving pin: ProRes, MPEG-4 part 2 and a JPEG track are "
       "refused whether or not a decoder would open them",
       P, '"av01": "av1", "vp08": "vp8", "vp09": "vp9"}',
       '"av01": "av1", "vp08": "vp8", "vp09": "vp9", "apcn": "prores", "mp4v": "mpeg4",\n'
       '              "jpeg": "jpeg"}',
       "test_a_container_with_no_servable_video_track_is_refused"),
    _m("quicktime_brand_ignored",
       "a `qt  ` major brand is QuickTime, which is a different accepted type and a "
       "different cache extension",
       P, "    return Probed(mime=QUICKTIME_MIME if brand.startswith(b\"qt\") else MP4_MIME,",
       "    return Probed(mime=MP4_MIME,",
       "test_the_probe_reads_the_container_the_bytes_describe"),
    _m("iso_element_budget_removed",
       "the walk stops after MAX_ELEMENTS headers, so a few kilobytes of empty boxes cannot "
       "cost seconds of CPU",
       P, '            if walked > MAX_ELEMENTS:\n                raise _refuse("too-many-boxes")',
       '            if walked > MAX_ELEMENTS * 1000:\n                raise _refuse("too-many-boxes")',
       "test_many_empty_boxes_stop_at_the_element_budget"),
    _m("iso_depth_limit_removed",
       "nesting is bounded, so caller-controlled depth is not a stack",
       P, '            elif kind in ("moov", "trak", "mdia", "minf", "stbl"):\n'
          "                if depth >= MAX_DEPTH:",
       '            elif kind in ("moov", "trak", "mdia", "minf", "stbl"):\n'
          "                if False:",
       "test_a_deeply_nested_container_stops_at_the_depth_limit"),
    _m("ebml_unknown_size_accepted_anywhere",
       "an unknown size is legal on a streamed Segment only; anywhere else it lets the rest "
       "of the file be read as that element's children",
       P, '            if unknown:\n                if element != SEGMENT:\n'
          '                    raise _refuse("unknown-size")\n                size = end - body',
       "            if unknown:\n                size = end - body",
       "test_a_hostile_length_is_refused_not_followed"),
    _m("ebml_element_length_unchecked",
       "an EBML element may not claim more bytes than its parent has",
       P, "            if size < 0 or body + size > end:", "            if size < 0:",
       "test_a_hostile_length_is_refused_not_followed"),
    _m("ebml_uint_width_unchecked",
       "an EBML unsigned integer is at most eight bytes wide: wider ones were accepted as "
       "absurd values, overflowed the arithmetic, or broke the refusal's own formatting - a "
       "500 where the contract says unsupported_media (review B1)",
       P, "    if not 0 < size <= 8:\n        raise _refuse(\"bad-uint\")", "    pass",
       "test_a_hostile_length_is_refused_not_followed"),
    _m("timecode_scale_assumed_default",
       "a Matroska duration is in timecode-scale units, and the scale is in the file",
       P, "                scale = _ebml_uint(data, body, size) or DEFAULT_TIMECODE_SCALE",
       "                scale = DEFAULT_TIMECODE_SCALE",
       "test_the_probe_reads_the_container_the_bytes_describe"),
    _m("matroska_codec_allowlist_widened",
       "the Matroska codec table is the same serving pin: Theora is refused",
       P, '"V_AV1": "av1", "V_VP8": "vp8", "V_VP9": "vp9"}',
       '"V_AV1": "av1", "V_VP8": "vp8", "V_VP9": "vp9",\n                     '
       '"V_THEORA": "theora"}',
       "test_a_container_with_no_servable_video_track_is_refused"),
    _m("matroska_track_type_ignored",
       "the track type is what says there are frames: a video codec on an audio track is "
       "not a video",
       P, '    video = next((t for t in tracks\n                  if t["type"] == VIDEO_TRACK_TYPE '
          'and t["codec"] in MATROSKA_CODECS), None)',
       '    video = next((t for t in tracks\n                  if t["codec"] in MATROSKA_CODECS), None)',
       "test_a_container_with_no_servable_video_track_is_refused"),
    _m("frame_size_unchecked",
       "0x0 and 65535x65535 are declarations, not frames: the cheapest decode bombs there are",
       P, "    if not 0 < probed.width <= MAX_DIMENSION or not 0 < probed.height <= MAX_DIMENSION:\n"
          '        raise _refuse(f"dimensions:{probed.width}x{probed.height}")',
       "    pass",
       "test_a_frame_size_that_is_a_declaration_is_refused"),
    _m("non_finite_duration_allowed_out",
       "`inf`, `nan` and a negative duration never leave the probe (the engine computes a "
       "frame budget from this number)",
       P, '    if not (0 < probed.duration_s < float("inf")):\n'
          '        raise _refuse(f"duration:{probed.duration_s}")',
       "    pass",
       "test_a_non_finite_duration_never_leaves_the_probe",
       "test_a_container_that_states_no_usable_duration_is_refused"),

    # ======================================================================
    # M2: preparation, the profile table and the processing cache (`media/prepare.py`)
    # ----------------------------------------------------------------------
    _m("no_probe_at_materialization",
       "the ref's duration and type are measured from the bytes; without the probe the "
       "engine refuses every video request (S2M D2)",
       R, "        probed = await self.probed(data)\n        self.profile.check(probed, len(data))\n"
          "        return probed.mime, probed.duration_s",
       "        return mime, None",
       "test_preparation_measures_the_clip_and_fills_the_record",
       "test_what_preparation_produces_is_what_the_engine_adapter_accepts"),
    _m("declared_type_kept_over_the_container",
       "the sniffed container wins over the `Content-Type`, because the extension the engine "
       "opens and the object's content type must match its bytes",
       R, "        return probed.mime, probed.duration_s", "        return mime, probed.duration_s",
       "test_the_container_wins_over_the_declared_type"),
    _m("profile_not_checked_before_the_write",
       "media the profile refuses is never stored: the check runs on the measurement, before "
       "the object exists",
       R, "        self.profile.check(probed, len(data))\n        return probed.mime",
       "        return probed.mime",
       "test_a_clip_over_the_duration_cap_is_refused_before_it_is_stored",
       "test_media_the_profile_refuses_is_never_stored"),
    _m("duration_cap_removed",
       "MAX_VIDEO_SECONDS is what keeps an accepted clip at the trained 2 fps (S2M D9)",
       R, "        if probed.duration_s > self.max_duration_s:", "        if False:",
       "test_a_clip_over_the_duration_cap_is_refused_before_it_is_stored",
       "test_a_tightened_profile_refuses_media_the_probe_can_read"),
    _m("profile_codec_allowlist_removed",
       "the profile's codec set is the pin, which may be tighter than what the probe parses",
       R, "        if probed.codec not in self.allowed_codecs:", "        if False:",
       "test_a_tightened_profile_refuses_media_the_probe_can_read"),
    _m("profile_mime_allowlist_removed",
       "the profile's container set is the pin, likewise",
       R, "        if probed.mime not in self.allowed_mime:", "        if False:",
       "test_a_tightened_profile_refuses_media_the_probe_can_read"),
    _m("profile_byte_cap_removed",
       "the profile's byte cap is enforced on what was really decoded",
       R, "        if nbytes > self.max_bytes:", "        if False:",
       "test_a_tightened_profile_refuses_media_the_probe_can_read"),
    _m("duration_cap_not_read_from_the_settings",
       "the pinned profile reads MAX_VIDEO_SECONDS from the settings, not a literal",
       R, "        return cls(version=valid_profile(version), max_duration_s=limits.max_video_seconds,",
       "        return cls(version=valid_profile(version), max_duration_s=1e9,",
       "test_a_clip_over_the_duration_cap_is_refused_before_it_is_stored"),
    _m("parts_per_request_uncapped",
       "one clip per request is a capacity fact, and it is checked before anything is fetched",
       R, "        if len(sources) > self.profile.max_parts:", "        if False:",
       "test_more_parts_than_the_profile_allows_are_refused"),
    _m("request_org_coherence_removed",
       "R10: a request is prepared for its own organization or not at all",
       R, "        if request.org_id != org_id:\n"
          '            raise errors.Forbidden("a request may only be prepared for its own org")',
       "        if False:\n"
          '            raise errors.Forbidden("a request may only be prepared for its own org")',
       "test_a_request_may_only_be_prepared_for_its_own_org"),
    _m("callers_media_tuple_kept",
       "`NormalizedRequest.media` is a claim: the prepared record carries the refs "
       "preparation actually materialized",
       R, '                                          "media": refs})',
       '                                          "media": request.media or refs})',
       "test_a_ref_the_caller_put_in_the_record_is_discarded"),
    _m("rewrite_out_of_order",
       "R58 pairs the n-th media part with the n-th ref; out of order is an answer about "
       "another clip",
       R, "                parts.append({\"type\": VIDEO_PART, VIDEO_PART: {REF_KEY: refs[consumed].handle}})",
       "                parts.append({\"type\": VIDEO_PART, VIDEO_PART: {REF_KEY: refs[-1].handle}})",
       "test_the_parts_and_the_refs_stay_in_order"),
    _m("video_part_shape_unchecked",
       "a part with no url, or one that is not a string, is a typed refusal naming the part "
       "(declared: without it the kill is an AttributeError inside materialization)",
       R, "        if not isinstance(source, str) or not source:\n"
          '            raise errors.InvalidRequest("a video part carries exactly {url}", param="messages")',
       "        if False:\n"
          '            raise errors.InvalidRequest("a video part carries exactly {url}", param="messages")',
       "test_a_video_part_that_is_not_exactly_a_url_is_refused"),
    _m("preparation_pool_unbounded",
       "r1 R1: PREPARATION_CONCURRENCY bounds how many clips are in memory at once",
       R, "        self.gate = asyncio.Semaphore(max(1, self.limits.preparation_concurrency))",
       "        self.gate = asyncio.Semaphore(1024)",
       "test_preparation_runs_at_most_the_pool_width_at_once"),
    _m("request_memory_budget_removed",
       "one request has one media budget, however many parts it spreads it over",
       R, "        if self.spent > self.allowed:", "        if False:",
       "test_one_request_cannot_exceed_the_media_budget"),
    _m("probe_deadline_removed",
       "PROBE_TIMEOUT_S bounds a decoder that never returns",
       R, "            return await asyncio.wait_for(work, self.limits.probe_timeout_s)",
       "            return await work",
       "test_a_probe_that_never_returns_is_bounded_by_its_deadline"),
    _m("head_check_removed",
       "the cheap check answers first: HEAD says the object is gone, so 64 MiB is never read",
       R, "            if await self.objects.head(key) != ref.digest:", "            if False:",
       "test_an_object_that_vanished_between_attach_and_prepare_is_not_found"),
    _m("digest_not_rechecked_after_the_read",
       "HEAD and digest, not HEAD alone: a store that answers with other bytes must not have "
       "them prepared and answered about",
       R, "                if data is None or digest_of(data) != ref.digest:",
       "                if data is None:",
       "test_an_object_whose_content_changed_is_not_prepared"),
    _m("cache_hit_stands_in_for_the_durable_artifact",
       "a cache hit is a hit on the local copy; the durable prepared artifact is the record, "
       "so its absence runs the whole path again",
       R, "            if entry is None or await self.objects.head(prepared_key) is None:",
       "            if entry is None:",
       "test_a_prepared_artifact_that_is_gone_is_written_again"),
    _m("prepared_key_uses_the_sources_profile",
       "01: the profile version namespaces the cache, so it is the *requested* profile in "
       "the prepared key",
       R, '                "profile_version": version, "storage_ref": prepared_key,',
       '                "profile_version": version,\n                "storage_ref": self._key('
       'ref.org_id, ref.digest, ref.profile_version, "prepared"),',
       "test_the_profile_version_namespaces_the_prepared_artifact"),
    _m("local_copy_written_before_the_durable_artifact",
       "durable before local: a cache entry written first survives a failed durable write "
       "and the next attempt reports a job prepared against an object that is not there "
       "(review R12)",
       R, "                # Durable before local: the prepared artifact exists in the object store\n"
          "                # before anything downstream can be told the job is prepared.\n"
          "                await self._write_once(prepared_key, body, probed.mime)\n"
          "                entry = (self.cache.put(ref.org_id, ref.digest, version, body, probed)\n"
          "                         if self.cache.enabled\n"
          '                         else CacheEntry("", probed, len(body), 0.0))',
       "                entry = (self.cache.put(ref.org_id, ref.digest, version, body, probed)\n"
          "                         if self.cache.enabled\n"
          '                         else CacheEntry("", probed, len(body), 0.0))\n'
          "                await self._write_once(prepared_key, body, probed.mime)",
       "test_a_failed_durable_write_leaves_no_local_copy"),
    _m("duration_cap_is_a_literal",
       "the duration bound is this deployment's MAX_VIDEO_SECONDS, not the number the "
       "default happens to carry (review R7)",
       R, "        return cls(version=valid_profile(version), max_duration_s=limits.max_video_seconds,",
       "        return cls(version=valid_profile(version), max_duration_s=120.0,",
       "test_a_clip_over_the_duration_cap_is_refused_before_it_is_stored"),
    _m("duration_cap_excludes_its_own_bound",
       "the cap is inclusive: 120.000 s is 240 frames at 2 fps, the worst case the profile "
       "is sized for (review R24)",
       R, "        if probed.duration_s > self.max_duration_s:",
       "        if probed.duration_s >= self.max_duration_s:",
       "test_a_clip_exactly_at_the_cap_is_accepted"),
    _m("prepared_artifact_not_persisted",
       "the prepared artifact is durable before anything downstream is told the job is "
       "prepared; the local cache is a copy, not the record",
       R, "                await self._write_once(prepared_key, body, probed.mime)",
       "                pass",
       "test_prepare_persists_the_artifact_and_a_file_the_engine_can_open"),
    _m("prepared_ref_trusts_the_attached_duration",
       "the prepared duration is the measurement, not the number on the attached record "
       "(a job row can hand back whatever it stored) (review R1/R23)",
       R, '                "duration_s": entry.probed.duration_s}))',
       '                "duration_s": ref.duration_s}))',
       "test_the_prepared_ref_carries_the_measurement_not_the_attached_record"),
    _m("prepared_ref_trusts_the_attached_mime",
       "likewise the container: it decides the cache file's extension and what the engine "
       "is told it is opening",
       R, '                "mime": entry.probed.mime, "bytes": entry.bytes,',
       '                "mime": ref.mime, "bytes": entry.bytes,',
       "test_the_prepared_ref_carries_the_measurement_not_the_attached_record"),
    _m("prepared_ref_trusts_the_attached_size",
       "likewise the size of the artifact that was actually prepared",
       R, '                "mime": entry.probed.mime, "bytes": entry.bytes,',
       '                "mime": entry.probed.mime, "bytes": ref.bytes,',
       "test_the_prepared_ref_carries_the_measurement_not_the_attached_record"),
    _m("prepared_ref_carries_no_duration",
       "the prepared ref carries the measured duration, which is what the engine budgets "
       "frames from",
       R, '                "duration_s": entry.probed.duration_s}))', '                "duration_s": None}))',
       "test_prepare_persists_the_artifact_and_a_file_the_engine_can_open",
       "test_what_preparation_produces_is_what_the_engine_adapter_accepts"),
    _m("prepared_refs_replace_the_attached_sources",
       "R46 allows bounded preparation retries, so a second attempt re-derives the same "
       "answer instead of preparing the first attempt's output",
       R, "        self.prepared_by_job[job_id] = tuple(prepared)",
       "        self.by_job[job_id] = tuple(prepared)",
       "test_preparing_twice_is_the_same_answer"),
    _m("unknown_job_prepared_as_empty",
       "r1 R46/q23: `prepare` resolves this job's refs or nothing; an empty prepared set for "
       "an unknown job looks like a finished preparation",
       R, "        if sources is None:\n"
          '            raise errors.NotFound(f"no staged media for job {job_id}")',
       "        if sources is None:\n            sources = ()",
       "test_prepare_refuses_a_job_it_knows_nothing_about"),
    _m("profile_version_unvalidated_before_the_read",
       "the profile version is validated before the object is read, so a malformed one costs "
       "no download and reaches no path",
       R, "        version = valid_profile(profile)", "        version = profile",
       "test_a_profile_version_cannot_escape_the_cache_root"),
    _m("cache_expiry_removed",
       "PROCESSING_CACHE_TTL_S is a retention obligation: past it the entry is unreadable",
       R, "        if self.clock() - entry.stored_at >= self.ttl_s:", "        if False:",
       "test_a_cache_entry_expires_and_its_file_goes_with_it"),
    _m("cache_file_existence_unchecked",
       "the index is a hint about the disk: an entry pointing at a file that is gone is a "
       "miss, not a path handed to a worker",
       R, "        if not os.path.exists(entry.local_path):", "        if False:",
       "test_a_cache_file_deleted_behind_the_index_is_a_miss"),
    _m("cache_path_without_the_tenant",
       "MEDIA-SEC: the organization is in the path, so identical bytes in two tenants are two "
       "files and neither tenant can reach the other's",
       R, "        path = os.path.join(self.root, valid_org(org_id), valid_profile(profile),",
       "        path = os.path.join(self.root, valid_profile(profile),",
       "test_one_tenants_cache_entry_is_not_another_tenants",
       "test_the_cache_path_is_built_from_validated_parts_only"),
    _m("cache_digest_segment_width",
       "a key and a cache path carry the first 16 hex characters of the digest; a shorter "
       "segment is a different address space for the same content (review survivor R9)",
       S, '    return digest[len("sha256:"):][:16]', '    return digest[len("sha256:"):][:12]',
       "test_the_cache_path_is_built_from_validated_parts_only",
       "test_a_fetched_source_becomes_a_tenant_scoped_content_addressed_object"),
    _m("cache_root_check_rejects_valid_paths",
       "the cache-root check must accept the paths the cache builds. **Removing** it is not "
       "killable and is not claimed: `valid_org`, `valid_profile` and `valid_digest` already "
       "exclude every segment that could escape, so it is belt to their braces (review "
       "survivor R10) - what is proved here is that the belt does not refuse a valid path",
       R, "        if os.path.commonpath([self.root, os.path.abspath(path)]) != self.root:",
       "        if os.path.commonpath([self.root, os.path.abspath(path)]) == self.root:",
       "test_the_cache_path_is_built_from_validated_parts_only"),
    _m("cache_path_without_the_profile",
       "R61 as amended: the profile version is a path segment as well as an index key, or "
       "two profiles of one source share one file and expiring one deletes the other's "
       "bytes (review B2)",
       R, "        path = os.path.join(self.root, valid_org(org_id), valid_profile(profile),\n"
          '                            valid_digest(digest), f"{SOURCE_FILENAME}.{extension}")',
       "        path = os.path.join(self.root, valid_org(org_id),\n"
          '                            valid_digest(digest), f"{SOURCE_FILENAME}.{extension}")',
       "test_two_profiles_of_one_object_are_two_local_files",
       "test_the_cache_path_is_built_from_validated_parts_only"),
    _m("cache_extension_unchecked",
       "a cache file is named for a container the profile serves; `source.None` is not a "
       "file any decoder opens",
       R, "        extension = EXTENSIONS.get(mime)\n        if extension is None:",
       "        extension = EXTENSIONS.get(mime)\n        if False:",
       "test_the_cache_path_is_built_from_validated_parts_only"),
    _m("sweep_removes_live_entries",
       "a sweep removes what is past its life and nothing else",
       R, "                   if now - entry.stored_at >= self.ttl_s]", "                   if True]",
       "test_a_sweep_removes_expired_entries_and_leaves_live_ones"),

    # --- M1's store, where M2's probe hook meets it ------------------------
    _m("facts_hook_not_consulted",
       "what a ref records about the bytes comes from `facts`, which M2 overrides with a "
       "probe; hard-coding the fetcher's answer loses the duration and the container",
       S, "        mime, duration_s = await self.facts(fetched.data, fetched.mime)",
       "        mime, duration_s = fetched.mime, None",
       "test_preparation_measures_the_clip_and_fills_the_record",
       "test_the_container_wins_over_the_declared_type"),
    _m("stored_content_type_is_the_declared_one",
       "the object is stored as the container it is, not as the type the server declared",
       S, "        await self._write_once(ref.storage_ref, fetched.data, ref.mime)",
       "        await self._write_once(ref.storage_ref, fetched.data, fetched.mime)",
       "test_the_container_wins_over_the_declared_type"),
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
