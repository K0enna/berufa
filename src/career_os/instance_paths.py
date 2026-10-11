"""Explicit, side-effect-free paths for one Rolevane instance."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class InstancePaths:
    """Absolute roots owned by one participant instance, never by the package."""

    root: Path
    config: Path
    candidate_evidence: Path
    pipeline: Path
    applications: Path
    runtime: Path
    exports: Path
    logs: Path

    def __post_init__(self) -> None:
        for field in (
            "root",
            "config",
            "candidate_evidence",
            "pipeline",
            "applications",
            "runtime",
            "exports",
            "logs",
        ):
            path = Path(getattr(self, field)).expanduser()
            if not path.is_absolute():
                raise ValueError(f"Instance path {field!r} must be absolute: {path!s}")
            object.__setattr__(self, field, path.resolve(strict=False))

    @classmethod
    def from_root(cls, root: str | Path) -> InstancePaths:
        """Describe the conventional instance layout without reading or creating it."""
        resolved = Path(root).expanduser().resolve(strict=False)
        return cls(
            root=resolved,
            config=resolved / "config",
            candidate_evidence=resolved / "context",
            pipeline=resolved / "pipeline",
            applications=resolved / "applications",
            runtime=resolved / "data" / "runtime",
            exports=resolved / "tmp",
            logs=resolved / "data" / "runtime" / "logs",
        )

    def resolve(self, value: str | Path) -> Path:
        """Resolve an instance-relative path while preserving explicit absolute overrides."""
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        if path.drive:
            raise ValueError(f"Instance-relative path cannot include a drive: {value!s}")
        candidate = (self.root / path).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(
                f"Instance-relative path escapes the instance root: {value!s}"
            ) from exc
        return candidate

    @property
    def production_database(self) -> Path:
        return self.runtime / "career.db"

    @property
    def shadow_database(self) -> Path:
        return self.runtime / "shadow" / "source-shadow.db"


def rebase_namespace_paths(
    namespace: object,
    fields: Iterable[str],
    *,
    root_field: str = "instance_root",
) -> InstancePaths | None:
    """Rebase selected relative namespace fields when an instance root was supplied."""
    raw_root = getattr(namespace, root_field, None)
    if raw_root in (None, ""):
        return None
    paths = InstancePaths.from_root(raw_root)
    for field in fields:
        value = getattr(namespace, field, None)
        if value in (None, "", "-") or not isinstance(value, str | Path):
            continue
        original = Path(value)
        resolved = paths.resolve(value)
        if not original.is_absolute():
            setattr(namespace, field, str(resolved))
    setattr(namespace, root_field, str(paths.root))
    return paths
