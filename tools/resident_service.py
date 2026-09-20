"""Resident local Store/Index serving boundary.

This module owns transport and residency only. The typed Store records and
derived Index projection are supplied by callers; Commons remains the source
authority for pressure lifecycle and family meaning. The MNCS contract in
``src/svc/store_index_protocol.mncs`` is the semantic admission/freshness
contract exercised by ``tools/svc_test.py``.

The stdio loop is an explicit local adapter, not a hidden network daemon:
one process keeps the open Store/Index state, admits bounded requests, and
returns JSON only at the external inspection boundary.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from typing import TextIO


@dataclass
class ServiceState:
    phase: str = "constructed"
    in_flight: int = 0
    max_in_flight: int = 8
    operation_count: int = 0


class ResidentQueryService:
    def __init__(self, index, *, max_in_flight: int = 8, source_authority: str, protocol=None):
        if max_in_flight <= 0:
            raise ValueError("max_in_flight must be positive")
        self.index = index
        self.source_authority = source_authority
        self.protocol = protocol
        self.state = ServiceState(max_in_flight=max_in_flight)

    def start(self) -> None:
        if self.state.phase not in {"constructed", "stopped"}:
            raise RuntimeError("service is already running")
        self.state.phase = "ready"

    def stop(self) -> None:
        if self.state.phase == "stopped":
            return
        if self.protocol is not None:
            decision = self.protocol.call(
                "shutdown_decision",
                [
                    {"integer": {"value": self.state.in_flight, "type": {"bits": 64, "signed": False}}},
                    {"boolean": {"value": True}},
                    {"boolean": {"value": False}},
                ],
            )
            if decision == 1:
                self.state.phase = "draining"
                raise RuntimeError("service must drain admitted requests before stop")
            if decision == 2:
                return
        elif self.state.in_flight:
            self.state.phase = "draining"
            raise RuntimeError("service must drain admitted requests before stop")
        self.state.phase = "stopped"

    def observe_store_generation(self, generation: int) -> None:
        self.index.observe_store_generation(generation)

    def _admit(self, limit: int) -> None:
        if self.protocol is not None:
            decision = self.protocol.call(
                "admit",
                [
                    {"boolean": {"value": self.state.phase == "ready"}},
                    {"integer": {"value": self.state.in_flight, "type": {"bits": 64, "signed": False}}},
                    {"integer": {"value": self.state.max_in_flight, "type": {"bits": 64, "signed": False}}},
                    {"integer": {"value": limit, "type": {"bits": 64, "signed": False}}},
                ],
            )
            if decision == 1:
                raise RuntimeError("service is not ready")
            if decision == 2:
                raise RuntimeError("service overloaded")
            if decision == 3:
                raise ValueError("limit must be positive")
        else:
            if self.state.phase != "ready":
                raise RuntimeError("service is not ready")
            if limit <= 0:
                raise ValueError("limit must be positive")
            if self.state.in_flight >= self.state.max_in_flight:
                raise RuntimeError("service overloaded")
        self.state.in_flight += 1

    def query_relations(self, request: dict) -> dict:
        """Serve one bounded typed relation query as an external projection."""
        limit = int(request.get("limit", 32))
        self._admit(limit)
        try:
            target = bytes.fromhex(request["target_identity"])
            kind = request.get("kind")
            result = self.index.query_relations(target=target, kind=kind)
            projected = result.as_projection()
            projected["relations"] = projected["relations"][:limit]
            projected["source_authority"] = self.source_authority
            if self.protocol is not None:
                freshness = self.protocol.call(
                    "freshness",
                    [
                        {"integer": {"value": result.index_through_generation, "type": {"bits": 64, "signed": False}}},
                        {"integer": {"value": result.store_generation, "type": {"bits": 64, "signed": False}}},
                    ],
                )
                complete = self.protocol.call(
                    "response_complete",
                    [
                        {"integer": {"value": result.index_through_generation, "type": {"bits": 64, "signed": False}}},
                        {"integer": {"value": result.store_generation, "type": {"bits": 64, "signed": False}}},
                        {"integer": {"value": limit, "type": {"bits": 64, "signed": False}}},
                    ],
                )
                projected["freshness"] = ("complete", "stale", "future_invalid")[freshness]
                projected["complete"] = bool(complete)
            projected["operation_identity"] = hashlib.sha256(
                json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            projected["service_operation_count"] = self.state.operation_count + 1
            self.state.operation_count += 1
            return projected
        finally:
            if self.protocol is not None:
                self.state.in_flight = int(
                    self.protocol.call(
                        "release",
                        [{"integer": {"value": self.state.in_flight, "type": {"bits": 64, "signed": False}}}],
                    )
                )
            else:
                self.state.in_flight -= 1

    def close(self) -> None:
        self.stop()
        if self.protocol is not None:
            self.protocol.close()

    def handle(self, request: dict) -> dict:
        if request.get("operation") == "relations_for":
            return self.query_relations(request)
        if request.get("operation") == "status":
            return {
                "phase": self.state.phase,
                "in_flight": self.state.in_flight,
                "store_generation": self.index.store_generation,
                "index_through_generation": self.index.index_through_generation,
            }
        raise ValueError("unknown resident service operation")


def serve_stdio(service: ResidentQueryService, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    service.start()
    for line in stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = service.handle(request)
        except Exception as error:  # external protocol boundary
            response = {"error": type(error).__name__, "message": str(error)}
        stdout.write(json.dumps(response, sort_keys=True) + "\n")
        stdout.flush()
    if service.state.in_flight == 0:
        service.state.phase = "stopped"
