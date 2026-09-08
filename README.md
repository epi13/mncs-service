# mncs-service

Machine-native application-service infrastructure for MNCS.

`mncs-service` is the reusable layer for APIs, workers and long-running application services. It pressures `mncs-language` on structured concurrency, lifecycle management, dependency/resource ownership, queues, scheduling, configuration, retries, observability and production failure semantics.

## Initial scope

- service lifecycle and structured startup/shutdown
- dependency/resource construction and ownership
- jobs, queues and worker pools
- timers and schedules
- retries, deadlines, cancellation and idempotency
- caching and configuration
- health/readiness and observability
- test harnesses for time, failures and concurrency

## Repository layout

- `docs/ARCHITECTURE.md`
- `docs/rfcs/0001-foundation.md`
- `docs/LANGUAGE_PRESSURES.md`
- `AGENTS.md`
