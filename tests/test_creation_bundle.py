"""Exercise optional host-only distribution and its extracted file route."""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tools.build_companion_bundle import build

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "creation_fictional_host_only"


@pytest.fixture(
    params=["creation_skills_fictional_only", "supplied_content_host_only_v1"]
    + ([PROFILE] if (ROOT / "plugins/rolevane-creation-fictional/plugin.json").is_file() else [])
)
def profile(request):
    return request.param


@pytest.fixture
def installed(tmp_path, profile):
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    assert build(ROOT, first, profile=profile) == build(ROOT, second, profile=profile)
    with pytest.raises(FileExistsError):
        build(ROOT, first, profile=profile)
    installed = tmp_path / "extracted"
    with zipfile.ZipFile(first) as archive:
        assert "mcp.json" not in archive.namelist()
        assert ("plugin.json" in archive.namelist()) == (profile == PROFILE)
        assert json.loads(archive.read("BUNDLE-MANIFEST.json"))["profile"] == profile
        archive.extractall(installed)
    return installed


def test_extracted_host_bundle_keeps_shared_resources(installed):
    # The public CI has no optional formatter libraries; validate the installable
    # advice/prose route independently of PDF availability.
    import re

    skills = list((installed / "skills").glob("*/SKILL.md"))
    assert skills
    for skill in skills:
        links = re.findall(r"\]\((\.\./[^)]+)\)", skill.read_text(encoding="utf-8"))
        assert links
        for link in links:
            resource = (skill.parent / link).resolve()
            assert resource.is_relative_to(installed.resolve())
            assert resource.is_file()


def test_extracted_host_bundle_delivers_files_from_disjoint_roots(tmp_path, installed):
    pytest.importorskip("reportlab")
    from pypdf import PdfReader

    inputs, outputs, third = (tmp_path / name for name in ("inputs", "outputs", "third"))
    for directory in (inputs, outputs, third):
        directory.mkdir()
    source = inputs / "supplied.md"
    source.write_text(
        "# Fictional CV\n\n## Experience\n\n"
        "- Contributed to a shared improvement of about 12% in 2024.\n"
        "- Completed coursework toward a degree.\n",
        encoding="utf-8",
    )
    command = [
        sys.executable,
        str(installed / "scripts/render_documents.py"),
        "--input-root",
        str(inputs),
        "--input",
        str(source),
        "--output-root",
        str(outputs),
        "--output-dir",
        str(outputs / "first"),
        "--format",
        "pdf",
    ]
    result = subprocess.run(command, cwd=third, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["visual_review"] == "required"
    pdf = PdfReader(receipt["files"][0])
    text = "".join(page.extract_text() for page in pdf.pages)
    assert "shared improvement of about 12%" in text
    assert "coursework toward a degree" in text
    # The creation output must be usable by the shared core without loosening
    # its PDF expansion/filter policy; a renderer-only readback missed this.
    from career_os.evidence_vault import EvidenceVault
    from career_os.infrastructure.documents import create_document_service
    from career_os.infrastructure.instances import create_instance_service
    from career_os.public_api import DocumentImportRequest

    workspace = tmp_path / "fictional-workspace"
    create_instance_service(workspace).initialize(actor="local_user")
    EvidenceVault(workspace).record_consent(accepted=True)
    documents = create_document_service(workspace)
    imported = documents.import_version(
        DocumentImportRequest(
            "creation-pdf",
            "Fictional creation output",
            "cv",
            "Extracted installed renderer",
            {"md": source.read_bytes(), "pdf": Path(receipt["files"][0]).read_bytes()},
        ),
        actor="local_user",
    )
    assert documents.inspect(imported.ref).review_state == "unreviewed"
    assert set(documents.inspect(imported.ref).artifacts) == {"md", "pdf"}
    assert list(third.iterdir()) == []
    assert list(inputs.iterdir()) == [source]


@pytest.mark.parametrize("option", ["instance_root", "python_executable"])
def test_host_only_profile_rejects_connected_configuration_before_output(tmp_path, option, profile):
    output = tmp_path / "rejected.zip"
    value = tmp_path if option == "instance_root" else Path(sys.executable)
    with pytest.raises(ValueError):
        build(ROOT, output, profile=profile, **{option: value})
    assert not output.exists()


def test_complete_skills_distribution_preserves_separate_contracts(tmp_path):
    import hashlib

    from career_os.skill_validation import validate_skill_pack

    output = tmp_path / "all-skills.zip"
    build(ROOT, output, profile="supplied_content_host_only_v1", include_workflow_skills=True)
    extracted = tmp_path / "installed"
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("PACKAGE-MANIFEST.json"))
        assert manifest["profile"] == "supplied_content_host_only_v1"
        for member in manifest["files"]:
            assert hashlib.sha256(archive.read(member["path"])).hexdigest() == member["sha256"]
        assert set(archive.namelist()) == {item["path"] for item in manifest["files"]} | {
            "PACKAGE-MANIFEST.json"
        }
        assert not any(
            Path(name).name in {"plugin.json", "mcp.json"} for name in archive.namelist()
        )
        archive.extractall(extracted)
    assert len(list(extracted.rglob("SKILL.md"))) == 9
    lifecycle = extracted / "workflow/docs/INSTANCE_RECOVERY.md"
    assert lifecycle.is_file()
    assert "rolevane-instance" in lifecycle.read_text(encoding="utf-8")
    creation = extracted / "creation"
    assert (
        json.loads((creation / "BUNDLE-MANIFEST.json").read_text())["profile"]
        == manifest["profile"]
    )
    test_extracted_host_bundle_keeps_shared_resources(creation)
    assert validate_skill_pack(extracted / "workflow/skills") == []
    assert (extracted / "workflow/skills/manifest.toml").read_bytes() == (
        (ROOT / "public_repo" if (ROOT / "public_repo").is_dir() else ROOT) / "skills/manifest.toml"
    ).read_bytes().replace(b"\r\n", b"\n")
    second = tmp_path / "reproduced.zip"
    build(ROOT, second, profile=manifest["profile"], include_workflow_skills=True)
    assert output.read_bytes() == second.read_bytes()


