"""Build explicit plugin or Skills-only bundles without publishing or connecting data."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "plugins/career-os"
# Explicit source/export membership. No directory walk, private history or runtime data.
MEMBERS = (
    (f"{PLUGIN}/plugin.json", "plugin.json"),
    (f"{PLUGIN}/mcp.json", "mcp.json"),
    (f"{PLUGIN}/README.md", "README.md"),
    (f"{PLUGIN}/scripts/career_status.py", "scripts/career_status.py"),
    (
        f"{PLUGIN}/skills/career-connection-status/SKILL.md",
        "skills/career-connection-status/SKILL.md",
    ),
    ("public_repo/LICENSE", "LICENSE"),
    ("public_repo/NOTICE", "NOTICE"),
)
FICTIONAL_PLUGIN = "plugins/rolevane-fictional"
FICTIONAL_SKILLS = (
    "career-connect-profile",
    "career-assess-supplied-role",
    "career-resume-work",
    "career-prepare-interview",
    "career-compare-offers",
    "career-manage-evidence-data",
)
FICTIONAL_MEMBERS = (
    *(
        (f"{FICTIONAL_PLUGIN}/{name}", name)
        for name in (
            "plugin.json",
            "mcp.json",
            "README.md",
            "scripts/career_companion.py",
            "references/workflows.md",
        )
    ),
    *(
        (f"{FICTIONAL_PLUGIN}/skills/{name}/SKILL.md", f"skills/{name}/SKILL.md")
        for name in FICTIONAL_SKILLS
    ),
    (f"{PLUGIN}/scripts/career_status.py", "scripts/career_status.py"),
    ("public_repo/LICENSE", "LICENSE"),
    ("public_repo/NOTICE", "NOTICE"),
)
CREATION_PLUGIN = "plugins/rolevane-creation-fictional"
CREATION_MEMBERS = (
    *(
        (f"{CREATION_PLUGIN}/{name}", name)
        for name in (
            "plugin.json",
            "README.md",
            "scripts/render_documents.py",
            "references/writing.md",
            "references/documents.md",
            "references/skill-gaps.md",
            "skills/career-advice/SKILL.md",
            "skills/career-advice/agents/openai.yaml",
            "skills/career-create-content/SKILL.md",
            "skills/career-create-content/agents/openai.yaml",
            "examples/fictional-input.md",
            "examples/advice.md",
            "examples/cv-en-v1.md",
            "examples/cv-en-v2.md",
            "examples/application-answer-en-v1.md",
            "examples/application-answer-en-v2.md",
            "examples/application-answer-de-v1.md",
            "examples/application-answer-de-v2.md",
            "examples/interview-v1.md",
            "examples/interview-v2.md",
            "examples/standalone-edit-and-critique.md",
            "examples/recruiter-inquiry.md",
            "examples/correction-and-basis-notes.md",
        )
    ),
    ("public_repo/LICENSE", "LICENSE"),
    ("public_repo/NOTICE", "NOTICE"),
)
CREATION_SKILLS_MEMBERS = (
    *(item for item in CREATION_MEMBERS if item[1] not in {"plugin.json", "README.md"}),
    ("public_repo/docs/CREATION_SKILLS.md", "README.md"),
)
SUPPLIED_PROFILE = "supplied_content_host_only_v1"
SUPPLIED_MEMBERS = (
    *CREATION_SKILLS_MEMBERS,
    ("public_repo/docs/SUPPLIED_CONTENT_PROFILE.md", "references/supplied-content-profile.md"),
)
WORKFLOW_SKILLS = (
    "rolevane-setup",
    "career-evidence-onboarding",
    "career-role-evaluation",
    "career-application-preparation",
    "career-interview-preparation",
    "career-offer-comparison",
    "career-outcome-review",
)
WORKFLOW_MEMBERS = (
    ("public_repo/skills/manifest.toml", "workflow/skills/manifest.toml"),
    ("public_repo/docs/INSTANCE_RECOVERY.md", "workflow/docs/INSTANCE_RECOVERY.md"),
    *(
        (f"public_repo/skills/{name}/{file}", f"workflow/skills/{name}/{file}")
        for name in WORKFLOW_SKILLS
        for file in ("SKILL.md", "agents/openai.yaml")
    ),
    ("public_repo/docs/BERUFA_SKILLS.md", "README.md"),
    ("public_repo/LICENSE", "LICENSE"),
    ("public_repo/NOTICE", "NOTICE"),
)
STANDALONE_PROFILE = "selected_text_standalone_v1"
# Useful conversational counterparts; never replace the core's legacy Skills/manifest.
STANDALONE_MEMBERS = (
    *(item for item in SUPPLIED_MEMBERS if item[1] != "README.md"),
    ("public_repo/docs/STANDALONE_SKILLS.md", "README.md"),
    ("public_repo/docs/STANDALONE_PROFILE.md", "references/standalone-profile.md"),
    *(
        (f"{CREATION_PLUGIN}/standalone-skills/{name}/{file}", f"skills/{name}/{file}")
        for name in WORKFLOW_SKILLS
        for file in ("SKILL.md", "agents/openai.yaml")
    ),
    (f"{CREATION_PLUGIN}/examples/standalone-workflows.md", "examples/standalone-workflows.md"),
    (
        f"{CREATION_PLUGIN}/examples/standalone-runtime-scenarios.json",
        "examples/standalone-runtime-scenarios.json",
    ),
)
STANDALONE_PLUGIN_MEMBERS = (
    *(item for item in STANDALONE_MEMBERS if item[1] != "README.md"),
    ("plugins/berufa-career/README.md", "README.md"),
    ("plugins/berufa-career/plugin.json", "plugin.json"),
    ("plugins/berufa-career/assets/icon.svg", "assets/icon.svg"),
)
HOST_ONLY_PROFILES = {
    "creation_fictional_host_only",
    "creation_skills_fictional_only",
    SUPPLIED_PROFILE,
    STANDALONE_PROFILE,
}


def build(
    root: Path,
    output: Path,
    *,
    python_executable: Path | None = None,
    profile: str = "content_free_status_only",
    instance_root: Path | None = None,
    include_workflow_skills: bool = False,
    as_plugin: bool = False,
) -> dict[str, object]:
    if as_plugin and profile != STANDALONE_PROFILE:
        raise ValueError("The plugin wrapper requires the selected-text standalone profile")
    if profile not in {
        "content_free_status_only",
        "sealed_fictional_only",
        "creation_fictional_host_only",
        "creation_skills_fictional_only",
        SUPPLIED_PROFILE,
        STANDALONE_PROFILE,
    }:
        raise ValueError("Unsupported companion profile")
    if include_workflow_skills and profile != SUPPLIED_PROFILE:
        raise ValueError("The complete Skills pack requires the supplied-text Skills-only profile")
    if profile in HOST_ONLY_PROFILES and python_executable is not None:
        raise ValueError("The host-only creation profile has no configured MCP interpreter")
    if instance_root is not None and (
        profile != "sealed_fictional_only" or not instance_root.is_absolute()
    ):
        raise ValueError("An absolute fictional instance requires the fictional profile")
    if python_executable is not None and (
        not python_executable.is_absolute() or not python_executable.is_file()
    ):
        raise ValueError("Selected Python must be an existing absolute file path")
    inputs = []
    members = {
        "content_free_status_only": MEMBERS,
        "sealed_fictional_only": FICTIONAL_MEMBERS,
        "creation_fictional_host_only": CREATION_MEMBERS,
        "creation_skills_fictional_only": CREATION_SKILLS_MEMBERS,
        SUPPLIED_PROFILE: SUPPLIED_MEMBERS,
        STANDALONE_PROFILE: STANDALONE_MEMBERS,
    }[profile]
    if as_plugin:
        members = STANDALONE_PLUGIN_MEMBERS
    for source, destination in (*members, *(WORKFLOW_MEMBERS if include_workflow_skills else ())):
        path = root / source
        # The private source owner and its sanitized public export are both supported callers.
        if (
            profile in {"creation_skills_fictional_only", SUPPLIED_PROFILE, STANDALONE_PROFILE}
            and source.startswith("public_repo/")
            and not ((root / "public_repo").exists() or (root / "public_repo").is_symlink())
        ):
            path = root / source.removeprefix("public_repo/")
        if any(parent.is_symlink() for parent in [path, *path.parents]) or not path.is_file():
            raise ValueError("Bundle input must be a regular file without indirection")
        # Every reviewed member is text. Canonical LF makes Windows/POSIX checkouts agree.
        data = path.read_bytes().replace(b"\r\n", b"\n")
        if destination == "mcp.json" and (
            python_executable is not None or instance_root is not None
        ):
            declaration = json.loads(data)
            # Do not resolve a venv symlink to its base interpreter: that loses its environment.
            server_name = (
                "rolevane-status" if profile == "content_free_status_only" else "rolevane-fictional"
            )
            server = declaration["mcpServers"][server_name]
            # Agent Plugins requires a bare executable or contained plugin path. Select the
            # external venv through this server's process PATH, never the host/global PATH.
            server["env"] = {}
            if python_executable is not None:
                server["command"] = python_executable.name
                server["env"]["PATH"] = str(python_executable.parent)
            if instance_root is not None:
                # Trusted machine-local composition only. Never inspect or initialize it.
                server["env"]["ROLEVANE_FICTIONAL_INSTANCE"] = str(instance_root)
            data = (json.dumps(declaration, indent=2) + "\n").encode()
        inputs.append((destination, data))
    creation_inputs = inputs[: len(members)]
    identity = {
        "format": 1,
        "profile": profile,
        "files": [
            {"path": name, "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in creation_inputs
        ],
    }
    if profile == STANDALONE_PROFILE:
        identity["standalone"] = {
            "product": "Berufa",
            "version": "0.2.3",
            "content_boundary": "references/standalone-profile.md",
            "skills": ["career-advice", "career-create-content", *WORKFLOW_SKILLS],
            "acceptance_report": "See the accompanying release acceptance report.",
        }
    bundle_manifest = (json.dumps(identity, indent=2) + "\n").encode()
    if include_workflow_skills:
        # Keep the creation profile adjacent to its readers and the seven-Skill
        # manifest adjacent to its validator. Composition grants no new data access.
        inputs = [(f"creation/{name}", data) for name, data in creation_inputs] + inputs[
            len(members) :
        ]
        inputs.append(("creation/BUNDLE-MANIFEST.json", bundle_manifest))
        package_manifest = {
            "format": 1,
            "profile": profile,
            "workflow_contract": "workflow/skills/manifest.toml",
            "files": [
                {"path": name, "sha256": hashlib.sha256(data).hexdigest()} for name, data in inputs
            ],
        }
        inputs.append(
            ("PACKAGE-MANIFEST.json", (json.dumps(package_manifest, indent=2) + "\n").encode())
        )
    else:
        inputs.append(("BUNDLE-MANIFEST.json", bundle_manifest))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream, zipfile.ZipFile(stream, "w", zipfile.ZIP_STORED) as archive:
        for name, data in sorted(inputs):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return {"sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "members": len(inputs)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New explicit bundle ZIP; never overwritten")
    parser.add_argument(
        "--profile",
        choices=(
            "content_free_status_only",
            "sealed_fictional_only",
            "creation_fictional_host_only",
            "creation_skills_fictional_only",
            SUPPLIED_PROFILE,
            STANDALONE_PROFILE,
        ),
        default="content_free_status_only",
    )
    parser.add_argument(
        "--instance-root",
        type=Path,
        help="Absolute fictional root for a machine-local fictional bundle; never inspected",
    )
    parser.add_argument(
        "--python-executable",
        type=Path,
        help="Absolute isolated Python for a machine-local bundle; omitted for portable distribution",
    )
    parser.add_argument(
        "--as-plugin",
        action="store_true",
        help="Wrap the selected-text standalone Skills as the Berufa Career desktop plugin",
    )
    parser.add_argument(
        "--include-workflow-skills",
        action="store_true",
        help="Include all seven bounded workflow Skills alongside the two creation Skills",
    )
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                build(
                    ROOT,
                    args.output,
                    python_executable=args.python_executable,
                    profile=args.profile,
                    instance_root=args.instance_root,
                    include_workflow_skills=args.include_workflow_skills,
                    as_plugin=args.as_plugin,
                )
            )
        )
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Companion bundle failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
