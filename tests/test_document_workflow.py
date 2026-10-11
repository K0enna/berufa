"""Real free-CLI round trip, independent of the optional desktop."""

import json
from pathlib import Path

from career_os.document_workflow import main
from career_os.evidence_vault import EvidenceVault
from career_os.infrastructure.instances import create_instance_service


def test_free_document_cli_import_review_export_preserves_exact_text(tmp_path, capsys, monkeypatch):
    root = tmp_path / "private"
    create_instance_service(root).initialize(actor="local_user")
    EvidenceVault(root).record_consent(accepted=True)
    source = tmp_path / "supplied.md"
    content = b"# Fictional supplied CV\n\nCoursework only. Degree not completed.\n"
    source.write_bytes(content)
    third = tmp_path / "caller"
    third.mkdir()
    monkeypatch.chdir(third)
    args = ["--instance-root", str(root)]
    assert (
        main(
            [
                *args,
                "import",
                "--input",
                str(source),
                "--request-id",
                "cli-draft",
                "--label",
                "Fictional CV",
                "--purpose",
                "cv",
                "--provenance",
                "Wholly fictional supplied draft",
                "--confirm-local-import",
            ]
        )
        == 0
    )
    imported = json.loads(capsys.readouterr().out)
    ref = [
        "--document-id",
        imported["ref"]["document_id"],
        "--fingerprint",
        imported["ref"]["fingerprint"],
    ]
    assert main([*args, "inspect", *ref]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert (
        inspected["basis_current"] is None
        and inspected["artifacts"]["md"]["text"] == content.decode()
    )
    review = [*args, "review", *ref, "--disposition", "accepted", "--acknowledge-unknown-basis"]
    assert main(review) == 0
    proposal = json.loads(capsys.readouterr().out)
    assert main([*review, "--confirm-review", proposal["confirmation_fingerprint"]]) == 0
    accepted = json.loads(capsys.readouterr().out)
    assert (
        main(
            [
                *args,
                "export",
                *ref,
                "--format",
                "md",
                "--review-id",
                accepted["review"]["review_id"],
                "--confirm-local-export",
            ]
        )
        == 0
    )
    exported = json.loads(capsys.readouterr().out)
    assert (Path(exported["path"]) / "draft.md").read_bytes() == content
    assert {path.name for path in Path(exported["path"]).iterdir()} == {"draft.md"}
    assert source.read_bytes() == content
    assert not (root / "data/runtime/outcome-learning-v2.jsonl").exists()
    replacement = third / "replacement.txt"
    replacement.write_text("Completed coursework; degree not awarded.", encoding="utf-8")
    edit = [
        *args,
        "edit",
        *ref,
        "--request-id",
        "cli-edit",
        "--format",
        "md",
        "--selected-text",
        "Coursework only. Degree not completed.",
        "--replacement-input",
        str(replacement),
    ]
    assert main(edit) == 0
    preview = json.loads(capsys.readouterr().out)
    assert "degree not awarded" in preview["change_summary"]
    assert main([*edit, "--confirm-edit", preview["confirmation_fingerprint"]]) == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved["changed"] and saved["ref"] != imported["ref"]
    assert main([*edit, "--confirm-edit", preview["confirmation_fingerprint"]]) == 0
    assert json.loads(capsys.readouterr().out) == {**saved, "changed": False}
    assert source.read_bytes() == content
