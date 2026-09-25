#!/usr/bin/env python3
"""The run profile a resource-consuming bench.py run must declare (consumer-v1/05 §1).

    python models/marlin2b/bench.py --profile p.json --validate-only <the run's own flags>

`profiles/run-profile.v1.schema.json` is the versioned schema; this module interprets the
subset of JSON Schema it uses (no dependency) and adds what a schema cannot say: the
profile must agree with the run it is attached to (target host allowlisted, identity of the
workload by manifest hash and item ids, every flag within its bounds), the projected spend
of the whole schedule PLUS the outstanding holds must fit the budget, and no string in the
profile may look like a secret. Nothing here opens a connection or starts a request.

Verdict: `errors` refuse the run outright; `blocks` (no approved rates, budget or declared
holds) refuse a PAID run but not a local/fake one - validation and local fixtures never
need a price (§1: "A missing rate/budget blocks paid testing, not profile validation").
"""
import json, os, re, urllib.parse
from decimal import Decimal
from hashlib import sha256

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "profiles", "run-profile.v1.schema.json")
SCHEMA_ID = "infrx.run-profile/1"
TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
         "boolean": bool, "null": type(None)}
# A value shaped like a credential. Reported by PATH only - the value is never echoed.
SECRET_SHAPE = re.compile(r"(?i)(\bsk[-_][a-z0-9]|\bbearer\s|://[^/\s:@]+:[^/\s@]+@|"
                          r"password\s*[=:]|private key|\bAKIA[0-9A-Z]{12})")
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
# P-24 amendment: unit-neutral spend keys (the unit is bounds.spend.currency) -> the legacy
# USD-only names, still read as USD so committed profiles keep validating.
SPEND_KEYS = {"max_spend": "max_usd", "outstanding_holds": "outstanding_holds_usd"}
RATE_KEYS = {"input_per_mtok": "input_usd_per_mtok", "output_per_mtok": "output_usd_per_mtok"}


