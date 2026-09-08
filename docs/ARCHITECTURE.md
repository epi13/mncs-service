# Architecture

## Layers

1. **Lifecycle** — service scopes, resources, startup, readiness, shutdown and cleanup.
2. **Concurrency** — tasks, worker pools, cancellation, deadlines and supervision.
3. **Work** — jobs, queues, schedules, retries and idempotency metadata.
4. **State/support** — caching, configuration and dependency/resource graphs.
5. **Operations** — health, metrics, tracing, structured logs and failure evidence.
6. **Testing** — virtual clocks, deterministic fixtures, injected failures and lifecycle assertions.

## First milestones

1. Structured service/resource scope.
2. Worker pool and bounded queue.
3. Cancellation/deadline/retry semantics.
4. Scheduling and virtual-time tests.
5. Health/observability contracts and realistic service examples.
