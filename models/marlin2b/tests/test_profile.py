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
        quiet, _, _, _ = run_bench(argv_for(tmp, manifest, edge), FakeGateway(),
                                   env={"MARLIN_API_KEY": KEY})
        assert quiet["validity"]["verdict"] == "INVALID"
        assert any("P4 observed no 429" in r for r in quiet["validity"]["reasons"])
        loud, _, _, _ = run_bench(argv_for(tmp, manifest, edge),
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
