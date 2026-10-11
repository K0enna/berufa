from __future__ import annotations

import hashlib
import io
import os
import struct
import zipfile
import zlib
from pathlib import Path

import pypdf
import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NullObject,
    NumberObject,
    TextStringObject,
)

from career_os import document_intake
from career_os.document_intake import (
    DocumentIntakeLimits,
    extract_candidate_document,
    extract_document_bytes,
)


def test_output_bytes_allow_only_ordinary_pdf_links_and_preserve_source_rejection(tmp_path):
    path = tmp_path / "draft.pdf"
    _write_text_pdf(path, "Fictional retained draft", external_action=True)
    content = path.read_bytes()
    with pytest.raises(ValueError, match="active"):
        extract_candidate_document(path)
    accepted = extract_document_bytes(content, filename="draft.pdf", allow_safe_hyperlinks=True)
    assert accepted.text == "Fictional retained draft"
    assert accepted.original_content == content
    reader = pypdf.PdfReader(io.BytesIO(content))
    action = reader.pages[0]["/Annots"][0]["/A"]
    for target in (
        "file:///secret",
        "https://@example.invalid",
        "https://example.invalid:99999",
        "https://example.invalid/\npath",
        "https://.",
        "https://example.invalid:",
        "https://%65xample.invalid",
    ):
        action[NameObject("/URI")] = TextStringObject(target)
        assert document_intake._pdf_has_active_content(
            reader, max_objects=100, max_items=1000, allow_safe_hyperlinks=True
        )
    action[NameObject("/URI")] = TextStringObject("https://example.invalid")
    action[NameObject("/Next")] = DictionaryObject({NameObject("/S"): NameObject("/JavaScript")})
    assert document_intake._pdf_has_active_content(
        reader, max_objects=100, max_items=1000, allow_safe_hyperlinks=True
    )
    del action["/Next"]
    reader.root_object[NameObject("/OpenAction")] = action
    assert document_intake._pdf_has_active_content(
        reader, max_objects=100, max_items=1000, allow_safe_hyperlinks=True
    )


@pytest.mark.parametrize(
    "kind,target,accepted",
    [
        ("hyperlink", "https://example.invalid/profile", True),
        ("image", "https://example.invalid/image", False),
        ("hyperlink", "javascript:alert(1)", False),
        ("hyperlink", "https://user@example.invalid", False),
    ],
)
def test_output_docx_external_relationship_is_narrowly_validated(tmp_path, kind, target, accepted):
    path = tmp_path / "draft.docx"
    rels = f'<Relationships xmlns="{document_intake._PACKAGE_RELATIONSHIP_NAMESPACE}"><Relationship Id="link" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}" Target="{target}" TargetMode="External"/></Relationships>'.encode()
    _write_docx(path, extra={"word/_rels/document.xml.rels": rels})
    with pytest.raises(ValueError, match="external"):
        extract_candidate_document(path)
    if accepted:
        result = extract_document_bytes(
            path.read_bytes(), filename="draft.docx", allow_safe_hyperlinks=True
        )
        assert "Synthetic services leader" in result.text
    else:
        with pytest.raises(ValueError, match="external"):
            extract_document_bytes(
                path.read_bytes(), filename="draft.docx", allow_safe_hyperlinks=True
            )


def test_bytes_intake_enforces_its_own_size_and_basename_limits():
    for content in (b"", b"ab"):
        with pytest.raises(ValueError, match="size"):
            extract_document_bytes(
                content, filename="draft.txt", limits=DocumentIntakeLimits(max_document_bytes=1)
            )
    with pytest.raises(ValueError, match="basename"):
        extract_document_bytes(b"Fictional", filename="../draft.txt")


@pytest.mark.parametrize("key,value", [("/S", "/GoToR"), ("/Subtype", "/RichMedia")])
def test_pdf_active_content_resolves_indirect_action_and_annotation_names(key, value) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer._root_object[NameObject("/SyntheticAction")] = DictionaryObject(
        {
            NameObject(key): writer._add_object(NameObject(value)),
            NameObject("/F"): TextStringObject("other.pdf"),
            NameObject("/D"): TextStringObject("destination"),
        }
    )
    stream = io.BytesIO()
    writer.write(stream)
    reader = pypdf.PdfReader(io.BytesIO(stream.getvalue()))
    assert document_intake._pdf_has_active_content(reader, max_objects=100, max_items=1000)


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO replacement semantics")
def test_fifo_replacement_is_rejected_without_blocking_open(tmp_path, monkeypatch) -> None:
    path = tmp_path / "candidate.txt"
    path.write_text("Synthetic source", encoding="utf-8")
    original_open = os.open

    def replace_before_open(selected, flags, *args, **kwargs):
        if Path(selected) == path:
            path.unlink()
            os.mkfifo(path)
            # Fail before a blocking syscall on a regressed implementation.
            assert flags & os.O_NONBLOCK
        return original_open(selected, flags, *args, **kwargs)

    monkeypatch.setattr(document_intake.os, "open", replace_before_open)
    with pytest.raises(ValueError, match="regular non-link"):
        document_intake._read_bounded_regular_file(path, DocumentIntakeLimits())


CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
PACKAGE_RELATIONSHIPS = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
DOCUMENT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Synthetic services leader.</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Team outcome: 18 percent.</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
  </w:body>
</w:document>
"""


def _write_text_pdf(
    path: Path,
    text: str,
    *,
    active: bool = False,
    inert_open_action: bool = False,
    external_action: bool = False,
    xfa: bool = False,
    structural_attributes: bool = False,
    padding_bytes: int = 0,
) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(
        f"BT /F1 11 Tf 72 720 Td ({safe_text}) Tj ET".encode("latin-1")
        + (b"\n%" + b"x" * padding_bytes if padding_bytes else b"")
    )
    page.replace_contents(stream.flate_encode() if padding_bytes else stream)
    if active:
        writer._root_object[NameObject("/OpenAction")] = DictionaryObject(  # noqa: SLF001
            {
                NameObject("/S"): NameObject("/JavaScript"),
                NameObject("/JS"): TextStringObject("app.alert('synthetic')"),
            }
        )
    if inert_open_action:
        writer._root_object[NameObject("/OpenAction")] = ArrayObject(  # noqa: SLF001
            [page.indirect_reference, NameObject("/Fit")]
        )
    if external_action:
        page[NameObject("/Annots")] = ArrayObject(
            [
                DictionaryObject(
                    {
                        NameObject("/Subtype"): NameObject("/Link"),
                        NameObject("/A"): DictionaryObject(
                            {
                                NameObject("/S"): NameObject("/URI"),
                                NameObject("/URI"): TextStringObject(
                                    "https://example.invalid/synthetic"
                                ),
                            }
                        ),
                    }
                )
            ]
        )
    if structural_attributes:
        writer._root_object[NameObject("/StructTreeRoot")] = DictionaryObject(  # noqa: SLF001
            {
                NameObject("/A"): DictionaryObject(
                    {
                        NameObject("/O"): NameObject("/Layout"),
                        NameObject("/TextAlign"): NameObject("/Center"),
                    }
                )
            }
        )
    if xfa:
        writer._root_object[NameObject("/AcroForm")] = DictionaryObject(  # noqa: SLF001
            {NameObject("/XFA"): TextStringObject("synthetic-xfa")}
        )
    with path.open("wb") as handle:
        writer.write(handle)


def _write_docx(
    path: Path,
    *,
    document: bytes = DOCUMENT_XML,
    main_part: str = "word/document.xml",
    relationships: bytes = PACKAGE_RELATIONSHIPS,
    content_types: bytes = CONTENT_TYPES,
    extra: dict[str, bytes] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
) -> None:
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr(main_part, document)
        for name, content in (extra or {}).items():
            archive.writestr(name, content)


def test_text_and_markdown_preserve_original_identity_and_line_locations(
    tmp_path: Path,
) -> None:
    content = b"\nSynthetic role\n\nSynthetic team outcome\n"
    first = tmp_path / "career.txt"
    renamed = tmp_path / "renamed.txt"
    changed = tmp_path / "changed.md"
    first.write_bytes(content)
    renamed.write_bytes(content)
    changed.write_bytes(content + b"Changed\n")

    result = extract_candidate_document(first)
    renamed_result = extract_candidate_document(renamed)
    changed_result = extract_candidate_document(changed)

    assert result.input_format == "text"
    assert result.media_type == "text/plain"
    assert result.original_sha256 == hashlib.sha256(content).hexdigest()
    assert result.original_content == content
    assert renamed_result.original_sha256 == result.original_sha256
    assert changed_result.original_sha256 != result.original_sha256
    assert result.text == content.decode("utf-8")
    assert [location.ordinal for location in result.locations] == [1, 2, 3, 4]
    assert result.provenance()["model_calls"] is False
    assert "text" not in result.provenance()
    assert "original_content" not in result.provenance()


def test_pdf_extracts_text_with_page_provenance_and_rejects_active_or_image_only(
    tmp_path: Path,
) -> None:
    source = tmp_path / "career.pdf"
    active = tmp_path / "active.pdf"
    inert_open_action = tmp_path / "inert-open-action.pdf"
    external_action = tmp_path / "external-action.pdf"
    xfa = tmp_path / "xfa.pdf"
    scan = tmp_path / "scan.pdf"
    tagged = tmp_path / "tagged.pdf"
    _write_text_pdf(source, "Synthetic customer leadership evidence.")
    _write_text_pdf(active, "Synthetic active document.", active=True)
    _write_text_pdf(
        inert_open_action,
        "Synthetic inert destination.",
        inert_open_action=True,
    )
    _write_text_pdf(
        external_action,
        "Synthetic external-action document.",
        external_action=True,
    )
    _write_text_pdf(
        tagged,
        "Synthetic tagged document.",
        structural_attributes=True,
    )
    _write_text_pdf(xfa, "Synthetic XFA document.", xfa=True)
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with scan.open("wb") as handle:
        writer.write(handle)

    result = extract_candidate_document(source)

    assert result.input_format == "pdf"
    assert result.page_count == 1
    assert "Synthetic customer leadership evidence" in result.text
    assert [location.to_dict() for location in result.locations] == [
        {"kind": "page", "ordinal": 1, "start_line": 1, "end_line": 1}
    ]
    assert "Synthetic tagged document" in extract_candidate_document(tagged).text
    assert "Synthetic inert destination" in extract_candidate_document(inert_open_action).text
    with pytest.raises(ValueError, match="active or embedded"):
        extract_candidate_document(active)
    with pytest.raises(ValueError, match="active or embedded"):
        extract_candidate_document(external_action)
    with pytest.raises(ValueError, match="active or embedded"):
        extract_candidate_document(xfa)
    with pytest.raises(ValueError, match="OCR is disabled"):
        extract_candidate_document(scan)


def test_pdf_rejects_unsupported_content_filter_chains_with_compatibility_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unsupported-filter-chain.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    stream.set_data(b"BT (Synthetic filter chain.) Tj ET")
    encoded = stream.flate_encode()
    encoded[NameObject("/Filter")] = ArrayObject(
        [NameObject("/FlateDecode"), NameObject("/ASCII85Decode")]
    )
    page.replace_contents(encoded)
    with source.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(ValueError, match="unsupported content-stream filter"):
        extract_candidate_document(source)


def test_pdf_rejects_decoded_content_expansion_before_text_extraction(tmp_path: Path) -> None:
    source = tmp_path / "compressed.pdf"
    nested_form = tmp_path / "compressed-form.pdf"
    _write_text_pdf(
        source,
        "Synthetic compressed document.",
        padding_bytes=8_192,
    )

    with pytest.raises(ValueError, match="decoded content exceeds"):
        extract_candidate_document(
            source,
            limits=DocumentIntakeLimits(max_pdf_decoded_content_bytes=1_024),
        )

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    form = DecodedStreamObject()
    form.set_data(b"q\n%" + b"x" * 8_192)
    form[NameObject("/Type")] = NameObject("/XObject")
    form[NameObject("/Subtype")] = NameObject("/Form")
    form[NameObject("/BBox")] = ArrayObject(
        [NumberObject(0), NumberObject(0), NumberObject(1), NumberObject(1)]
    )
    form[NameObject("/Resources")] = DictionaryObject()
    form_reference = writer._add_object(form.flate_encode())  # noqa: SLF001
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/Fm0"): form_reference})}
    )
    page_stream = DecodedStreamObject()
    page_stream.set_data(b"q /Fm0 Do Q")
    page.replace_contents(page_stream)
    with nested_form.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(ValueError, match="decoded content exceeds"):
        extract_candidate_document(
            nested_form,
            limits=DocumentIntakeLimits(max_pdf_decoded_content_bytes=1_024),
        )


def test_pdf_decode_params_reject_predictors_before_pypdf_decode_and_allow_identity() -> None:
    class SyntheticStream(dict[str, object]):
        def __init__(self, decode_parms: object) -> None:
            super().__init__({"/Filter": "/FlateDecode", "/DecodeParms": decode_parms})
            self._data = zlib.compress(b"q Q")

        def get_data(self) -> bytes:
            raise AssertionError("pypdf decode must not run during bounded preflight")

    identity_parms = (
        None,
        NullObject(),
        DictionaryObject(),
        DictionaryObject({NameObject("/Predictor"): NumberObject(1)}),
        ArrayObject([NullObject()]),
        ArrayObject([DictionaryObject({NameObject("/Predictor"): NumberObject(1)})]),
    )
    for parms in identity_parms:
        assert (
            document_intake._pdf_content_stream_data(
                SyntheticStream(parms),
                limit=10,
            )
            == b"q Q"
        )

    for parms in (
        DictionaryObject({NameObject("/Predictor"): NumberObject(15)}),
        ArrayObject([DictionaryObject({NameObject("/Predictor"): NumberObject(2)})]),
    ):
        with pytest.raises(ValueError, match="DecodeParms"):
            document_intake._pdf_content_stream_data(SyntheticStream(parms), limit=10)


def test_pdf_content_parser_ignores_inline_image_operators_and_bounds_operations() -> None:
    for image_data in (b"/Fm Do", b"( /Fm Do"):
        content = b"BI /W 1 /H 1 /BPC 8 /CS /G ID " + image_data + b" EI Q /Fm Do"
        assert list(
            document_intake._pdf_form_invocations(
                content,
                None,
                operation_budget=[0],
                operation_limit=10,
            )
        ) == ["/Fm"]

    with pytest.raises(ValueError, match="content operations"):
        list(
            document_intake._pdf_form_invocations(
                b"q q q",
                None,
                operation_budget=[0],
                operation_limit=2,
            )
        )


def test_pdf_split_contents_form_invocation_is_counted_before_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "split-contents.pdf"
    _write_text_pdf(source, "Synthetic classic-xref control.")

    class SyntheticStream(dict[str, object]):
        def __init__(self, data: bytes, **values: object) -> None:
            super().__init__(values)
            self._data = data

        def get_data(self) -> bytes:
            raise AssertionError("pypdf stream decode must not precede work preflight")

    class SyntheticPage(dict[str, object]):
        def extract_text(self) -> str:
            raise AssertionError("text extraction must not run before Form work preflight")

    form = SyntheticStream(b"q Q", **{"/Subtype": "/Form", "/Resources": {}})
    page = SyntheticPage(
        {
            "/Contents": [SyntheticStream(b"/Fm"), SyntheticStream(b"Do /Fm Do")],
            "/Resources": {"/XObject": {"/Fm": form}},
        }
    )
    reader = type(
        "SyntheticReader",
        (),
        {"is_encrypted": False, "pages": [page], "root_object": {}},
    )()
    monkeypatch.setattr(pypdf, "PdfReader", lambda *_args, **_kwargs: reader)

    with pytest.raises(ValueError, match="Form execution"):
        document_intake._inspect_pdf_bytes(
            source.read_bytes(),
            limits=DocumentIntakeLimits(),
            reject_active_content=False,
            work_limits=document_intake._PdfWorkLimits(form_invocations=1),
        )


def test_pdf_work_budgets_reject_before_text_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "work-budget.pdf"
    _write_text_pdf(source, "Synthetic classic-xref control.")

    class SyntheticStream(dict[str, object]):
        def __init__(self, data: bytes, **values: object) -> None:
            super().__init__(values)
            self._data = data

        def get_data(self) -> bytes:
            raise AssertionError("preflight must inspect raw stream bytes")

    class SyntheticPage(dict[str, object]):
        def extract_text(self) -> str:
            raise AssertionError("text extraction must not run before work preflight")

    cases = [
        (
            SyntheticPage(
                {
                    "/Contents": SyntheticStream(
                        zlib.compress(b"q Q"),
                        **{
                            "/Filter": "/FlateDecode",
                            "/DecodeParms": DictionaryObject(
                                {NameObject("/Predictor"): NumberObject(15)}
                            ),
                        },
                    ),
                    "/Resources": {},
                }
            ),
            document_intake._PdfWorkLimits(),
            "DecodeParms",
        ),
        (
            SyntheticPage(
                {
                    "/Contents": SyntheticStream(b""),
                    "/Resources": {
                        "/Font": {
                            "/F1": {
                                "/DescendantFonts": [{"/W": [0, 1_000, 500]}],
                            }
                        }
                    },
                }
            ),
            document_intake._PdfWorkLimits(font_width_entries=10),
            "font widths",
        ),
        (
            SyntheticPage(
                {
                    "/Contents": SyntheticStream(b"/Fm Do /Fm Do /Fm Do"),
                    "/Resources": {
                        "/XObject": {
                            "/Fm": SyntheticStream(
                                b"q Q",
                                **{
                                    "/Subtype": "/Form",
                                    "/Resources": {},
                                },
                            )
                        }
                    },
                }
            ),
            document_intake._PdfWorkLimits(form_invocations=2),
            "Form execution",
        ),
        (
            SyntheticPage(
                {
                    "/Contents": SyntheticStream(b"BT /F1 11 Tf (x) Tj ET"),
                    "/Resources": {
                        "/Font": {
                            "/F1": {
                                "/ToUnicode": SyntheticStream(
                                    b"1 beginbfchar\n<00> <0041004200430044>\nendbfchar"
                                )
                            }
                        }
                    },
                }
            ),
            document_intake._PdfWorkLimits(cmap_value_characters=2),
            "ToUnicode mapping",
        ),
        (
            SyntheticPage(
                {
                    "/Contents": SyntheticStream(b"BT /F1 11 Tf (x) Tj ET"),
                    "/Resources": {
                        "/Font": {
                            "/F1": {
                                "/ToUnicode": SyntheticStream(
                                    b"1 beginbfrange\n<00 00> <ff ff> <00 00>\nendbfrange"
                                )
                            }
                        }
                    },
                }
            ),
            document_intake._PdfWorkLimits(cmap_entries=10),
            "entry limit",
        ),
        (
            SyntheticPage(
                {
                    "/Contents": SyntheticStream(b"BT /F1 11 Tf (x) Tj ET"),
                    "/Resources": {
                        "/Font": {
                            "/F1": {
                                "/ToUnicode": SyntheticStream(
                                    b"1 beginbfchar\n<00> <00410042>\nendbfchar"
                                )
                            }
                        }
                    },
                }
            ),
            document_intake._PdfWorkLimits(text_work=10),
            "mapped text",
        ),
    ]

    for page, work_limits, message in cases:
        reader = type(
            "SyntheticReader",
            (),
            {"is_encrypted": False, "pages": [page], "root_object": {}},
        )()
        monkeypatch.setattr(pypdf, "PdfReader", lambda *_args, _reader=reader, **_kwargs: _reader)
        with pytest.raises(ValueError, match=message):
            document_intake._inspect_pdf_bytes(
                source.read_bytes(),
                limits=DocumentIntakeLimits(),
                reject_active_content=False,
                work_limits=work_limits,
            )


def test_pdf_cmap_range_parser_preserves_multiple_bfchar_mappings() -> None:
    cmap = b"3 beginbfchar\n<01> <0041>\n<02> <0042>\n<03> <0043>\nendbfchar\n"

    assert document_intake._pdf_cmap_stats(
        cmap,
        work_limits=document_intake._PdfWorkLimits(cmap_entries=3),
    ) == (3, 1)


def test_pdf_direct_value_budget_rejects_before_text_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "traversal-budget.pdf"
    _write_text_pdf(source, "Synthetic classic-xref control.")

    class SyntheticPage(dict[str, object]):
        def extract_text(self) -> str:
            raise AssertionError("text extraction must not run before traversal preflight")

    reader = type(
        "SyntheticReader",
        (),
        {"is_encrypted": False, "pages": [SyntheticPage()], "root_object": {}},
    )()
    monkeypatch.setattr(pypdf, "PdfReader", lambda *_args, **_kwargs: reader)

    with pytest.raises(ValueError, match="direct-value traversal"):
        document_intake._inspect_pdf_bytes(
            source.read_bytes(),
            limits=DocumentIntakeLimits(),
            reject_active_content=True,
            work_limits=document_intake._PdfWorkLimits(traversal_items=1),
        )


def test_pdf_direct_value_fanout_rejects_before_pending_queue_growth() -> None:
    class LargeChildren:
        def __len__(self) -> int:
            return 10_000

        def __iter__(self):
            raise AssertionError("large child collection must not enter the pending queue")

    class LargeDirectDictionary(dict[str, object]):
        def values(self):
            return LargeChildren()

    reader = type("SyntheticReader", (), {"root_object": LargeDirectDictionary(), "pages": [{}]})()

    with pytest.raises(ValueError, match="direct-value traversal"):
        document_intake._pdf_has_active_content(reader, max_objects=50, max_items=3)


def test_docx_xml_preflight_rejects_before_element_tree_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from defusedxml import ElementTree

    monkeypatch.setattr(
        ElementTree,
        "fromstring",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ElementTree must not run before XML preflight")
        ),
    )

    with pytest.raises(ValueError, match="XML element limit"):
        document_intake._xml_root(
            CONTENT_TYPES,
            label="content types",
            limits=DocumentIntakeLimits(),
            budget=document_intake._DocxXmlBudget(),
            xml_limits=document_intake._DocxXmlLimits(elements=1),
        )


def test_docx_xml_preflight_bounds_names_namespaces_and_attributes() -> None:
    content = b'<root xmlns:a="urn:a" xmlns:b="urn:b"><a:item b:first="1" b:second="2"/></root>'
    cases = [
        (document_intake._DocxXmlLimits(namespaces=1), "namespace declaration"),
        (document_intake._DocxXmlLimits(namespace_length=4), "overlong XML namespace"),
        (document_intake._DocxXmlLimits(unique_namespaces=1), "unique XML namespace"),
        (document_intake._DocxXmlLimits(name_length=5), "overlong XML name"),
        (document_intake._DocxXmlLimits(attributes=1), "attribute limit"),
        (document_intake._DocxXmlLimits(unique_names=2), "unique XML name"),
    ]
    for xml_limits, message in cases:
        with pytest.raises(ValueError, match=message):
            document_intake._validate_docx_xml_preflight(
                content,
                label="synthetic",
                limits=DocumentIntakeLimits(),
                budget=document_intake._DocxXmlBudget(),
                xml_limits=xml_limits,
            )


@pytest.mark.parametrize(
    "xref_body",
    [
        b"1 0 obj\n<< /Type /XRef /Size 2 /W [1 4 2] /Length 1 >>\nstream\n0\nendstream\nendobj\n",
        b"xref\n0 1\n0000000000 65535 f \ntrailer\n<< /Size 1 /X#52efStm 9 >>\n",
    ],
)
def test_pdf_rejects_compressed_or_hybrid_xrefs_before_parser_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    xref_body: bytes,
) -> None:
    source = tmp_path / "compressed-xref.pdf"
    header = b"%PDF-1.5\n"
    source.write_bytes(header + xref_body + f"startxref\n{len(header)}\n%%EOF\n".encode())

    def fail_if_constructed(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("PdfReader must not run before container preflight")

    monkeypatch.setattr(pypdf, "PdfReader", fail_if_constructed)

    with pytest.raises(ValueError, match="compressed or hybrid|compressed cross-reference"):
        extract_candidate_document(source)


def test_pdf_rejects_large_classic_xref_before_parser_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "large-xref.pdf"
    _write_text_pdf(source, "Synthetic classic-xref control.")

    def fail_if_constructed(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("PdfReader must not run before container preflight")

    monkeypatch.setattr(pypdf, "PdfReader", fail_if_constructed)

    with pytest.raises(ValueError, match="cross-reference object limit"):
        extract_candidate_document(
            source,
            limits=DocumentIntakeLimits(max_pdf_objects=1),
        )


def test_docx_extracts_paragraphs_and_table_text_with_paragraph_provenance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "career.docx"
    _write_docx(source)

    result = extract_candidate_document(source)

    assert result.input_format == "docx"
    assert result.page_count is None
    assert result.text.splitlines() == [
        "Synthetic services leader.",
        "Team outcome: 18 percent.",
    ]
    assert [location.kind for location in result.locations] == ["paragraph", "paragraph"]
    assert [location.ordinal for location in result.locations] == [1, 2]
    assert [location.part for location in result.locations] == [
        "word/document.xml",
        "word/document.xml",
    ]
    assert result.locations[0].to_dict() == {
        "kind": "paragraph",
        "ordinal": 1,
        "start_line": 1,
        "end_line": 1,
        "part": "word/document.xml",
    }


@pytest.mark.parametrize("content_type_route", ["override", "default", "case-insensitive-override"])
def test_docx_resolves_exactly_one_authoritative_main_document(
    tmp_path: Path,
    content_type_route: str,
) -> None:
    alternate = tmp_path / "alternate-main.docx"
    missing = tmp_path / "missing-main-relationship.docx"
    duplicate = tmp_path / "duplicate-main-relationship.docx"
    duplicate_identifier = tmp_path / "duplicate-relationship-identifier.docx"
    alternate_content_types = CONTENT_TYPES.replace(
        b'/word/document.xml"',
        b'/word/main.xml"',
    )
    if content_type_route == "default":
        alternate_content_types = alternate_content_types.replace(
            b'<Default Extension="xml" ContentType="application/xml"/>', b""
        ).replace(b'<Override PartName="/word/main.xml"', b'<Default Extension="XML"')
    elif content_type_route == "case-insensitive-override":
        alternate_content_types = alternate_content_types.replace(
            b"/word/main.xml", b"/WORD/MAIN.XML"
        )
    alternate_relationships = PACKAGE_RELATIONSHIPS.replace(
        b'Target="word/document.xml"',
        b'Target="word/main.xml"',
    )
    _write_docx(
        alternate,
        main_part="word/main.xml",
        relationships=alternate_relationships,
        content_types=alternate_content_types,
    )
    _write_docx(
        missing,
        relationships=b"""<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>
