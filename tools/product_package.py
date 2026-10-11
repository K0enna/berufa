"""Validate the explicit participant-package manifest and its import closure."""

from __future__ import annotations

import ast
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MANIFEST_RELATIVE_PATH = "config/product_package.toml"


@dataclass(frozen=True, slots=True)
class ProductPackage:
    name: str
    files: tuple[str, ...]
    resources: tuple[str, ...]
    dependencies: tuple[str, ...]
    scripts: dict[str, str]


def _tracked_paths(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return {item.decode("utf-8") for item in result.stdout.split(b"\0") if item}


def _string_sequence(value: object, *, field: str, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValueError(f"Product package {field} must be a list")
    result = tuple(value)
    if any(not isinstance(item, str) or not item for item in result):
        raise ValueError(f"Product package {field} must contain non-empty strings")
    if len(result) != len(set(result)):
        raise ValueError(f"Product package {field} must not contain duplicates")
    return result


def _validate_source_path(root: Path, relative: str, tracked: set[str]) -> None:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or pure.suffix != ".py"
        or len(pure.parts) < 3
        or pure.parts[:2] != ("src", "career_os")
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError(f"Product package path is unsafe or not package source: {relative}")
    if relative not in tracked:
        raise ValueError(f"Product package path is not tracked: {relative}")
    path = root.joinpath(*pure.parts)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Product package path is not a regular file: {relative}")
    current = path.parent
    source_root = root / "src"
    while current != source_root:
        if current.is_symlink():
            raise ValueError(f"Product package path uses directory indirection: {relative}")
        current = current.parent


def _validate_resource_path(root: Path, relative: str, tracked: set[str]) -> None:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or pure.suffix != ".json"
        or len(pure.parts) < 4
        or pure.parts[:2] != ("src", "career_os")
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError(f"Product package resource is unsafe or unsupported: {relative}")
    if relative not in tracked:
        raise ValueError(f"Product package resource is not tracked: {relative}")
    path = root.joinpath(*pure.parts)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Product package resource is not a regular file: {relative}")
    current = path.parent
    source_root = root / "src"
    while current != source_root:
        if current.is_symlink():
            raise ValueError(f"Product package resource uses directory indirection: {relative}")
        current = current.parent


def _module_path(module: str, files: set[str]) -> str | None:
    stem = f"src/{module.replace('.', '/')}"
    for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
        if candidate in files:
            return candidate
    return None


def _internal_imports(path: str, text: str, available: set[str]) -> set[str]:
    tree = ast.parse(text, filename=path)
    dependencies: set[str] = set()
    parts = path.split("/")
    for depth in range(2, len(parts[:-1]) + 1):
        initializer = "/".join((*parts[:depth], "__init__.py"))
        if initializer != path:
            dependencies.add(initializer)
    module_name = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
    package_parts = (
        module_name.removesuffix(".__init__").split(".")
        if module_name.endswith(".__init__")
        else module_name.split(".")[:-1]
    )
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = max(0, len(package_parts) - (node.level - 1))
                base_parts = package_parts[:keep]
                if node.module:
                    base_parts.extend(node.module.split("."))
                base = ".".join(base_parts)
            else:
                base = node.module or ""
            if base:
                modules.append(base)
                modules.extend(f"{base}.{alias.name}" for alias in node.names)
        for module in modules:
            if not module.startswith("career_os"):
                continue
            dependency = _module_path(module, available)
            if dependency is not None:
                dependencies.add(dependency)
    dependencies.discard(path)
    return dependencies


def _validate_import_closure(root: Path, files: tuple[str, ...], tracked: set[str]) -> None:
    included = set(files)
    available = {path for path in tracked if path.startswith("src/career_os/")}
    for path in files:
        try:
            dependencies = _internal_imports(
                path,
                (root / path).read_text(encoding="utf-8"),
                available,
            )
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            raise ValueError(f"Cannot inspect packaged module {path}: {exc}") from exc
        missing = sorted(dependency for dependency in dependencies if dependency not in included)
        if missing:
            raise ValueError(
                f"Product package module has unpackaged internal imports: "
                f"{path} -> {', '.join(missing)}"
            )


def load_product_package(root: str | Path, *, require_tracked: bool = True) -> ProductPackage:
    repository = Path(root).resolve(strict=True)
    manifest_path = repository / MANIFEST_RELATIVE_PATH
    try:
        payload = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Invalid product package manifest: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Product package manifest requires version 1")
    if set(payload) != {"version", "name", "dependencies", "files", "resources", "scripts"}:
        raise ValueError("Product package manifest must contain exactly the required fields")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Product package name is required")
    files = _string_sequence(payload.get("files"), field="files", allow_empty=False)
    resources = _string_sequence(payload.get("resources"), field="resources", allow_empty=True)
    dependencies = _string_sequence(
        payload.get("dependencies"), field="dependencies", allow_empty=True
    )
    scripts = payload.get("scripts")
    if (
        not isinstance(scripts, dict)
        or not scripts
        or any(
            not isinstance(key, str) or not key or not isinstance(value, str) or ":" not in value
            for key, value in scripts.items()
        )
    ):
        raise ValueError("Product package scripts must map names to module callables")
    tracked = (
        _tracked_paths(repository)
        if require_tracked
        else {
            path.relative_to(repository).as_posix()
            for path in (repository / "src" / "career_os").rglob("*")
            if path.is_file()
        }
    )
    for relative in files:
        _validate_source_path(repository, relative, tracked)
    for relative in resources:
        _validate_resource_path(repository, relative, tracked)
    if "src/career_os/__init__.py" not in files:
        raise ValueError("Product package requires the career_os package initializer")
    for target in scripts.values():
        module = target.split(":", 1)[0]
        if _module_path(module, set(files)) is None:
            raise ValueError(f"Product package script targets an unpackaged module: {target}")
    _validate_import_closure(repository, files, tracked)
    return ProductPackage(
        name=name.strip(),
        files=files,
        resources=resources,
        dependencies=dependencies,
        scripts=dict(scripts),
    )


__all__ = ["MANIFEST_RELATIVE_PATH", "ProductPackage", "load_product_package"]
