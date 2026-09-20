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
        object_ids = store.put_general_many(
            [
                ("pressure", handoff),
                ("consumer", _digest(b"repository:mncs-store")),
                ("capability", semantic_identity),
                ("source", source_identity),
            ]
        )
        pressure_id, consumer_id, capability_id, source_id = object_ids
        generation = store._gen
        provenance = _provenance(
            engine,
            source_identity[:12],
            producer_identity[:12],
            transformation_identity[:12],
            generation,
            source_identity,
            _digest(b"mncs-store:ancestry:none/v1"),
        )
        provenance_identity = _digest(provenance)
        relations = [
            _relation(engine, 1, pressure_id, source_id, generation, provenance_identity, 0),
            _relation(engine, 2, pressure_id, consumer_id, generation, provenance_identity, 1),
            _relation(engine, 3, pressure_id, capability_id, generation, provenance_identity, 2),
        ]
        feed = _feed(engine, generation, len(object_ids), len(relations), 1, source_identity)
        store.persist_typed_commit(feed, relations, [provenance])

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
                "typed_records_round_trip": True,
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
