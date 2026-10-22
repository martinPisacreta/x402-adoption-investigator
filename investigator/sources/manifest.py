"""fixtures/raw/MANIFEST.json loader: request key -> file, capturedAt, sha256 (SPEC.md section 7.3). Owner: B1.

The manifest has a human capture record ("sources") and, once scripts/build_manifest_requests.py has run, a
"requests" map {request_key: {file, capturedAt, sha256, http, contentType, url, method, element?}} covering
every fixture file. The map is generated, never written by hand. `element` is the index inside a JSON array
file for the one capture that holds a 2-element JSON-RPC batch (eth_blockNumber / eth_chainId): two request
keys name the same file with element 0 and 1. fetchedAt is the SampleClock start instant.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

REQUESTS_GENERATED_BY = "scripts/build_manifest_requests.py"


@dataclass
class ManifestEntry:
    request_key: str
    file: str
    captured_at: str
    sha256: str
    http: Optional[int] = None
    content_type: Optional[str] = None
    url: Optional[str] = None
    method: str = "GET"
    element: Optional[int] = None       # index into a JSON array payload (batch fixtures), else None
    family: Optional[str] = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"file": self.file, "capturedAt": self.captured_at, "sha256": self.sha256,
                                "http": self.http, "contentType": self.content_type, "url": self.url,
                                "method": self.method}
        if self.element is not None:
            data["element"] = self.element
        if self.family:
            data["family"] = self.family
        return data


@dataclass
class Manifest:
    path: Path
    fetched_at: str
    note: str = ""
    sources: dict[str, Any] = field(default_factory=dict)
    requests: dict[str, ManifestEntry] = field(default_factory=dict)

    def files(self) -> list[str]:
        """Every fixture file named in the human capture record."""
        names: list[str] = []
        for source in self.sources.values():
            for name in source.get("files", []):
                if name not in names:
                    names.append(name)
        return names

    def request_files(self) -> list[str]:
        names: list[str] = []
        for entry in self.requests.values():
            if entry.file not in names:
                names.append(entry.file)
        return names

    def resolve(self, request_key: str) -> Optional[ManifestEntry]:
        return self.requests.get(request_key)

    def entries_for_file(self, file: str) -> list[ManifestEntry]:
        return [e for e in self.requests.values() if e.file == file]


def parse_requests(data: dict[str, Any]) -> dict[str, ManifestEntry]:
    requests: dict[str, ManifestEntry] = {}
    for key, entry in (data.get("requests") or {}).items():
        requests[key] = ManifestEntry(
            request_key=key, file=entry["file"], captured_at=entry["capturedAt"], sha256=entry["sha256"],
            http=entry.get("http"), content_type=entry.get("contentType"), url=entry.get("url"),
            method=entry.get("method", "GET"), element=entry.get("element"), family=entry.get("family"))
    return requests


def load_manifest(path: Path) -> Manifest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Manifest(path=Path(path), fetched_at=data["fetchedAt"], note=data.get("note", ""),
                    sources=data.get("sources", {}), requests=parse_requests(data))


def write_requests(path: Path, requests: dict[str, ManifestEntry]) -> None:
    """Rewrite MANIFEST.json with a fresh `requests` map; `note`, `fetchedAt` and `sources` are kept verbatim
    (content, not formatting). Keys are sorted so the file is reproducible."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["requestsGeneratedBy"] = REQUESTS_GENERATED_BY
    data["requests"] = {key: requests[key].to_json() for key in sorted(requests)}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
