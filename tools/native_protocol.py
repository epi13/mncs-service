"""Retained MNCS protocol kernel for the local Service adapter."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import tempfile
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
LANGUAGE_ROOT = Path(
    os.environ.get("MNCS_LANGUAGE_ROOT", "/home/epi13/Documents/Projects/mncs-language")
)
MNCS_BIN = Path(
    os.environ.get("MNCS_BIN", str(LANGUAGE_ROOT / "target" / "debug" / "mncs"))
)
EMBED_LIB = Path(
    os.environ.get(
        "MNCS_EMBED_LIB", str(LANGUAGE_ROOT / "target" / "debug" / "libmncs_embed.so")
    )
)
SOURCE = "src/svc/store_index_protocol.mncs"
MODULE = "svc.store_index_protocol"


class NativeProtocol:
    """Compile once, then retain the verified Service contract artifact."""

    def __init__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="mncs-service-artifact-")
        environment = dict(os.environ)
        environment["MNCS_LIBRARY_PATH"] = str(LANGUAGE_ROOT / "library")
        completed = subprocess.run(
            [
                str(MNCS_BIN),
                "compile",
                str(SERVICE_ROOT / SOURCE),
                "--emit",
                "backend",
                "--output-dir",
                self._temporary.name,
                "--target",
                "mncs-research-bytecode",
            ],
            capture_output=True,
            text=True,
            timeout=280,
            env=environment,
            check=False,
        )
        artifact_path = Path(self._temporary.name) / "backend.json"
        if completed.returncode != 0 or not artifact_path.is_file():
            detail = (completed.stderr or completed.stdout)[-3000:]
            self.close()
            raise RuntimeError(f"Service protocol artifact admission failed: {detail}")
        self._artifact = artifact_path.read_bytes()
        self._library = ctypes.CDLL(str(EMBED_LIB))
        self._library.mncs_session_open.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        self._library.mncs_session_open.restype = ctypes.c_void_p
        self._library.mncs_session_call.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint64,
        ]
        self._library.mncs_session_call.restype = ctypes.c_void_p
        self._library.mncs_response_text.argtypes = [ctypes.c_void_p]
        self._library.mncs_response_text.restype = ctypes.c_char_p
        self._library.mncs_response_free.argtypes = [ctypes.c_void_p]
        self._library.mncs_response_free.restype = None
        self._library.mncs_session_close.argtypes = [ctypes.c_void_p]
        self._library.mncs_session_close.restype = None
        self._library.mncs_last_error.argtypes = []
        self._library.mncs_last_error.restype = ctypes.c_char_p
        self._handle = self._library.mncs_session_open(self._artifact, len(self._artifact))
        if not self._handle:
            error = self._library.mncs_last_error()
            detail = error.decode() if error else "unknown session-open failure"
            self.close()
            raise RuntimeError(f"Service protocol session open failed: {detail}")
        self.closed = False

    def call(self, function: str, arguments: list[dict]) -> int | bool:
        if self.closed:
            raise RuntimeError("native Service protocol is closed")
        response = self._library.mncs_session_call(
            self._handle,
            MODULE.encode(),
            function.encode(),
            json.dumps(arguments, separators=(",", ":")).encode(),
            None,
            8192,
        )
        if not response:
            error = self._library.mncs_last_error()
            detail = error.decode() if error else "unknown protocol call failure"
            raise RuntimeError(detail)
        try:
            value = json.loads(self._library.mncs_response_text(response).decode())
        finally:
            self._library.mncs_response_free(response)
        if value.get("status") != "returned" or len(value.get("returned", [])) != 1:
            raise RuntimeError(value.get("failure_reason", "native protocol call failed"))
        returned = value["returned"][0]
        if "boolean" in returned:
            return bool(returned["boolean"]["value"])
        return int(returned["integer"]["value"])

    def close(self):
        if getattr(self, "closed", False):
            return
        handle = getattr(self, "_handle", None)
        library = getattr(self, "_library", None)
        if handle and library is not None:
            library.mncs_session_close(handle)
        self._handle = None
        temporary = getattr(self, "_temporary", None)
        if temporary is not None:
            temporary.cleanup()
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()
