# MNCS language-pressure ledger

Workload-driven pressure from building `mncs-service` against the
MNCS compiler and runtime. Each entry carries enough information for a
future `mncs-language` agent to reproduce and solve it: the affected
service component, the intended design, the observed compiler/runtime
behavior, a minimal reproducer (in-repo unless noted), the workaround
used, and the desired capability.

Conventions:

- **Reproducer paths** are relative to this repository unless prefixed
  with `mncs-language:`.
- **Compiler under test:** the prebuilt `mncs` binary at
  `../mncs-language/target/debug/mncs` (built 2026-09-10 from
  `mncs-language` at profile 0.13). Source profiles used here:
  `mncs 0.13` throughout.
- **Status values:** `open` (no workaround possible / blocks design),
  `worked-around` (development continues with a documented cost),
  `observation` (behavior worth pinning, not necessarily a defect).

## Index

| ID | Title | Category | Priority | Status |
|----|-------|----------|----------|--------|
| SVC-P-001 | No task/spawn/channel runtime primitives | async/tasking | P0 | open |
| SVC-P-002 | No socket/TCP/UDP/network effects | networking | P0 | open |
| SVC-P-003 | 64-byte transport chunk forces app-level reassembly | platform APIs | P1 | worked-around |
| SVC-P-004 | Counted-iteration ceiling (32) and 2-level nesting bound pool/queue scans | async/tasking | P1 | worked-around |
| SVC-P-005 | Acyclic calls: retry loops cannot live in-language | control flow | P1 | worked-around |
| SVC-P-006 | Immutable values only: no ownership transfer for queue payloads | ownership | P1 | worked-around |
| SVC-P-007 | Finite match has no wildcard: exhaustive arms only | pattern matching | P2 | worked-around |
| SVC-P-008 | No virtual clock: `clock_read` is wall time behind a grant flag | testing | P1 | worked-around |
| SVC-P-009 | Single untyped host_read/host_write stream per capability | platform APIs | P1 | worked-around |
| SVC-P-010 | No recursive types: causal error chains truncated | type system | P1 | worked-around |
| SVC-P-011 | No text type: names/metadata are u64 hashes | type system | P2 | worked-around |
| SVC-P-012 | `fail`, `next`, `over` reserved: common service names refused | syntax | P2 | worked-around |
| SVC-P-013 | Ambient CMP301 obligations hold verdicts at UNKNOWN | diagnostics | P1 | observation |
| SVC-P-014 | Grants are whole-program CLI flags, not dynamic authority | effects/capabilities | P1 | open |
| SVC-P-015 | No timer/sleep/randomness effects: backoff and jitter are data | effects/capabilities | P2 | worked-around |
| SVC-P-016 | Queue/pool invariants are boolean functions, not proofs | proof kernel | P2 | worked-around |
| SVC-P-017 | Effects are declarations, not values: contracts carry a `pure` bit | reflection | P2 | open |
| SVC-P-018 | Chained `a.b.c[i]` projections do not parse | syntax | P2 | worked-around |
| SVC-P-019 | Corpus boundary is exact: signedness mismatches reject | tooling | P2 | observation |
| SVC-P-020 | Step budgets are host-declared fuel with no in-language query | runtime | P2 | worked-around |
| SVC-P-021 | WASM backend miscompiles match-built sequence records (regression) | optimizer/backend | P0 | open |

---

## SVC-P-001 — No task/spawn/channel runtime primitives

- **Affected component:** `src/svc/worker.mncs`, `src/svc/service.mncs`,
  `examples/worker_service.mncs`, the host runtime simulator
  (`docs/rfcs/0002-*.md`, `tools/svc_test.py`).
- **Intended design:** worker pools that actually schedule: dispatch a
  job token onto a slot and have the runtime run it, with cancellation
  propagation, join, and child-failure policy enforced by the language.
- **Observed behavior:** the language has no threads, no spawn, no
  async, no preemption. `mncs.std.task.v1` and `mncs.std.scope.v1`
  explicitly document that they own only the *source-semantics* half
  (lifecycle values, acceptance verdicts); execution is a host concern.
  There is no executor in the toolchain that runs two MNCS functions
  concurrently.
- **Reproducer:** `src/svc/worker.mncs` — the entire pool is a record
  updated by straight-line calls; `tools/svc_test.py` plays scheduler.