""",
    )
    _write_docx(
        duplicate,
        relationships=b"""<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
        """,
    )
    _write_docx(
        duplicate_identifier,
        relationships=b"""<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
""",
    )

    result = extract_candidate_document(alternate)

    assert result.locations[0].part == "word/main.xml"
    with pytest.raises(ValueError, match="exactly one internal package officeDocument"):
        extract_candidate_document(missing)
    with pytest.raises(ValueError, match="exactly one internal package officeDocument"):
        extract_candidate_document(duplicate)
    with pytest.raises(ValueError, match="duplicate relationship identifiers"):
        extract_candidate_document(duplicate_identifier)


@pytest.mark.parametrize("header_default", [False, True])
def test_docx_extracts_referenced_visible_parts_once_with_part_provenance(
    tmp_path: Path,
    header_default: bool,
) -> None:
    source = tmp_path / "visible-parts.docx"
    content_types = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
  <Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
  <Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>
  <Override PartName="/word/endnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml"/>
</Types>
"""
    if header_default:
        content_types = content_types.replace(
            b'<Default Extension="xml" ContentType="application/xml"/>', b""
        ).replace(b'<Override PartName="/word/header1.xml"', b'<Default Extension="XML"')
    document_relationships = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdHeader" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>
  <Relationship Id="rIdHeaderAgain" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>
  <Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>
  <Relationship Id="rIdFootnotes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
  <Relationship Id="rIdEndnotes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes" Target="endnotes.xml"/>
