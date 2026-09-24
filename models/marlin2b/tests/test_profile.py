#!/usr/bin/env python3
"""E1C: the versioned run profile, `--validate-only`, and the §6 result fields.

    apps/infrx-api/.venv/bin/python -m pytest -q models/marlin2b/tests/test_profile.py

No request leaves in any validation case: the fake gateway's own counters prove it.
"""
import contextlib, io, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import bench
import runprofile
from fake_gateway import FakeGateway
from test_bench import KEY, base_argv, make_clips, run_bench, with_clips

SHA = "0123456789abcdef" * 4


def setup(tmp, n=4):
    """Clips (with their byte sizes, as the committed manifest records them) and a manifest
    file whose digest the profile pins."""
    clips = make_clips(n, tmp)
    for c in clips:
        c["bytes"] = os.path.getsize(c["path"])
    with_clips(clips)
    manifest = os.path.join(tmp, "manifest.json")
    with open(manifest, "w", encoding="utf-8") as f:
        json.dump({"corpus_version": "test", "clips": [c["id"] for c in clips]}, f)
    return clips, manifest


def profile_for(clips, manifest, **over):
    p = {
        "schema": "infrx.run-profile/1",
        "identity": {"run_id": "e1c-local-1", "source_sha": "a" * 40, "deployed_sha": "b" * 40,
                     "image_digest": "sha256:" + SHA, "weights_sha256": "sha256:" + SHA,
                     "tokenizer_sha256": "sha256:" + SHA, "processor_sha256": "sha256:" + SHA,
                     "template_sha256": "sha256:" + SHA, "model_revision": "marlin2b",
                     "preparation_profile": "v1", "engine_options": {"max_num_seqs": 8},
                     "migration_version": "0018", "config_version": "pilot-1"},
        "target": {"path": "direct-gateway", "allowlist": ["fake.invalid"],
                   "environment_owner": "e1c lane (local fake)", "allowed_fault_targets": [],
                   "tenant_key_env": ["MARLIN_API_KEY", "INFRX_API_KEY"],
                   "test_key_ids": ["3c3c3c3c"], "customer_traffic": False,
                   "maintenance_window": None},
        "bounds": {"max_duration_s": 60, "max_requests": 8, "max_input_bytes": 1 << 20,
                   "max_input_tokens_per_request": 20000, "max_output_tokens_per_request": 512,
                   "max_output_tokens": 4096, "max_concurrency": 2, "max_drain_s": 30,
                   "spend": {"currency": "USD", "max_usd": 1.0, "outstanding_holds_usd": 0.0,
                             "rates": {"input_usd_per_mtok": 0.10, "output_usd_per_mtok": 0.30,
                                       "source": "P-22 pv_marlin2b_usd_2026_09",
                                       "as_of": "2026-09-24"}},
                   "stop_thresholds": {}},
        "workload": {"manifest_sha256": runprofile.file_sha256(manifest),
                     "data_source": "synthetic test clips", "item_ids": [c["id"] for c in clips],
                     "dataset_version": "cell-1", "seed": 0, "forms": ["video_b64"],
                     "max_tokens_mix": [512], "max_clip_duration_s": 82.0, "transport": "sse",
                     "tenants": 1, "expected_invalid": []},
        "measurement": {"profile_class": "P3",
                        "client": {"location": "same host", "resources": "1 process",
                                   "clock": "perf_counter"},
                        "arrival": "closed-loop", "rate_per_s": None, "concurrency": 1,
                        "warmup_excluded": True, "cache_regime": "warm-distinct",
                        "max_driver_lag_s": 1.0, "min_samples": 6, "repeats": 1,
                        "thresholds": {}, "fault_schedule": [], "price": None},
        "cleanup": {"policy": "drain", "owned_prefix": "infrx-e1c-", "restore": "none needed",
                    "max_cleanup_s": 60, "evidence_destination": "models/marlin2b/results/"},
    }
    for path, value in over.items():            # "bounds.max_requests" -> value
        *parents, leaf = path.split(".")
        node = p
        for key in parents:
            node = node[key]
        if value is DELETE:
            del node[leaf]
        else:
            node[leaf] = value
    return p


