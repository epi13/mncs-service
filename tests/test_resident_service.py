"""Focused resident serving obligations using the Index projection contract."""

import sys
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]
INDEX_RUNNER = SERVICE.parent / "mncs-index" / "runner"
sys.path.insert(0, str(INDEX_RUNNER))
sys.path.insert(0, str(SERVICE / "tools"))

from mncs_index.store_feed import DerivedStoreIndex, StoreCommit, StoreFeed, StoreRelation  # noqa: E402
from native_protocol import NativeProtocol  # noqa: E402
from resident_service import ResidentQueryService  # noqa: E402


def _fixture():
    source = bytes.fromhex("000000000000000000000001")
    target = bytes.fromhex("000000000000000000000002")
    raw = bytearray(80)
    raw[:8] = b"MR\x01\x00" + bytes([2, 0, 0, 0])
    raw[8:20] = source
    raw[20:32] = target
    raw[32:40] = (7).to_bytes(8, "big")
    raw[40:72] = bytes([9]) * 32
    relation = StoreRelation.decode(bytes(raw))
    feed = bytearray(56)
    feed[:4] = b"MC\x01\x00"
    feed[4:12] = (7).to_bytes(8, "big")
    feed[16:20] = (1).to_bytes(4, "big")
    feed[24:56] = bytes([7]) * 32
    return source, target, StoreCommit(StoreFeed.decode(bytes(feed)), (relation,))


def test_resident_query_exposes_authority_generation_and_result_identity():
    _source, target, commit = _fixture()
    index = DerivedStoreIndex.rebuild([commit])
    service = ResidentQueryService(
        index,
        source_authority="Commons pressure lifecycle and family semantics",
    )
    service.start()
    result = service.handle({
        "operation": "relations_for",
        "target_identity": target.hex(),
        "kind": 2,
        "limit": 8,
    })
    assert result["complete"] is True
    assert result["store_generation"] == 7
    assert result["index_through_generation"] == 7
    assert result["source_authority"].startswith("Commons")
    assert len(result["result_identity"]) == 64
    service.stop()


def test_restart_reproduces_generation_aware_result():
    _source, target, commit = _fixture()
    index = DerivedStoreIndex.rebuild([commit])
    service = ResidentQueryService(index, source_authority="Commons")
    service.start()
    first = service.handle({"operation": "relations_for", "target_identity": target.hex()})
    service.stop()
    service.start()
    second = service.handle({"operation": "relations_for", "target_identity": target.hex()})
    assert first["result_identity"] == second["result_identity"]
    assert second["index_through_generation"] == 7


def test_resident_service_reports_stale_index_without_fabricating_completeness():
    _source, target, commit = _fixture()
    index = DerivedStoreIndex.rebuild([commit])
    index.observe_store_generation(8)
    service = ResidentQueryService(index, source_authority="Commons")
    service.start()
    result = service.handle({"operation": "relations_for", "target_identity": target.hex()})
    assert result["freshness"] == "stale"
    assert result["complete"] is False


def test_native_protocol_drives_resident_admission_and_freshness():
    _source, target, commit = _fixture()
    index = DerivedStoreIndex.rebuild([commit])
    protocol = NativeProtocol()
    service = ResidentQueryService(
        index,
        source_authority="Commons",
        protocol=protocol,
    )
    try:
        service.start()
        result = service.handle({"operation": "relations_for", "target_identity": target.hex()})
        assert result["complete"] is True
        assert result["freshness"] == "complete"
    finally:
        service.close()
