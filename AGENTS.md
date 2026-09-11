# Agent and contributor contract

- Prefer `mncs-language` for implementation.
- Make lifecycle, cancellation, resource ownership and failure policy explicit.
- Avoid hidden background work that cannot be joined, cancelled or inspected.
- Separate retryability from generic failure and document idempotency assumptions.
- Tests should use controllable clocks, deterministic scheduling where practical and injected failures.
- Record language/compiler/runtime pressure in `docs/LANGUAGE_PRESSURES.md`.
- Keep service primitives independent from one specific transport or application.
- Run `python3 tools/svc_test.py` (see README) before committing; the suite
  must report 0 failures. Research-backend observations gate semantics;
  wasm divergences are pinned per case (SVC-P-021), never silently
  skipped. The top-level `UNKNOWN` verdict is ambient (SVC-P-013) — the
  gate is per-case `returned` + `expectation_met` plus pins.