def load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def schema_errors(value, schema, path="$"):
    """The JSON Schema subset the v1 schema uses; every violation, each with its path."""
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        if not any(isinstance(value, TYPES[t]) and not (t in ("integer", "number")
                                                        and isinstance(value, bool))
                   for t in types):
            return [f"{path}: expected {'|'.join(types)}"]
    out = []
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: must be one of {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append(f"{path}: empty")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: below {schema['minimum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            out.append(f"{path}: must exceed {schema['exclusiveMinimum']}")
    if isinstance(value, dict):
        out += [f"{path}.{k}: required" for k in schema.get("required", ()) if k not in value]
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            out += [f"{path}.{k}: not allowed" for k in value if k not in props]
        for k, sub in props.items():
            if k in value:
                out += schema_errors(value[k], sub, f"{path}.{k}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "items" in schema:
            for i, item in enumerate(value):
                out += schema_errors(item, schema["items"], f"{path}[{i}]")
    return out


def secret_paths(value, keys, carries_key, path="$"):
    """Paths whose key or string value looks like a credential or carries the API key."""
    if isinstance(value, dict):
        return [p for k, v in value.items()
                for p in ([f"{path}.<key>"] if SECRET_SHAPE.search(str(k)) else [])
                + secret_paths(v, keys, carries_key, f"{path}.{k}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in secret_paths(v, keys, carries_key,
                                                                    f"{path}[{i}]")]
    if isinstance(value, str) and (SECRET_SHAPE.search(value) or carries_key(value, keys)):
        return [path]
    return []


def file_sha256(path):
    h = sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def key_inventory_errors(inventory, allowed):
    """S3 F8 / P-24: every key active on the target must be one of the run's test keys.
    `inventory` is the coordinator's sanitized read: {"active_key_id_prefixes": [...],
    "taken_at": ..., "source": ...} - id prefixes only, never key material."""
    prefixes = inventory.get("active_key_id_prefixes") if isinstance(inventory, dict) else None
    if not isinstance(prefixes, list) or not all(isinstance(p, str) and p for p in prefixes):
        return ["key inventory: needs active_key_id_prefixes, a list of id prefixes"]
    match = lambda p, a: p[:min(len(p), len(a))] == a[:min(len(p), len(a))]
    foreign = [p for p in prefixes if not any(match(p, a) for a in allowed)]
    return [f"key inventory: {len(foreign)} active key(s) outside target.test_key_ids: "
            f"{sorted(foreign)} - external consumer traffic may exist"] if foreign else []


def validate(profile, a, schedule, *, keys=(), carries_key=lambda v, k: False, local=False,
             key_env=("MARLIN_API_KEY", "INFRX_API_KEY"), inventory=None):
    """The profile against the run `a` (bench's parsed flags) and its declared `schedule`."""
    errors = schema_errors(profile, load_schema())
    res = {"schema": SCHEMA_ID, "errors": errors, "blocks": [], "warnings": [], "derived": {}}
    if errors:                       # the cross-field checks below read what the schema holds
        return finish(res, local)
    errors += [f"{p}: looks like a secret; reference it by id or env var name"
               for p in secret_paths(profile, keys, carries_key)]
    ident, target, bounds = profile["identity"], profile["target"], profile["bounds"]
    w, m = profile["workload"], profile["measurement"]

    url = urllib.parse.urlsplit(a.base_url)
    host = (url.hostname or "").lower()
    if host not in target["allowlist"] and f"{host}:{url.port}" not in target["allowlist"]:
        errors.append(f"target: {host} is not on target.allowlist")
    used = a.tenant_env or ([n for n in key_env if os.environ.get(n, "").strip()][:1]
                            or [key_env[-1]])
    if not set(used) <= set(target["tenant_key_env"]):
        errors.append(f"target: key env {used} not declared in target.tenant_key_env")
    if w["tenants"] != len(used):
        errors.append(f"workload.tenants is {w['tenants']}, the run uses {len(used)}")
    for fault in m["fault_schedule"]:
        if fault["target"] not in target["allowed_fault_targets"]:
            errors.append(f"measurement.fault_schedule: {fault['target']} is not an allowed "
                          f"fault target")
    if m["fault_schedule"] and target["customer_traffic"] and not target["maintenance_window"]:
        errors.append("faults on a target with customer traffic need a maintenance window")
    if inventory is None:
        res["blocks"].append("no --key-inventory: the target's active keys are unknown (P-24)")
    else:
        errors += key_inventory_errors(inventory, target["test_key_ids"])
        errors += [f"key inventory{p[1:]}: looks like a secret"
                   for p in secret_paths(inventory, keys, carries_key)]
    if m["profile_class"] == "P4" and target["path"] != "public-edge":
        errors.append("P4 overload must enter through the public edge (S3 F5): a direct "
                      "gateway cell cannot show drained-429 delivery through Caddy")
    # target.path is checked against the run, not taken on trust: the engine is bench's
    # --target direct, both gateway hops are --target gateway, and the public edge is TLS on
    # its default port of a non-loopback host (the gateway port behind Caddy is not the edge).
    wants = "direct" if target["path"] == "direct-engine" else "gateway"
    if a.target != wants:
        errors.append(f"target.path {target['path']} needs --target {wants}, the run uses "
                      f"--target {a.target}")
    if target["path"] == "public-edge" and (url.scheme != "https" or host in LOOPBACK
                                            or url.port not in (None, 443)):
        errors.append("target.path public-edge needs an https base URL on a non-loopback host "
                      "with no explicit port: this base URL bypasses the edge (S3 F5)")

    source = a.corpus or a.video
    if not source or not os.path.isfile(source):
        errors.append("workload: the manifest/video to hash is not a local file")
    elif file_sha256(source) != w["manifest_sha256"]:
        errors.append("workload.manifest_sha256 does not match the run's manifest")
    # a run with no seed (dataset.py: the manifest order is the schedule) pins none
    for name, got in (("dataset_version", a.dataset_version),
                      ("seed", w["seed"] if a.seed is None else a.seed),
                      ("forms", [f.strip() for f in a.forms.split(",") if f.strip()]),
                      ("max_tokens_mix", list(a.max_tokens_mix))):
        if w[name] != got:
            errors.append(f"workload.{name} is {w[name]!r}, the run uses {got!r}")
    ids = {item["clip_id"] for item in schedule if item["clip_id"]}
    if ids - set(w["item_ids"]):
        errors.append(f"workload: {len(ids - set(w['item_ids']))} scheduled item(s) are not "
                      f"in workload.item_ids")
    over = {item["clip_id"] for item in schedule if (item["duration_s"] or 0) >
            w["max_clip_duration_s"] and item["form"] != "text"}
    if over - set(w["expected_invalid"]):
        errors.append(f"workload: {len(over - set(w['expected_invalid']))} over-cap item(s) "
                      f"are not declared in workload.expected_invalid")

    arrival = "open-loop" if a.rate else "closed-loop"
    if m["arrival"] != arrival:
        errors.append(f"measurement.arrival is {m['arrival']}, the run is {arrival}")
    elif a.rate and m["rate_per_s"] != a.rate:
        errors.append(f"measurement.rate_per_s is {m['rate_per_s']}, the run uses {a.rate}")
    elif not a.rate and m["concurrency"] != a.concurrency:
        errors.append(f"measurement.concurrency is {m['concurrency']}, the run uses "
                      f"{a.concurrency}")

    n = len(schedule)
    out_tokens = sum(item["max_tokens"] for item in schedule)
    media = [item for item in schedule if item["form"] != "text" and item["clip_id"]]
    sizes = [(item.get("clip") or {}).get("bytes") for item in media]
    if a.video and not a.corpus and os.path.isfile(a.video):
        sizes = [os.path.getsize(a.video)] * len(media)
    checks = (("max_requests", n, "scheduled requests"),
              ("max_output_tokens_per_request", max(a.max_tokens_mix, default=0), "--max-tokens"),
              ("max_output_tokens", out_tokens, "scheduled output-token ceiling"))
    for bound, value, what in checks:
        if value > bounds[bound]:
            errors.append(f"bounds.{bound} {bounds[bound]} < {what} {value}")
    if not a.rate and a.concurrency > bounds["max_concurrency"]:
        errors.append(f"bounds.max_concurrency {bounds['max_concurrency']} < -c {a.concurrency}")
    if a.rate and n / a.rate > bounds["max_duration_s"]:
        errors.append(f"bounds.max_duration_s {bounds['max_duration_s']} < the schedule's "
                      f"{n / a.rate:.0f} s")
    if None in sizes:
        errors.append("workload: input bytes unknown for a scheduled media item")
    elif sum(sizes) > bounds["max_input_bytes"]:
        errors.append(f"bounds.max_input_bytes {bounds['max_input_bytes']} < {sum(sizes)}")

    spend = spend_projection(bounds, n)
    errors += spend["errors"]
    res["blocks"] += spend["blocks"]
    res["warnings"] += spend["warnings"]
    res["derived"]["spend_currency"] = spend["currency"]
    if spend["projected"] is not None:
        res["derived"]["projected_spend"] = str(spend["projected"])
    res["derived"].update(scheduled_requests=n, output_token_ceiling=out_tokens,
                          media_bytes=None if None in sizes else sum(sizes),
                          expect_model=ident["model_revision"], target_path=target["path"],
                          profile_class=m["profile_class"],
                          max_driver_lag_s=m["max_driver_lag_s"],
                          profile_sha256=sha256(json.dumps(profile, sort_keys=True)
                                                .encode()).hexdigest())
    return finish(res, local)


def exact(v):
    """A JSON number as an exact Decimal: str() of the parsed float is the literal written."""
    # ponytail: a literal with more than 15 significant digits is rounded by json first;
    # parse with parse_float=Decimal if a profile ever needs one.
    return None if v is None else Decimal(str(v))


def spend_projection(bounds, n):
    """The schedule's spend ceiling plus outstanding holds against the cap, in
    bounds.spend.currency, in exact decimals. USD and CREDIT are never converted: the legacy
    *_usd keys are read as USD and only in a USD profile, and never mixed with the new keys."""
    spend, cur = bounds["spend"], bounds["spend"]["currency"]
    out = {"currency": cur, "projected": None, "errors": [], "blocks": [], "warnings": []}
    groups = [(spend, SPEND_KEYS, "bounds.spend")]
    if spend["rates"] is not None:
        groups.append((spend["rates"], RATE_KEYS, "bounds.spend.rates"))
    legacy = [f"{p}.{old}" for node, keys, p in groups for old in keys.values() if old in node]
    if legacy and any(new in node for node, keys, _ in groups for new in keys):
        out["errors"].append(f"spend: {legacy} mix the legacy *_usd keys with the unit-neutral "
                             f"ones; one profile states one unit")
        return out
    if legacy and cur != "USD":
        out["errors"].append(f"spend: {legacy} are USD amounts but bounds.spend.currency is "
                             f"{cur}; units are never converted")
        return out
    if legacy:
        out["warnings"].append("bounds.spend: the *_usd keys are deprecated (read as USD); "
                               "write max_spend/outstanding_holds/input_per_mtok/"
                               "output_per_mtok with currency USD")
    name = lambda new, old: old if legacy else new
    missing = [f"{p}.{new}: required" for node, keys, p in groups for new, old in keys.items()
               if name(new, old) not in node]
    if missing:
        out["errors"] += missing
        return out
    v = {new: exact(node[name(new, old)]) for node, keys, _ in groups
         for new, old in keys.items()}
    if spend["rates"] is None or v["max_spend"] is None:
        out["blocks"].append("no approved rates or spend budget: a paid run is refused")
    if v["outstanding_holds"] is None:
        out["blocks"].append("outstanding holds undeclared: the budget check needs them")
    if spend["rates"] is not None and v["max_spend"] is not None:
        per_request = (bounds["max_input_tokens_per_request"] * v["input_per_mtok"]
                       + bounds["max_output_tokens_per_request"] * v["output_per_mtok"]) / 10 ** 6
        out["projected"] = n * per_request + (v["outstanding_holds"] or 0)
        if out["projected"] > v["max_spend"]:
            out["errors"].append(f"spend: the schedule's ceiling plus outstanding holds "
                                 f"({out['projected']:.4f} {cur}) exceeds bounds.spend.max_spend "
                                 f"{v['max_spend']} {cur}")
    return out


def finish(res, local):
    res["valid"] = not res["errors"]
    res["runnable"] = res["valid"] and (local or not res["blocks"])
    if local and res["blocks"]:
        res["warnings"].append("blocks do not apply to a local or fake target")
    return res


def is_local(a):
    """A target that costs nothing: an injected transport, or loopback."""
    return bool(a.dry_run_transport) or (urllib.parse.urlsplit(a.base_url).hostname or "") \
        in LOOPBACK
