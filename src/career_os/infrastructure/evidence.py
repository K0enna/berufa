"""Explicit local evidence composition and bounded private request loading."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from career_os.application.evidence import MAX_PROPOSAL_BYTES, EvidenceService
from career_os.document_intake import extract_candidate_document
from career_os.evidence_vault import EvidenceVault
from career_os.file_transactions import file_identity, open_anchored_binary


def create_evidence_service(instance_root: str | Path) -> EvidenceService:
    return EvidenceService(EvidenceVault(instance_root), extract_candidate_document)


def read_private_json(
    instance_root: Path, path: Path, *, label: str, maximum_bytes: int = MAX_PROPOSAL_BYTES
) -> tuple[object, bytes]:
    if type(maximum_bytes) is not int or maximum_bytes < 1:
        raise ValueError("Private JSON byte limit must be a positive integer")
    requested_root = instance_root.expanduser().absolute()
    candidate = path.expanduser().absolute()
    try:
        root = requested_root.resolve(strict=True)
        root_identity = file_identity(root)
        try:
            relative = candidate.relative_to(requested_root)
        except ValueError:
            relative = candidate.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"{label} file must be a regular file inside the private instance"
        ) from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"{label} file must be a regular file inside the private instance")
    try:
        with open_anchored_binary(
            root,
            root_identity,
            relative,
            label=f"{label} file",
        ) as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or not 1 <= opened.st_size <= maximum_bytes:
                raise ValueError(f"{label} file must contain 1-{maximum_bytes} bytes")
            content = stream.read(maximum_bytes + 1)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ValueError(f"{label} file could not be read safely") from exc
    if len(content) > maximum_bytes:
        raise ValueError(f"{label} file exceeds {maximum_bytes} bytes")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} file must be valid UTF-8 JSON") from exc
    return payload, content