- **Workaround used:** pools as explicit scheduling *state* stepped by
  the host harness. Dispatch/settle/reap are total functions; the loop
  lives outside the language.
- **Why undesirable:** scheduling policy (fairness, wakeups, deadlock
  freedom) cannot be expressed or tested in-language; every service
  re-implements the driver loop ad hoc; cancellation is cooperative by
  convention only.
- **Desired capability:** spawned tasks with join handles, structured
  scopes with cancellation propagation, and a scheduler contract the
  language can reason about (at minimum: what interleavings are
  possible, what `join` guarantees).
- **Safety impact:** high — unstructured host-driven stepping is where
  use-after-retire and lost-wakeup bugs will hide.
- **Priority:** P0. **Status:** open.

## SVC-P-002 — No socket/TCP/UDP/network effects

- **Affected component:** `examples/echo_service.mncs`,
  `src/svc/framing.mncs`.
- **Intended design:** a TCP listener accepting framed connections with
  per-connection lifecycle, limits, timeouts, and graceful drain.
- **Observed behavior:** no network effects exist in any profile
  through 0.13. The only byte-moving intrinsics are `host_read` /
  `host_write` (bounded blobs behind file-backed grants), plus
  granted-filesystem observation (`fs_*`). `mncs.std.platform.v1`
  describes platform facts, not sockets.
- **Reproducer:** `examples/echo_service.mncs` — the "transport" is a
  grant file; the codec is real but the socket is simulated.
- **Workaround used:** host-boundary transport: the harness feeds
  request bytes via `--grant-read` and collects ledger bytes via
  `--grant-write`. Framing, admission, and handling stay in-language.
- **Why undesirable:** connection lifecycle, backpressure across a real
  socket, partial reads/writes, and listener limits cannot be exercised;
  the echo example proves the codec, not the networking.
- **Desired capability:** capability-scoped socket effects (listen /
  accept / read / write / close with explicit grants), or an explicit
  statement that network realizations stay host-side behind a contract
  like `mncs.std.channel.v1`.
- **Priority:** P0. **Status:** open.

## SVC-P-003 — 64-byte transport chunk forces app-level reassembly

- **Affected component:** `src/svc/framing.mncs`.
- **Intended design:** frames sized by the protocol (headers plus
  payloads up to a few KB), with the transport chunking beneath them.
- **Observed behavior:** `host_read` delivers at most 64 bytes per
  call; sequences cap at 64 elements (`MNE182` family); `host_write`
  appends at most the 64-byte view per call. Payloads above 56 bytes
  cannot cross in one frame.
- **Reproducer:** `src/svc/framing.mncs` (`max_payload` 56) and
  `candidate_frame_rejects` (OVERSIZED rank 5 for a 57-byte payload).
- **Workaround used:** 61-byte max frame (4 header + 56 payload + 1
  checksum); multi-chunk reassembly deferred to an explicit sequence
  design.
- **Why undesirable:** every protocol must reinvent chunking; larger
  payloads (compiler artifacts, model weights, index pages) are the
  normal case for the planned compiler service.
- **Desired capability:** wider bounded views (e.g. 4 KiB chunks) or a
  first-class streaming/chunk-sequence effect with reassembly semantics.
- **Performance impact:** per-chunk call overhead and copy-per-chunk in
  `widen`; measured step counts in run artifacts (`target/svc-test/`).
- **Priority:** P1. **Status:** worked-around.

## SVC-P-004 — Counted-iteration ceiling and nesting bound

- **Affected component:** `src/svc/queue.mncs`, `src/svc/worker.mncs`,
  `src/svc/retry.mncs`.
- **Intended design:** scans over the full backing array (8 slots
  today, larger pools later) and retry walks bounded by policy.
- **Observed behavior:** counted loops bind `1..=32` (`MNE142`);
  nesting past two levels is refused (`MNE147`); traversal covers
  `len(data)` up to the 64-element sequence bound. Pools wider than 32
  slots cannot be scanned by counted loops at all.
- **Reproducer:** `delay_for` in `src/svc/retry.mncs` always runs 6
  steps and masks inactive ones — the bound is static, the work is
  constant regardless of `attempt`.
- **Workaround used:** fixed 8-slot arrays with traversal loops;
  shift caps (`shift_cap: 6`) keep exponential math inside the static
  bound; `max_attempts > 32` is a config error (code 6).
- **Why undesirable:** constant-work loops burn steps (visible in
  `steps` per case); pool capacity is architecture, not policy, and it
  is dictated by elaboration ceilings.
