# Architecture (as built)

`mncs-service` is a machine-native service framework in MNCS source
(profile 0.13), plus a host-side driver that sequences what the
language cannot yet express. Small composable primitives, no monolith:
each module owns one decision domain as total bounded functions over
explicit state values.

## The split

Every behavior has two halves (RFC 0002):

- **Decision half (MNCS, `src/svc/`).** Pure state transitions with
  explicit acceptance verdicts. Compiles under `mncs 0.13`; research-backend
  observations are the semantic gate, wasm-backend observations are recorded
  and pinned where they diverge (SVC-P-021). All asserted by corpora in
  `tests/corpora/`.
- **Driving half (host, `tools/svc_test.py`).** The event loop the
  language lacks: feeds request bytes via `--grant-read`, collects
  ledger bytes via `--grant-write`, stamps time via `--grant-time`,
  sequences steps. Deliberately dumb — it never decides.

The seam is the capability-grant boundary: every effect names its
capability; the driver realizes capabilities only from explicit
operator grants.

## Layers and modules

1. **Lifecycle** — `svc.lifecycle`: 8 phases
   (CONSTRUCTED→CONFIGURED→STARTING→READY⇄DEGRADED→DRAINING→STOPPING→STOPPED,
   plus FAILED), every edge an explicit `Transition{state, accepted}`.
   Invalid edges reject, never trap. FAILED/STOPPED restart into a new
   epoch (`restart` bumps `epoch`, preserves `failures`). Predicates:
   `admits` (READY|DEGRADED), `is_ready` (READY only), `is_terminal`.
2. **Concurrency** — `svc.worker`: fixed 8-slot pool, IDLE→BUSY→
   DONE|FAILED per slot, `dispatch`/`settle`/`requeue`/`retire`,
   timeout `reap` (code 7), abort-on-failure policy as data. Bounded
   concurrency falls out of the slot count; `dispatch` refusal is pool
   backpressure. Vocabulary mirrors `mncs.std.task.v1`/`scope.v1`,
   which own the language-side lifecycle half.
3. **Work** — `svc.queue` (bounded FIFO of fixed-shape request
   envelopes with REJECT/DROP_NEWEST/DROP_OLDEST shed policies,
   high-water mark, deadline reaping), `svc.retry` (policy as data:
   exponential backoff with shift cap, attempt budgets, deadline-aware
   `should_retry`), `svc.errors` (typed taxonomy: 8 classes with
   retryability, attempt counting, escalation to PERMANENT).
4. **State/support** — `svc.config` (typed record, `defaults`,
   `Partial` overlay, deterministic `validate` codes 1–6),
   `svc.contract` (machine-readable operations: id, input width,
   idempotency, purity, budget; `find`/`width_ok`/`retry_safe`).
5. **Operations** — `svc.observe` (event stream with sequence numbers,
   six counters, `health_of` derivation), `svc.time` (pure deadline
   arithmetic over explicit instants; the only clock read happens at
   the effect edge), `svc.framing` (61-byte wire codec: magic, version,
   op, len, payload ≤56, wrapping checksum; every malformation maps to
   a `FrameError`, never a trap).
6. **Composition** — `svc.service` (one `Service` value threading
   lifecycle+queue+pool+contract+observe+config; deterministic
   admission order with layered refusal codes 10/11/12; snapshot
   receipts), `svc.shutdown` (the 7-step graceful story as a `Drain`
   state machine: signal, close admission, notify, drain, deadline,
   release, receipt).
7. **Testing** — `tools/svc_test.py` (static/use/corpus cross-checks,
   per-module corpora on both backends with cross-backend agreement,
   grant-driven integration with an independent Python framing oracle,
   backend-differential canary plus per-case pinned divergences in
   `WASM_KNOWN_DIVERGENT` — research gates semantics, wasm agreement
   asserted where it holds, divergence pinned where found, SVC-P-021).

## Key invariants (checked, not just documented)

- Queues/pools expose `valid()` boolean predicates; corpora assert
  them after every operation sequence (proof-kernel discharge is
  future work — SVC-P-016).
- State-machine transitions are total: exhaustive matches, acceptance
  verdicts, rejection batteries in corpora (e.g. 13 invalid lifecycle
  edges, double-settle, early close).
- Time is always an explicit argument inside decision code; wall time
  enters once per step at the edge.
- Errors never collapse into strings: `{class, code, attempts}` with
  class-driven policy.

## What is deliberately not here

Distributed consensus, authentication ecosystems, HTTP frameworks,
multi-KB frames, jittered backoff without a randomness capability,
dynamic authority (grants are process flags — SVC-P-014). Each has a
pressure entry or an RFC non-goal, not a half-built subsystem.

## Relation to stdlib

Built *on* `mncs.std.*`, not beside it: `clock.v1` (relational time),
`task.v1`/`scope.v1` (lifecycle vocabulary), `channel.v1`
(backpressure-as-data precedent, `replace` idiom), `text_*`/`bytes`
(byte discipline), `json_emit` (Writer pattern echoed by the codec).
Nothing here duplicates a stdlib contract.
