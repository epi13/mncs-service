# NEXT: where mncs-service goes from here

Ordered by architectural leverage. Each item names its unblocker
(language pressure or framework work) so a future run can sequence
itself.

## 1. Retire the driver loop (needs SVC-P-001)

Move step sequencing into the language as spawn/scope runtimes land:
`svc.worker` dispatch becomes real scheduling, the harness shrinks to
a corpus runner. Keep the decision/driving seam — only reimplement
the driving half.

## 2. Real transport (needs SVC-P-002, SVC-P-014)

Socket effects with per-connection capabilities turn `echo_service`
from grant files into a TCP service: listener limits, per-connection
drain, connection-scoped revocation. The codec and admission path do
not change; the grant flags become socket grants.

## 3. Streaming frames (needs SVC-P-003)

Multi-chunk reassembly as an explicit sequence design over wider
views: sequence-numbered chunks, reassembly buffer with bounds,
out-of-order policy. Unblocks compiler-artifact and model-weight
payloads.

## 4. Durable service state (builds on `mncs.std.store.v1`)

Crash-safe generation counters, snapshot handles for readers,
retention policy for the event ledger — the store contracts exist in
stdlib; `mncs-service` needs the service-facing recipe (which state
is durable, what readers observe across restarts).

## 5. Proof discharge for `valid` (needs SVC-P-016)

Promote `queue.valid` / `worker.valid` from corpus-asserted booleans
to statically discharged invariants as the proof kernel gains
pre/postconditions over bounded functions.

## 6. Effect-aware contracts (needs SVC-P-017)

Replace the `pure` bit with reified effect sets once effects become
values: routers shed/cache/replay by rule, and the admission path
checks authority, not just shape.

## 7. Service mesh primitives (needs 1–3)

Dependency declarations with health-gated dialing, capability
advertisement between services, structured retries across the mesh
with deadline propagation — all designed as records first, following
the contract pattern in RFC 0003.

## 8. Compiler-service workload

The target workload from the project brief: concurrent compile
clients, job admission with cancellation, shared caches, streamed
diagnostics, graceful restart. The current primitives cover
admission/cancellation/health/shutdown shapes; missing are
prioritization, cache-share accounting, and diagnostics streaming —
items 2–4 unblock each in turn.

## Deliberately not next

Distributed consensus, an auth ecosystem, an HTTP framework, a
metrics-query language. Abstractions first (identity, capability,
health are in place); systems later, driven by a real second service.