- **Desired capability:** wider (or policy-parameterized) iteration
  bounds with cost accounting the service can budget, not a fixed 32.
- **Priority:** P1. **Status:** worked-around.

## SVC-P-005 — Acyclic calls: retry loops cannot live in-language

- **Affected component:** `src/svc/retry.mncs`,
  `examples/retry_client.mncs`.
- **Intended design:** `retry_loop(policy, op)` that attempts, backs
  off, and returns the terminal outcome — the loop in-language, driven
  by effects.
- **Observed behavior:** calls must be acyclic (profile 0.3
  `acyclic_calls`); there is no `while`, and bounded iteration cannot
  carry effectful steps with early exit driven by runtime values beyond
  its static bound. A retry loop over an *unbounded* (policy-bounded
  but runtime-decided) attempt sequence is inexpressible.
- **Reproducer:** `examples/retry_client.mncs` — three attempts are
  straight-line code; the fourth would need another source line.
- **Workaround used:** policy as data (`delay_for`, `should_retry`);
  the loop lives in the host driver.
- **Why undesirable:** every caller re-implements loop discipline
  (attempt counting, escalation on exit); a bug in any driver silently
  changes retry semantics while the policy *looks* shared.
- **Desired capability:** bounded effectful loops (fuel- or
  policy-bounded `while` with explicit termination evidence) or
  first-class retry combinators in the runtime.
- **Priority:** P1. **Status:** worked-around.

## SVC-P-006 — Immutable values only: no ownership transfer

- **Affected component:** `src/svc/queue.mncs`, `src/svc/worker.mncs`.
- **Intended design:** enqueue transfers a request payload into the
  queue; dequeue transfers it to a worker — move semantics with no
  copies, checked by the compiler.
- **Observed behavior:** sequences and records are immutable values;
  `replace` builds a new array per write (channel.v1 precedent). There
  is no borrow/move vocabulary (RFC 0009 explicitly unresolved), so
  every "transfer" is a copy by semantics, and payloads cannot be
  *moved* at all — only referenced by id with bytes held host-side.
- **Reproducer:** `admit_evict` + `shift_slots` in `src/svc/queue.mncs`
  copy up to 8 envelopes per dequeue; payloads are `id` tokens by
  design (see module header).
- **Workaround used:** token architecture: the queue owns order and
  pressure; host buffers own bytes.
- **Why undesirable:** copies scale with queue depth on every dequeue;
  the token split means the compiler cannot check that a payload outlives
  its token (lifetime gap documented in code, enforced nowhere).
- **Desired capability:** move/borrow semantics for bounded values, or
  at minimum a lifetime relationship between a token and its backing
  buffer that the compiler checks.
- **Performance impact:** O(depth) envelope copies per dequeue; step
  counts per case pinned in run artifacts.
- **Priority:** P1. **Status:** worked-around.

## SVC-P-007 — Finite match has no wildcard

- **Affected component:** `src/svc/lifecycle.mncs` (9-arm matches),
  `src/svc/errors.mncs`, `src/svc/observe.mncs`.
- **Intended design:** total matching with a checked catch-all for
  "all other phases behave identically" (e.g. nine arms where seven
  return the same verdict).
- **Observed behavior:** a bare `_` on a finite/enum subject is a
  *variant pattern* (a type may declare a variant literally named `_`),
  so catch-alls are rejected (`MNE138`/`MNE140`); integer subjects do
  get `MNE140`-checked `_` defaults. Every lifecycle transition spells
  all nine arms.
- **Reproducer:** the first lifecycle probe failed with `MNE138,
  MNE140` on an `is_ready` wildcard; expanding to nine arms fixed it
  (development history, 2026-09-11).
- **Workaround used:** fully expanded matches everywhere (~9 arms × 12
  transitions in lifecycle alone).
- **Why undesirable:** verbosity hides the interesting arms; adding a
  tenth phase requires editing every match by hand (good totality, bad
  ergonomics — the compiler *could* check a wildcard exhaustively).
- **Desired capability:** an exhaustiveness-checked wildcard for finite
  subjects (reject the wildcard only when it would hide a missing arm,
  never silently).
- **Priority:** P2. **Status:** worked-around.

## SVC-P-008 — No virtual clock

- **Affected component:** `src/svc/time.mncs`,
  `examples/graceful_shutdown.mncs`, `tools/svc_test.py`.
