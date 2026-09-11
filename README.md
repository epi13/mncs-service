# mncs-service

Machine-native application-service infrastructure for MNCS: small
composable primitives for building long-running, concurrent,
networked services — lifecycle, bounded queues, worker pools,
retries, framing, configuration, contracts, observability, and
graceful shutdown — written in MNCS language (profile 0.13) as total
bounded functions, plus a host-side driver for what the language
cannot yet express.

## What this is

The foundation for machine-native services: compiler services,
distributed workers, agents, model services, storage/index servers,
daemons, meshes. Every behavior splits into a **decision half**
(MNCS: state machines, admission verdicts, codecs, policies) and a
**driving half** (host: feeds bytes, collects ledgers, stamps time,
sequences steps). The seam is the capability-grant boundary — no
ambient authority crosses it. See `docs/rfcs/0002-runtime-simulator.md`.

## Layout

- `src/svc/` — 12 MNCS library modules (lifecycle, errors, retry,
  time, queue, worker, framing, config, observe, contract, shutdown,
  service), each with executable `candidate_*` test entries.
- `examples/` — 8 runnable programs: minimal, echo (framed
  request/response over granted transport), worker pool,
  backpressure, graceful shutdown, observable (ledger events),
  retry client, contract matrix.
- `tests/corpora/` — execution corpora with toolchain-checked
  `expected` values (23 unit cases + example cases).
- `tools/svc_test.py` — the test harness (static checks, backend
  runs, grant scenarios, independent framing oracle).
- `docs/` — `ARCHITECTURE.md` (as built), `LANGUAGE_PRESSURES.md`
  (20 ledger entries, SVC-P-001…020), `rfcs/`, `NEXT.md`.

## Prerequisites

- A built `mncs-language` binary (read-only use; this repo never
  builds or modifies it). Default lookup:
  `../mncs-language/target/debug/mncs`.
- Python 3.8+ (stdlib only) for the harness.
- `MNCS_LIBRARY_PATH` is set by the harness automatically
  (`src/` + the language `library/`).

## Build / run / test

```bash
# everything (static + unit on both backends + integration)
python3 tools/svc_test.py

# subsets
python3 tools/svc_test.py --suite static
python3 tools/svc_test.py --suite unit --backend research
python3 tools/svc_test.py --suite integration

# explicit binary / library locations
python3 tools/svc_test.py --mncs-bin /path/to/mncs --lang-lib /path/to/mncs-language/library

# pin a private compiler copy for a stable run (the language repo's
# binary may be rebuilt at any time; the suite aborts if it changes
# mid-run and logs path/sha256/mtime on every invocation)
cp ../mncs-language/target/debug/mncs target/mncs-pin-local
MNCS_BIN=$PWD/target/mncs-pin-local python3 tools/svc_test.py

# inspect one module directly
MNCS_LIBRARY_PATH=src:../mncs-language/library \
  ../mncs-language/target/debug/mncs source-study src/svc/queue.mncs --node-id local

# machine-readable contract of a module
MNCS_LIBRARY_PATH=src:../mncs-language/library \
  ../mncs-language/target/debug/mncs abi src/svc/contract.mncs
```

Run artifacts land in `target/svc-test/` (git-ignored): per-run
result JSON, backend artifacts, grant files, ledgers.

## Defining a service

```mncs
use svc.config; use svc.contract; use svc.service; ...

let cfg: svc.config.Config = svc.config.defaults();
let op: svc.contract.Op = svc.contract.Op { id: 1, input_words: 3, idempotent: true, pure: true, max_ms: 500 };
let c: svc.contract.Contract = svc.contract.contract1(77, op);
let s0: svc.service.Service = svc.service.assemble(cfg, c);
// drive configure -> start -> mark_ready, then admit_request,
// worker dispatch/settle, shutdown sweep. See examples/minimal_service.mncs.
```

## How lifecycle / concurrency / failure / shutdown work

- **Lifecycle:** 8 phases, explicit `Transition{state, accepted}`
  edges; invalid edges reject. `admits` gates work, `is_ready`
  gates traffic, `restart` opens a new epoch.
- **Concurrency:** fixed worker pool (bounded by construction);
  dispatch refusal is backpressure; timeouts reap to FAILED;
  failure policy is data. No threads exist yet — the pool is
  explicit scheduling state stepped by the driver (SVC-P-001).
- **Failure:** typed `{class, code, attempts}` errors, 8 classes with
  retryability; escalation to PERMANENT on policy exhaustion.
- **Shutdown:** 7-step `Drain` machine (signal, close admission,
  notify, drain, deadline, release, receipt) with clean/forced/misuse
  paths tested.
- **Backpressure:** bounded queues with shed policies, pressure
  classes, deadline reaping; saturation is counted, never silent.

## Current status and limits

Works now: all 12 modules and all 8 examples assert green on the
research backend (23 unit cases + example packs); framed echo +
ledger observability run through real grants with byte-exact
ledgers. Known divergence (SVC-P-021, P0): the wasm backend is
unsound for service-shaped code in current builds (wrong values, one
pack corruption, one out-of-bounds trap — the set shifts between
compiler builds), so 10 wasm cases pin their divergent observations
while research gates semantics — see `tools/svc_test.py`
(`WASM_KNOWN_DIVERGENT`, labeled with the pinning binary hash) and
`tests/probes/` (self-contained canary). The suite passes with pins;
a wasm run that stops matching its pin fails loudly for re-basing.
Experimental: everything networked beyond the grant-file transport,
multi-KB frames, jitter, dynamic authority — each recorded as
language pressure with a reproducer. See `docs/NEXT.md` for the
roadmap and `docs/LANGUAGE_PRESSURES.md` for what the language must
improve.

The suite logs the exact compiler binary (path, sha256, mtime) on
every run: the binary belongs to `mncs-language` and may be rebuilt
at any time — which is precisely how SVC-P-021 was caught.