</Relationships>
"""

    def part_xml(root: str, text: str) -> bytes:
        return (
            f'<?xml version="1.0"?><w:{root} '
            f'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:{root}>"
        ).encode()

    _write_docx(
        source,
        content_types=content_types,
        extra={
            "word/_rels/document.xml.rels": document_relationships,
            "word/header1.xml": part_xml("hdr", "Synthetic header."),
            "word/footer1.xml": part_xml("ftr", "Synthetic footer."),
            "word/footnotes.xml": part_xml("footnotes", "Synthetic footnote."),
            "word/endnotes.xml": part_xml("endnotes", "Synthetic endnote."),
        },
    )

    result = extract_candidate_document(source)

    assert result.text.splitlines() == [
        "Synthetic services leader.",
        "Team outcome: 18 percent.",
        "Synthetic header.",
        "Synthetic footer.",
        "Synthetic footnote.",
        "Synthetic endnote.",
    ]
    assert [location.part for location in result.locations] == [
        "word/document.xml",
        "word/document.xml",
        "word/header1.xml",
        "word/footer1.xml",
        "word/footnotes.xml",
        "word/endnotes.xml",
    ]


@pytest.mark.parametrize(
    ("declaration", "error"),
    [
        (
            b'<Default Extension="XML" ContentType="application/xml"/>',
            "duplicate content type defaults",
        ),
        (b'<Default Extension="" ContentType="application/xml"/>', "default is malformed"),
        (b'<Default Extension="../txt" ContentType="application/xml"/>', "default is malformed"),
        (b'<Default Extension="txt"/>', "default is malformed"),
        (
            b'<Default Extension="txt" ContentType="application/vnd.ms-word.document.macroEnabled.12"/>',
            "macro-enabled",
        ),
        (
            b'<Override PartName="/WORD/DOCUMENT.XML" ContentType="application/xml"/>',
            "duplicate content type overrides",
        ),
    ],
)
def test_docx_rejects_ambiguous_or_unsafe_content_type_declarations(
    tmp_path: Path,
    declaration: bytes,
    error: str,
) -> None:
    source = tmp_path / "invalid-content-types.docx"
    _write_docx(source, content_types=CONTENT_TYPES.replace(b"</Types>", declaration + b"</Types>"))
    with pytest.raises(ValueError, match=error):
        extract_candidate_document(source)


@pytest.mark.parametrize(
    "field_xml",
    [
        b'<w:fldSimple w:instr="DDEAUTO synthetic"><w:r><w:t>Unsafe</w:t></w:r></w:fldSimple>',
        b'<w:fldSimple w:instr="HYPERLINK https://example.invalid"><w:r><w:t>Unsafe</w:t></w:r></w:fldSimple>',
        b'<w:r><w:fldChar w:fldCharType="begin"/><w:instrText>D</w:instrText><w:instrText>DE synthetic</w:instrText><w:fldChar w:fldCharType="end"/></w:r>',
    ],
)
def test_docx_rejects_dangerous_field_instructions(
    tmp_path: Path,
    field_xml: bytes,
) -> None:
    source = tmp_path / "dangerous-field.docx"
    document = (
        b'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p>'
        + field_xml
        + b"</w:p></w:body></w:document>"
    )
    _write_docx(source, document=document)

    with pytest.raises(ValueError, match="dangerous field instruction"):
        extract_candidate_document(source)


def test_docx_allows_inert_field_instructions(tmp_path: Path) -> None:
    source = tmp_path / "inert-field.docx"
    document = b"""<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:fldSimple w:instr="PAGE"><w:r><w:t>1</w:t></w:r></w:fldSimple></w:p></w:body>
