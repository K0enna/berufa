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