- **Intended design:** deterministic virtual-time tests: advance a fake
  clock, fire deadlines, assert drain behavior without sleeping.
- **Observed behavior:** `clock_read()` is wall time behind
  `--grant-time <capability>`; no mock/parameterized grant exists.
  The observable-service test must accept wall-clock nondeterminism
  (plausibility window instead of exact instants).
- **Reproducer:** `tools/svc_test.py` observable assertions use a
  `[t0-5000, t1+5000]` window; `examples/graceful_shutdown.mncs`
  threads `now` as a plain argument to stay deterministic.
- **Workaround used:** pure time functions over explicit instants
  (`svc.time`); virtual instants as arguments; wall clock only at the
  effect edge.
- **Why undesirable:** any logic that reads time internally (timeouts
  inside pool reap driven by grants) cannot be tested deterministically
  end-to-end.
- **Desired capability:** effect parameterization — grant a clock
  *value* or script per capability so corpora pin instants exactly.
- **Priority:** P1. **Status:** worked-around.

## SVC-P-009 — Single untyped host stream per capability

- **Affected component:** `examples/echo_service.mncs`,
  `examples/observable_service.mncs`.
- **Intended design:** named channels: one capability yielding framed
  requests, another emitting typed events, multiplexed without
  hand-rolled framing.
- **Observed behavior:** `host_read()` takes no arguments and returns
  one blob; `host_write(view)` appends one blob. One stream per
  capability; multiplexing is the application's framing problem.
- **Workaround used:** `svc.framing` as the multiplexer; op ids route.
- **Why undesirable:** every service re-implements demux; a malformed
  stream head can stall all logical channels behind it (head-of-line
  blocking with no language-visible streams).
- **Desired capability:** multiple named byte streams per capability,
  or channel endpoints as grantable values.
- **Priority:** P1. **Status:** worked-around.

## SVC-P-010 — No recursive types: error causality truncated

- **Affected component:** `src/svc/errors.mncs`.
- **Intended design:** causal chains (`caused_by` links) so a worker
  timeout inside a drain inside a deployment reads as one traceable
  value.
- **Observed behavior:** no recursive/nested-variable types; records
  are flat and fixed. An `Error` carries `{class, code, attempts}` —
  one link.
- **Workaround used:** escalating codes preserve the *latest* cause;
  history beyond one link is reconstructed from the event stream.
- **Why undesirable:** programmatic root-cause analysis must join the
  event ledger; the error value alone underdetermines the cause.
- **Desired capability:** bounded recursive types (depth-capped cause
  chains) or a first-class causal annotation the toolchain preserves.
- **Priority:** P1. **Status:** worked-around.

## SVC-P-011 — No text type

- **Affected component:** `src/svc/config.mncs`, `src/svc/contract.mncs`,
  `src/svc/observe.mncs`.
- **Intended design:** service names, operation names, config keys, and
  human-readable notes as bounded text values.
- **Observed behavior:** no string type; text is `[byte; up_to 64]`
  views with producer-attested validity (`mncs.std.text_*`). Config
  keys and service names are u64 hashes by convention.
- **Workaround used:** u64 ids everywhere; human rendering is a host
  projection outside the language.
- **Why undesirable:** misrouted ids are silent (no type distinction
  between a service id and an op id); config files cannot be parsed
  in-language, so the parse adapter is untrusted host code.
- **Desired capability:** bounded text with distinct nominal wrappers
  (or at minimum distinct u64 newtypes) so id kinds do not mix.
- **Priority:** P2. **Status:** worked-around.

## SVC-P-012 — Reserved words collide with service vocabulary

- **Affected component:** `src/svc/lifecycle.mncs` (`fail` ->
  `mark_failed`), `src/svc/queue.mncs` (`next` -> `admitted_q`),
  `src/svc/config.mncs` (`over` -> `lay`).
- **Intended design:** `fail`, `next`, `over` as ordinary function and
  binding names in service code.
- **Observed behavior:** `fail` is the failure-terminator keyword
  (`MNP059`-family refusal as a fn name); `next` opens the
  iteration-step clause and cannot bind (`MNP050` cascade); `over`
  opens traversal and cannot be a parameter name (`MNP024` cascade).
  Profile 0.13 contextualized `next` for *field* positions only.
