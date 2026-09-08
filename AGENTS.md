# Agent and contributor contract

- Prefer `mncs-language` for implementation.
- Make lifecycle, cancellation, resource ownership and failure policy explicit.
- Avoid hidden background work that cannot be joined, cancelled or inspected.
- Separate retryability from generic failure and document idempotency assumptions.
- Tests should use controllable clocks, deterministic scheduling where practical and injected failures.
- Record language/compiler/runtime pressure in `docs/LANGUAGE_PRESSURES.md`.
- Keep service primitives independent from one specific transport or application.
