# Local resident Store/Index service

`ResidentQueryService` is a transport-neutral serving boundary. It keeps an
Index projection and its Store generation in memory, admits bounded relation
queries, exposes explicit freshness, and stops only after admitted work is
released. The native contract in `src/svc/store_index_protocol.mncs` owns the
admission, freshness, response-completeness, release, and shutdown decisions.

`tools/native_protocol.py` admits that contract once through `mncs-embed` and
retains the artifact for the resident lifetime. `tools/resident_service.py`
owns only the local stdio adapter, state handles, and JSON projection. A
network/socket adapter is intentionally not implied; networking remains a
genuine runtime pressure.

Every response carries:

- Store generation;
- Index through-generation;
- explicit `complete`, `stale`, or `future_invalid` state;
- Commons source authority;
- deterministic result identity independent of JSON formatting;
- external operation identity.

The scheduled `tools/vertical_path.py` proof restarts the service after
reopening Store and rebuilding Index from typed records.