- **Workaround used:** renames (`mark_failed`, `admitted_q`, `lay`).
- **Why undesirable:** ergonomics only, but it bites exactly the words
  service code reaches for first; each rename is a paper cut in review.
- **Desired capability:** contextual reservation for `fail`/`over` as
  done for `next` fields, or a documented reserved-word list early in
  service onboarding docs.
- **Priority:** P2. **Status:** worked-around.

## SVC-P-013 — Ambient obligations hold verdicts at UNKNOWN

- **Affected component:** `tools/svc_test.py` (gate design), all corpora.
- **Intended design:** a green suite reports PASS; a red case reports
  FAIL.
- **Observed behavior:** every module compiles with retained
  `body:machine-intent` obligations (CMP301, conservative fallback —
  even `mncs.std.clock.v1` shows them), and `experiment run` reports
  top-level `UNKNOWN` with `compilation retained required unresolved
  obligations` although every case returns and every `expected` value
  is met (`expectation_met: true`). `--validation-profile
  artifact-build` adds a second unresolved reason instead of clearing.
- **Reproducer:** any corpus run, e.g. `tests/corpora/lifecycle.json`:
  3/3 `expectation_met: true`, verdict UNKNOWN.
- **Workaround used:** the harness gates on per-case `status ==
  returned` plus `expectation_met`, and separately asserts
  cross-backend agreement. The top-level verdict is recorded, not
  gated.
- **Why undesirable:** CI-style consumers must reimplement verdict
  semantics; a *real* breakage (wrong value) and ambient obligations
  both surface as "not PASS", eroding trust in the gate.
- **Desired capability:** verdicts that distinguish "all observations
  matched, obligations deferred" from "observation mismatch" —
  distinct statuses or a machine-readable breakdown the harness can
  gate on without string-matching reason lists.
- **Priority:** P1. **Status:** observation.

## SVC-P-014 — Grants are whole-program CLI flags

- **Affected component:** `examples/echo_service.mncs`,
  `tools/svc_test.py` grant scenarios.
- **Intended design:** per-connection authority: accept a connection,
  mint a scoped capability for exactly that peer, revoke on close.
- **Observed behavior:** grants are process flags
  (`--grant-read cap=path`, `--grant-time cap`, ...); every call with
  the same capability shares one file/clock; no minting, attenuation,
  or revocation exists in-language.
- **Workaround used:** one capability per stream role; revocation by
  process exit.
- **Why undesirable:** connection-scoped authority, multi-tenant
  isolation, and credential lifetimes cannot be modeled — the exact
  properties a service framework must enforce.
- **Desired capability:** first-class grantable values with attenuate /
  revoke, or an explicit capability-object design in a near profile.
- **Safety impact:** high — ambient-by-flag authority is one confused
  caller away from cross-tenant reads.
- **Priority:** P1. **Status:** open.

## SVC-P-015 — No timer/sleep/randomness effects

- **Affected component:** `src/svc/retry.mncs`.
- **Intended design:** `sleep(delay)` between attempts; jittered
  backoff from a randomness capability.
- **Observed behavior:** no timer or randomness effects through 0.13
  (delays are computed data; the host sleeps). Deterministic corpora
  are the upside: every delay is exactly asserted.
- **Workaround used:** delays as pure values; jitter explicitly
  deferred (documented in module header as dishonest without a
  randomness capability).
- **Why undesirable:** thundering-herd risk without jitter; the host
  driver must be trusted to actually wait the computed delay (the
  language cannot observe whether it did).
- **Desired capability:** timer + randomness capabilities with
  deterministic corpus modes (scripted time, seeded RNG).
- **Priority:** P2. **Status:** worked-around.

## SVC-P-016 — Invariants are boolean functions, not proofs

- **Affected component:** `src/svc/queue.mncs` (`valid`),
  `src/svc/worker.mncs` (`valid`), `src/svc/contract.mncs`.
- **Intended design:** `valid(q)` discharged statically: admit/dequeue
  preserve it by proof, and the corpus checks the interesting
  *transitions*, not the invariant itself.
- **Observed behavior:** the proof kernel (`mncs.core.proof_*`) handles
  proof *terms* and admission, not program verification; there is no
  `maintains valid` annotation, no pre/postconditions, no induction
  over iteration. Corpora re-check `valid` after sequences instead.
- **Workaround used:** `valid` as a boolean function asserted in
  corpora after every operation sequence; transition-table tests as
  packed candidates.
