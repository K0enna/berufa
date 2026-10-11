"""Content-free compatibility report for the legacy candidate evidence catalogue."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from career_os.evidence_contract import EVIDENCE_CONTRACT_VERSION
from career_os.file_transactions import atomic_replace_anchored, file_identity, open_anchored_binary

REQUIRED_CONTEXT_FILES = (
    "CANDIDATE_PROFILE.md",
    "ACHIEVEMENTS.md",
    "CAREER_STRATEGY.md",
    "APPLICATION_STANDARDS.md",
)
LEGACY_CATALOG_FILE = "CANDIDATE_EVIDENCE.json"
MAX_CONTEXT_BYTES = 2_000_000
MAX_CATALOG_BYTES = 128 * 1024
LEGACY_BOUNDARY_MAP = {
    "do_not_use": "private",
    "direct_application": "application",
    "direct_application_and_interview": "application_and_interview",
    "interview_only": "interview",
    "public": "public",
}
MISSING_V2_FIELDS = (
    "kind",
    "ownership",
    "confidence",
    "approximation",
    "source_line_span",
    "role_family_relevance",
    "relationships",
    "retention_policy",
)
_LEGACY_FACT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")


@dataclass(frozen=True, slots=True)
class MigrationSource:
    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class MigrationCandidate:
    legacy_id_fingerprint: str
    source_path: str
    source_heading_fingerprint: str
    source_hash: str
    mapped_publication_boundary: str
    approval_status: str
    missing_v2_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MigrationDryRun:
    migration: str
    target_contract_version: int
    content_included: bool
    vault_changed: bool
    approval_inferred: bool
    sources: tuple[MigrationSource, ...]
    candidates: tuple[MigrationCandidate, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _bounded_bytes(path: Path, maximum: int, root_identity: tuple[int, int]) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Migration input must be a regular file: {path.name}")
    with open_anchored_binary(
        path.parent, root_identity, Path(path.name), label="Migration input"
    ) as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"Migration input must be a regular file: {path.name}")
        if metadata.st_size > maximum:
            raise ValueError(f"Migration input exceeds its size limit: {path.name}")
        payload = stream.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError(f"Migration input exceeds its size limit: {path.name}")
    return payload


def _fingerprint(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def dry_run_migration(context_root: str | Path) -> MigrationDryRun:
    """Inspect legacy inputs without creating a vault or copying candidate content."""
    root = Path(context_root).expanduser().resolve(strict=False)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Legacy context root must be a regular local directory")
    root_identity = file_identity(root)
    sources: list[MigrationSource] = []
    source_hashes: dict[str, str] = {}
    for filename in REQUIRED_CONTEXT_FILES:
        path = root / filename
        if path.parent.resolve(strict=False) != root:
            raise ValueError("Migration input crossed the context boundary")
        payload = _bounded_bytes(path, MAX_CONTEXT_BYTES, root_identity)
        digest = hashlib.sha256(payload).hexdigest()
        source_hashes[filename] = digest
        sources.append(MigrationSource(filename, digest, len(payload)))

    candidates: list[MigrationCandidate] = []
    warnings: list[str] = []
    catalog_path = root / LEGACY_CATALOG_FILE
    if catalog_path.exists():
        raw = _bounded_bytes(catalog_path, MAX_CATALOG_BYTES, root_identity)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Legacy evidence catalogue is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("Legacy evidence catalogue must use schema_version 1")
        entries = payload.get("evidence")
        if not isinstance(entries, list):
            raise ValueError("Legacy evidence catalogue requires an evidence list")
        seen_fact_ids: dict[str, int] = {}
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ValueError(f"Legacy evidence entry {index} must be an object")
            source_file = str(entry.get("source_file") or "")
            if source_file not in REQUIRED_CONTEXT_FILES:
                raise ValueError(f"Legacy evidence entry {index} has an unsupported source")
            boundary = str(entry.get("publication_boundary") or "")
            if boundary not in LEGACY_BOUNDARY_MAP:
                raise ValueError(f"Legacy evidence entry {index} has an invalid boundary")
            fact_id = str(entry.get("fact_id") or "").strip()
            heading = str(entry.get("source_heading") or "").strip()
            if not _LEGACY_FACT_ID.fullmatch(fact_id):
                raise ValueError(f"Legacy evidence entry {index} has an invalid fact_id")
            if fact_id in seen_fact_ids:
                raise ValueError(
                    f"Duplicate legacy evidence identity at entries {seen_fact_ids[fact_id]} and {index}"
                )
            seen_fact_ids[fact_id] = index
            if not heading or len(heading) > 300:
                raise ValueError(f"Legacy evidence entry {index} has an invalid source heading")
            candidates.append(
                MigrationCandidate(
                    legacy_id_fingerprint=_fingerprint(fact_id),
                    source_path=source_file,
                    source_heading_fingerprint=_fingerprint(heading),
                    source_hash=source_hashes[source_file],
                    mapped_publication_boundary=LEGACY_BOUNDARY_MAP[boundary],
                    approval_status="unapproved",
                    missing_v2_fields=MISSING_V2_FIELDS,
                )
            )
    else:
        warnings.append("No legacy evidence catalogue was present; only sources were inventoried")
    if candidates:
        warnings.append(
            "All candidates require local review because legacy catalogue presence is not approval"
        )
    return MigrationDryRun(
        migration="legacy-context-catalogue-to-evidence-vault-v2",
        target_contract_version=EVIDENCE_CONTRACT_VERSION,
        content_included=False,
        vault_changed=False,
        approval_inferred=False,
        sources=tuple(sources),
        candidates=tuple(candidates),
        warnings=tuple(warnings),
    )


def _reject_output_collisions(output: Path, context_root: Path) -> None:
    reserved = tuple(
        context_root / filename for filename in (*REQUIRED_CONTEXT_FILES, LEGACY_CATALOG_FILE)
    )
    resolved_output = output.resolve(strict=False)
    for input_path in reserved:
        if resolved_output == input_path.resolve(strict=False):
            raise ValueError("Migration report output must not identify a migration input")
        if output.exists() and input_path.exists() and output.samefile(input_path):
            raise ValueError("Migration report output must not identify a migration input")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-root", default="context")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    context_root = Path(args.context_root).expanduser().resolve(strict=False)
    report = dry_run_migration(context_root)
    rendered = json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        output = args.output.expanduser().resolve(strict=False)
        output.parent.mkdir(parents=True, exist_ok=True)
        parent_identity = file_identity(output.parent)
        _reject_output_collisions(output, context_root)
        encoded = rendered.encode("utf-8")
        atomic_replace_anchored(
            output,
            encoded,
            expected_parent_identity=parent_identity,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "LEGACY_BOUNDARY_MAP",
    "MISSING_V2_FIELDS",
    "MigrationCandidate",
    "MigrationDryRun",
    "MigrationSource",
    "dry_run_migration",
    "main",
]