</w:document>
"""
    _write_docx(source, document=document)

    assert extract_candidate_document(source).text == "1"


def test_docx_rejects_excessive_xml_depth_without_recursive_traversal(tmp_path: Path) -> None:
    source = tmp_path / "deep.docx"
    nested = b"<w:r>" * 12 + b"<w:t>Synthetic deep text.</w:t>" + b"</w:r>" * 12
    document = (
        b'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p>'
        + nested
        + b"</w:p></w:body></w:document>"
    )
    _write_docx(source, document=document)

    with pytest.raises(ValueError, match="safe XML depth limit"):
        extract_candidate_document(
            source,
            limits=DocumentIntakeLimits(max_docx_xml_depth=8),
        )


def test_docx_rejects_excess_central_directory_before_parser_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declared = tmp_path / "declared-members.docx"
    forged = tmp_path / "forged-members.docx"
    with zipfile.ZipFile(declared, "w") as archive:
        for index in range(6):
            archive.writestr(f"member-{index}.xml", b"")
    forged.write_bytes(declared.read_bytes())
    damaged = bytearray(forged.read_bytes())
    eocd = damaged.rfind(b"PK\x05\x06")
    assert eocd >= 0
    struct.pack_into("<HH", damaged, eocd + 8, 1, 1)
    forged.write_bytes(damaged)

    def fail_if_constructed(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ZipFile must not run before container preflight")

    monkeypatch.setattr(document_intake.zipfile, "ZipFile", fail_if_constructed)

    limits = DocumentIntakeLimits(max_docx_members=5)
    with pytest.raises(ValueError, match="unsafe number of archive members"):
        extract_candidate_document(declared, limits=limits)
    with pytest.raises(ValueError, match="unsafe number of archive members"):
        extract_candidate_document(forged, limits=limits)


def test_docx_does_not_duplicate_nested_text_box_paragraphs(tmp_path: Path) -> None:
    source = tmp_path / "text-box.docx"
    document = b"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:r><w:t>Outer evidence.</w:t></w:r>
      <w:r><w:drawing><w:txbxContent><w:p><w:r><w:t>Text box evidence.</w:t></w:r></w:p></w:txbxContent></w:drawing></w:r>
    </w:p>
  </w:body>
</w:document>
"""
    _write_docx(source, document=document)

    result = extract_candidate_document(source)

    assert result.text.splitlines() == ["Outer evidence.", "Text box evidence."]
    assert result.text.count("Text box evidence.") == 1


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        ({"../escape.xml": b"synthetic"}, "unsafe archive paths"),
        ({"word/vbaProject.bin": b"synthetic"}, "macros, embedded objects"),
        ({"word/embeddings/object.bin": b"synthetic"}, "macros, embedded objects"),
    ],
)
def test_docx_rejects_unsafe_archive_paths_and_active_content(
    tmp_path: Path,
    extra: dict[str, bytes],
    error: str,
) -> None:
    source = tmp_path / "unsafe.docx"
    _write_docx(source, extra=extra)

    with pytest.raises(ValueError, match=error):
        extract_candidate_document(source)