def test_complete_skills_distribution_rejects_plugin_composition(tmp_path):
    output = tmp_path / "rejected.zip"
    with pytest.raises(ValueError):
        build(ROOT, output, profile=PROFILE, include_workflow_skills=True)
    assert not output.exists()


def test_standalone_cli_archive_is_installable_closed_and_reproducible(tmp_path):
    """Exercise the public CLI, extracted discovery and exact bytes from disjoint roots."""
    import hashlib
    import re

    profile = "selected_text_standalone_v1"
    outputs, third = tmp_path / "outputs", tmp_path / "unrelated-cwd"
    outputs.mkdir()
    third.mkdir()
    first, second = outputs / "candidate.zip", outputs / "rebuilt.zip"
    command = [sys.executable, str(ROOT / "tools/build_companion_bundle.py")]
    for cwd, destination in [(ROOT, first), (third, second)]:
        result = subprocess.run(
            [*command, str(destination), "--profile", profile],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert (
            json.loads(result.stdout)["sha256"]
            == hashlib.sha256(destination.read_bytes()).hexdigest()
        )
    assert first.read_bytes() == second.read_bytes()
    original = first.read_bytes()
    refused = subprocess.run(
        [*command, str(first), "--profile", profile],
        cwd=third,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert refused.returncode == 1
    assert first.read_bytes() == original
    assert list(third.iterdir()) == []

    # Independent public identifiers, not a copy of the producer's member inventory.
    expected = {
        "career-advice",
        "career-create-content",
        "rolevane-setup",
        "career-evidence-onboarding",
        "career-role-evaluation",
        "career-application-preparation",
        "career-interview-preparation",
        "career-offer-comparison",
        "career-outcome-review",
    }
    installation = tmp_path / "project/.agents/skills/berufa"
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        manifest = json.loads(archive.read("BUNDLE-MANIFEST.json"))
        assert manifest["profile"] == profile
        assert set(manifest["standalone"]) == {
            "product",
            "version",
            "content_boundary",
            "skills",
            "acceptance_report",
        }
        assert manifest["standalone"]["version"] == "0.2.1"
        assert set(manifest["standalone"]["skills"]) == expected
        assert manifest["standalone"]["content_boundary"] in names
        assert isinstance(manifest["standalone"]["acceptance_report"], str)
        assert manifest["standalone"]["acceptance_report"].strip()
        assert set(names) == {item["path"] for item in manifest["files"]} | {"BUNDLE-MANIFEST.json"}
        for member in manifest["files"]:
            assert hashlib.sha256(archive.read(member["path"])).hexdigest() == member["sha256"]
        # No connected/core entry points or the incompatible legacy contract can ship here.
        assert not any(
            Path(name).name in {"plugin.json", "mcp.json", "manifest.toml"}
            or name.startswith(("src/", "workflow/", "config/"))
            for name in names
        )
        archive.extractall(installation)

    skills = list((installation / "skills").glob("*/SKILL.md"))
    assert {path.parent.name for path in skills} == expected
    for skill in skills:
        frontmatter = skill.read_text(encoding="utf-8").split("---", 2)
        assert len(frontmatter) == 3 and not frontmatter[0].strip()
        fields = dict(line.split(":", 1) for line in frontmatter[1].strip().splitlines())
        assert fields["name"].strip() == skill.parent.name
        assert fields["description"].strip()
        interface = (skill.parent / "agents/openai.yaml").read_text(encoding="utf-8")
        assert f"${skill.parent.name}" in interface

    # Inspect every local Markdown resource reference in the delivered archive, not source
    # filenames or prose length. Broken progressive references fail at the consumer boundary.
    for document in installation.rglob("*.md"):
        for link in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if "://" in link or link.startswith("#"):
                continue
            relative = link.split("#", 1)[0]
            resource = (document.parent / relative).resolve()
            assert resource.is_relative_to(installation.resolve()), (document, link)
            assert resource.is_file(), (document, link)


@pytest.mark.parametrize(
    "options",
    [
        {"instance_root": ROOT},
        {"python_executable": Path(sys.executable)},
        {"include_workflow_skills": True},
    ],
)
def test_standalone_profile_refuses_connected_or_legacy_composition_before_output(
    tmp_path, options
):
    output = tmp_path / "rejected.zip"
    with pytest.raises(ValueError):
        build(ROOT, output, profile="selected_text_standalone_v1", **options)
    assert list(tmp_path.iterdir()) == []


def test_standalone_missing_resource_refuses_partial_archive(tmp_path):
    import shutil

    public = tmp_path / "public-source"
    # Copy only actual package owners, leaving private records and the core out of the fixture.
    for directory in ("plugins/rolevane-creation-fictional", "docs"):
        shutil.copytree(ROOT / directory, public / directory)
    for name in ("LICENSE", "NOTICE"):
        shutil.copyfile(ROOT / name, public / name)
    (public / "docs/STANDALONE_PROFILE.md").unlink()
    output = tmp_path / "must-not-exist.zip"
    with pytest.raises(ValueError):
        build(public, output, profile="selected_text_standalone_v1")
    assert not output.exists()
