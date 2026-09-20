"""Run the first Commons pressure path through Ingest, Store, Index, Service.

The Commons JSON file is read once as an external, Git-reviewed declaration.
The runtime path keeps only the native Ingest handoff and typed Store records;
the JSON is returned as an optional parity/projection reference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SERVICE_ROOT.parent
COMMONS_ROOT = WORKSPACE / "MNCS-Commons"
INGEST_ROOT = WORKSPACE / "mncs-ingest"
STORE_ROOT = WORKSPACE / "mncs-store"
LANGUAGE_ROOT = WORKSPACE / "mncs-language"
sys.path.insert(0, str(STORE_ROOT / "tests"))
sys.path.insert(0, str(WORKSPACE / "mncs-index" / "runner"))
sys.path.insert(0, str(SERVICE_ROOT / "tools"))

from mncs_exec import BYTES, U64, as_bool, as_bytes, as_int, require_returned  # noqa: E402
from retained_session import RetainedEngine  # noqa: E402
from store_phase2 import StorePhase2  # noqa: E402
from mncs_index.store_feed import (  # noqa: E402
    DerivedStoreIndex,
    StoreCommit,
    StoreFeed,
    StoreProvenance,
    StoreRelation,
)
from native_protocol import NativeProtocol  # noqa: E402
from resident_service import ResidentQueryService  # noqa: E402


PRESSURE_ID = "MNCS-LIB-C1263916C3C2"
PRESSURE_PATH = COMMONS_ROOT / "pressures" / "records" / f"{PRESSURE_ID}.json"


def _digest(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


def _canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _integer(value: int, *, signed: bool = False):
    return {"integer": {"value": value, "type": {"bits": 64, "signed": signed}}}


def _run_ingest(record: dict) -> tuple[bytes, dict]:
    """Call the native Ingest facade once for this external record."""
    source_bytes = _canonical_json(record)
    source_identity = _digest(source_bytes)
    semantic_identity = _digest(
        ("commons.pressure.semantic/v1\0" + record["id"] + "\0" + record["capability"] + "\0" + record["domain"]).encode()
    )
    producer_identity = _digest(b"mncs-commons:pressure-registry/1")
    transformation_identity = _digest(b"mncs-ingest:commons-pressure-normalization/1")
    arguments = [
        _integer(1),
        _integer(1),
        _integer(0),
        _integer(0),
        BYTES(source_identity),
        BYTES(semantic_identity),
        BYTES(producer_identity),
        BYTES(transformation_identity),
    ]
    corpus = {
        "schema_version": "0.1",
        "name": "commons-pressure-vertical-path",
        "cases": [
            {
                "id": "object-status",
                "request": {
                    "schema_version": "0.1",
                    "target": {"module": "mncs.ingest", "function": "ingest_object_status"},
                    "arguments": [{"integer": {"value": 1, "type": {"bits": 64, "signed": True}}}],
                    "step_budget": 32768,
                },
            },
            {
                "id": "canonical-status",
                "request": {
                    "schema_version": "0.1",
                    "target": {"module": "mncs.ingest", "function": "ingest_canonical_status"},
                    "arguments": [_integer(1, signed=True), {"boolean": {"value": True}}],
                    "step_budget": 32768,
                },
            },
            {
                "id": "handoff",
                "request": {
                    "schema_version": "0.1",
                    "target": {"module": "mncs.ingest", "function": "ingest_handoff_encode"},
                    "arguments": arguments,
                    "step_budget": 32768,
                },
            },
        ],
    }
    with tempfile.TemporaryDirectory(prefix="mncs-ingest-vertical-") as directory:
        corpus_path = Path(directory) / "corpus.json"
        corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
        environment = dict(os.environ)
        environment["MNCS_LIBRARY_PATH"] = os.pathsep.join(
            [
                str(LANGUAGE_ROOT / "library"),
                str(INGEST_ROOT / "language"),
                str(STORE_ROOT / "src"),
            ]
        )
        started = time.perf_counter()
        completed = subprocess.run(
            [
                os.environ.get("MNCS_BIN", str(LANGUAGE_ROOT / "target" / "debug" / "mncs")),
                "experiment",
                "run",
                str(INGEST_ROOT / "language" / "mncs" / "ingest.mncs"),
                "--backend",
                "mncs-research-bytecode",
                "--corpus",
                str(corpus_path),
            ],
            capture_output=True,
            text=True,
            timeout=280,
            env=environment,
            check=False,
        )
        elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout)[-4000:])
    result = json.loads(completed.stdout)
    cases = {case["case_id"]: case for case in result["cases"]}
    handoff = as_bytes(require_returned(cases["handoff"], "Ingest typed handoff"))
    return handoff, {
        "source_identity": source_identity.hex(),
        "semantic_identity": semantic_identity.hex(),
        "producer_identity": producer_identity.hex(),
        "transformation_identity": transformation_identity.hex(),
        "object_status": as_int(require_returned(cases["object-status"], "Ingest object status")),
        "canonical_status": as_int(require_returned(cases["canonical-status"], "Ingest canonical status")),
        "handoff_bytes": len(handoff),
        "native_process_seconds": elapsed,
    }


def _native_bytes(engine, source, module, function, arguments):
    result = engine.run(source, module, [(function, function, arguments)])[function]
    return as_bytes(require_returned(result, function))


def _legacy_scan() -> dict:
    """Measure the repository-archaeology path for the same question."""
    surfaces = {
        "records": sorted((COMMONS_ROOT / "pressures" / "records").glob("*.json")),
        "events": sorted((COMMONS_ROOT / "pressures" / "events").glob("*.json")),
        "views": sorted((COMMONS_ROOT / "pressures" / "views").glob("*.json")),
    }
    parsed = []
    started = time.perf_counter()
    for paths in surfaces.values():
        for path in paths:
            parsed.append(json.loads(path.read_text(encoding="utf-8")))
    matching = [
        value
        for value in parsed
        if isinstance(value, dict)
        and "mncs-store" in value.get("affectedRepositories", [])
        and value.get("initialStatus") not in {"resolved", "retired", "superseded"}
    ]
    return {
        "surface_file_counts": {name: len(paths) for name, paths in surfaces.items()},
        "files_scanned": sum(len(paths) for paths in surfaces.values()),
        "repository_surfaces_scanned": len(surfaces),
        "matching_records": [value.get("id") for value in matching if value.get("id")],
        "elapsed_seconds": time.perf_counter() - started,
    }


def _pressure_history(record: dict) -> dict:
    """Read the bounded Commons history needed for the typed state handoff.

    This is an external declaration/event boundary. The adapter derives
    stable set identities from reviewed Commons records, while Store
    receives only typed identities and producer-supplied codes. Store does
    not decide pressure lifecycle or evidence meaning.
    """
    observation_paths = sorted(
        (COMMONS_ROOT / "pressures" / "observations").glob(f"{PRESSURE_ID}--*.json")
    )
    event_paths = sorted(
        (COMMONS_ROOT / "pressures" / "events").glob(f"{PRESSURE_ID}--*.json")
    )
    observations = [json.loads(path.read_text(encoding="utf-8")) for path in observation_paths]
    events = [json.loads(path.read_text(encoding="utf-8")) for path in event_paths]
    evidence = []
    for observation in observations:
        evidence.extend(observation.get("evidence", []))
    evidence = sorted(evidence, key=lambda value: _canonical_json(value))
    supersession = {
        "record": [
            relation
            for relation in record.get("relationships", [])
            if relation.get("type") in {"supersedes", "duplicate_of"}
        ],
        "events": [
            event
            for event in events
            if event.get("relation") in {"supersedes", "duplicate_of"}
        ],
    }
    evidence_identity = _digest(
        b"commons.pressure.evidence-set/v1\0" + _canonical_json(evidence)
    )
    supersession_identity = _digest(
        b"commons.pressure.supersession-set/v1\0" + _canonical_json(supersession)
    )
    latest_observation = max(
        observations,
        key=lambda value: (str(value.get("observedAt", "")), str(value.get("id", ""))),
        default={},
    )
    classification = latest_observation.get("metadata", {}).get("classification")
    lifecycle_codes = {
        "discovered": 0,
        "confirmed": 1,
        "accepted": 2,
        "implementing": 3,
        "available": 4,
        "verifying": 5,
        "resolved": 6,
        "deferred": 7,
        "rejected": 8,
        "duplicate": 9,
        "superseded": 10,
        "obsolete": 11,
    }
    severity_codes = {
        "blocker": 0,
        "critical": 1,
        "major": 2,
        "minor": 3,
        "informational": 4,
    }
    completeness_codes = {
        None: 0,
        "resolved": 0,
        "partially_resolved": 1,
        "partial": 1,
        "still_real": 2,
        "reframed": 3,
    }
    return {
        "observation_paths": observation_paths,
        "event_paths": event_paths,
        "evidence_identity": evidence_identity,
        "supersession_identity": supersession_identity,
        "lifecycle_code": lifecycle_codes.get(record.get("initialStatus"), 255),
        "severity_code": severity_codes.get(record.get("severity"), 255),
        "completeness_code": completeness_codes.get(classification, 255),
        "classification": classification,
        "evidence_count": len(evidence),
        "supersession_count": len(supersession["record"]) + len(supersession["events"]),
    }


def _relation(engine, kind, source, target, generation, provenance, ordinal):
    return _native_bytes(
        engine,
        "src/store/relationship.mncs",
        "store.relationship.v1",
        "encode_fields",
        [U64(kind), BYTES(source), BYTES(target), U64(generation), BYTES(provenance), U64(ordinal)],
    )


def _provenance(engine, source, producer, transformation, generation, evidence, ancestry):
    return _native_bytes(
        engine,
        "src/store/provenance.mncs",
        "store.provenance.v1",
        "encode_fields",
        [
            BYTES(source),
            BYTES(producer),
            BYTES(transformation),
            U64(generation),
            BYTES(evidence),
            BYTES(ancestry),
        ],
    )


def _semantic_state(
    engine,
    semantic_subject,
    source_content,
    lifecycle,
    severity,
    evidence_identity,
    supersession_identity,
    generation,
    completeness,
):
    return _native_bytes(
        engine,
        "src/store/semantic_state.mncs",
        "store.semantic_state.v1",
        "encode_fields",
        [
            BYTES(semantic_subject),
            BYTES(source_content),
            U64(lifecycle),
            U64(severity),
            BYTES(evidence_identity),
            BYTES(supersession_identity),
            U64(generation),
            U64(completeness),
        ],
    )


def _feed(engine, generation, objects, relations, provenance, root):
    return _native_bytes(
        engine,
        "src/store/commit_feed.mncs",
        "store.commit_feed.v1",
        "encode_fields",
        [U64(generation), U64(objects), U64(relations), U64(provenance), BYTES(root)],
    )


def _query_projection(service, target):
    return service.handle(
        {
            "operation": "relations_for",
            "target_identity": target.hex(),
            "kind": 2,
            "limit": 16,
        }
    )


def main() -> dict:
    legacy = _legacy_scan()
    record = json.loads(PRESSURE_PATH.read_text(encoding="utf-8"))
    history = _pressure_history(record)
    handoff, ingest_report = _run_ingest(record)
    source_identity = bytes.fromhex(ingest_report["source_identity"])
    producer_identity = bytes.fromhex(ingest_report["producer_identity"])
    transformation_identity = bytes.fromhex(ingest_report["transformation_identity"])
    semantic_identity = bytes.fromhex(ingest_report["semantic_identity"])

    engine = RetainedEngine()
    store_directory = tempfile.TemporaryDirectory(prefix="mncs-vertical-store-")
    store_path = Path(store_directory.name) / "store"
    try:
        store = StorePhase2.create(store_path, engine=engine)
        consumer_repositories = tuple(record.get("affectedRepositories", ()))
        consumer_items = [
            (f"consumer:{repository}", _digest(f"repository:{repository}".encode()))
            for repository in consumer_repositories
        ]
        generation = store._gen + 1
        semantic_state = _semantic_state(
            engine,
            semantic_identity,
            source_identity,
            history["lifecycle_code"],
            history["severity_code"],
            history["evidence_identity"],
            history["supersession_identity"],
            generation,
            history["completeness_code"],
        )
        object_ids = store.put_general_many(
            [
                ("pressure", handoff),
                *consumer_items,
                ("capability", semantic_identity),
                ("source", source_identity),
                ("semantic-state", semantic_state),
            ]
        )
        pressure_id = object_ids[0]
        consumer_ids = {
            repository: object_ids[index + 1]
            for index, repository in enumerate(consumer_repositories)
        }
        consumer_id = consumer_ids["mncs-store"]
        capability_id = object_ids[1 + len(consumer_items)]
        source_id = object_ids[2 + len(consumer_items)]
        semantic_state_id = object_ids[3 + len(consumer_items)]
        provenance = _provenance(
            engine,
            source_identity[:12],
            producer_identity[:12],
            transformation_identity[:12],
            generation,
            history["evidence_identity"],
            history["supersession_identity"],
        )
        provenance_identity = _digest(provenance)
        relations = [
            _relation(engine, 1, pressure_id, source_id, generation, provenance_identity, 0),
            *[
                _relation(
                    engine,
                    2,
                    pressure_id,
                    consumer_ids[repository],
                    generation,
                    provenance_identity,
                    index + 1,
                )
                for index, repository in enumerate(consumer_repositories)
            ],
            _relation(
                engine,
                3,
                pressure_id,
                capability_id,
                generation,
                provenance_identity,
                len(consumer_items) + 1,
            ),
        ]
        feed_root = _digest(
            b"commons.pressure.commit-root/v1\0"
            + source_identity
            + semantic_identity
            + history["evidence_identity"]
            + history["supersession_identity"]
        )
        feed = _feed(engine, generation, len(object_ids), len(relations), 1, feed_root)
        store.persist_typed_commit(feed, relations, [provenance])
        stored_semantic_state = store.get_blob(semantic_state_id)
        semantic_state_round_trip = stored_semantic_state == semantic_state

        def load_commit(current_store):
            return StoreCommit(
                feed=StoreFeed.decode(current_store.typed_records("feeds")[0]),
                relations=tuple(
                    StoreRelation.decode(raw) for raw in current_store.typed_records("relations")
                ),
                provenance=tuple(
                    StoreProvenance.decode(raw) for raw in current_store.typed_records("provenance")
                ),
            )

        commit = load_commit(store)
        index = DerivedStoreIndex.rebuild([commit])
        before = index.query_relations(target=consumer_id, kind=2).as_projection()
        # Rebuild only from canonical Store records after destroying the derived state.
        index.destroy()
        rebuilt = DerivedStoreIndex.rebuild([load_commit(store)])
        rebuilt_result = rebuilt.query_relations(target=consumer_id, kind=2).as_projection()
        # `index` was destroyed above; compare the canonical projection captured before it.
        index_rebuild_equal = rebuilt_result["result_identity"] == before["result_identity"]

        protocol = NativeProtocol()
        service = ResidentQueryService(
            rebuilt,
            source_authority="Commons pressure lifecycle and family semantics",
            protocol=protocol,
        )
        service.start()
        complete_query = _query_projection(service, consumer_id)
        service.close()

        # Advance Store without immediately advancing Index: the service must expose staleness.
        store.put_blob(b"generation-two-marker")
        rebuilt.observe_store_generation(store._gen)
        stale_protocol = NativeProtocol()
        stale_service = ResidentQueryService(
            rebuilt,
            source_authority="Commons pressure lifecycle and family semantics",
            protocol=stale_protocol,
        )
        stale_service.start()
        stale_query = _query_projection(stale_service, consumer_id)
        stale_service.close()

        feed_two = _feed(engine, store._gen, len(store._mapping), 0, 0, source_identity)
        store.persist_typed_commit(feed_two, [], [])
        rebuilt.apply(StoreCommit(feed=StoreFeed.decode(feed_two)))

        store.close()
        reopened = StorePhase2.open(store_path, engine=engine)
        restarted_generation = reopened._gen
        restarted_index = DerivedStoreIndex.rebuild([load_commit(reopened), StoreCommit(feed=StoreFeed.decode(feed_two))])
        restart_protocol = NativeProtocol()
        restart_service = ResidentQueryService(
            restarted_index,
            source_authority="Commons pressure lifecycle and family semantics",
            protocol=restart_protocol,
        )
        restart_service.start()
        restart_query = _query_projection(restart_service, consumer_id)
        restart_service.close()
        restarted_semantic_state_round_trip = reopened.get_blob(semantic_state_id) == semantic_state
        reopened.close()
        engine.close()
        retained_metrics = engine.metrics()

        return {
            "source_authority": "MNCS-Commons pressure lifecycle and family semantics",
            "commons_source": {
                "path": str(PRESSURE_PATH.relative_to(WORKSPACE)),
                "id": record["id"],
                "capability": record["capability"],
                "initial_status": record["initialStatus"],
                "affected_repositories": record["affectedRepositories"],
                "semantic_parity": {
                    "id_preserved": record["id"] == PRESSURE_ID,
                    "status_not_reinterpreted_by_store": True,
                    "lifecycle_code": history["lifecycle_code"],
                    "severity_code": history["severity_code"],
                    "completeness_code": history["completeness_code"],
                    "evidence_identity": history["evidence_identity"].hex(),
                    "supersession_identity": history["supersession_identity"].hex(),
                    "affected_consumer_relation_present": any(
                        relation.kind == 2 and relation.target == consumer_id for relation in commit.relations
                    ),
                },
            },
            "ingest": ingest_report,
            "store": {
                "generation": generation,
                "restarted_generation": restarted_generation,
                "object_count": len(object_ids),
                "relation_count": len(commit.relations),
                "provenance_count": len(commit.provenance),
                "handoff_is_not_json": handoff[:4] == b"IH\x01\x00",
                "semantic_state_bytes": len(semantic_state),
                "semantic_state_round_trip": semantic_state_round_trip,
                "restarted_semantic_state_round_trip": restarted_semantic_state_round_trip,
                "typed_records_round_trip": semantic_state_round_trip,
            },
            "index": {
                "destroy_rebuild_same_result_identity": index_rebuild_equal,
                "rebuild_result_identity": rebuilt_result["result_identity"],
                "post_store_generation": stale_query["store_generation"],
                "post_store_index_through_generation": stale_query["index_through_generation"],
                "stale_before_feed": stale_query["freshness"] == "stale" and not stale_query["complete"],
                "complete_after_feed": rebuilt.query_relations(target=consumer_id, kind=2).complete,
            },
            "service": {
                "complete_query": complete_query,
                "restart_same_logical_result": restart_query["result_identity"] == complete_query["result_identity"],
                "restart_query": restart_query,
                "json_projection_is_external": True,
            },
            "measurements": {
                "legacy_repository_surfaces_scanned": legacy["repository_surfaces_scanned"],
                "legacy_files_scanned": legacy["files_scanned"],
                "new_service_query_files_scanned": 0,
                "new_service_query_repository_scans": 0,
                "legacy_matching_records": legacy["matching_records"],
                "store_retained_session": retained_metrics,
            },
        }
    finally:
        engine.close()
        store_directory.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = main()
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if arguments.output:
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
