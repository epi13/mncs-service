# RFC 0003: Machine-inspectable service contracts

Status: Accepted (implemented, first slice)

## Context

A machine-native service framework should let a machine answer —
without parsing prose — what a service offers, what each operation
costs and risks, and whether a request is admissible. MNCS effects are
currently declarations, not values (pressure SVC-P-017), and there is
no text type (SVC-P-011), so contracts must be built from records,
bounded arrays, and numeric ids.

## Decision

`src/svc/contract.mncs` defines the contract as data:

- `Op { id, input_words, idempotent, pure, max_ms }`: identity,
  scalar input width, retry safety, effect-freedom, per-attempt time
  budget.
- `Contract { ops: [Op; 4], count, service }`: up to four operations
  under one service id.
- Queries: `find` (typed hit/miss, never a trap), `width_ok`
  (over-wide requests are PROTOCOL errors), `retry_safe`
  (idempotency decides at-most-once vs safe retry).

The admission path (`svc.service.admit_request`) consults the
contract on every request in deterministic refusal order: lifecycle
gate, contract lookup, width check, queue admission. Refusal codes
(10/11/12) name the failing layer.

`mncs abi <program>` provides the complementary compiler-owned view
(source envelope, lexical/syntactic artifacts, identities); the
contract record is the service-owned view. Both are JSON a machine
can read.

## Non-goals for this slice

- More than four operations per contract (static capacity, openly
  stated; pressure SVC-P-010 tracks variable-length tables).
- Rich effect sets (one `pure` bit until effects reify; SVC-P-017).
- Human-readable names in-language (u64 ids; rendering is a host
  projection; SVC-P-011).
- Distributed discovery/consensus (no gossip, no registry, no raft —
  contracts are local values passed to `assemble`).

## Consequences

- Routers, load shedders, and agents can implement admission,
  retry-routing, and cache policy from the record alone; the
  `contract_demo` example pins the decision matrix.
- Adding an operation is a record edit plus a corpus case — the
  compiler checks every consumer through the fixed array bound.
- When effects become values, `Op` gains an effect-set field without
  changing the query shapes.