- **Why undesirable:** coverage is by example, not by argument; a
  missed sequence is a missed invariant violation.
- **Desired capability:** lightweight `requires`/`ensures` with
  automated discharge for bounded functions, reusing the existing
  obligation machinery (which already tracks per-operation facts).
- **Priority:** P2. **Status:** worked-around.

## SVC-P-017 — Effects are declarations, not values

- **Affected component:** `src/svc/contract.mncs` (`pure` bit).
- **Intended design:** contracts that name their effects precisely
  (`reads store X`, `emits kind 2`, `no effects`) so routers can shed,
  cache, and replay by rule.
- **Observed behavior:** effects are function-header declarations
  (`effect host_write authorized_by ledger`) visible to the compiler
  but not reified as values a contract could carry. `Op.pure` is one
  coarse bit by necessity.
- **Workaround used:** the `pure` bit plus out-of-band knowledge.
- **Why undesirable:** routers cannot distinguish "pure but
  rate-limited" from "pure and freely replayable"; shedding may drop
  cacheable work or replay effectful work.
- **Desired capability:** effect sets as inspectable values (or a
  compiler query the contract builder can call) with subsumption.
- **Priority:** P2. **Status:** open.

## SVC-P-018 — Chained `a.b.c[i]` projections do not parse

- **Affected component:** `src/svc/worker.mncs`
  (`candidate_worker_reap`).
- **Intended design:** `r0.pool.slots[1]` — project through two
  records, then index.
- **Observed behavior:** `MNP054` parse refusal at the second `[`;
  `a.b` and `a[i]` parse, `a.b[i]` (field-then-index through a double
  projection) does not.
- **Reproducer:** `let kept: Worker = r0.pool.slots[1];` failed;
  binding `let swept: Pool = r0.pool;` first parses.
- **Workaround used:** intermediate bindings before indexing.
- **Why undesirable:** minor ergonomics; deeper compositions (service
  snapshots over nested state) will accumulate boilerplate bindings.
- **Desired capability:** full projection/index chaining in the
  expression grammar.
- **Priority:** P2. **Status:** worked-around.

## SVC-P-019 — Corpus boundary is exact on scalar signedness

- **Affected component:** `tools/svc_test.py`, `tests/corpora/queue.json`.
- **Intended design:** `5000` means 5000; the toolchain coerces or
  reports a precise, actionable error.
- **Observed behavior:** passing `{"integer": {"value": 5000,
  "type": {"bits": 64, "signed": true}}}` for a `u64` parameter yields
  `invalid_request` with a precise message (`expected u64
  (integer:unsigned:64) but received integer(value=5000, type=i64)`).
  The behavior is correct; the error message is good.
- **Workaround used:** exact `signed: false` encodings in corpora;
  static suite does not yet check signedness (noted as future work).
