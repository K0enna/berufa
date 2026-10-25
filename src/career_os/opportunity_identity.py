from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_IDENTITY_FILE_BYTES = 256 * 1024
MAX_WORKSPACE_MANIFEST_BYTES = 128 * 1024
MAX_BINDINGS = 1_000
MAX_WORKSPACE_DIRECTORIES = 2_000
MAX_WORKSPACE_MANIFESTS = 200
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
IDENTITY_BINDING_FIELDS = frozenset(
    {
        "alias_id",
        "canonical_opportunity_id",
        "confirmed_at",
        "workspace_manifest",
        "workspace_manifest_sha256",
        "reason",
    }
)
CONFIRMED_AT_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00")
_IDENTITY_PAYLOAD_UNSET = object()


@dataclass(frozen=True, slots=True)
class OpportunityIdentityBinding:
    alias_id: str
    canonical_opportunity_id: str
    confirmed_at: str
    workspace_manifest: str
    workspace_manifest_sha256: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OpportunityIdentityRegistry:
    bindings: tuple[OpportunityIdentityBinding, ...] = ()

    def resolve(self, opportunity_id: object) -> str:
        identity = str(opportunity_id or "").strip()
        if not identity:
            return ""
        aliases = {binding.alias_id: binding.canonical_opportunity_id for binding in self.bindings}
        return aliases.get(identity, identity)