DELETE = object()


def argv_for(tmp, manifest, profile, *extra, **kw):
    kw = {"requests": 4, "concurrency": 1, "dataset_version": "cell-1", **kw}
    a = base_argv(tmp, **kw)
    a[a.index("--corpus") + 1] = manifest
    fd, path = tempfile.mkstemp(dir=tmp, prefix="profile-", suffix=".json")   # one per call
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(profile, f)
    return a + ["--profile", path, *extra]


def validate_only(argv, gw=None):
    """(exit code, verdict) of a --validate-only call; no request may leave."""
    gw = gw or FakeGateway()
    bench.load_transport = lambda spec: gw.transport()
    old = os.environ.get("MARLIN_API_KEY")
    os.environ["MARLIN_API_KEY"] = KEY
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = bench.main(argv + ["--validate-only"])
    finally:
        os.environ.pop("MARLIN_API_KEY") if old is None else os.environ.__setitem__(
            "MARLIN_API_KEY", old)
    assert not gw.seen and not gw.uploads and gw.home is None, "validation sent a request"
    return code, json.loads(out.getvalue())


def test_validate_only_passes_a_complete_profile_and_sends_nothing():
    """Oracle: a validation path that runs inference or provisions anything (the fake saw a
    request), or that rejects a complete profile."""
    with tempfile.TemporaryDirectory() as tmp:
        clips, manifest = setup(tmp)
        code, v = validate_only(argv_for(tmp, manifest, profile_for(clips, manifest)))
        assert code == 0 and v["valid"] and v["runnable"], v
        # a local fake needs no key inventory: the block is reported, not enforced
        assert v["blocks"] == ["no --key-inventory: the target's active keys are unknown (P-24)"]
        assert v["derived"]["projected_spend_usd"] == round(4 * (20000 * 0.1 + 512 * 0.3) / 1e6, 6)


def test_a_missing_group_or_field_refuses_the_run_before_any_request():
    """Oracle: a resource-consuming run that starts without its declared identity, target,
    bounds, workload, measurement or cleanup."""
    with tempfile.TemporaryDirectory() as tmp:
        clips, manifest = setup(tmp)
        for group in ("identity", "target", "bounds", "workload", "measurement", "cleanup",
                      "identity.deployed_sha", "bounds.spend", "workload.manifest_sha256",
                      "measurement.arrival", "cleanup.max_cleanup_s", "target.path"):
            code, v = validate_only(argv_for(tmp, manifest,
                                             profile_for(clips, manifest, **{group: DELETE})))
            assert code == 2 and not v["valid"], group
            assert any(e.endswith(f"{group.split('.')[-1]}: required") for e in v["errors"]), \
                (group, v["errors"])
        # and the RUN, not only the validator, refuses: nothing reaches the gateway
        gw = FakeGateway()
        bench.load_transport = lambda spec: gw.transport()
        broken = profile_for(clips, manifest, bounds=DELETE)
        with contextlib.redirect_stderr(io.StringIO()):
            os.environ["MARLIN_API_KEY"] = KEY
            try:
                code = bench.main(argv_for(tmp, manifest, broken))
            finally:
                os.environ.pop("MARLIN_API_KEY")
        assert code == 2 and not gw.seen