def test_docx_rejects_external_relationships_and_prohibited_xml(tmp_path: Path) -> None:
    external = tmp_path / "external.docx"
    entity = tmp_path / "entity.docx"
    embedded = tmp_path / "embedded.docx"
    external_relationships = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="synthetic" Target="https://example.invalid" TargetMode="External"/>
</Relationships>
"""
    _write_docx(external, relationships=external_relationships)
    embedded_relationships = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="payload.xml"/>
</Relationships>
"""
    _write_docx(
        embedded,
        extra={"word/_rels/document.xml.rels": embedded_relationships, "word/payload.xml": b"x"},
    )
    _write_docx(
        entity,
        document=b"""<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "synthetic">]>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&e;</w:t></w:r></w:p></w:body></w:document>""",
    )

    with pytest.raises(ValueError, match="external relationship"):
        extract_candidate_document(external)
    with pytest.raises(ValueError, match="DTD or entity"):
        extract_candidate_document(entity)
    with pytest.raises(ValueError, match="active or embedded relationship"):
        extract_candidate_document(embedded)


def test_docx_converts_deferred_archive_read_errors_to_safe_rejections(tmp_path: Path) -> None:
    source = tmp_path / "bad-crc.docx"
    _write_docx(source, compression=zipfile.ZIP_STORED)
    damaged = bytearray(source.read_bytes())
    offset = damaged.find(DOCUMENT_XML)
    assert offset >= 0
    damaged[offset + len(DOCUMENT_XML) // 2] ^= 1
    source.write_bytes(damaged)

    with pytest.raises(ValueError, match="archive member could not be read safely"):
        extract_candidate_document(source)


def test_intake_rejects_signature_mismatch_expansion_and_file_indirection(
    tmp_path: Path,
) -> None:
    renamed_pdf = tmp_path / "renamed.txt"
    renamed_pdf.write_bytes(b"%PDF-1.4\nsynthetic")
    compressed = tmp_path / "compressed.docx"
    _write_docx(compressed)

    with pytest.raises(ValueError, match="signature does not match"):
        extract_candidate_document(renamed_pdf)
    with pytest.raises(ValueError, match="compression ratio"):
        extract_candidate_document(
            compressed,
            limits=DocumentIntakeLimits(max_docx_compression_ratio=1),
        )

    target = tmp_path / "target.txt"
    target.write_text("Synthetic", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symbolic links are unavailable in this environment")
    with pytest.raises(ValueError, match="regular non-link"):
        extract_candidate_document(link)


def test_intake_rejects_path_replaced_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "candidate.txt"
    replacement = tmp_path / "replacement.txt"
    source.write_text("Synthetic original", encoding="utf-8")
    replacement.write_text("Synthetic replacement", encoding="utf-8")
    probe = tmp_path / "probe.txt"
    try:
        probe.symlink_to(replacement)
        probe.unlink()
    except OSError:
        pytest.skip("Symbolic links are unavailable in this environment")
    real_open = os.open

    def replace_then_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int
    ) -> int:
        source.unlink()
        source.symlink_to(replacement)
        return real_open(path, flags)

    monkeypatch.setattr(document_intake.os, "open", replace_then_open)

    with pytest.raises(ValueError, match="read safely|changed or became a link"):
        extract_candidate_document(source)


def test_intake_rejects_unsupported_empty_oversized_and_encrypted_documents(
    tmp_path: Path,
) -> None:
    unsupported = tmp_path / "career.rtf"
    empty = tmp_path / "empty.md"
    oversized = tmp_path / "oversized.txt"
    encrypted = tmp_path / "encrypted.pdf"
    unsupported.write_text("Synthetic", encoding="utf-8")
    empty.write_bytes(b"")
    oversized.write_bytes(b"123456")
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("synthetic-password")
    with encrypted.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(ValueError, match="must be a .txt"):
        extract_candidate_document(unsupported)
    with pytest.raises(ValueError, match="empty"):
        extract_candidate_document(empty)
    with pytest.raises(ValueError, match="exceeds"):
        extract_candidate_document(
            oversized,
            limits=DocumentIntakeLimits(max_document_bytes=5),
        )
    with pytest.raises(ValueError, match="Encrypted PDF"):
        extract_candidate_document(encrypted)