def _is_indirect(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        if callable(isjunction) and isjunction(path):
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _workspace_root(
    entry: object,
    *,
    project_root: Path,
    application_root: Path,
) -> Path:
    source = Path(_entry_value(entry, "workspace_path"))
    lexical = source if source.is_absolute() else project_root / source
    lexical = Path(os.path.abspath(lexical))
    try:
        relative = lexical.relative_to(application_root)
    except ValueError as exc:
        raise ValueError("Pipeline workspace is outside the configured application root") from exc
    current = application_root
    for part in relative.parts:
        current /= part
        if _is_indirect(current):
            raise ValueError("Pipeline workspace traverses a link or reparse point")
    if not lexical.is_dir():
        raise ValueError("Pipeline workspace is unavailable")
    return lexical.resolve(strict=True)


def _workspace_manifests(workspace: Path, application_root: Path) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    directories = 0

    def fail_inventory(error: OSError) -> None:
        raise error

    for directory, names, files in os.walk(
        workspace,
        topdown=True,
        onerror=fail_inventory,
        followlinks=False,
    ):
        directories += 1
        if directories > MAX_WORKSPACE_DIRECTORIES:
            raise ValueError("Pipeline workspace exceeds the bounded directory inventory")
        current = Path(directory)
        if current != workspace and _is_indirect(current):
            raise ValueError("Pipeline workspace inventory encountered directory indirection")
        for name in tuple(names):
            if _is_indirect(current / name):
                raise ValueError("Pipeline workspace inventory encountered directory indirection")
        if "workspace.json" not in files:
            continue
        candidate = current / "workspace.json"
        relative = candidate.relative_to(application_root).as_posix()
        safe = _safe_manifest(application_root, relative)
        encoded = _read_bounded_bytes(
            safe,
            maximum_bytes=MAX_WORKSPACE_MANIFEST_BYTES,
            artifact="Identity binding workspace manifest",
        )
        try:
            payload = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Workspace manifest is invalid JSON: {relative}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Workspace manifest must be an object: {relative}")
        schema_version = payload.get("schema_version")
        opportunity_id = payload.get("opportunity_id")
        job_key = payload.get("job_key")
        if (
            type(schema_version) is not int
            or schema_version not in {1, 2}
            or not isinstance(opportunity_id, str)
            or not opportunity_id.strip()
            or not isinstance(job_key, str)
            or not job_key.strip()
        ):
            raise ValueError(f"Workspace manifest identity contract is invalid: {relative}")
        manifests.append(
            {
                "workspace_manifest": relative,
                "workspace_manifest_sha256": hashlib.sha256(encoded).hexdigest(),
                "manifest_opportunity_id": opportunity_id.strip(),
                "manifest_job_key": job_key.strip(),
            }
        )
        if len(manifests) > MAX_WORKSPACE_MANIFESTS:
            raise ValueError("Pipeline workspace exceeds the bounded manifest inventory")
    return sorted(manifests, key=lambda item: item["workspace_manifest"])


def _inventory_workspace_identities(
    entries: tuple[object, ...],
    *,
    project_root: Path,
    application_root: Path,
) -> tuple[dict[int, list[dict[str, Any]]], dict[int, str], dict[str, list[str]]]:
    evidence_by_line: dict[int, list[dict[str, Any]]] = {}
    errors_by_line: dict[int, str] = {}
    alias_claims: dict[str, list[str]] = {}
    for index, entry in enumerate(entries):
        source_line = int(_entry_value(entry, "source_line") or index)
        if not _entry_value(entry, "workspace_path"):
            continue
        try:
            workspace = _workspace_root(
                entry,
                project_root=project_root,
                application_root=application_root,
            )
            manifests = _workspace_manifests(workspace, application_root)
        except (OSError, UnicodeError, ValueError) as exc:
            errors_by_line[source_line] = str(exc)
            continue
        evidence_by_line[source_line] = manifests
        for evidence in manifests:
            alias_id = evidence["manifest_opportunity_id"]
            if alias_id != _entry_value(entry, "opportunity_id"):
                alias_claims.setdefault(alias_id, []).append(_entry_value(entry, "opportunity_id"))
    return evidence_by_line, errors_by_line, alias_claims


def _timestamp(value: object) -> datetime:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Identity binding confirmed_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Identity binding confirmed_at must include a timezone")
    normalized = parsed.astimezone(UTC)
    if not CONFIRMED_AT_PATTERN.fullmatch(raw):
        raise ValueError("Identity binding confirmed_at must be canonical UTC ISO-8601")
    return normalized


def _entry_value(entry: object, field: str) -> str:
    value = entry.get(field) if isinstance(entry, Mapping) else getattr(entry, field, None)
    return str(value or "").strip()


def _safe_manifest(application_root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not relative.strip():
        raise ValueError("Identity binding workspace_manifest must be application-root relative")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("Identity binding workspace_manifest contains an unsafe path segment")
    root = application_root.resolve(strict=True)
    manifest = root.joinpath(candidate)
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Identity binding workspace manifest is unavailable or not a regular file")
    resolved = manifest.resolve(strict=True)
    if root not in resolved.parents:
        raise ValueError("Identity binding workspace manifest escapes the application root")
    current = manifest.parent
    while current != root:
        if current.is_symlink() or bool(
            getattr(os.path, "isjunction", lambda _path: False)(current)
        ):
            raise ValueError("Identity binding workspace manifest uses directory indirection")
        current = current.parent
    if manifest.stat().st_size > MAX_WORKSPACE_MANIFEST_BYTES:
        raise ValueError("Identity binding workspace manifest exceeds the size limit")
    return manifest


def _read_bounded_bytes(path: Path, *, maximum_bytes: int, artifact: str) -> bytes:
    try:
        with path.open("rb") as stream:
            content = stream.read(maximum_bytes + 1)
    except OSError as exc:
        raise ValueError(f"{artifact} changed while being read") from exc
    if len(content) > maximum_bytes:
        raise ValueError(f"{artifact} exceeds the size limit")
    return content


def load_opportunity_identity_registry(
    path: str | Path,
    *,
    pipeline: Iterable[object] | Callable[[], Iterable[object]],
    application_root: str | Path,
    project_root: str | Path = ".",
    as_of: datetime | None = None,
    _payload: object = _IDENTITY_PAYLOAD_UNSET,
    _check_alias_ownership: bool = True,
) -> OpportunityIdentityRegistry:
    """Load exact, evidence-bound aliases without changing any source authority."""

    if _payload is _IDENTITY_PAYLOAD_UNSET:
        source = Path(path)
        if source.is_symlink():
            raise ValueError("Opportunity identity binding file must not be a symlink")
        if not source.exists():
            return OpportunityIdentityRegistry()
        if not source.is_file() or source.stat().st_size > MAX_IDENTITY_FILE_BYTES:
            raise ValueError("Opportunity identity binding file is unavailable or too large")
        try:
            content = _read_bounded_bytes(
                source,
                maximum_bytes=MAX_IDENTITY_FILE_BYTES,
                artifact="Opportunity identity binding file",
            )
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Opportunity identity binding file is invalid JSON") from exc
    else:
        payload = _payload
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "bindings"}
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
    ):
        raise ValueError("Unsupported opportunity identity binding schema")
    raw_bindings = payload.get("bindings")
    if not isinstance(raw_bindings, list) or len(raw_bindings) > MAX_BINDINGS:
        raise ValueError("Opportunity identity bindings must be a bounded array")

    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise ValueError("Opportunity identity as_of must include a timezone")
    cutoff = as_of.astimezone(UTC) if as_of is not None else None
    if not raw_bindings:
        return OpportunityIdentityRegistry()

    root = Path(application_root)
    pipeline_by_id: dict[str, list[object]] | None = None
    alias_claims: dict[str, list[str]] = {}
    bindings: list[OpportunityIdentityBinding] = []
    seen_aliases: set[str] = set()
    for index, raw in enumerate(raw_bindings):
        if not isinstance(raw, dict):
            raise ValueError(f"Identity binding {index} must be an object")
        if set(raw) != IDENTITY_BINDING_FIELDS or any(
            not isinstance(raw.get(field), str) for field in IDENTITY_BINDING_FIELDS
        ):
            raise ValueError(
                f"Identity binding {index} must contain exactly the required string fields"
            )
        if (
            raw["alias_id"] != raw["alias_id"].strip()
            or raw["canonical_opportunity_id"] != raw["canonical_opportunity_id"].strip()
            or raw["workspace_manifest"] != raw["workspace_manifest"].strip()
        ):
            raise ValueError(f"Identity binding {index} contains non-canonical whitespace")
        if not CONFIRMED_AT_PATTERN.fullmatch(raw["confirmed_at"]):
            raise ValueError("Identity binding confirmed_at must be canonical UTC ISO-8601")
        if not SHA256_PATTERN.fullmatch(raw["workspace_manifest_sha256"]):
            raise ValueError(f"Identity binding {index} has an invalid manifest SHA-256")
        alias_id = str(raw.get("alias_id") or "").strip()
        canonical_id = str(raw.get("canonical_opportunity_id") or "").strip()
        confirmed_at = str(raw.get("confirmed_at") or "").strip()
        relative_manifest = str(raw.get("workspace_manifest") or "").strip()
        expected_hash = str(raw.get("workspace_manifest_sha256") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if not alias_id or len(alias_id) > 200 or not canonical_id or len(canonical_id) > 200:
            raise ValueError(f"Identity binding {index} has an invalid opportunity identifier")
        if alias_id == canonical_id:
            raise ValueError(f"Identity binding {index} does not change identity")
        if alias_id in seen_aliases:
            raise ValueError(f"Identity binding alias {alias_id!r} is duplicated")
        if not reason or len(reason) > 1_000:
            raise ValueError(f"Identity binding {index} requires a bounded reason")
        if not relative_manifest or len(relative_manifest) > 1_000:
            raise ValueError(f"Identity binding {index} has an invalid workspace manifest path")
        confirmed = _timestamp(confirmed_at)
        if cutoff is not None and confirmed > cutoff:
            continue
        if pipeline_by_id is None:
            if root.is_symlink() or not root.is_dir():
                raise ValueError("Application root is unavailable for identity binding validation")
            pipeline_by_id = {}
            pipeline_entries = tuple(pipeline() if callable(pipeline) else pipeline)
            for entry in pipeline_entries:
                identity = _entry_value(entry, "opportunity_id") or _entry_value(
                    entry, "pipeline_key"
                )
                if identity:
                    pipeline_by_id.setdefault(identity, []).append(entry)
        if alias_id in pipeline_by_id:
            raise ValueError(
                f"Identity binding alias {alias_id!r} is an authoritative pipeline identity"
            )
        targets = pipeline_by_id.get(canonical_id, [])
        if len(targets) != 1:
            raise ValueError(
                f"Identity binding target {canonical_id!r} is not unique in the pipeline"
            )
        manifest = _safe_manifest(root, relative_manifest)
        content = _read_bounded_bytes(
            manifest,
            maximum_bytes=MAX_WORKSPACE_MANIFEST_BYTES,
            artifact="Identity binding workspace manifest",
        )
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise ValueError(f"Identity binding {index} workspace manifest hash changed")
        try:
            manifest_payload = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Identity binding {index} workspace manifest is invalid") from exc
        if not isinstance(manifest_payload, dict):
            raise ValueError(f"Identity binding {index} workspace manifest must be an object")
        manifest_schema = manifest_payload.get("schema_version")
        if type(manifest_schema) is not int or manifest_schema not in {1, 2}:
            raise ValueError(f"Identity binding {index} workspace manifest schema is unsupported")
        required_workspace_fields = ("company", "role", "job_key", "generated_at")
        if any(
            not isinstance(manifest_payload.get(field), str)
            or not str(manifest_payload[field]).strip()
            for field in required_workspace_fields
        ):
            raise ValueError(
                f"Identity binding {index} workspace manifest is missing required fields"
            )
        raw_manifest_id = manifest_payload.get("opportunity_id")
        if not isinstance(raw_manifest_id, str) or not raw_manifest_id.strip():
            raise ValueError(
                f"Identity binding {index} workspace manifest requires an explicit opportunity_id"
            )
        manifest_id = raw_manifest_id.strip()
        if manifest_id != alias_id:
            raise ValueError(
                f"Identity binding {index} alias does not match the workspace manifest"
            )
        workspace_path = _entry_value(targets[0], "workspace_path")
        if not workspace_path:
            raise ValueError(f"Identity binding target {canonical_id!r} has no workspace path")
        try:
            pipeline_workspace = Path(workspace_path)
            if not pipeline_workspace.is_absolute():
                pipeline_workspace = Path(project_root) / pipeline_workspace
            resolved_pipeline_workspace = pipeline_workspace.resolve(strict=True)
            resolved_manifest_parent = manifest.parent.resolve(strict=True)
            exact_workspace = (
                resolved_pipeline_workspace == resolved_manifest_parent
                or resolved_pipeline_workspace in resolved_manifest_parent.parents
            )
        except OSError as exc:
            raise ValueError(
                f"Identity binding target {canonical_id!r} workspace is unavailable"
            ) from exc
        if not exact_workspace:
            raise ValueError(
                f"Identity binding target {canonical_id!r} does not point to the evidence workspace"
            )
        seen_aliases.add(alias_id)
        bindings.append(
            OpportunityIdentityBinding(
                alias_id=alias_id,
                canonical_opportunity_id=canonical_id,
                confirmed_at=confirmed_at,
                workspace_manifest=relative_manifest,
                workspace_manifest_sha256=expected_hash,
                reason=reason,
            )
        )

    canonical_ids = {binding.canonical_opportunity_id for binding in bindings}
    chained = canonical_ids & {binding.alias_id for binding in bindings}
    if chained:
        raise ValueError("Opportunity identity bindings must not contain chains or cycles")
    if bindings and _check_alias_ownership:
        _evidence, errors, alias_claims = _inventory_workspace_identities(
            pipeline_entries,
            project_root=Path(project_root).resolve(),
            application_root=root.resolve(),
        )
        if errors:
            raise ValueError("Workspace identity inventory is incomplete")
        for binding in bindings:
            if alias_claims.get(binding.alias_id, []) != [binding.canonical_opportunity_id]:
                raise ValueError(f"Active alias ownership is ambiguous for {binding.alias_id}")
    return OpportunityIdentityRegistry(tuple(bindings))


def validate_opportunity_identity_payload(
    payload: object,
    *,
    pipeline: Iterable[object] | Callable[[], Iterable[object]],
    application_root: str | Path,
    project_root: str | Path = ".",
    as_of: datetime | None = None,
    _check_alias_ownership: bool = True,
) -> OpportunityIdentityRegistry:
    """Validate an in-memory binding document through the canonical read-side contract."""

    return load_opportunity_identity_registry(
        "",
        pipeline=pipeline,
        application_root=application_root,
        project_root=project_root,
        as_of=as_of,
        _payload=payload,
        _check_alias_ownership=_check_alias_ownership,
    )


def canonicalize_opportunity_records(
    records: Iterable[Mapping[str, Any]],
    registry: OpportunityIdentityRegistry,
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for record in records:
        row = dict(record)
        identity = str(row.get("opportunity_id") or "").strip()
        canonical = registry.resolve(identity)
        if identity and canonical != identity:
            row["source_opportunity_id"] = identity
            row["opportunity_id"] = canonical
        projected.append(row)
    return projected


__all__ = [
    "OpportunityIdentityBinding",
    "OpportunityIdentityRegistry",
    "canonicalize_opportunity_records",
    "load_opportunity_identity_registry",
    "validate_opportunity_identity_payload",
]
