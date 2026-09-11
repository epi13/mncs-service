# RFC 0002: Host-boundary runtime simulator and the vertical slice

Status: Accepted (implemented)

## Context

MNCS through profile 0.13 has no tasks, no scheduler, no sockets, and
no timers (pressures SVC-P-001, SVC-P-002, SVC-P-015). A service
framework nevertheless needs an end-to-end story now — definition to
wire bytes to shutdown — both to prove the primitives compose and to
generate precise language pressure. This RFC records the chosen shape:
decisions in-language, driving on the host, with a contract for how
the driver disappears as language capabilities land.

## Decision

`mncs-service` splits every service behavior into two halves:

1. **Decision half (MNCS, in this repo).** Total bounded functions
   over explicit state: lifecycle transitions, admission verdicts,
   queue/pool mutations, retry gates, frame codecs, deadline checks,
   health derivation, drain steps. These compile under `mncs 0.13`,
   execute on every backend, and are asserted by corpora.
2. **Driving half (host, `tools/svc_test.py` today).** The event loop
   the language cannot yet express: feeding inbound bytes via
   `--grant-read`, collecting ledger bytes via `--grant-write`,
   observing wall time via `--grant-time`, sequencing steps, and
   enforcing computed delays by actually waiting.

The seam between the halves is the **capability-grant boundary**:
every effect names its capability, and the driver realizes each
capability from an explicit operator grant. No ambient authority
crosses the seam in either direction.

## The vertical slice

The reference slice is the framed echo service
(`examples/echo_service.mncs`):

```
service definition (svc.service + contract)
  -> runtime start (lifecycle configure/start/mark_ready)
  -> transport (host_read grant file = the stand-in socket)
  -> request admission (contract lookup + lifecycle gate + queue admit)
  -> task handling (worker dispatch/settle)
  -> typed response/error (framed echo or rejection frame)
  -> structured event emission (host_write ledger frames)
  -> graceful shutdown (svc.shutdown 7-step sweep)
  -> deterministic cleanup (retire + STOPPED receipt)
```

`tools/svc_test.py::integration_suite` executes this slice with three
grant scenarios (valid frame, hostile frame, empty read) plus a
clock-stamped observable service, asserting both return values and
ledger bytes. Ledger bytes are decoded by an independent Python
framing oracle — never by the MNCS codec under test.

## What the driver must never do

- Interpret service semantics (admission, retry, drain decisions stay
  in MNCS; the driver only sequences granted effects).
- Hide language gaps with host intelligence (every gap gets a
  `SVC-P-*` entry; the driver is deliberately dumb: feed bytes,
  collect bytes, stamp time).
- Become load-bearing architecture: each driver behavior names the
  language capability that retires it (spawn/scope runtime for the
  step loop, socket effects for the grant files, timer effects for
  the sleeps).

## Consequences

- Tests are reproducible without the network, and every assertion is
  either toolchain-checked (`expected`/`expectation_met`) or
  oracle-checked (Python framing decode).
- Wall-clock nondeterminism is contained to the two clock-stamped
  tests, which assert plausibility windows rather than instants
  (pressure SVC-P-008).
- When the language gains a scheduler, `tools/svc_test.py` shrinks to
  a corpus runner: the decision half does not change.
