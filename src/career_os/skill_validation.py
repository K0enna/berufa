"""Validate the versioned public Rolevane skill pack."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
RELEASE_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?")
MANIFEST_FIELDS = {
    "name",
    "contract_version",
    "package_compatibility",
    "maturity",
    "supported_intents",
    "required_inputs",
    "authorities",
    "commands",
    "model_data_classes",
    "no_model_fallback",
    "outputs",
    "confirmations",
    "external_action_prohibitions",
    "failure_recovery",
    "stop_conditions",
}
MATURITY_LEVELS = {"supported", "synthetic-only", "contract-only"}
PUBLIC_V1_MATURITY = "synthetic-only"
PUBLIC_V1_MODEL_DATA_CLASSES = [
    "reviewed_package_source",
    "synthetic_demo",
    "content_free_validation",
]


def _release_version(value: str) -> tuple[int, int, int]:
    match = RELEASE_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("versions must be numeric MAJOR.MINOR or MAJOR.MINOR.PATCH releases")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or "0")


def _validate_compatibility(value: object, package_version: str) -> None:
    if not isinstance(value, str):
        raise ValueError("package_compatibility must be a version range")
    bounds = [part.strip() for part in value.split(",")]
    if len(bounds) != 2 or not bounds[0].startswith(">=") or not bounds[1].startswith("<"):
        raise ValueError("package_compatibility must use >=LOWER,<UPPER")
    lower, upper = _release_version(bounds[0][2:]), _release_version(bounds[1][1:])
    if lower >= upper:
        raise ValueError("package_compatibility must have an increasing, nonempty range")
    if not lower <= _release_version(package_version) < upper:
        raise ValueError(
            f"package_compatibility {value!r} excludes installed Rolevane {package_version}"
        )


def _frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    opening = "---\n"
    if not text.startswith(opening):
        raise ValueError("SKILL.md must start with YAML frontmatter")
    remainder = text[len(opening) :]
    closing = re.search(r"(?m)^---(?:\n|$)", remainder)
    if closing is None:
        raise ValueError("SKILL.md frontmatter is not closed")
    raw = remainder[: closing.start()]
    body = remainder[closing.end() :]
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise ValueError("SKILL.md frontmatter must use one-line key/value fields")
        fields[key.strip()] = value.strip()
    return fields, body


def _string_list(value: object, *, field: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValueError(f"{field} must be a {'possibly empty ' if allow_empty else ''}list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must contain non-empty strings")
    return value


def _validate_openai_metadata(skill: Path) -> list[str]:
    path = skill / "agents" / "openai.yaml"
    if not path.is_file():
        return ["agents/openai.yaml is missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"agents/openai.yaml must be JSON-compatible YAML: {exc}"]
    interface = payload.get("interface") if isinstance(payload, dict) else None
    if not isinstance(interface, dict):
        return ["agents/openai.yaml requires an interface object"]
    required = {"display_name", "short_description", "default_prompt"}
    if set(interface) != required:
        return ["agents/openai.yaml interface must contain exactly the required fields"]
    errors: list[str] = []
    display_name = interface.get("display_name")
    if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 64:
        errors.append("display_name must contain 1-64 characters")
    short = interface.get("short_description")
    if not isinstance(short, str) or not 25 <= len(short.strip()) <= 64:
        errors.append("short_description must contain 25-64 characters")
    prompt = interface.get("default_prompt")
    prompt_pattern = re.compile(rf"\${re.escape(skill.name)}(?![A-Za-z0-9_-])")
    if not isinstance(prompt, str) or not prompt_pattern.search(prompt):
        errors.append(f"default_prompt must mention ${skill.name}")
    return errors


def _validate_skill(skill: Path) -> list[str]:
    path = skill / "SKILL.md"
    if not path.is_file():
        return ["SKILL.md is missing"]
    try:
        frontmatter, body = _frontmatter(path)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return [str(exc)]
    errors: list[str] = []
    if set(frontmatter) != {"name", "description"}:
        errors.append("frontmatter must contain only name and description")
    if frontmatter.get("name") != skill.name:
        errors.append("frontmatter name must match the skill directory")
    if not NAME_PATTERN.fullmatch(skill.name) or len(skill.name) > 64:
        errors.append("skill name must be lowercase hyphen-case and at most 64 characters")
    description = frontmatter.get("description", "")
    if not description or len(description) > 1024:
        errors.append("description must contain 1-1024 characters")
    if not body.strip():
        errors.append("SKILL.md body is empty")
    if "TODO" in body:
        errors.append("SKILL.md contains a TODO placeholder")
    errors.extend(_validate_openai_metadata(skill))
    return errors


def validate_skill_pack(root: str | Path) -> list[str]:
    pack = Path(root)
    manifest_path = pack / "manifest.toml"
    try:
        payload = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [f"manifest.toml is invalid: {exc}"]
    if set(payload) != {"version", "skills"} or payload.get("version") != 1:
        return ["manifest.toml must contain version 1 and skills"]
    entries = payload.get("skills")
    if not isinstance(entries, list) or not entries:
        return ["manifest.toml skills must be a non-empty array"]
    try:
        package_version = version("rolevane")
    except PackageNotFoundError:
        try:
            package_version = version("career-os")
        except PackageNotFoundError:
            package_version = None
    if package_version is None:
        return ["Rolevane package metadata is missing; install the package before skill validation"]
    errors: list[str] = []
    names: list[str] = []
    for index, entry in enumerate(entries):
        label = f"manifest skills[{index}]"
        if not isinstance(entry, dict) or set(entry) != MANIFEST_FIELDS:
            errors.append(f"{label} must contain exactly the required contract fields")
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            errors.append(f"{label}.name must be a string")
            continue
        names.append(name)
        if entry.get("contract_version") != 1:
            errors.append(f"{name}: contract_version must be 1")
        try:
            _validate_compatibility(entry.get("package_compatibility"), package_version)
        except ValueError as exc:
            errors.append(f"{name}: {exc}")
        maturity = entry.get("maturity")
        if not isinstance(maturity, str) or maturity not in MATURITY_LEVELS:
            errors.append(f"{name}: maturity is invalid")
        elif maturity != PUBLIC_V1_MATURITY:
            errors.append(f"{name}: maturity must be {PUBLIC_V1_MATURITY!r} for public v1")
        for field in (
            "supported_intents",
            "required_inputs",
            "authorities",
            "outputs",
            "confirmations",
            "external_action_prohibitions",
            "failure_recovery",
            "stop_conditions",
        ):
            try:
                _string_list(entry.get(field), field=f"{name}.{field}")
            except ValueError as exc:
                errors.append(str(exc))
        for field in ("commands", "model_data_classes"):
            try:
                _string_list(entry.get(field), field=f"{name}.{field}", allow_empty=True)
            except ValueError as exc:
                errors.append(str(exc))
        if entry.get("model_data_classes") != PUBLIC_V1_MODEL_DATA_CLASSES:
            errors.append(
                f"{name}: model_data_classes must be exactly {PUBLIC_V1_MODEL_DATA_CLASSES!r} "
                "for public v1"
            )
        if entry.get("no_model_fallback") is not True:
            errors.append(f"{name}: no_model_fallback must be true for public v1")
    if len(names) != len(set(names)):
        errors.append("manifest.toml skill names must be unique")
    directories = sorted(path.name for path in pack.iterdir() if path.is_dir())
    if sorted(names) != directories:
        errors.append("manifest skill names must exactly match the skill directories")
    for name in directories:
        errors.extend(f"{name}: {error}" for error in _validate_skill(pack / name))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default="skills")
    args = parser.parse_args(argv)
    errors = validate_skill_pack(args.root)
    if errors:
        print("Rolevane public skill validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    count = len([path for path in Path(args.root).iterdir() if path.is_dir()])
    print(f"Validated {count} public Rolevane skills.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
