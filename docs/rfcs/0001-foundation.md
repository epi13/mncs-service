# RFC 0001: Service foundation

Status: Implemented (first slice — see ARCHITECTURE.md, RFCs 0002/0003)

## Principles

- Every background task belongs to a visible scope.
- Shutdown has defined cancellation, drain and cleanup semantics.
- Queues are bounded or explicitly documented as unbounded.
- Retry policy is typed/configured and distinguishes transient from terminal outcomes.
- Resources have deterministic ownership and teardown order.
- Time, network, filesystem and external-system access are explicit effects and replaceable in tests.
- Operational evidence is part of service correctness.

## Pressure objectives

Structured concurrency, async lifetimes, cancellation, typed errors, resource/finalizer semantics, dependency graphs, channels/queues, clocks/timers, generics, configuration schemas, effect handling, macros/metadata and deterministic async testing.