def test_every_flag_must_sit_inside_the_declared_target_workload_and_bounds():
    """Oracle: a profile that validates while the run differs from it - another host, other
    data, more requests, another arrival model - bounds nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        clips, manifest = setup(tmp)
        good = profile_for(clips, manifest)
        cases = {
            "host": (argv_for(tmp, manifest, good, "--base-url", "http://other.invalid/v1"),
                     "not on target.allowlist"),
            "requests": (argv_for(tmp, manifest, good, requests=9), "bounds.max_requests"),
            "max tokens": (argv_for(tmp, manifest, good, max_tokens=1024),
                           "bounds.max_output_tokens_per_request"),
            "concurrency": (argv_for(tmp, manifest, profile_for(
                clips, manifest, **{"measurement.concurrency": 4}), concurrency=4),
                "bounds.max_concurrency"),
            "dataset": (argv_for(tmp, manifest, good, dataset_version="cell-2"),
                        "workload.dataset_version"),
            "seed": (argv_for(tmp, manifest, good, seed=7), "workload.seed"),
            "arrival": (argv_for(tmp, manifest, good, rate=1.0), "measurement.arrival"),
            "manifest": (argv_for(tmp, manifest, profile_for(
                clips, manifest, **{"workload.manifest_sha256": SHA})), "manifest_sha256"),
            "items": (argv_for(tmp, manifest, profile_for(
                clips, manifest, **{"workload.item_ids": ["clip000"]})), "workload.item_ids"),
            "over-cap undeclared": (argv_for(tmp, manifest, profile_for(
                clips, manifest, **{"workload.max_clip_duration_s": 3.0})), "expected_invalid"),
            "bytes": (argv_for(tmp, manifest, profile_for(
                clips, manifest, **{"bounds.max_input_bytes": 100})), "max_input_bytes"),
            "duration": (argv_for(tmp, manifest, profile_for(
                clips, manifest, **{"measurement.arrival": "open-loop",
                                    "measurement.rate_per_s": 0.01}), rate=0.01),
                         "bounds.max_duration_s"),
            "fault target": (argv_for(tmp, manifest, profile_for(
                clips, manifest, **{"measurement.fault_schedule": [
                    {"at_s": 1, "target": "infrx-worker", "action": "kill"}]})),
                "not an allowed fault target"),
            # fix round 0-M4: the schedule's output-token ceiling, the customer-traffic
            # window, and a tenant key env the profile never declared
            "output ceiling": (argv_for(tmp, manifest, profile_for(
                clips, manifest, **{"bounds.max_output_tokens": 1000})),
                "scheduled output-token ceiling"),
            "fault on customer traffic": (argv_for(tmp, manifest, profile_for(
                clips, manifest, **{"target.allowed_fault_targets": ["infrx-worker"],
                                    "target.customer_traffic": True,
                                    "measurement.fault_schedule": [
                                        {"at_s": 1, "target": "infrx-worker", "action": "kill"}]})),
                "maintenance window"),
            "tenant env": (argv_for(tmp, manifest, good, tenant_keys="E1C_OTHER_KEY"),
                           "not declared in target.tenant_key_env"),
        }
        for why, (args, needle) in cases.items():
            code, v = validate_only(args)
            assert code == 2 and any(needle in e for e in v["errors"]), (why, v["errors"])


def test_the_budget_counts_outstanding_holds_and_a_missing_rate_blocks_only_paid_runs():
    """Oracle: a budget check over settled usage alone (holds ignored) admits a run that
    overdraws; a missing price that blocks local fixtures, or that lets a paid run start."""
    with tempfile.TemporaryDirectory() as tmp:
        clips, manifest = setup(tmp)
        # 4 x 0.0021536 = 0.0086 USD for the schedule; the holds push it over 0.01
        tight = {"bounds.spend.max_usd": 0.01}
        code, v = validate_only(argv_for(tmp, manifest, profile_for(clips, manifest, **tight)))
        assert code == 0, v
        code, v = validate_only(argv_for(tmp, manifest, profile_for(
            clips, manifest, **tight, **{"bounds.spend.outstanding_holds_usd": 0.005})))
        assert code == 2 and any("outstanding holds" in e for e in v["errors"]), v
        unpriced = profile_for(clips, manifest, **{"bounds.spend.rates": None})
        code, v = validate_only(argv_for(tmp, manifest, unpriced))
        assert code == 0 and v["runnable"] and any("no approved rates" in b for b in v["blocks"])
        paid = argv_for(tmp, manifest, unpriced)
        paid.remove("--dry-run-transport")
        paid.remove("fake_gateway:transport")
        code, v = validate_only(paid)
        assert code == 2 and v["valid"] and not v["runnable"], v


def test_no_secret_may_sit_in_a_profile_and_none_is_echoed():
    """Oracle: a profile carrying a key, a bearer or a DSN password validates, or the
    refusal quotes the secret it found."""
    with tempfile.TemporaryDirectory() as tmp:
        clips, manifest = setup(tmp)
        leaky = profile_for(clips, manifest, **{
            "target.environment_owner": f"ops {KEY}",
            "cleanup.restore": "postgresql://infrx:hunter2secret@db.invalid/infrx",
            "identity.engine_options": {"note": "Bearer abcdefghijkl"}})
        code, v = validate_only(argv_for(tmp, manifest, leaky))
        assert code == 2
        flagged = {e.split(":")[0] for e in v["errors"]}
        assert {"$.target.environment_owner", "$.cleanup.restore",
                "$.identity.engine_options.note"} <= flagged, v["errors"]
        blob = json.dumps(v)
        assert KEY not in blob and "hunter2secret" not in blob and "abcdefghijkl" not in blob


def test_a_key_inventory_outside_the_test_keys_refuses_the_run():
    """Oracle (S3 F8 / P-24): a paid window that starts while a pre-cutover consumer key is
    still active measures (and bills) alongside external traffic."""
    with tempfile.TemporaryDirectory() as tmp:
        clips, manifest = setup(tmp)
        p = profile_for(clips, manifest)
        inv = os.path.join(tmp, "keys.json")
        for prefixes, ok in ((["3c3c3c3c"], True), (["3c3c3c3c", "9f9f9f9f"], False)):
            with open(inv, "w", encoding="utf-8") as f:
                json.dump({"active_key_id_prefixes": prefixes, "taken_at": "2026-09-24T22:00Z",
                           "source": "coordinator read-only op"}, f)
            code, v = validate_only(argv_for(tmp, manifest, p, "--key-inventory", inv))
            assert (code == 0) is ok, v
            assert v["blocks"] == [], "an inventory was supplied: nothing is unknown"
            if not ok:
                assert any("9f9f9f9f" in e and "outside target.test_key_ids" in e
                           for e in v["errors"])


def test_p4_enters_through_the_public_edge_and_must_observe_refusals():
    """Oracle (S3 F5): the bda1586 cells had zero 429s and ran behind Caddy's back, so they
    could not show drained-429 delivery. A P4 profile on the direct gateway is refused; a P4
    cell that saw no 429/503 is INVALID; the refusals are reported by target path."""
    with tempfile.TemporaryDirectory() as tmp:
        clips, manifest = setup(tmp)
        direct = profile_for(clips, manifest, **{"measurement.profile_class": "P4"})
        code, v = validate_only(argv_for(tmp, manifest, direct))
        assert code == 2 and any("public edge" in e for e in v["errors"])
        edge = profile_for(clips, manifest, **{"measurement.profile_class": "P4",
                                               "target.path": "public-edge"})
        https = ("--base-url", "https://fake.invalid/v1")          # the edge is TLS, no port
        quiet, _, _, _ = run_bench(argv_for(tmp, manifest, edge, *https), FakeGateway(),
                                   env={"MARLIN_API_KEY": KEY})
        assert quiet["validity"]["verdict"] == "INVALID"
        assert any("P4 observed no 429" in r for r in quiet["validity"]["reasons"])
        loud, _, _, _ = run_bench(argv_for(tmp, manifest, edge, *https),
                                  FakeGateway(statuses={1: 429, 2: 503}, retry_after="0"),
                                  env={"MARLIN_API_KEY": KEY})
        assert loud["validity"]["verdict"] == "VALID", loud["validity"]
        assert loud["measurement"]["overload_by_target_path"] == {"public-edge": {
            "status_429": 1, "status_503": 1, "retry_after_present": 2, "transport_losses": 0,
            "transport_loss_classes": {}}}


def test_a_profiled_run_emits_the_section6_fields_and_labels_its_cost():
    """Oracle: a summary missing a §6 field, a deliberate-invalid clip counted as capacity,
    or a cost printed as a number (or zero) with no price evidence behind it."""
    with tempfile.TemporaryDirectory() as tmp:
        clips, manifest = setup(tmp)
        # clip003 (5 s) is over a 4.5 s cap and declared as the deliberate over-cap control
        p = profile_for(clips, manifest, **{"workload.max_clip_duration_s": 4.5,
                                            "workload.expected_invalid": ["clip003"]})
        summary, raw, _, rc = run_bench(argv_for(tmp, manifest, p), FakeGateway(),
                                        env={"MARLIN_API_KEY": KEY})
        assert rc == 0 and summary["validity"]["verdict"] == "VALID", summary["validity"]
        m = summary["measurement"]
        for field in ("schema", "run_id", "candidate", "corpus_sha256", "profile_sha256", "counts",
                      "error_rate", "accepted_service_success", "actual_rate_per_s",
                      "peak_in_flight", "warmup_count", "driver_lag", "latency_by_class",
                      "successful_clip_seconds", "clip_seconds_per_hour", "slo_goodput",
                      "charges", "infrastructure_cost", "telemetry", "reconciliation",
                      "stop_cleanup", "verdict", "reasons", "target_path",
                      "overload_by_target_path"):
            assert field in m, field
        assert m["corpus_sha256"] == runprofile.file_sha256(manifest)
        assert m["counts"]["deliberate_invalid"] == 1 and m["counts"]["valid_offers"] == 3
        over = [r for r in raw if r["clip_id"] == "clip003"]
        assert m["successful_clip_seconds"] == sum(r["duration_s"] for r in raw) - over[0]["duration_s"]
        assert m["infrastructure_cost"]["status"] == "unavailable"
        assert m["infrastructure_cost"]["usd_per_successful_video_hour"] is None
        assert m["slo_goodput"] == {"status": "not gated: no threshold declared"}
        assert m["charges"]["credit"] is None and m["charges"]["usd"] is None
        assert m["stop_cleanup"]["stopped_by"] == "schedule_end"

    wall, secs = 36.0, 18.0
    est = bench.video_hour_cost({"usd_per_hour": 2.24, "instances": 1, "source": "HANDOFF.md:24",
                                 "as_of": "2026-09-19"}, wall, secs)
    assert est["status"] == "estimated" and est["label"] == "estimated (HANDOFF.md:24, 2026-09-19)"
    assert est["usd_per_successful_video_hour"] == round(2.24 * wall / 3600 / (secs / 3600), 6)
    sourced = bench.video_hour_cost({"usd_per_hour": 1.0, "instances": 2,
                                     "source": "research/cross-cutting/cloud-pricing.md#l40s",
                                     "as_of": "2026-09-01"}, wall, 0.0)
    assert sourced["status"] == "sourced" and sourced["usd_per_successful_video_hour"] is None


def test_the_declared_duration_bound_stops_new_requests_and_invalidates():
    """Oracle: a bound that is only checked before the run, while a slow target lets a
    closed-loop run go on for as long as it takes."""
    with tempfile.TemporaryDirectory() as tmp:
        clips, manifest = setup(tmp)
        p = profile_for(clips, manifest, **{"bounds.max_duration_s": 0.05})
        summary, raw, _, _ = run_bench(argv_for(tmp, manifest, p), FakeGateway(ttft=0.04),
                                       env={"MARLIN_API_KEY": KEY})
        assert len(raw) < 4 and summary["measurement"]["stop_cleanup"]["stopped_by"] == \
            "bounds.max_duration_s"
        assert summary["validity"]["verdict"] == "INVALID" and \
            summary["validity"]["missing_attempts"] == 4 - len(raw)


PROFILES = os.path.join(os.path.dirname(HERE), "profiles")
SMOKE_ARGV = ["--corpus", os.path.join(PROFILES, "smoke-corpus.json"), "--subset", "full",
              "--target", "gateway", "--base-url", "https://marlin2b.callbill.ai/v1",
              "--model", "nemostation/marlin-2b@2026-09-01",
              "--forms", "upload,upload,video_b64,video_b64", "--requests", "4", "-c", "1",
              "--max-tokens", "128", "--retries", "0", "--seed", "20260922",
              "--dataset-version", "e1c-smoke-1", "--label", "e1c-smoke"]


def test_the_hosted_smoke_template_refuses_until_filled_and_then_fits_its_command():
    """Oracle: the committed P0 template validating with its FILL placeholders (a paid run
    starting on an undeclared identity), or the coordinator's exact smoke command drifting
    from the profile it is meant to run under (in-cap clip + declared over-cap control)."""
    import test_bench
    bench.load_corpus = test_bench.REAL_LOAD_CORPUS          # the committed smoke manifest
    with tempfile.TemporaryDirectory() as tmp:
        template = json.load(open(os.path.join(PROFILES, "P0-hosted-smoke.template.json")))
        inv = os.path.join(tmp, "keys.json")
        with open(inv, "w", encoding="utf-8") as f:
            json.dump({"active_key_id_prefixes": ["3c3c3c3c"]}, f)

        def smoke(profile):
            path = os.path.join(tmp, "p.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(profile, f)
            out = io.StringIO()
            old = os.environ.pop("INFRX_API_KEY", None)
            try:
                with contextlib.redirect_stdout(out):
                    code = bench.main(SMOKE_ARGV + ["--out", os.path.join(tmp, "b.jsonl"),
                                                    "--profile", path, "--key-inventory", inv,
                                                    "--validate-only"])
            finally:
                if old is not None:
                    os.environ["INFRX_API_KEY"] = old
            return code, json.loads(out.getvalue())

        code, v = smoke(template)
        assert code == 2 and any("FILL" not in e and "does not match" in e for e in v["errors"])
        filled = json.loads(json.dumps(template).replace(
            '"FILL: 8-12 hex id prefix of the certify/E1B consumer key"', '"3c3c3c3c"'))
        filled["identity"].update(source_sha="c" * 40, image_digest="sha256:" + SHA,
                                  weights_sha256="sha256:" + SHA, tokenizer_sha256="sha256:" + SHA,
                                  processor_sha256="sha256:" + SHA,
                                  template_sha256="sha256:" + SHA, config_version="env-1")
        filled["measurement"]["client"]["location"] = "operator host"
        code, v = smoke(filled)
        assert code == 2 and v["valid"] and v["blocks"] == [
            "outstanding holds undeclared: the budget check needs them"], v
        filled["bounds"]["spend"]["outstanding_holds_usd"] = 0.0
        code, v = smoke(filled)
        assert code == 0 and v["runnable"], v
        assert v["derived"]["scheduled_requests"] == 4 and v["derived"]["target_path"] == "public-edge"
        # fix round: the evidence's unprofiled smoke command parses and would start as written
        # (--unprofiled smoke), and one request more would not; neither sends anything
        for n, want in (("4", 0), ("5", 2)):
            args = SMOKE_ARGV + ["--out", os.path.join(tmp, "b.jsonl"), "--unprofiled", "smoke",
                                 "--validate-only"]
            args[args.index("--requests") + 1] = n
            out, gw = io.StringIO(), FakeGateway()
            with contextlib.redirect_stdout(out), routed_to(gw):
                assert bench.main(args) == want, (n, out.getvalue())
            assert json.loads(out.getvalue())["warnings"] == [bench.UNPROFILED]
            assert not gw.seen and not gw.uploads and gw.home is None


# ------------------------------------------------------------------ E1C fix round


def test_the_drain_bound_cuts_what_is_still_in_flight_in_either_loop():
    """Oracle (fix round 0-B1): a closed-loop run checked the deadline only before taking new
    work, so a request in flight at max_duration_s ran on to --timeout (measured 1.51 s against
    a 0.05 s + 0.1 s profile); an open-loop drain with no timeout waits the same. Past the
    declared duration + drain what is open is cancelled, its rows are missing, the cell is
    INVALID and says which bound stopped it."""
    with tempfile.TemporaryDirectory() as tmp:
        clips, manifest = setup(tmp)
        closed = profile_for(clips, manifest, **{"bounds.max_duration_s": 0.05,
                                                 "bounds.max_drain_s": 0.1})
        opened = profile_for(clips, manifest, **{"bounds.max_duration_s": 1,
                                                 "bounds.max_drain_s": 0.1,
                                                 "measurement.arrival": "open-loop",
                                                 "measurement.rate_per_s": 100.0})
        for mode, p, kw in (("closed", closed, {}), ("open", opened, {"rate": 100.0})):
            summary, raw, _, _ = run_bench(argv_for(tmp, manifest, p, **kw),
                                           FakeGateway(ttft=1.5), env={"MARLIN_API_KEY": KEY})
            assert summary["wall_s"] < 1.0, (mode, summary["wall_s"])
            assert summary["measurement"]["stop_cleanup"]["stopped_by"] == \
                "bounds.max_drain_s", mode
            v = summary["validity"]
            assert v["verdict"] == "INVALID" and v["missing_attempts"] == 4 - len(raw) > 0, \
                (mode, v)


def test_the_duration_bound_holds_at_run_time_when_poisson_arrivals_overshoot_it():
    """Oracle (fix round p05): the preflight checks n/rate against max_duration_s, but Poisson
    arrivals overshoot their mean; without the run-time check in the open loop the late
    arrivals are still sent and the cell reads as a complete schedule."""
    with tempfile.TemporaryDirectory() as tmp:
        clips, manifest = setup(tmp)
        rate, n = 100.0, 4
        seed = next(s for s in range(200) if bench.build_schedule(
            n, clips, ["video_b64"], rate=rate, seed=s)[-1]["arrival_s"] > 2 * n / rate)
        p = profile_for(clips, manifest, **{"bounds.max_duration_s": n / rate,
                                            "workload.seed": seed,
                                            "measurement.arrival": "open-loop",
                                            "measurement.rate_per_s": rate})
        summary, raw, _, _ = run_bench(argv_for(tmp, manifest, p, rate=rate, seed=seed),
                                       FakeGateway(), env={"MARLIN_API_KEY": KEY})
        assert len(raw) < n and summary["measurement"]["stop_cleanup"]["stopped_by"] == \
            "bounds.max_duration_s", (len(raw), summary["measurement"]["stop_cleanup"])
        assert summary["validity"]["verdict"] == "INVALID"


def test_the_profile_identity_and_lag_bound_are_applied_to_the_run():
    """Oracle (fix round 0-M3): a profiled run that falls back to the default 1 s lag bound and
    to no identity check at all - the profile's model_revision and max_driver_lag_s declared
    but never applied."""
    with tempfile.TemporaryDirectory() as tmp:
        clips, manifest = setup(tmp)
        other = profile_for(clips, manifest, **{"identity.model_revision": "marlin2b@other"})
        summary, raw, _, _ = run_bench(argv_for(tmp, manifest, other), FakeGateway(),
                                       env={"MARLIN_API_KEY": KEY})
        assert {r["served_model"] for r in raw} == {"marlin2b"}       # the fake echoes --model
        assert any(r.startswith("identity mismatch") for r in summary["validity"]["reasons"]), \
            summary["validity"]
        tight = profile_for(clips, manifest, **{"measurement.arrival": "open-loop",
                                                "measurement.rate_per_s": 100.0,
                                                "measurement.max_driver_lag_s": 1e-9})
        summary, raw, _, _ = run_bench(argv_for(tmp, manifest, tight, rate=100.0),
                                       FakeGateway(), env={"MARLIN_API_KEY": KEY})
        v = summary["validity"]
        assert v["driver_lag_bound_s"] == 1e-9 and v["driver_lag_max_s"] > 1e-9, v
        assert any("driver lag" in r for r in v["reasons"]), v


def test_the_declared_target_path_must_be_the_path_the_run_takes():
    """Oracle (fix round 2-E1C-ACC-03): target.path was declaration-only - a P4 cell declared
    public-edge while it went to the gateway port behind Caddy's back (S3 F5), or a
    direct-engine profile ran through the gateway. Also the enum itself (p02)."""
    with tempfile.TemporaryDirectory() as tmp:
        clips, manifest = setup(tmp)
        edge = lambda **o: profile_for(clips, manifest, **{"target.path": "public-edge", **o})
        cases = {
            "enum": (argv_for(tmp, manifest, profile_for(
                clips, manifest, **{"target.path": "direct-db"})), "must be one of"),
            "edge on the gateway port": (argv_for(
                tmp, manifest, edge(**{"measurement.profile_class": "P4",
                                       "target.allowlist": ["127.0.0.1:8001"]}),
                "--base-url", "http://127.0.0.1:8001/v1"), "public-edge"),
            "edge over plain http": (argv_for(tmp, manifest, edge()), "public-edge"),
            "edge on an explicit port": (argv_for(
                tmp, manifest, edge(**{"target.allowlist": ["fake.invalid:8443"]}),
                "--base-url", "https://fake.invalid:8443/v1"), "public-edge"),
            "engine through the gateway": (argv_for(tmp, manifest, profile_for(
                clips, manifest, **{"target.path": "direct-engine"})), "--target direct"),
            "gateway declared, engine run": (argv_for(tmp, manifest, profile_for(
                clips, manifest), "--target", "direct"), "--target gateway"),
        }
        for why, (args, needle) in cases.items():
            code, v = validate_only(args)
            assert code == 2 and any(needle in e for e in v["errors"]), (why, v["errors"])
        code, v = validate_only(argv_for(tmp, manifest, edge(), "--base-url",
                                         "https://fake.invalid/v1"))
        assert code == 0 and v["derived"]["target_path"] == "public-edge", v


def test_an_unprofiled_run_against_a_paid_target_refuses_to_start():
    """Oracle (fix round 0-B2 / 2-E1C-ACC-01, tasks.json E1C "no paid run starts unbounded"):
    a run with no --profile against a non-local target dispatched its requests and was only
    labelled INVALID afterwards. It now exits 2 before any request, unless the explicit,
    logged opt-out is given - `smoke` capped at 4 requests of <= 512 tokens, `certify` for
    certify's runner until E2C passes it profiles."""
    paid = ["--base-url", "https://paid.invalid/v1"]
    with tempfile.TemporaryDirectory() as tmp:
        clips, manifest = setup(tmp)

        def go(*extra, **kw):
            gw = FakeGateway()
            args = argv_for(tmp, manifest, {}, **kw)
            args = args[:args.index("--profile")] + list(extra)     # no profile at all
            args.remove("--dry-run-transport")
            args.remove("fake_gateway:transport")
            with routed_to(gw), contextlib.redirect_stderr(io.StringIO()) as err:
                try:
                    summary, raw, _, rc = run_bench(args, gw, env={"MARLIN_API_KEY": KEY})
                except ValueError:                           # nothing printed: refused
                    summary, raw, rc = None, None, None
            return gw, summary, raw, err.getvalue()

        gw, summary, _, err = go(*paid)
        assert summary is None and not gw.seen and not gw.uploads and gw.home is None
        assert "refusing to start" in err and "--profile" in err
        for extra, kw in ((("--unprofiled", "smoke"), {"requests": 5}),
                          (("--unprofiled", "smoke"), {"max_tokens": 1024})):
            gw, summary, _, err = go(*paid, *extra, **kw)
            assert summary is None and not gw.seen, (extra, kw)
        for kind in ("smoke", "certify"):
            gw, summary, raw, err = go(*paid, "--unprofiled", kind)
            assert len(gw.seen) == 4 and len(raw) == 4, kind
            assert summary["unprofiled_opt_out"] == kind and f"--unprofiled {kind}" in err
            assert summary["validity"]["verdict"] == "INVALID"
            assert bench.UNPROFILED in summary["validity"]["reasons"]
        # a local target needs no opt-out (the fake, loopback): its runs are free
        gw, summary, raw, _ = go("--base-url", "http://127.0.0.1:9/v1")
        assert len(raw) == 4 and summary["unprofiled_opt_out"] is None


@contextlib.contextmanager
def routed_to(gw):
    """Every httpx client bench opens goes to `gw`, whatever host it names: the run believes
    it is talking to a paid endpoint, and the fake counts what actually left."""
    import httpx
    real = httpx.AsyncClient

    class Routed(real):
        def __init__(self, *args, **kw):
            super().__init__(*args, **{**kw, "transport": gw.transport()})
    httpx.AsyncClient = Routed
    try:
        yield
    finally:
        httpx.AsyncClient = real
