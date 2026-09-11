#!/usr/bin/env python3
"""mncs-service test harness: static checks + MNCS experiment runs.

Usage:
    python3 tools/svc_test.py [--suite static|unit|integration|all]
                              [--backend research|wasm|both]
                              [--mncs-bin PATH] [--lang-lib DIR]

The harness drives the MNCS compiler binary (read-only; the binary is
never built here) over the checked-in corpora in tests/corpora/:

- static: module/use/corpus cross-references, JSON validity, reserved
  identifier traps. Needs no compiler binary.
- unit: one corpus per src/svc module on each selected backend. Every
  case must execute (status ``returned``) and every toolchain-checked
  ``expected`` value must be met (``expectation_met``). The top-level
  PASS verdict is intentionally NOT the gate: current MNCS retains
  ambient machine-intent obligations (CMP301) that hold the verdict at
  UNKNOWN even when every expectation is met (see SVC-P-013). Pure
  corpora must additionally agree byte-for-byte across backends.
- integration: example programs, including effectful steps driven with
  explicit capability grants. Ledger bytes are decoded by an
  independent Python framing oracle (not by the MNCS codec under
  test), so malformed output cannot hide behind the implementation.

Exit status is nonzero on any failure. Run artifacts land in
target/svc-test/ (git-ignored).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src")
CORPORA = os.path.join(REPO, "tests", "corpora")
TARGET = os.path.join(REPO, "target", "svc-test")

BACKENDS = {
    "research": "mncs-research-bytecode",
    "wasm": "mncs-portable-wasm-mvp",
}

# ---------------------------------------------------------------------------
# Small MNCS value helpers (mirror the corpus encodings).

def I64(value):
    return {"integer": {"value": value, "type": {"bits": 64, "signed": True}}}


def U64(value):
    return {"integer": {"value": value, "type": {"bits": 64, "signed": False}}}


def byte_seq(values):
    return {"sequence": {"values": [{"byte": {"value": v}} for v in values]}}


# ---------------------------------------------------------------------------
# Independent framing oracle (Python reimplementation of svc.framing).

MAGIC = 165
VERSION = 1


def frame(op, payload):
    assert len(payload) <= 56
    body = bytes([MAGIC, VERSION, op & 0xFF, len(payload)]) + bytes(payload)
    return body + bytes([sum(body) % 256])


def parse_frame(blob):
    assert len(blob) >= 5, f"truncated: {len(blob)}"
    assert blob[0] == MAGIC, f"bad magic: {blob[0]}"
    assert blob[1] == VERSION, f"bad version: {blob[1]}"
    op, ln = blob[2], blob[3]
    assert ln <= 56, f"oversized: {ln}"
    assert len(blob) == ln + 5, f"length mismatch: {len(blob)} != {ln + 5}"
    assert blob[ln + 4] == sum(blob[: ln + 4]) % 256, "bad checksum"
    return op, bytes(blob[4: ln + 4])


# ---------------------------------------------------------------------------
# Harness context.

class Ctx:
    def __init__(self, args):
        self.failures = []
        self.passes = 0
        backend_sel = args.backend
        names = ["research", "wasm"] if backend_sel == "both" else [backend_sel]
        self.backends = [(n, BACKENDS[n]) for n in names]
        self.mncs_bin = (
            args.mncs_bin
            or os.environ.get("MNCS_BIN")
            or os.path.join(REPO, "..", "mncs-language", "target", "debug", "mncs")
        )
        self.lang_lib = (
            args.lang_lib
            or os.environ.get("MNCS_LIBRARY_PATH", "").split(":")[-1]
            or os.path.join(REPO, "..", "mncs-language", "library")
        )
        self.env = dict(os.environ)
        self.env["MNCS_LIBRARY_PATH"] = SRC + ":" + self.lang_lib
        os.makedirs(TARGET, exist_ok=True)
        # The compiler binary belongs to another repository and may be
        # rebuilt at any time (this exact failure was observed
        # mid-run). Record its identity and refuse to mix binaries
        # within one suite invocation.
        self.bin_stat = self._bin_stat()
        if self.bin_stat is None:
            print(f"WARNING: cannot stat MNCS binary at {self.mncs_bin}")

    @staticmethod
    def _bin_stat_static(path):
        try:
            st = os.stat(path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _bin_stat(self):
        return self._bin_stat_static(self.mncs_bin)

    def check_binary_stable(self):
        now = self._bin_stat()
        if self.bin_stat is not None and now != self.bin_stat:
            return False
        return True

    def check(self, name, cond, detail=""):
        if cond:
            self.passes += 1
            print(f"  ok   {name}")
        else:
            self.failures.append(name)
            print(f"  FAIL {name} {detail}")

    def need_binary(self):
        if os.path.isfile(self.mncs_bin) and os.access(self.mncs_bin, os.X_OK):
            return True
        print(f"SKIP dynamic suites: no executable MNCS binary at {self.mncs_bin}")
        print("     set --mncs-bin or MNCS_BIN to a built mncs-language binary")
        return False

    def run_experiment(self, source, corpus, tag, grants=()):
        if not self.check_binary_stable():
            return None, "MNCS binary rebuilt mid-run; aborting to avoid mixing compilers"
        out_path = os.path.join(TARGET, tag + ".json")
        out_dir = os.path.join(TARGET, tag + ".d")
        os.makedirs(out_dir, exist_ok=True)
        cmd = [self.mncs_bin, "experiment", "run", source,
               "--backend", self.backend, "--corpus", corpus,
               "--output-dir", out_dir, *grants]
        # The binary couples its exit code to the verdict (nonzero on
        # FAIL), so stdout is parsed regardless of return code; only
        # unparseable output is a harness-level failure.
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              env=self.env, cwd=REPO, timeout=600)
        if not proc.stdout.strip():
            return None, f"exit={proc.returncode} stderr={proc.stderr[:300]}"
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return None, f"unparseable stdout: {exc} exit={proc.returncode}"
        with open(out_path, "w") as fh:
            json.dump(result, fh, indent=1)
        return result, ""


# ---------------------------------------------------------------------------
# Static suite.

RESERVED_BINDINGS = ("fail", "next", "over")


def static_suite(ctx):
    print("== static ==")
    modules = sorted(f for f in os.listdir(os.path.join(SRC, "svc"))
                     if f.endswith(".mncs"))
    ctx.check("svc modules present", len(modules) >= 12, f"found {len(modules)}")
    sources = {}
    for mod in modules:
        path = os.path.join(SRC, "svc", mod)
        text = open(path).read()
        stem = mod[:-len(".mncs")]
        sources[f"svc.{stem}"] = (path, text)
        m = re.search(r"^module\s+([\w.]+)\s*;", text, re.M)
        ctx.check(f"module decl {mod}", m and m.group(1) == f"svc.{stem}")
    for ex in sorted(os.listdir(os.path.join(REPO, "examples"))):
        if not ex.endswith(".mncs"):
            continue
        text = open(os.path.join(REPO, "examples", ex)).read()
        m = re.search(r"^module\s+([\w.]+)\s*;", text, re.M)
        ctx.check(f"example decl {ex}", m and m.group(1) == f"examples.{ex[:-5]}")
        sources[m.group(1)] = (os.path.join(REPO, "examples", ex), text)
    probedir = os.path.join(REPO, "tests", "probes")
    if os.path.isdir(probedir):
        for pr in sorted(os.listdir(probedir)):
            if not pr.endswith(".mncs"):
                continue
            text = open(os.path.join(probedir, pr)).read()
            m = re.search(r"^module\s+([\w.]+)\s*;", text, re.M)
            ctx.check(f"probe decl {pr}", m and m.group(1) == f"probe.{pr[:-5]}")
            if m:
                sources[m.group(1)] = (os.path.join(probedir, pr), text)
    # use-resolution
    for name, (path, text) in sources.items():
        for used in re.findall(r"^use\s+([\w.]+)", text, re.M):
            if used.startswith("mncs."):
                continue
            dotted = used.replace(".", "/") + ".mncs"
            ok = (os.path.isfile(os.path.join(SRC, dotted))
                  or os.path.isfile(os.path.join(REPO, dotted)))
            ctx.check(f"use {used} in {name}", ok, f"unresolved from {path}")
    # reserved identifier traps
    for name, (path, text) in sources.items():
        for word in RESERVED_BINDINGS:
            hit = re.search(rf"^\s*(?:fn|let)\s+{word}\b", text, re.M)
            ctx.check(f"no reserved binding `{word}` in {name}", not hit)
    # corpora cross-references
    for corp in sorted(os.listdir(CORPORA)):
        if not corp.endswith(".json"):
            continue
        path = os.path.join(CORPORA, corp)
        try:
            doc = json.load(open(path))
        except (json.JSONDecodeError, ValueError) as exc:
            ctx.check(f"corpus parses {corp}", False, str(exc)[:120])
            continue
        ctx.check(f"corpus parses {corp}", True)
        ids = [c.get("id") for c in doc.get("cases", [])]
        ctx.check(f"corpus case ids unique {corp}", len(ids) == len(set(ids)))
        for c in doc.get("cases", []):
            tgt = c["request"]["target"]
            dotted = tgt["module"].replace(".", "/") + ".mncs"
            if tgt["module"].startswith("svc."):
                mod_path = os.path.join(SRC, dotted)
            elif tgt["module"].startswith("probe."):
                tail = tgt["module"].split(".")[-1] + ".mncs"
                mod_path = os.path.join(REPO, "tests", "probes", tail)
            else:
                mod_path = os.path.join(REPO, dotted)
            exists = os.path.isfile(mod_path)
            ctx.check(f"corpus {corp}:{c['id']} module", exists, dotted)
            if exists:
                body = open(mod_path).read()
                fn = tgt["function"]
                ctx.check(f"corpus {corp}:{c['id']} function",
                          re.search(rf"^fn\s+{re.escape(fn)}\s*\(", body, re.M) is not None)
            ctx.check(f"corpus {corp}:{c['id']} budget",
                      1 <= c["request"].get("step_budget", 0) <= 1_000_000)


# ---------------------------------------------------------------------------
# Unit suite.

UNIT = ["lifecycle", "errors", "retry", "time", "queue", "worker",
        "framing", "config", "observe", "contract", "shutdown", "service"]

# Backend-differential pins (SVC-P-021), observed under the compiler
# binary recorded in the suite header (see PINNED_BINARY_SHA below).
# The wasm backend is unsound for service-shaped code in that build:
# research returns the source-semantic value while wasm returns wrong
# values (-1 assertion trips, one 222222 pack corruption) or traps
# out-of-bounds. Keys are "corpus-stem:case-id"; values pin the wasm
# observation as {"status": ..., "value": ...} ("value" omitted when
# the pinned outcome is a trap). Research always gates semantics; a
# wasm run that stops matching its pin (fixed OR newly broken) fails
# loudly so pins are revisited, never silently stale. A rebuild of the
# compiler can shift the divergence set — that is itself SVC-P-021
# evidence, not suite rot: re-run, re-observe, re-pin, record the hash.
PINNED_BINARY_SHA = "0ff46ac8"
WASM_KNOWN_DIVERGENT = {
    "lifecycle:reject": {"status": "returned", "value": -1},
    "queue:shed": {"status": "returned", "value": -1},
    "worker:flow": {"status": "returned", "value": -1},
    "worker:reap": {"status": "returned", "value": -1},
    "observe:stream": {"status": "returned", "value": 222222},
    "service:admit": {"status": "runtime_failure"},
    "service:complete": {"status": "returned", "value": -1},
    "ex_minimal_service:run": {"status": "returned", "value": -1},
    "ex_worker_service:run": {"status": "returned", "value": -1},
    "ex_graceful_shutdown:run": {"status": "returned", "value": -1},
}


def assert_cases(ctx, result, corpus_name, backend_name, pins=None):
    pins = pins or {}
    cases = result.get("cases", [])
    for c in cases:
        label = f"{corpus_name}:{c.get('case_id')}@{backend_name}"
        pin = pins.get(f"{corpus_name}:{c.get('case_id')}")
        if pin is not None:
            if "expected" in c and c.get("expectation_met") is True:
                ctx.check(f"{label} divergence resolved — remove pin", False,
                          f"wasm now agrees (SVC-P-021 revisit): {json.dumps(c.get('returned'))[:120]}")
                continue
            vals = c.get("returned") or []
            scalar = (vals[0]["integer"]["value"] if len(vals) == 1 and "integer" in vals[0] else None)
            want_value = pin.get("value", scalar)
            matches = c.get("status") == pin["status"] and scalar == want_value
            ctx.check(f"{label} divergence pinned (SVC-P-021)", matches,
                      f"status={c.get('status')} returned={json.dumps(c.get('returned'))[:160]} "
                      f"reason={json.dumps(c.get('failure_reason'))[:120]} pin={pin}")
            continue
        ctx.check(f"{label} returned", c.get("status") == "returned",
                  f"status={c.get('status')} reason={json.dumps(c.get('failure_reason'))[:160]}")
        if "expected" in c:
            ctx.check(f"{label} expectation met",
                      c.get("expectation_met") is True,
                      f"returned={json.dumps(c.get('returned'))[:160]}")
    return {c.get("case_id"): c.get("returned") for c in cases}


def unit_suite(ctx):
    print("== unit ==")
    if not ctx.need_binary():
        return
    by_backend = {}
    # (source-file, corpus-stem) pairs: library modules plus the
    # backend-differential probe (tests/probes/).
    targets = [(os.path.join(SRC, "svc", mod + ".mncs"), mod) for mod in UNIT]
    targets.append((os.path.join(REPO, "tests", "probes", "wasm_match_records.mncs"),
                    "probe_wasm_match_records"))
    for name, backend in ctx.backends:
        ctx.backend = backend
        print(f"-- backend {name} --")
        for source, stem in targets:
            corpus = os.path.join(CORPORA, stem + ".json")
            pins = WASM_KNOWN_DIVERGENT if name == "wasm" else {}
            result, err = ctx.run_experiment(source, corpus, f"unit-{stem}-{name}")
            if result is None:
                ctx.check(f"unit {stem}@{name} runs", False, err)
                continue
            by_backend.setdefault(stem, {})[name] = assert_cases(ctx, result, stem, name, pins)
    if len(ctx.backends) == 2:
        print("-- cross-backend agreement --")
        for mod, per in by_backend.items():
            if any(k.split(":")[0] == mod for k in WASM_KNOWN_DIVERGENT):
                print(f"  skip unit {mod} research==wasm (pinned divergence, SVC-P-021)")
                continue
            names = list(per)
            if len(names) == 2:
                same = json.dumps(per[names[0]], sort_keys=True) == json.dumps(per[names[1]], sort_keys=True)
                ctx.check(f"unit {mod} research==wasm", same)


# ---------------------------------------------------------------------------
# Integration suite.

PURE_EXAMPLES = [
    ("minimal_service", "run", [], I64(200001)),
    ("worker_service", "run", [], I64(200002)),
    ("backpressure_demo", "run", [], I64(200003)),
    ("graceful_shutdown", "run", [], I64(200004)),
    ("retry_client", "run", [], I64(200005)),
    ("contract_demo", "run", [], I64(200006)),
]


def write_corpus(name, cases):
    # Example corpora are generated, not hand-edited; rewrite only on
    # content change so runs don't churn mtimes.
    path = os.path.join(CORPORA, name + ".json")
    doc = {"schema_version": "0.1", "name": name, "cases": cases}
    text = json.dumps(doc, indent=1) + "\n"
    try:
        with open(path) as fh:
            if fh.read() == text:
                return path
    except OSError:
        pass
    with open(path, "w") as fh:
        fh.write(text)
    return path


def mk_case(cid, module, fn, args=(), expected=(), budget=65536):
    c = {"id": cid, "request": {"schema_version": "0.1",
         "target": {"module": module, "function": fn},
         "arguments": list(args), "step_budget": budget}}
    if expected != ():
        c["expected"] = list(expected)
    return c


def ret_u64(case):
    vals = case.get("returned") or []
    if len(vals) == 1 and "integer" in vals[0]:
        return vals[0]["integer"]["value"]
    return None


def integration_suite(ctx):
    print("== integration ==")
    if not ctx.need_binary():
        return
    # Pure examples behave like unit corpora (plus cross-backend check).
    pure_returns = {}
    for ex, fn, args, expected in PURE_EXAMPLES:
        corpus = write_corpus(f"ex_{ex}", [mk_case("run", f"examples.{ex}", fn, args, [expected])])
        source = os.path.join(REPO, "examples", ex + ".mncs")
        for name, backend in ctx.backends:
            ctx.backend = backend
            pins = WASM_KNOWN_DIVERGENT if name == "wasm" else {}
            result, err = ctx.run_experiment(source, corpus, f"ex-{ex}-{name}")
            if result is None:
                ctx.check(f"example {ex}@{name} runs", False, err)
                continue
            pure_returns.setdefault(ex, {})[name] = assert_cases(ctx, result, f"ex_{ex}", name, pins)
    if len(ctx.backends) == 2:
        for ex, per in pure_returns.items():
            if any(k.split(":")[0] == f"ex_{ex}" for k in WASM_KNOWN_DIVERGENT):
                print(f"  skip example {ex} research==wasm (pinned divergence, SVC-P-021)")
                continue
            if len(per) == 2:
                names = list(per)
                same = json.dumps(per[names[0]], sort_keys=True) == json.dumps(per[names[1]], sort_keys=True)
                ctx.check(f"example {ex} research==wasm", same)
    # Effectful echo: same corpus, three grant scenarios, research backend.
    ctx.backend = BACKENDS["research"]
    echo_corpus = write_corpus("ex_echo", [mk_case("serve", "examples.echo_service", "serve")])
    echo_source = os.path.join(REPO, "examples", "echo_service.mncs")

    def grant_files(payload_bytes):
        rd = os.path.join(TARGET, "echo-request.bin")
        wr = os.path.join(TARGET, "echo-ledger.bin")
        with open(rd, "wb") as fh:
            fh.write(payload_bytes)
        with open(wr, "wb") as fh:
            fh.write(b"")
        return rd, wr

    # Scenario 1: valid frame op 7, payload [10, 20, 30].
    rd, wr = grant_files(frame(7, [10, 20, 30]))
    result, err = ctx.run_experiment(
        echo_source, echo_corpus, "ex-echo-ok",
        grants=["--grant-read", f"req_reader={rd}", "--grant-write", f"resp_writer={wr}"])
    if result is None:
        ctx.check("echo ok runs", False, err)
    else:
        cases = assert_cases(ctx, result, "ex_echo", "research")
        _ = cases
        got = ret_u64(result["cases"][0])
        ctx.check("echo ok returns 8", got == 8, f"got {got}")
        ledger = open(wr, "rb").read()
        try:
            op, payload = parse_frame(ledger)
            ctx.check("echo ok ledger echoes frame", op == 7 and payload == bytes([10, 20, 30]),
                      f"op={op} payload={list(payload)}")
        except AssertionError as exc:
            ctx.check("echo ok ledger echoes frame", False, str(exc))

    # Scenario 2: bad magic -> rejection frame op 0, rank 2, length 6.
    bad = bytearray(frame(7, [10, 20, 30]))
    bad[0] = 0
    rd, wr = grant_files(bytes(bad))
    result, err = ctx.run_experiment(
        echo_source, echo_corpus, "ex-echo-bad",
        grants=["--grant-read", f"req_reader={rd}", "--grant-write", f"resp_writer={wr}"])
    if result is None:
        ctx.check("echo bad runs", False, err)
    else:
        assert_cases(ctx, result, "ex_echo", "research")
        got = ret_u64(result["cases"][0])
        ctx.check("echo bad returns 6", got == 6, f"got {got}")
        ledger = open(wr, "rb").read()
        try:
            op, payload = parse_frame(ledger)
            ctx.check("echo bad ledger rejects rank 2", op == 0 and payload == bytes([2]),
                      f"op={op} payload={list(payload)}")
        except AssertionError as exc:
            ctx.check("echo bad ledger rejects rank 2", False, str(exc))

    # Scenario 3: empty read emits nothing.
    rd, wr = grant_files(b"")
    result, err = ctx.run_experiment(
        echo_source, echo_corpus, "ex-echo-empty",
        grants=["--grant-read", f"req_reader={rd}", "--grant-write", f"resp_writer={wr}"])
    if result is None:
        ctx.check("echo empty runs", False, err)
    else:
        assert_cases(ctx, result, "ex_echo", "research")
        got = ret_u64(result["cases"][0])
        ctx.check("echo empty returns 0", got == 0, f"got {got}")
        ctx.check("echo empty ledger empty", open(wr, "rb").read() == b"")

    # Observable service: two framed events stamped by the granted clock.
    obs_corpus = write_corpus("ex_observable", [mk_case("report", "examples.observable_service", "report")])
    obs_source = os.path.join(REPO, "examples", "observable_service.mncs")
    ledger = os.path.join(TARGET, "obs-ledger.bin")
    open(ledger, "wb").close()
    t0 = int(time.time() * 1000)
    result, err = ctx.run_experiment(
        obs_source, obs_corpus, "ex-observable",
        grants=["--grant-time", "ticker", "--grant-write", f"ledger={ledger}"])
    t1 = int(time.time() * 1000)
    if result is None:
        ctx.check("observable runs", False, err)
    else:
        assert_cases(ctx, result, "ex_observable", "research")
        got = ret_u64(result["cases"][0])
        ctx.check("observable returns 26", got == 26, f"got {got}")
        blob = open(ledger, "rb").read()
        try:
            op1, pay1 = parse_frame(blob[:13])
            op2, pay2 = parse_frame(blob[13:])
            at1 = int.from_bytes(pay1, "big")
            at2 = int.from_bytes(pay2, "big")
            ctx.check("observable two framed events",
                      op1 == 1 and op2 == 2 and len(blob) == 26,
                      f"ops={op1},{op2} len={len(blob)}")
            ctx.check("observable instants plausible",
                      t0 - 5000 <= at1 <= t1 + 5000 and t0 - 5000 <= at2 <= t1 + 5000,
                      f"at={at1},{at2} window={t0}..{t1}")
        except AssertionError as exc:
            ctx.check("observable ledger parses", False, str(exc))


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="all",
                        choices=["static", "unit", "integration", "all"])
    parser.add_argument("--backend", default="both",
                        choices=["research", "wasm", "both"])
    parser.add_argument("--mncs-bin", default=None)
    parser.add_argument("--lang-lib", default=None)
    args = parser.parse_args()
    ctx = Ctx(args)
    # Record exactly which compiler validated this run: the binary is
    # owned by another repository and may be rebuilt at any time.
    try:
        import hashlib
        with open(ctx.mncs_bin, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()[:16]
        mtime = os.path.getmtime(ctx.mncs_bin)
        print(f"mncs binary: {ctx.mncs_bin} sha256:{digest} mtime:{int(mtime)}")
    except OSError as exc:
        print(f"mncs binary identity unavailable: {exc}")
    if args.suite in ("static", "all"):
        static_suite(ctx)
    if args.suite in ("unit", "all"):
        unit_suite(ctx)
    if args.suite in ("integration", "all"):
        integration_suite(ctx)
    print(f"-- {ctx.passes} passed, {len(ctx.failures)} failed --")
    if ctx.failures:
        print("failures:")
        for name in ctx.failures:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