- **Why undesirable:** only a footgun, well-signposted. Listed so a
  future corpus-lint (SVC-P-013's desired verdict work) covers it.
- **Priority:** P2. **Status:** observation.

## SVC-P-020 — Step budgets are host-declared fuel, opaque in-language

- **Affected component:** all corpora (`step_budget`), `tools/svc_test.py`.
- **Intended design:** services budget work explicitly: a drain sweep
  declares its step envelope and the language reports consumption
  against it.
- **Observed behavior:** `step_budget` is a per-case host integer;
  exhaustion surfaces as `budget_exhausted` (framing needed 65536
  where 4096 sufficed for lifecycle); results report `steps` used but
  no function can observe remaining fuel. Budgets were tuned by trial
  (see corpus headers).
- **Workaround used:** per-corpus budgets with headroom; `steps`
  recorded in run artifacts for future tuning.
- **Measured (2026-09-11, identical cases both backends):** step units
  are backend-specific, not portable. research-bytecode vs
  portable-wasm-mvp steps: config 473/4610 (9.7x), queue-flow
  1007/8263 (8.2x), retry 1680/10523 (6.3x), framing-roundtrip
  6881/125625 (18.3x), service-admit 3201/28615 (8.9x). Observations
  agree byte-for-byte in every case; only the cost units differ.
  Budgets must therefore target the most expensive backend, and
  `steps` cannot compare costs across backends today.
- **Why undesirable:** trial-and-error budgets rot (code changes shift
  consumption silently); services cannot shed load based on remaining
  fuel.
- **Desired capability:** in-language fuel query and/or compiler-derived
  step bounds per function so budgets become assertions, not guesses.
- **Performance impact:** direct — step counts are the only cost model
  available, and they are currently write-only.
- **Priority:** P2. **Status:** worked-around.

## SVC-P-021 — WASM backend unsound for service-shaped code (shifting)

- **Affected component:** wasm runs of `lifecycle:reject`,
  `queue:shed`, `worker:flow/reap`, `observe:stream`,
  `service:admit/complete`, `ex_minimal_service`,
  `ex_worker_service`, `ex_graceful_shutdown` (binary-dependent set);
  `tests/probes/wasm_match_records.mncs` (differential canary).
- **Intended design:** ordinary service code — admission paths, drain
  receipts, snapshots, transition batteries — executing identically on
  every backend.
- **Observed behavior:** `mncs-research-bytecode` returns the
  source-semantic value on every run; `mncs-portable-wasm-mvp`
  returns wrong values or traps, and the divergence SET shifts
  between compiler builds. Three failure modes observed:
  1. wrong-value `-1` (assertion trip): lifecycle:reject,
     queue:shed, worker:flow/reap, service:complete, ex_minimal,
     ex_worker, ex_graceful;
  2. wrong-value pack corruption: observe:stream returns 222222 for
     111212;
  3. backend trap: service:admit ends `runtime_failure` with
     `backend trap: out-of-bounds memory load`.
  Bisected 2026-09-11 over nine probe programs: plain spread updates,
  local accept/refusal mimics, and enum-plus-match routing to one
  shared callee all agree; `match` arms building sequence-holding
  records (inline or via distinct callees) with an earlier live
  record read after diverge — but the exact divergent set moves with
  the build (see below), so the trigger family is broader than one
  shape and the backend is best described as unsound-under-surgery
  rather than one neat miscompile.
- **Build dependence (the key finding):** identical sources and
  corpora produce different wasm verdicts under different
  `target/debug/mncs` binaries, all built 2026-09-10 from a dirty
  tree with in-flight `crates/mncs-codegen/src/wasm.rs` changes
  (a peer agent's session rebuilding roughly every 10 minutes):
  - 07:33 build: backpressure/worker/graceful/retry examples diverge
    (-1), everything else agrees;
  - 19:30 build: probe agrees again, backpressure/retry agree, but
    lifecycle:reject, queue:shed, worker, observe, service,
    minimal newly diverge (including an OOB trap);
  - 19:43 build (`sha256 0ff46ac8…`, pinned for this run): same
    10-case divergence set as 19:30 (see `WASM_KNOWN_DIVERGENT`).
  A full suite run that straddles a rebuild executes different cases
  under different compilers — garbage verdicts. The harness now
  records the binary hash/mtime in its header and aborts when the
  binary changes mid-run.
- **Reproducer:** `tests/probes/wasm_match_records.mncs` +
  `tests/corpora/probe_wasm_match_records.json` (self-contained, no
  imports; research expects `[1]`). It diverged under the 19:30
  build and agrees under 0ff46ac8 — kept as a canary either way.
- **Compiler diagnostic:** none for wrong values (silent, exit code 1
  with verdict FAIL — the binary couples exit code to verdict, 0
  otherwise; the harness parses stdout regardless). The trap case
  reports `backend trap: out-of-bounds memory load`.
- **Workaround used:** NONE in service code — restructuring service
  logic around a moving backend target would contort the
  architecture and hide the bug. Instead the harness pins each
  divergent wasm case (`WASM_KNOWN_DIVERGENT` in `tools/svc_test.py`,
  labeled with the pinning binary hash): research gates semantics;
  a wasm run matching its pin passes as "divergence pinned"; a wasm
  run that stops matching (fixed or newly broken) fails loudly so
  pins are re-observed and re-based, never silently stale.
- **Why pinning instead of avoidance:** the trigger family is what
  natural service code looks like; avoiding it means writing worse
  services — and the set moves per build anyway, so avoidance cannot
  even target it.
- **Desired capability:** stabilize and fix the wasm backend (verify
  against pristine HEAD, bisect the wasm.rs delta), then add
  differential research-vs-wasm execution to language CI so silent
  wrong-value regressions cannot land.
- **Safety impact:** critical — silent wrong values in admission and
  drain paths are the worst failure mode a service framework can
  inherit. Nothing may gate on wasm observations until this closes;
  research is the only semantic authority in the meantime.
- **Priority:** P0. **Status:** open.
