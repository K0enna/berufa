"""Bounded, local-only extraction of user-supplied career documents.

Document bytes are untrusted data. This module extracts text and provenance only; it never
executes document content, calls a model or provider, persists candidate data, or approves
evidence.
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import os
import posixpath
import re
import stat
import struct
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit
from xml.etree.ElementTree import ParseError
from xml.parsers import expat

from career_os.evidence_records import (
    DocumentFormat as DocumentFormat,
)
from career_os.evidence_records import (
    DocumentLocation as DocumentLocation,
)
from career_os.evidence_records import (
    ExtractedCandidateDocument as ExtractedCandidateDocument,
)
from career_os.evidence_records import (
    LocationKind as LocationKind,
)

MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_PDF_OBJECTS = 50_000
MAX_PDF_XREF_SECTIONS = 64
MAX_PDF_TRAILER_NESTING = 64
MAX_PDF_DECODED_CONTENT_BYTES = 8 * 1024 * 1024
MAX_PDF_TRAVERSAL_ITEMS = 100_000
MAX_PDF_FONT_WIDTH_ENTRIES = 100_000
MAX_PDF_FORM_INVOCATIONS = 10_000
MAX_PDF_CONTENT_OPERATIONS = 100_000
MAX_PDF_EXECUTED_CONTENT_BYTES = 16 * 1024 * 1024
MAX_PDF_CMAP_ENTRIES = 100_000
MAX_PDF_CMAP_VALUE_CHARACTERS = 32
MAX_PDF_TEXT_WORK = 32 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 1_000_000
MAX_EXTRACTED_BYTES = 1_048_576
MAX_EXTRACTED_LINES = 50_000
MAX_DOCX_MEMBERS = 512
MAX_DOCX_CENTRAL_DIRECTORY_BYTES = 1024 * 1024
MAX_DOCX_MEMBER_BYTES = 10 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 30 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 100
MAX_DOCX_PARAGRAPHS = 20_000
MAX_DOCX_XML_DEPTH = 128
MAX_DOCX_XML_ELEMENTS = 100_000
MAX_DOCX_XML_ATTRIBUTES = 200_000
MAX_DOCX_XML_NAME_LENGTH = 512
MAX_DOCX_XML_NAMESPACES = 1_024
MAX_DOCX_XML_NAMESPACE_LENGTH = 2_048
MAX_DOCX_XML_UNIQUE_NAMES = 4_096
MAX_DOCX_XML_UNIQUE_NAMESPACES = 256

_FORMAT_BY_SUFFIX: dict[str, tuple[DocumentFormat, str]] = {
    ".txt": ("text", "text/plain"),
    ".md": ("markdown", "text/markdown"),
    ".pdf": ("pdf", "application/pdf"),
    ".docx": (
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
}
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_PACKAGE_RELATIONSHIP_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
_CONTENT_TYPE_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_PDF_ACTIVE_KEYS = frozenset(
    {
        "/AA",
        "/AF",
        "/EF",
        "/EmbeddedFiles",
        "/ImportData",
        "/JavaScript",
        "/JS",
        "/Launch",
        "/RichMediaContent",
        "/SubmitForm",
        "/URI",
        "/XFA",
    }
)
_PDF_ACTIVE_ACTION_TYPES = frozenset(
    {
        "/GoToR",
        "/GoToE",
        "/ImportData",
        "/JavaScript",
        "/Launch",
        "/Movie",
        "/Rendition",
        "/Sound",
        "/SubmitForm",
        "/URI",
    }
)
_PDF_ACTIVE_ANNOTATION_TYPES = frozenset(
    {"/3D", "/FileAttachment", "/Movie", "/RichMedia", "/Screen"}
)
_PDF_WHITESPACE = frozenset(b"\x00\t\n\x0c\r ")
_PDF_DELIMITERS = frozenset(b"()<>[]{}/%")
_ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"
_ZIP_EOCD_SIZE = 22
_ZIP_CENTRAL_DIRECTORY_RECORD_SIZE = 46
_PDF_CMAP_HEX_TOKEN = re.compile(rb"<([0-9A-Fa-f\x00\t\n\x0c\r ]*)>")
_PDF_CMAP_TOKEN = re.compile(
    rb"%[^\r\n]*|<[0-9A-Fa-f\x00\t\n\x0c\r ]*>|\[|\]|[^\x00\t\n\x0c\r <>\[\]%]+"
)
_DOCX_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
_DOCX_RELATIONSHIP_BASES = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/",
)
_DOCX_VISIBLE_PART_CONTENT_TYPES = {
    "header": "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml",
    "footer": "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
    "footnotes": "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
    "endnotes": "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml",
}
_DOCX_VISIBLE_PART_ROOTS = {
    "header": "hdr",
    "footer": "ftr",
    "footnotes": "footnotes",
    "endnotes": "endnotes",
}
_DOCX_VISIBLE_PART_ORDER = {
    kind: index for index, kind in enumerate(_DOCX_VISIBLE_PART_CONTENT_TYPES)
}
_DOCX_DANGEROUS_FIELD_COMMAND = re.compile(
    r"(?:^|\s)(?:DDEAUTO|DDE|HYPERLINK|INCLUDEPICTURE|INCLUDETEXT|LINK)(?:\s|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DocumentIntakeLimits:
    max_document_bytes: int = MAX_DOCUMENT_BYTES
    max_pdf_pages: int = MAX_PDF_PAGES
    max_pdf_objects: int = MAX_PDF_OBJECTS
    max_pdf_xref_sections: int = MAX_PDF_XREF_SECTIONS
    max_pdf_decoded_content_bytes: int = MAX_PDF_DECODED_CONTENT_BYTES
    max_extracted_characters: int = MAX_EXTRACTED_CHARACTERS
    max_extracted_bytes: int = MAX_EXTRACTED_BYTES
    max_extracted_lines: int = MAX_EXTRACTED_LINES
    max_docx_members: int = MAX_DOCX_MEMBERS
    max_docx_central_directory_bytes: int = MAX_DOCX_CENTRAL_DIRECTORY_BYTES
    max_docx_member_bytes: int = MAX_DOCX_MEMBER_BYTES
    max_docx_uncompressed_bytes: int = MAX_DOCX_UNCOMPRESSED_BYTES
    max_docx_compression_ratio: int = MAX_DOCX_COMPRESSION_RATIO
    max_docx_paragraphs: int = MAX_DOCX_PARAGRAPHS
    max_docx_xml_depth: int = MAX_DOCX_XML_DEPTH

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (
                self.max_document_bytes,
                self.max_pdf_pages,
                self.max_pdf_objects,
                self.max_pdf_xref_sections,
                self.max_pdf_decoded_content_bytes,
                self.max_extracted_characters,
                self.max_extracted_bytes,
                self.max_extracted_lines,
                self.max_docx_members,
                self.max_docx_central_directory_bytes,
                self.max_docx_member_bytes,
                self.max_docx_uncompressed_bytes,
                self.max_docx_compression_ratio,
                self.max_docx_paragraphs,
                self.max_docx_xml_depth,
            )
        ):
            raise ValueError("Document intake limits must be positive integers")


@dataclass(frozen=True, slots=True)
class _TextSegment:
    ordinal: int
    text: str
    part: str | None = None


@dataclass(frozen=True, slots=True)
class _DocxRelationship:
    kind: str
    target: str


@dataclass(frozen=True, slots=True)
class _PdfWorkLimits:
    traversal_items: int = MAX_PDF_TRAVERSAL_ITEMS
    font_width_entries: int = MAX_PDF_FONT_WIDTH_ENTRIES
    form_invocations: int = MAX_PDF_FORM_INVOCATIONS
    content_operations: int = MAX_PDF_CONTENT_OPERATIONS
    executed_content_bytes: int = MAX_PDF_EXECUTED_CONTENT_BYTES
    cmap_entries: int = MAX_PDF_CMAP_ENTRIES
    cmap_value_characters: int = MAX_PDF_CMAP_VALUE_CHARACTERS
    text_work: int = MAX_PDF_TEXT_WORK


@dataclass(frozen=True, slots=True)
class _DocxXmlLimits:
    elements: int = MAX_DOCX_XML_ELEMENTS
    attributes: int = MAX_DOCX_XML_ATTRIBUTES
    name_length: int = MAX_DOCX_XML_NAME_LENGTH
    namespaces: int = MAX_DOCX_XML_NAMESPACES
    namespace_length: int = MAX_DOCX_XML_NAMESPACE_LENGTH
    unique_names: int = MAX_DOCX_XML_UNIQUE_NAMES
    unique_namespaces: int = MAX_DOCX_XML_UNIQUE_NAMESPACES


@dataclass(slots=True)
class _DocxXmlBudget:
    elements: int = 0
    attributes: int = 0
    namespaces: int = 0
    unique_names: set[str] = field(default_factory=set)
    unique_namespaces: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _PdfInspection:
    reader: Any
    page_texts: tuple[str, ...]


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _read_bounded_regular_file(path: Path, limits: DocumentIntakeLimits) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ValueError("Candidate document must be a regular non-link file") from exc
    if _is_link_or_reparse(path) or not stat.S_ISREG(before.st_mode):
        raise ValueError("Candidate document must be a regular non-link file")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("Candidate document must be a regular non-link file")
            after = path.lstat()
            identity_before = (before.st_dev, before.st_ino)
            identity_open = (metadata.st_dev, metadata.st_ino)
            identity_after = (after.st_dev, after.st_ino)
            if (
                _is_link_or_reparse(path)
                or not stat.S_ISREG(after.st_mode)
                or identity_before != identity_open
                or identity_after != identity_open
            ):
                raise ValueError("Candidate document changed or became a link while opening")
            if metadata.st_size < 1:
                raise ValueError("Candidate document is empty")
            if metadata.st_size > limits.max_document_bytes:
                raise ValueError(
                    f"Candidate document exceeds the "
                    f"{limits.max_document_bytes // (1024 * 1024)} MiB limit"
                )
            content = stream.read(limits.max_document_bytes + 1)
    except OSError as exc:
        raise ValueError("Candidate document could not be read safely") from exc
    if len(content) > limits.max_document_bytes:
        raise ValueError(
            f"Candidate document exceeds the {limits.max_document_bytes // (1024 * 1024)} MiB limit"
        )
    return content


def _bounded_text(text: str, limits: DocumentIntakeLimits) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in normalized:
        raise ValueError("Extracted document text contains a null character")
    if len(normalized) > limits.max_extracted_characters:
        raise ValueError(
            f"Extracted document text exceeds {limits.max_extracted_characters:,} characters"
        )
    if len(normalized.encode("utf-8")) > limits.max_extracted_bytes:
        raise ValueError(
            f"Extracted document text exceeds {limits.max_extracted_bytes:,} UTF-8 bytes"
        )
    if len(normalized.splitlines()) > limits.max_extracted_lines:
        raise ValueError(f"Extracted document text exceeds {limits.max_extracted_lines:,} lines")
    if not normalized.strip():
        raise ValueError("Candidate document contains no extractable text")
    return normalized


def _result(
    *,
    path: Path,
    content: bytes,
    input_format: DocumentFormat,
    media_type: str,
    text: str,
    locations: tuple[DocumentLocation, ...],
    limits: DocumentIntakeLimits,
    page_count: int | None = None,
) -> ExtractedCandidateDocument:
    bounded = _bounded_text(text, limits)
    return ExtractedCandidateDocument(
        display_name=path.name,
        input_format=input_format,
        media_type=media_type,
        original_sha256=hashlib.sha256(content).hexdigest(),
        original_size_bytes=len(content),
        original_content=content,
        text=bounded,
        extracted_sha256=hashlib.sha256(bounded.encode("utf-8")).hexdigest(),
        line_count=len(bounded.splitlines()) or 1,
        locations=locations,
        page_count=page_count,
    )


def _text_document(
    path: Path,
    content: bytes,
    *,
    input_format: DocumentFormat,
    media_type: str,
    limits: DocumentIntakeLimits,
) -> ExtractedCandidateDocument:
    if content.startswith(b"%PDF-") or content.startswith(_ZIP_SIGNATURES):
        raise ValueError("Candidate document signature does not match its text suffix")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Candidate text document must be valid UTF-8") from exc
    normalized = _bounded_text(decoded, limits)
    selected = normalized.splitlines()
    locations = tuple(
        DocumentLocation(
            kind="line",
            ordinal=index + 1,
            start_line=index + 1,
            end_line=index + 1,
        )
        for index in range(len(selected))
    )
    return _result(
        path=path,
        content=content,
        input_format=input_format,
        media_type=media_type,
        text=normalized,
        locations=locations,
        limits=limits,
    )


def _object(value: object) -> object:
    getter = getattr(value, "get_object", None)
    return getter() if callable(getter) else value


def _safe_http_link(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        return False
    if (
        any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
        or "\\" in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if not host or "%" in host or parsed.netloc.endswith(":"):
            return False
        if ":" in host:
            ipaddress.IPv6Address(host)
        else:
            ascii_host = host.encode("idna").decode("ascii").rstrip(".")
            if (
                not ascii_host
                or len(ascii_host) > 253
                or any(
                    not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
                    for part in ascii_host.split(".")
                )
            ):
                return False
            if ascii_host.count(".") == 3 and all(part.isdigit() for part in ascii_host.split(".")):
                ipaddress.IPv4Address(ascii_host)
        return (
            parsed.scheme.lower() in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and (parsed.port is None or 1 <= parsed.port <= 65535)
        )
    except ValueError:
        return False


def _pdf_has_active_content(
    reader: Any, *, max_objects: int, max_items: int, allow_safe_hyperlinks: bool = False
) -> bool:
    pending: list[tuple[object, bool]] = [
        (reader.root_object, False),
        *((page, False) for page in reader.pages),
    ]
    seen: set[int] = set()
    inspected = 0
    traversed = 0
    active_content = False
    while pending:
        traversed += 1
        if traversed > max_items:
            raise ValueError("Candidate PDF exceeds the safe direct-value traversal limit")
        value, link_action = pending.pop()
        resolved = _object(value)
        if not isinstance(resolved, (dict, list, tuple)):
            continue
        marker = id(resolved)
        if isinstance(resolved, dict):
            keys = {str(key) for key in resolved}
            action_type = str(_object(resolved.get("/S", "")))
            safe_uri = (
                allow_safe_hyperlinks
                and link_action
                and action_type == "/URI"
                and str(_object(resolved.get("/Type", "/Action"))) == "/Action"
                and keys <= {"/Type", "/S", "/URI"}
                and _safe_http_link(_object(resolved.get("/URI")))
            )
            if keys & (_PDF_ACTIVE_KEYS - ({"/URI"} if safe_uri else set())):
                active_content = True
            if "/OpenAction" in resolved:
                open_action = _object(resolved["/OpenAction"])
                if not isinstance(open_action, (list, tuple)):
                    active_content = True
            if action_type in _PDF_ACTIVE_ACTION_TYPES and not safe_uri:
                active_content = True
            if str(_object(resolved.get("/Subtype", ""))) in _PDF_ACTIVE_ANNOTATION_TYPES:
                active_content = True
            if traversed + len(pending) + len(resolved.values()) > max_items:
                raise ValueError("Candidate PDF exceeds the safe direct-value traversal limit")
            is_link = str(_object(resolved.get("/Subtype", ""))) == "/Link"
            children = [
                (child, allow_safe_hyperlinks and is_link and str(key) == "/A")
                for key, child in resolved.items()
            ]
        else:
            if traversed + len(pending) + len(resolved) > max_items:
                raise ValueError("Candidate PDF exceeds the safe direct-value traversal limit")
            children = [(child, False) for child in resolved]
        if marker in seen:
            continue
        seen.add(marker)
        inspected += 1
        if inspected > max_objects:
            raise ValueError("Candidate PDF exceeds the safe object-inspection limit")
        if traversed + len(pending) + len(children) > max_items:
            raise ValueError("Candidate PDF exceeds the safe direct-value traversal limit")
        pending.extend(children)
    return active_content


def _bounded_flate_data(data: bytes, *, limit: int) -> bytes:
    last_error: zlib.error | None = None
    for window_bits in (zlib.MAX_WBITS, zlib.MAX_WBITS | 32):
        decoder = zlib.decompressobj(window_bits)
        total = 0
        pending = data
        chunks: list[bytes] = []
        try:
            while pending:
                output = decoder.decompress(pending, min(65_536, limit - total + 1))
                total += len(output)
                if total > limit:
                    raise ValueError("Candidate PDF decoded content exceeds the safe size limit")
                if output:
                    chunks.append(output)
                if decoder.unconsumed_tail == pending and not output:
                    raise ValueError("Candidate PDF content stream could not be decoded safely")
                pending = decoder.unconsumed_tail
            if not decoder.eof:
                raise ValueError("Candidate PDF content stream is malformed")
            return b"".join(chunks)
        except zlib.error as exc:
            last_error = exc
    raise ValueError("Candidate PDF content stream could not be decoded safely") from last_error


def _pdf_content_stream_data(stream: object, *, limit: int) -> bytes:
    resolved = _object(stream)
    if not isinstance(resolved, dict) or not hasattr(resolved, "get_data"):
        raise ValueError("Candidate PDF page contains an invalid content stream")
    raw = getattr(resolved, "_data", None)
    if not isinstance(raw, bytes):
        raise ValueError("Candidate PDF content stream could not be inspected safely")
    from pypdf.generic import NullObject

    decode_parms = _object(resolved.get("/DecodeParms"))
    if isinstance(decode_parms, (list, tuple)):
        if len(decode_parms) != 1:
            raise ValueError("Candidate PDF uses unsupported content-stream DecodeParms")
        decode_parms = _object(decode_parms[0])
    if decode_parms is not None and not isinstance(decode_parms, NullObject):
        if not isinstance(decode_parms, dict):
            raise ValueError("Candidate PDF uses unsupported content-stream DecodeParms")
        predictor = _object(decode_parms.get("/Predictor", 1))
        if isinstance(predictor, bool) or not isinstance(predictor, int) or predictor != 1:
            raise ValueError("Candidate PDF uses unsupported content-stream DecodeParms")
    filters = _object(resolved.get("/Filter", ()))
    if not filters:
        if len(raw) > limit:
            raise ValueError("Candidate PDF decoded content exceeds the safe size limit")
        return raw
    selected = list(filters) if isinstance(filters, (list, tuple)) else [filters]
    if len(selected) != 1 or str(_object(selected[0])) not in {"/Fl", "/FlateDecode"}:
        raise ValueError("Candidate PDF uses an unsupported content-stream filter")
    return _bounded_flate_data(raw, limit=limit)


def _pdf_form_invocations(
    content: bytes,
    reader: Any,
    *,
    operation_budget: list[int],
    operation_limit: int,
) -> Iterator[str]:
    from pypdf.generic import ContentStream, DecodedStreamObject, NameObject

    class BoundedOperations(list[tuple[Any, bytes]]):
        def append(self, operation: tuple[Any, bytes]) -> None:
            operation_budget[0] += 1
            if operation_budget[0] > operation_limit:
                raise ValueError("Candidate PDF content operations exceed the safe work limit")
            super().append(operation)

    stream = DecodedStreamObject()
    stream.set_data(content)
    parsed = ContentStream(stream, reader)
    parsed._operations = BoundedOperations()  # noqa: SLF001 - bound pypdf's append-before-return parser
    try:
        for operands, operator in parsed.operations:
            if operator != b"Do":
                continue
            if len(operands) != 1 or not isinstance(operands[0], NameObject):
                raise ValueError("Candidate PDF contains an invalid Form invocation")
            yield str(operands[0])
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Candidate PDF content operations could not be inspected safely") from exc


def _pdf_cmap_stats(data: bytes, *, work_limits: _PdfWorkLimits) -> tuple[int, int]:
    token_count = 0
    maximum_value_characters = 1
    uncommented = b"\n".join(line.split(b"%", 1)[0] for line in data.splitlines())
    for match in _PDF_CMAP_HEX_TOKEN.finditer(uncommented):
        token = b"".join(match.group(1).split())
        if not token:
            continue
        if len(token) % 2 or len(token) > work_limits.cmap_value_characters * 4:
            raise ValueError("Candidate PDF ToUnicode mapping contains an unsafe value")
        token_count += 1
        maximum_value_characters = max(maximum_value_characters, (len(token) + 3) // 4)
        if token_count > work_limits.cmap_entries * 3:
            raise ValueError("Candidate PDF ToUnicode mapping exceeds the safe entry limit")

    entries = token_count // 2
    tokens = (
        match.group()
        for match in _PDF_CMAP_TOKEN.finditer(data)
        if not match.group().startswith(b"%")
    )
    previous = b""
    for token in tokens:
        if token != b"beginbfrange":
            previous = token
            continue
        if not previous.isdigit() or len(previous) > 8:
            raise ValueError("Candidate PDF ToUnicode mapping contains an invalid range count")
        count = int(previous)
        if count > work_limits.cmap_entries:
            raise ValueError("Candidate PDF ToUnicode mapping exceeds the safe entry limit")
        for _ in range(count):
            try:
                first, last, destination = next(tokens), next(tokens), next(tokens)
            except StopIteration as exc:
                raise ValueError(
                    "Candidate PDF ToUnicode mapping contains an invalid range"
                ) from exc
            if not (
                first.startswith(b"<")
                and first.endswith(b">")
                and last.startswith(b"<")
                and last.endswith(b">")
                and (destination.startswith(b"<") or destination == b"[")
            ):
                raise ValueError("Candidate PDF ToUnicode mapping contains an invalid range")
            start_hex = b"".join(first[1:-1].split())
            stop_hex = b"".join(last[1:-1].split())
            if not start_hex or not stop_hex:
                raise ValueError("Candidate PDF ToUnicode mapping contains an invalid range")
            start = int(start_hex, 16)
            stop = int(stop_hex, 16)
            if stop < start:
                raise ValueError("Candidate PDF ToUnicode mapping contains an invalid range")
            entries += stop - start
            if entries > work_limits.cmap_entries:
                raise ValueError("Candidate PDF ToUnicode mapping exceeds the safe entry limit")
            if destination == b"[":
                for value in tokens:
                    if value == b"]":
                        break
                    if not value.startswith(b"<"):
                        raise ValueError(
                            "Candidate PDF ToUnicode mapping contains an invalid range"
                        )
                else:
                    raise ValueError("Candidate PDF ToUnicode mapping contains an invalid range")
        if next(tokens, None) != b"endbfrange":
            raise ValueError("Candidate PDF ToUnicode mapping contains an invalid range")
        previous = b""
    if entries > work_limits.cmap_entries:
        raise ValueError("Candidate PDF ToUnicode mapping exceeds the safe entry limit")
    return entries, maximum_value_characters


def _pdf_font_width_work(font: dict[object, object], *, limit: int) -> int:
    total = 0
    widths = _object(font.get("/Widths"))
    if isinstance(widths, (list, tuple)):
        total += len(widths)
    descendants = _object(font.get("/DescendantFonts"))
    if not isinstance(descendants, (list, tuple)):
        return total
    for descendant in descendants:
        descendant_object = _object(descendant)
        if not isinstance(descendant_object, dict):
            continue
        width_array = _object(descendant_object.get("/W"))
        if not isinstance(width_array, (list, tuple)):
            continue
        position = 0
        while position < len(width_array):
            start = _object(width_array[position])
            if isinstance(start, bool) or not isinstance(start, (int, float)):
                raise ValueError("Candidate PDF contains a malformed CID width definition")
            position += 1
            if position >= len(width_array):
                raise ValueError("Candidate PDF contains a malformed CID width definition")
            following = _object(width_array[position])
            if isinstance(following, (list, tuple)):
                total += len(following)
                position += 1
            else:
                if isinstance(following, bool) or not isinstance(following, (int, float)):
                    raise ValueError("Candidate PDF contains a malformed CID width definition")
                position += 1
                if position >= len(width_array):
                    raise ValueError("Candidate PDF contains a malformed CID width definition")
                width = _object(width_array[position])
                if isinstance(width, bool) or not isinstance(width, (int, float)):
                    raise ValueError("Candidate PDF contains a malformed CID width definition")
                first = int(start)
                last = int(following)
                if first < 0 or last < first or last > 0x10FFFF:
                    raise ValueError("Candidate PDF contains an unsafe CID width range")
                total += last - first + 1
                position += 1
            if total > limit:
                raise ValueError("Candidate PDF font widths exceed the safe work limit")
    if total > limit:
        raise ValueError("Candidate PDF font widths exceed the safe work limit")
    return total


def _validate_pdf_content_expansion(
    reader: Any,
    *,
    limits: DocumentIntakeLimits,
    work_limits: _PdfWorkLimits,
) -> None:
    total = 0
    stream_data: dict[int, bytes] = {}
    seen_resources: set[int] = set()
    seen_fonts: set[int] = set()
    resources_cache: dict[int, tuple[dict[str, object], int]] = {}
    total_font_widths = 0
    total_cmap_entries = 0
    maximum_cmap_value = 1
    operation_budget = [0]

    def decoded_stream(stream: object) -> bytes:
        nonlocal total
        resolved = _object(stream)
        marker = id(resolved)
        if marker in stream_data:
            return stream_data[marker]
        data = _pdf_content_stream_data(
            resolved,
            limit=limits.max_pdf_decoded_content_bytes - total,
        )
        total += len(data)
        if total > limits.max_pdf_decoded_content_bytes:
            raise ValueError("Candidate PDF decoded content exceeds the safe size limit")
        stream_data[marker] = data
        return data

    def content_streams(container: dict[object, object]) -> bytes:
        contents = _object(container.get("/Contents"))
        if contents is None:
            return b""
        streams = contents if isinstance(contents, (list, tuple)) else [contents]
        if len(streams) > 10_000:
            raise ValueError("Candidate PDF content stream array exceeds the safe limit")
        combined = bytearray()
        for stream in streams:
            combined.extend(decoded_stream(stream))
            if not combined or combined[-1] != ord("\n"):
                combined.append(ord("\n"))
        return bytes(combined)

    def inspect_resources(resources: object) -> tuple[dict[str, object], int]:
        nonlocal total_font_widths, total_cmap_entries, maximum_cmap_value
        resolved = _object(resources)
        if not isinstance(resolved, dict):
            return {}, 1
        marker = id(resolved)
        if marker in resources_cache:
            return resources_cache[marker]
        if marker in seen_resources:
            return {}, 1
        seen_resources.add(marker)
        forms: dict[str, object] = {}
        local_cmap_value = 1
        fonts = _object(resolved.get("/Font"))
        if isinstance(fonts, dict):
            for font in fonts.values():
                font_object = _object(font)
                if not isinstance(font_object, dict):
                    continue
                font_marker = id(font_object)
                if font_marker in seen_fonts:
                    continue
                seen_fonts.add(font_marker)
                total_font_widths += _pdf_font_width_work(
                    font_object,
                    limit=work_limits.font_width_entries - total_font_widths,
                )
                if total_font_widths > work_limits.font_width_entries:
                    raise ValueError("Candidate PDF font widths exceed the safe work limit")
                if "/ToUnicode" in font_object:
                    cmap_object = _object(font_object["/ToUnicode"])
                    if isinstance(cmap_object, dict) and hasattr(cmap_object, "get_data"):
                        entries, value_characters = _pdf_cmap_stats(
                            decoded_stream(cmap_object),
                            work_limits=work_limits,
                        )
                        total_cmap_entries += entries
                        if total_cmap_entries > work_limits.cmap_entries:
                            raise ValueError(
                                "Candidate PDF ToUnicode mapping exceeds the safe entry limit"
                            )
                        local_cmap_value = max(local_cmap_value, value_characters)
                        maximum_cmap_value = max(maximum_cmap_value, value_characters)
                descriptor = _object(font_object.get("/FontDescriptor"))
                if isinstance(descriptor, dict):
                    for font_file in ("/FontFile", "/FontFile2", "/FontFile3"):
                        if font_file in descriptor:
                            decoded_stream(descriptor[font_file])
        xobjects = _object(resolved.get("/XObject"))
        if isinstance(xobjects, dict):
            for name, candidate in xobjects.items():
                xobject = _object(candidate)
                if not isinstance(xobject, dict) or str(xobject.get("/Subtype", "")) == "/Image":
                    continue
                decoded_stream(xobject)
                forms[str(name)] = xobject
                _nested_forms, nested_cmap_value = inspect_resources(xobject.get("/Resources"))
                local_cmap_value = max(local_cmap_value, nested_cmap_value)
        result = forms, local_cmap_value
        resources_cache[marker] = result
        return result

    def form_execution_cost(
        form: object,
        inherited_resources: object,
        active: set[int],
        memo: dict[tuple[int, int], tuple[int, int]],
    ) -> tuple[int, int]:
        resolved = _object(form)
        if not isinstance(resolved, dict):
            return 0, 0
        marker = id(resolved)
        resources = resolved.get("/Resources") or inherited_resources
        resource_object = _object(resources)
        resource_marker = id(resource_object)
        key = marker, resource_marker
        if key in memo:
            return memo[key]
        if marker in active:
            return 0, 0
        active.add(marker)
        try:
            data = decoded_stream(resolved)
            forms, _cmap_value = inspect_resources(resources)
            cost = len(data)
            invocations = 0
            for name in _pdf_form_invocations(
                data,
                reader,
                operation_budget=operation_budget,
                operation_limit=work_limits.content_operations,
            ):
                invocations += 1
                nested = forms.get(name)
                if nested is not None:
                    nested_cost, nested_invocations = form_execution_cost(
                        nested,
                        resources,
                        active,
                        memo,
                    )
                    cost += nested_cost
                    invocations += nested_invocations
                if (
                    invocations > work_limits.form_invocations
                    or cost > work_limits.executed_content_bytes
                ):
                    raise ValueError("Candidate PDF Form execution exceeds the safe work limit")
        finally:
            active.remove(marker)
        memo[key] = cost, invocations
        return cost, invocations

    page_work: list[tuple[bytes, object]] = []
    for page in reader.pages:
        page_object = _object(page)
        if not isinstance(page_object, dict):
            raise ValueError("Candidate PDF page structure is invalid")
        content = content_streams(page_object)
        resources = page_object.get("/Resources")
        inspect_resources(resources)
        page_work.append((content, resources))

    executed_content = 0
    form_invocations = 0
    memo: dict[tuple[int, int], tuple[int, int]] = {}
    for data, resources in page_work:
        forms, _cmap_value = inspect_resources(resources)
        executed_content += len(data)
        for name in _pdf_form_invocations(
            data,
            reader,
            operation_budget=operation_budget,
            operation_limit=work_limits.content_operations,
        ):
            form_invocations += 1
            form = forms.get(name)
            if form is not None:
                form_cost, nested_invocations = form_execution_cost(
                    form,
                    resources,
                    set(),
                    memo,
                )
                executed_content += form_cost
                form_invocations += nested_invocations
            if (
                form_invocations > work_limits.form_invocations
                or executed_content > work_limits.executed_content_bytes
            ):
                raise ValueError("Candidate PDF Form execution exceeds the safe work limit")
    if executed_content * maximum_cmap_value > work_limits.text_work:
        raise ValueError("Candidate PDF mapped text exceeds the safe work limit")


def _segmented_text(
    segments: list[_TextSegment],
    *,
    kind: Literal["page", "paragraph"],
    blank_line_between_segments: bool,
    limits: DocumentIntakeLimits,
) -> tuple[str, tuple[DocumentLocation, ...]]:
    lines: list[str] = []
    locations: list[DocumentLocation] = []
    for source in segments:
        segment = source.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not segment:
            continue
        segment_lines = segment.splitlines()
        if lines and blank_line_between_segments:
            lines.append("")
        start = len(lines) + 1
        lines.extend(segment_lines)
        locations.append(
            DocumentLocation(
                kind=kind,
                ordinal=source.ordinal,
                start_line=start,
                end_line=len(lines),
                part=source.part,
            )
        )
    return _bounded_text("\n".join(lines), limits), tuple(locations)


def _pdf_skip_whitespace(content: bytes, position: int) -> int:
    while position < len(content) and content[position] in _PDF_WHITESPACE:
        position += 1
    return position


def _pdf_skip_space_and_comments(content: bytes, position: int) -> int:
    while True:
        position = _pdf_skip_whitespace(content, position)
        if position >= len(content) or content[position] != ord("%"):
            return position
        newline = content.find(b"\n", position + 1)
        carriage_return = content.find(b"\r", position + 1)
        endings = [candidate for candidate in (newline, carriage_return) if candidate >= 0]
        position = min(endings) + 1 if endings else len(content)


def _pdf_read_integer(content: bytes, position: int, *, label: str) -> tuple[int, int]:
    position = _pdf_skip_space_and_comments(content, position)
    start = position
    if position < len(content) and content[position] in (ord("+"), ord("-")):
        position += 1
    digits = position
    while position < len(content) and ord("0") <= content[position] <= ord("9"):
        position += 1
    if position == digits:
        raise ValueError(f"Candidate PDF {label} is malformed")
    try:
        return int(content[start:position]), position
    except ValueError as exc:
        raise ValueError(f"Candidate PDF {label} is malformed") from exc


def _pdf_read_name(content: bytes, position: int) -> tuple[bytes, int]:
    if position >= len(content) or content[position] != ord("/"):
        raise ValueError("Candidate PDF trailer dictionary is malformed")
    position += 1
    decoded = bytearray()
    while position < len(content):
        current = content[position]
        if current in _PDF_WHITESPACE or current in _PDF_DELIMITERS:
            break
        if current == ord("#"):
            if position + 2 >= len(content):
                raise ValueError("Candidate PDF trailer name is malformed")
            try:
                decoded.append(int(content[position + 1 : position + 3], 16))
            except ValueError as exc:
                raise ValueError("Candidate PDF trailer name is malformed") from exc
            position += 3
            continue
        decoded.append(current)
        position += 1
    return bytes(decoded), position


def _pdf_skip_literal_string(content: bytes, position: int) -> int:
    depth = 1
    position += 1
    while position < len(content):
        current = content[position]
        if current == ord("\\"):
            position += 2
            continue
        if current == ord("("):
            depth += 1
        elif current == ord(")"):
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    raise ValueError("Candidate PDF trailer string is malformed")


def _pdf_skip_composite(content: bytes, position: int) -> int:
    if content.startswith(b"<<", position):
        closers = [b">>"]
        position += 2
    elif content.startswith(b"[", position):
        closers = [b"]"]
        position += 1
    else:
        raise ValueError("Candidate PDF trailer value is malformed")
    while closers:
        position = _pdf_skip_space_and_comments(content, position)
        if position >= len(content):
            raise ValueError("Candidate PDF trailer value is malformed")
        if content.startswith(closers[-1], position):
            position += len(closers.pop())
        elif content.startswith(b"<<", position):
            closers.append(b">>")
            position += 2
        elif content.startswith(b"[", position):
            closers.append(b"]")
            position += 1
        elif content[position] == ord("("):
            position = _pdf_skip_literal_string(content, position)
        elif content[position] == ord("<"):
            ending = content.find(b">", position + 1)
            if ending < 0:
                raise ValueError("Candidate PDF trailer hex string is malformed")
            position = ending + 1
        else:
            position += 1
        if len(closers) > MAX_PDF_TRAILER_NESTING:
            raise ValueError("Candidate PDF trailer nesting exceeds the safe limit")
    return position


def _pdf_skip_value(content: bytes, position: int) -> int:
    position = _pdf_skip_space_and_comments(content, position)
    if position >= len(content):
        raise ValueError("Candidate PDF trailer value is malformed")
    if content.startswith((b"<<", b"["), position):
        return _pdf_skip_composite(content, position)
    if content[position] == ord("("):
        return _pdf_skip_literal_string(content, position)
    if content[position] == ord("<"):
        ending = content.find(b">", position + 1)
        if ending < 0:
            raise ValueError("Candidate PDF trailer hex string is malformed")
        return ending + 1
    if content[position] == ord("/"):
        return _pdf_read_name(content, position)[1]

    start = position
    while (
        position < len(content)
        and content[position] not in _PDF_WHITESPACE
        and content[position] not in _PDF_DELIMITERS
    ):
        position += 1
    if position == start:
        raise ValueError("Candidate PDF trailer value is malformed")

    first = content[start:position]
    if first.lstrip(b"+-").isdigit():
        second_start = _pdf_skip_space_and_comments(content, position)
        second_end = second_start
        while second_end < len(content) and ord("0") <= content[second_end] <= ord("9"):
            second_end += 1
        if second_end > second_start:
            reference = _pdf_skip_space_and_comments(content, second_end)
            if content.startswith(b"R", reference):
                return reference + 1
    return position


def _pdf_trailer_previous_offset(content: bytes, position: int) -> int | None:
    position = _pdf_skip_space_and_comments(content, position)
    if not content.startswith(b"<<", position):
        raise ValueError("Candidate PDF trailer dictionary is malformed")
    position += 2
    previous: int | None = None
    while True:
        position = _pdf_skip_space_and_comments(content, position)
        if content.startswith(b">>", position):
            return previous
        name, position = _pdf_read_name(content, position)
        position = _pdf_skip_space_and_comments(content, position)
        if name == b"XRefStm":
            raise ValueError(
                "Candidate PDF compressed or hybrid cross-reference streams are not supported"
            )
        if name == b"Prev":
            if previous is not None:
                raise ValueError("Candidate PDF trailer contains duplicate Prev entries")
            previous, position = _pdf_read_integer(
                content,
                position,
                label="previous cross-reference offset",
            )
            if previous < 0:
                raise ValueError("Candidate PDF previous cross-reference offset is invalid")
            continue
        position = _pdf_skip_value(content, position)


def _pdf_classic_xref_section(
    content: bytes,
    offset: int,
    *,
    remaining_objects: int,
) -> tuple[int, int | None]:
    if offset < 0 or not content.startswith(b"xref", offset):
        raise ValueError("Candidate PDF compressed cross-reference streams are not supported")
    position = offset + len(b"xref")
    if position < len(content) and content[position] not in _PDF_WHITESPACE:
        raise ValueError("Candidate PDF cross-reference table is malformed")
    entries = 0
    while True:
        position = _pdf_skip_space_and_comments(content, position)
        if content.startswith(b"trailer", position):
            position += len(b"trailer")
            return entries, _pdf_trailer_previous_offset(content, position)
        _first_object, position = _pdf_read_integer(
            content,
            position,
            label="cross-reference subsection",
        )
        count, position = _pdf_read_integer(
            content,
            position,
            label="cross-reference subsection",
        )
        if count < 1 or entries + count > remaining_objects:
            raise ValueError("Candidate PDF exceeds the safe cross-reference object limit")
        entries += count
        for _ in range(count):
            _object_offset, position = _pdf_read_integer(
                content,
                position,
                label="cross-reference entry",
            )
            _generation, position = _pdf_read_integer(
                content,
                position,
                label="cross-reference entry",
            )
            position = _pdf_skip_space_and_comments(content, position)
            if position >= len(content) or content[position] not in (ord("f"), ord("n")):
                raise ValueError("Candidate PDF cross-reference entry is malformed")
            position += 1


def _validate_pdf_container_preflight(
    content: bytes,
    *,
    limits: DocumentIntakeLimits,
) -> None:
    marker = content.rfind(b"startxref")
    if marker < 0:
        raise ValueError("Candidate PDF startxref marker is missing")
    offset, position = _pdf_read_integer(
        content,
        marker + len(b"startxref"),
        label="startxref offset",
    )
    position = _pdf_skip_whitespace(content, position)
    if not content.startswith(b"%%EOF", position):
        raise ValueError("Candidate PDF end marker is malformed")
    if _pdf_skip_whitespace(content, position + len(b"%%EOF")) != len(content):
        raise ValueError("Candidate PDF contains data after its final end marker")

    seen_offsets: set[int] = set()
    total_objects = 0
    sections = 0
    while True:
        if offset in seen_offsets:
            raise ValueError("Candidate PDF cross-reference chain contains a cycle")
        seen_offsets.add(offset)
        sections += 1
        if sections > limits.max_pdf_xref_sections:
            raise ValueError("Candidate PDF exceeds the safe cross-reference section limit")
        count, previous = _pdf_classic_xref_section(
            content,
            offset,
            remaining_objects=limits.max_pdf_objects - total_objects,
        )
        total_objects += count
        if previous is None:
            return
        offset = previous


def _inspect_pdf_bytes(
    content: bytes,
    *,
    limits: DocumentIntakeLimits,
    reject_active_content: bool,
    work_limits: _PdfWorkLimits | None = None,
    allow_safe_hyperlinks: bool = False,
) -> _PdfInspection:
    selected_work_limits = work_limits or _PdfWorkLimits()
    if not content.startswith(b"%PDF-"):
        raise ValueError("Candidate document signature does not match its PDF suffix")
    _validate_pdf_container_preflight(content, limits=limits)
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF candidate documents are not supported")
        page_count = len(reader.pages)
        if page_count < 1:
            raise ValueError("Candidate PDF has no pages")
        if page_count > limits.max_pdf_pages:
            raise ValueError(f"Candidate PDF exceeds the {limits.max_pdf_pages}-page limit")
        has_active_content = _pdf_has_active_content(
            reader,
            max_objects=limits.max_pdf_objects,
            max_items=selected_work_limits.traversal_items,
            allow_safe_hyperlinks=allow_safe_hyperlinks,
        )
        if reject_active_content and has_active_content:
            raise ValueError("Candidate PDF contains active or embedded content")
        _validate_pdf_content_expansion(
            reader,
            limits=limits,
            work_limits=selected_work_limits,
        )
        page_texts: list[str] = []
        extracted_characters = 0
        extracted_bytes = 0
        extracted_lines = 0
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception as exc:  # pypdf parser implementations expose varied failures.
                raise ValueError(
                    f"Could not extract text from candidate PDF page {page_number}"
                ) from exc
            extracted_characters += len(page_text)
            extracted_bytes += len(page_text.encode("utf-8"))
            extracted_lines += len(page_text.splitlines())
            if extracted_characters > limits.max_extracted_characters:
                raise ValueError("Extracted candidate PDF text exceeds the character limit")
            if extracted_bytes > limits.max_extracted_bytes:
                raise ValueError("Extracted candidate PDF text exceeds the UTF-8 byte limit")
            if extracted_lines > limits.max_extracted_lines:
                raise ValueError("Extracted candidate PDF text exceeds the line limit")
            page_texts.append(page_text)
    except ValueError:
        raise
    except (OSError, PdfReadError) as exc:
        raise ValueError("Candidate PDF could not be read safely") from exc
    except Exception as exc:
        raise ValueError("Candidate PDF could not be parsed safely") from exc
    return _PdfInspection(reader=reader, page_texts=tuple(page_texts))


def _pdf_document(
    path: Path,
    content: bytes,
    *,
    media_type: str,
    limits: DocumentIntakeLimits,
    allow_safe_hyperlinks: bool = False,
) -> ExtractedCandidateDocument:
    inspection = _inspect_pdf_bytes(
        content,
        limits=limits,
        reject_active_content=True,
        allow_safe_hyperlinks=allow_safe_hyperlinks,
    )
    pages = [
        _TextSegment(ordinal=page_number, text=page_text)
        for page_number, page_text in enumerate(inspection.page_texts, start=1)
    ]
    try:
        text, locations = _segmented_text(
            pages,
            kind="page",
            blank_line_between_segments=True,
            limits=limits,
        )
    except ValueError as exc:
        if "no extractable text" in str(exc):
            raise ValueError(
                "Candidate PDF contains no extractable text; OCR is disabled, so provide a "
                "text-searchable PDF, Markdown, DOCX, or plain text"
            ) from exc
        raise
    return _result(
        path=path,
        content=content,
        input_format="pdf",
        media_type=media_type,
        text=text,
        locations=locations,
        limits=limits,
        page_count=len(inspection.page_texts),
    )


def _safe_archive_name(name: str) -> bool:
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or "//" in name
        or ":" in name
        or name.startswith("/")
    ):
        return False
    trimmed = name[:-1] if name.endswith("/") else name
    if not trimmed:
        return False
    pure = PurePosixPath(trimmed)
    return not pure.is_absolute() and all(part not in {"", ".", ".."} for part in pure.parts)


def _archive_read(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except Exception as exc:  # ZIP readers expose format- and codec-specific failures.
        raise ValueError("Candidate DOCX archive member could not be read safely") from exc


def _validate_docx_xml_preflight(
    content: bytes,
    *,
    label: str,
    limits: DocumentIntakeLimits,
    budget: _DocxXmlBudget,
    xml_limits: _DocxXmlLimits,
) -> None:
    lowered = content.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError(f"DOCX {label} contains prohibited DTD or entity content")
    parser = expat.ParserCreate(namespace_separator="}")
    depth = 0

    def validate_name(name: str) -> None:
        if len(name) > xml_limits.name_length:
            raise ValueError(f"DOCX {label} contains an overlong XML name")
        budget.unique_names.add(name)
        if len(budget.unique_names) > xml_limits.unique_names:
            raise ValueError("Candidate DOCX exceeds the unique XML name limit")

    def start_namespace(_prefix: str | None, uri: str | None) -> None:
        namespace = uri or ""
        budget.namespaces += 1
        if budget.namespaces > xml_limits.namespaces:
            raise ValueError("Candidate DOCX exceeds the XML namespace declaration limit")
        if len(namespace) > xml_limits.namespace_length:
            raise ValueError(f"DOCX {label} contains an overlong XML namespace")
        budget.unique_namespaces.add(namespace)
        if len(budget.unique_namespaces) > xml_limits.unique_namespaces:
            raise ValueError("Candidate DOCX exceeds the unique XML namespace limit")

    def start_element(name: str, attributes: dict[str, str]) -> None:
        nonlocal depth
        depth += 1
        budget.elements += 1
        budget.attributes += len(attributes)
        if depth > limits.max_docx_xml_depth:
            raise ValueError(f"DOCX {label} exceeds the safe XML depth limit")
        if budget.elements > xml_limits.elements:
            raise ValueError("Candidate DOCX exceeds the XML element limit")
        if budget.attributes > xml_limits.attributes:
            raise ValueError("Candidate DOCX exceeds the XML attribute limit")
        validate_name(name)
        for attribute_name in attributes:
            validate_name(attribute_name)

    def end_element(_name: str) -> None:
        nonlocal depth
        depth -= 1

    def prohibited_declaration(*_args: object) -> None:
        raise ValueError(f"DOCX {label} contains prohibited DTD or entity content")

    parser.StartNamespaceDeclHandler = start_namespace
    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.StartDoctypeDeclHandler = prohibited_declaration
    parser.EntityDeclHandler = prohibited_declaration
    parser.ExternalEntityRefHandler = lambda *_args: 0
    try:
        for offset in range(0, len(content), 64 * 1024):
            parser.Parse(content[offset : offset + 64 * 1024], False)
        parser.Parse(b"", True)
    except ValueError:
        raise
    except expat.ExpatError as exc:
        raise ValueError(f"DOCX {label} could not be parsed safely") from exc


def _xml_root(
    content: bytes,
    *,
    label: str,
    limits: DocumentIntakeLimits,
    budget: _DocxXmlBudget,
    xml_limits: _DocxXmlLimits,
) -> object:
    _validate_docx_xml_preflight(
        content,
        label=label,
        limits=limits,
        budget=budget,
        xml_limits=xml_limits,
    )
    try:
        from defusedxml import ElementTree
        from defusedxml.common import DefusedXmlException

        return ElementTree.fromstring(content)
    except (DefusedXmlException, ParseError, ValueError) as exc:
        raise ValueError(f"DOCX {label} could not be parsed safely") from exc


def _iter_xml_elements(root: object) -> Iterator[object]:
    pending = [root]
    while pending:
        element = pending.pop()
        yield element
        pending.extend(reversed(list(element)))  # type: ignore[arg-type]


def _validate_xml_depth(root: object, *, label: str, limit: int) -> None:
    pending = [(root, 1)]
    while pending:
        element, depth = pending.pop()
        if depth > limit:
            raise ValueError(f"DOCX {label} exceeds the safe XML depth limit")
        pending.extend((child, depth + 1) for child in element)  # type: ignore[union-attr]


def _docx_relationship_source_part(relationship_name: str) -> str | None:
    if relationship_name == "_rels/.rels":
        return None
    relationship_path = PurePosixPath(relationship_name)
    if relationship_path.parent.name != "_rels" or not relationship_path.name.endswith(".rels"):
        raise ValueError("Candidate DOCX contains a malformed relationship part path")
    source_name = relationship_path.name[: -len(".rels")]
    source_path = relationship_path.parent.parent / source_name
    source = source_path.as_posix()
    if not _safe_archive_name(source):
        raise ValueError("Candidate DOCX contains a malformed relationship part path")
    return source


def _docx_content_type(
    part_name: str, overrides: dict[str, str], defaults: dict[str, str]
) -> str | None:
    key = part_name.translate(_ASCII_LOWER)
    return overrides.get(key, defaults.get(PurePosixPath(key).suffix[1:]))


def _docx_relationship_part_name(source_part: str) -> str:
    source = PurePosixPath(source_part)
    return (source.parent / "_rels" / f"{source.name}.rels").as_posix()


def _resolve_docx_relationship_target(source_part: str | None, target: str) -> str:
    if not target or any(character in target for character in ("\\", "%", "?", "#")):
        raise ValueError("Candidate DOCX contains an unsafe relationship target")
    if target.startswith("/"):
        joined = target.lstrip("/")
    else:
        base = PurePosixPath(source_part).parent.as_posix() if source_part else ""
        joined = posixpath.join(base, target)
    normalized = posixpath.normpath(joined)
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise ValueError("Candidate DOCX contains an unsafe relationship target")
    if not _safe_archive_name(normalized):
        raise ValueError("Candidate DOCX contains an unsafe relationship target")
    return normalized


def _docx_relationship_kind(relationship_type: str) -> str | None:
    for base in _DOCX_RELATIONSHIP_BASES:
        if relationship_type.startswith(base):
            kind = relationship_type[len(base) :]
            return kind if kind and "/" not in kind else None
    return None


def _read_docx_relationships(
    archive: zipfile.ZipFile,
    relationship_name: str,
    *,
    names: set[str],
    limits: DocumentIntakeLimits,
    xml_budget: _DocxXmlBudget,
    xml_limits: _DocxXmlLimits,
    allow_safe_hyperlinks: bool = False,
) -> list[_DocxRelationship]:
    source_part = _docx_relationship_source_part(relationship_name)
    root = _xml_root(
        _archive_read(archive, relationship_name),
        label="relationships",
        limits=limits,
        budget=xml_budget,
        xml_limits=xml_limits,
    )
    _validate_xml_depth(root, label="relationships", limit=limits.max_docx_xml_depth)
    if str(root.tag) != f"{{{_PACKAGE_RELATIONSHIP_NAMESPACE}}}Relationships":  # type: ignore[union-attr]
        raise ValueError("Candidate DOCX relationship part has an invalid root")
    relationships: list[_DocxRelationship] = []
    seen_ids: set[str] = set()
    active_relationship_kinds = {
        "afchunk",
        "attachedtemplate",
        "control",
        "externallink",
        "oleobject",
        "package",
        "vbaproject",
    }
    relationship_tag = f"{{{_PACKAGE_RELATIONSHIP_NAMESPACE}}}Relationship"
    for relationship in _iter_xml_elements(root):
        if str(relationship.tag) != relationship_tag:  # type: ignore[union-attr]
            continue
        relationship_id = str(relationship.attrib.get("Id", "")).strip()  # type: ignore[union-attr]
        relationship_type = str(relationship.attrib.get("Type", "")).strip()  # type: ignore[union-attr]
        raw_target = str(relationship.attrib.get("Target", "")).strip()  # type: ignore[union-attr]
        target_mode = str(relationship.attrib.get("TargetMode", "")).strip().casefold()  # type: ignore[union-attr]
        if not relationship_id or not relationship_type or not raw_target:
            raise ValueError("Candidate DOCX contains a malformed relationship")
        if relationship_id in seen_ids:
            raise ValueError("Candidate DOCX contains duplicate relationship identifiers")
        seen_ids.add(relationship_id)
        if target_mode == "external":
            if (
                allow_safe_hyperlinks
                and _docx_relationship_kind(relationship_type) == "hyperlink"
                and raw_target == relationship.attrib.get("Target", "")
                and _safe_http_link(raw_target)
            ):
                continue
            raise ValueError("Candidate DOCX contains an external relationship")
        if target_mode not in {"", "internal"}:
            raise ValueError("Candidate DOCX contains an invalid relationship target mode")
        target = _resolve_docx_relationship_target(source_part, raw_target)
        if target not in names:
            raise ValueError("Candidate DOCX relationship target is missing from the package")
        kind = _docx_relationship_kind(relationship_type)
        terminal_kind = relationship_type.rsplit("/", 1)[-1].casefold()
        if terminal_kind in active_relationship_kinds:
            raise ValueError("Candidate DOCX contains an active or embedded relationship")
        relationships.append(
            _DocxRelationship(
                kind=kind or "",
                target=target,
            )
        )
    return relationships


def _reject_dangerous_docx_field(instruction: str) -> None:
    normalized = " ".join(instruction.split())
    if normalized and _DOCX_DANGEROUS_FIELD_COMMAND.search(normalized):
        raise ValueError("Candidate DOCX contains a dangerous field instruction")


def _validate_docx_field_instructions(root: object) -> None:
    field_char_tag = f"{{{_WORD_NAMESPACE}}}fldChar"
    field_char_type = f"{{{_WORD_NAMESPACE}}}fldCharType"
    field_simple_tag = f"{{{_WORD_NAMESPACE}}}fldSimple"
    field_instruction = f"{{{_WORD_NAMESPACE}}}instr"
    instruction_text_tag = f"{{{_WORD_NAMESPACE}}}instrText"
    open_fields: list[list[str] | None] = []
    for element in _iter_xml_elements(root):
        tag = str(element.tag)  # type: ignore[union-attr]
        if tag == field_simple_tag:
            _reject_dangerous_docx_field(str(element.attrib.get(field_instruction, "")))  # type: ignore[union-attr]
        elif tag == field_char_tag:
            marker = str(element.attrib.get(field_char_type, "")).casefold()  # type: ignore[union-attr]
            if marker == "begin":
                open_fields.append([])
            elif marker in {"separate", "end"}:
                if not open_fields:
                    raise ValueError("Candidate DOCX contains malformed field instructions")
                current = open_fields[-1]
                if current is not None:
                    _reject_dangerous_docx_field("".join(current))
                if marker == "separate":
                    open_fields[-1] = None
                else:
                    open_fields.pop()
        elif tag == instruction_text_tag and element.text:  # type: ignore[union-attr]
            if open_fields and open_fields[-1] is not None:
                open_fields[-1].append(element.text)  # type: ignore[union-attr]
            else:
                _reject_dangerous_docx_field(element.text)  # type: ignore[union-attr]
    if open_fields:
        raise ValueError("Candidate DOCX contains malformed field instructions")


def _docx_text_segments(
    root: object,
    *,
    part: str,
    current_paragraph_count: int,
    limits: DocumentIntakeLimits,
) -> tuple[list[_TextSegment], int]:
    _validate_xml_depth(root, label=part, limit=limits.max_docx_xml_depth)
    _validate_docx_field_instructions(root)
    paragraph_tag = f"{{{_WORD_NAMESPACE}}}p"
    text_tag = f"{{{_WORD_NAMESPACE}}}t"
    tab_tag = f"{{{_WORD_NAMESPACE}}}tab"
    break_tags = {f"{{{_WORD_NAMESPACE}}}br", f"{{{_WORD_NAMESPACE}}}cr"}
    if any(
        str(element.tag).endswith("}altChunk")  # type: ignore[union-attr]
        for element in _iter_xml_elements(root)
    ):
        raise ValueError("Candidate DOCX contains alternate embedded content")

    segments: list[_TextSegment] = []
    part_ordinal = 0
    for paragraph in _iter_xml_elements(root):
        if str(paragraph.tag) != paragraph_tag:  # type: ignore[union-attr]
            continue
        part_ordinal += 1
        current_paragraph_count += 1
        if current_paragraph_count > limits.max_docx_paragraphs:
            raise ValueError("Candidate DOCX exceeds the paragraph limit")
        chunks: list[str] = []
        pending = list(reversed(list(paragraph)))  # type: ignore[arg-type]
        while pending:
            child = pending.pop()
            tag = str(child.tag)
            if tag == paragraph_tag:
                continue
            if tag == text_tag and child.text:
                chunks.append(child.text)
            elif tag == tab_tag:
                chunks.append("\t")
            elif tag in break_tags:
                chunks.append("\n")
            pending.extend(reversed(list(child)))
        paragraph_text = " ".join("".join(chunks).split())
        if paragraph_text:
            segments.append(_TextSegment(ordinal=part_ordinal, text=paragraph_text, part=part))
    return segments, current_paragraph_count


def _validate_docx_container_preflight(
    content: bytes,
    *,
    limits: DocumentIntakeLimits,
) -> None:
    search_start = max(0, len(content) - (_ZIP_EOCD_SIZE + 65_535))
    eocd_offset = content.rfind(_ZIP_EOCD_SIGNATURE, search_start)
    while eocd_offset >= 0:
        if eocd_offset + _ZIP_EOCD_SIZE <= len(content):
            comment_length = struct.unpack_from("<H", content, eocd_offset + 20)[0]
            if eocd_offset + _ZIP_EOCD_SIZE + comment_length == len(content):
                break
        eocd_offset = content.rfind(_ZIP_EOCD_SIGNATURE, search_start, eocd_offset)
    if eocd_offset < 0:
        raise ValueError("Candidate DOCX end-of-central-directory record is missing")

    (
        _signature,
        disk_number,
        central_directory_disk,
        entries_on_disk,
        total_entries,
        directory_size,
        directory_offset,
        _comment_length,
    ) = struct.unpack_from("<4s4H2LH", content, eocd_offset)
    if (
        eocd_offset >= 20
        and content[eocd_offset - 20 : eocd_offset - 16] == _ZIP64_LOCATOR_SIGNATURE
    ) or any(
        value == sentinel
        for value, sentinel in (
            (entries_on_disk, 0xFFFF),
            (total_entries, 0xFFFF),
            (directory_size, 0xFFFFFFFF),
            (directory_offset, 0xFFFFFFFF),
        )
    ):
        raise ValueError("ZIP64 DOCX candidate documents are not supported")
    if disk_number != 0 or central_directory_disk != 0 or entries_on_disk != total_entries:
        raise ValueError("Multidisk DOCX candidate documents are not supported")
    if total_entries < 1 or total_entries > limits.max_docx_members:
        raise ValueError("Candidate DOCX contains an unsafe number of archive members")
    if directory_size > limits.max_docx_central_directory_bytes:
        raise ValueError("Candidate DOCX central directory exceeds the safe size limit")
    directory_end = directory_offset + directory_size
    if directory_offset > eocd_offset or directory_end != eocd_offset:
        raise ValueError("Candidate DOCX central-directory layout is malformed")

    position = directory_offset
    actual_entries = 0
    while position < directory_end:
        if (
            position + _ZIP_CENTRAL_DIRECTORY_RECORD_SIZE > directory_end
            or content[position : position + 4] != _ZIP_CENTRAL_DIRECTORY_SIGNATURE
        ):
            raise ValueError("Candidate DOCX central-directory record is malformed")
        name_length, extra_length, comment_length = struct.unpack_from(
            "<HHH",
            content,
            position + 28,
        )
        record_size = (
            _ZIP_CENTRAL_DIRECTORY_RECORD_SIZE + name_length + extra_length + comment_length
        )
        position += record_size
        actual_entries += 1
        if position > directory_end or actual_entries > limits.max_docx_members:
            raise ValueError("Candidate DOCX contains an unsafe number of archive members")
    if position != directory_end or actual_entries != total_entries:
        raise ValueError("Candidate DOCX central-directory entry count is inconsistent")


def _docx_document(
    path: Path,
    content: bytes,
    *,
    media_type: str,
    limits: DocumentIntakeLimits,
    allow_safe_hyperlinks: bool = False,
) -> ExtractedCandidateDocument:
    if not content.startswith(_ZIP_SIGNATURES):
        raise ValueError("Candidate document signature does not match its DOCX suffix")
    _validate_docx_container_preflight(content, limits=limits)
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("Candidate DOCX could not be read safely") from exc
    with archive:
        xml_budget = _DocxXmlBudget()
        xml_limits = _DocxXmlLimits()
        members = archive.infolist()
        if not members or len(members) > limits.max_docx_members:
            raise ValueError("Candidate DOCX contains an unsafe number of archive members")
        names = [member.filename for member in members]
        if len(names) != len(set(names)) or any(not _safe_archive_name(name) for name in names):
            raise ValueError("Candidate DOCX contains duplicate or unsafe archive paths")
        total_size = 0
        for member in members:
            if member.flag_bits & 0x1:
                raise ValueError("Encrypted DOCX candidate documents are not supported")
            if member.file_size > limits.max_docx_member_bytes:
                raise ValueError("Candidate DOCX archive member exceeds the size limit")
            total_size += member.file_size
            if total_size > limits.max_docx_uncompressed_bytes:
                raise ValueError("Candidate DOCX expands beyond the uncompressed size limit")
            if (
                member.file_size > 0
                and member.file_size / max(member.compress_size, 1)
                > limits.max_docx_compression_ratio
            ):
                raise ValueError("Candidate DOCX archive compression ratio is unsafe")
        lowered_names = {name.casefold() for name in names}
        if any(
            name.endswith(".bin")
            or "/embeddings/" in name
            or "/activex/" in name
            or "/customui/" in name
            or name.startswith(("embeddings/", "activex/", "customui/"))
            or "/afchunk" in name
            for name in lowered_names
        ):
            raise ValueError("Candidate DOCX contains macros, embedded objects, or active content")
        required = {"[Content_Types].xml", "_rels/.rels"}
        if not required.issubset(names):
            raise ValueError("Candidate DOCX package structure is incomplete")

        content_types = _xml_root(
            _archive_read(archive, "[Content_Types].xml"),
            label="content types",
            limits=limits,
            budget=xml_budget,
            xml_limits=xml_limits,
        )
        _validate_xml_depth(
            content_types,
            label="content types",
            limit=limits.max_docx_xml_depth,
        )
        if str(content_types.tag) != f"{{{_CONTENT_TYPE_NAMESPACE}}}Types":  # type: ignore[union-attr]
            raise ValueError("Candidate DOCX content types have an invalid root")
        content_type_overrides: dict[str, str] = {}
        content_type_defaults: dict[str, str] = {}
        prohibited_content_type_tokens = ("activex", "macroenabled", "oleobject", "vbaproject")
        for item in _iter_xml_elements(content_types):
            if str(item.tag) not in {
                f"{{{_CONTENT_TYPE_NAMESPACE}}}Default",
                f"{{{_CONTENT_TYPE_NAMESPACE}}}Override",
            }:
                continue
            content_type = str(item.attrib.get("ContentType", ""))
            if any(token in content_type.casefold() for token in prohibited_content_type_tokens):
                raise ValueError("Candidate DOCX contains macro-enabled or embedded content")
            if str(item.tag) == f"{{{_CONTENT_TYPE_NAMESPACE}}}Override":
                raw_part_name = str(item.attrib.get("PartName", ""))
                if not raw_part_name.startswith("/"):
                    raise ValueError("Candidate DOCX content type override is malformed")
                part_name = raw_part_name[1:].translate(_ASCII_LOWER)
                if not _safe_archive_name(part_name) or not content_type:
                    raise ValueError("Candidate DOCX content type override is malformed")
                if part_name in content_type_overrides:
                    raise ValueError("Candidate DOCX contains duplicate content type overrides")
                content_type_overrides[part_name] = content_type
            else:
                extension = str(item.attrib.get("Extension", "")).translate(_ASCII_LOWER)
                if (
                    not extension
                    or any(character in extension for character in ".\\/")
                    or any(character.isspace() for character in extension)
                    or not _safe_archive_name(extension)
                    or not content_type
                ):
                    raise ValueError("Candidate DOCX content type default is malformed")
                if extension in content_type_defaults:
                    raise ValueError("Candidate DOCX contains duplicate content type defaults")
                content_type_defaults[extension] = content_type

        name_set = set(names)
        relationship_parts = {
            relationship_name: _read_docx_relationships(
                archive,
                relationship_name,
                names=name_set,
                limits=limits,
                xml_budget=xml_budget,
                xml_limits=xml_limits,
                allow_safe_hyperlinks=allow_safe_hyperlinks,
            )
            for relationship_name in sorted(
                name for name in names if name.casefold().endswith(".rels")
            )
        }
        package_office_documents = [
            relationship
            for relationship in relationship_parts["_rels/.rels"]
            if relationship.kind == "officeDocument"
        ]
        if len(package_office_documents) != 1:
            raise ValueError(
                "Candidate DOCX must contain exactly one internal package officeDocument "
                "relationship"
            )
        main_part = package_office_documents[0].target
        if (
            _docx_content_type(main_part, content_type_overrides, content_type_defaults)
            != _DOCX_MAIN_CONTENT_TYPE
        ):
            raise ValueError("Candidate DOCX main document content type is invalid")

        main_relationships = relationship_parts.get(
            _docx_relationship_part_name(main_part),
            [],
        )
        visible_parts_by_target: dict[str, str] = {}
        for relationship in main_relationships:
            if relationship.kind not in _DOCX_VISIBLE_PART_CONTENT_TYPES:
                continue
            expected_type = _DOCX_VISIBLE_PART_CONTENT_TYPES[relationship.kind]
            if (
                _docx_content_type(
                    relationship.target, content_type_overrides, content_type_defaults
                )
                != expected_type
            ):
                raise ValueError("Candidate DOCX visible text part content type is invalid")
            previous_kind = visible_parts_by_target.get(relationship.target)
            if previous_kind is not None and previous_kind != relationship.kind:
                raise ValueError("Candidate DOCX contains conflicting visible text relationships")
            visible_parts_by_target[relationship.target] = relationship.kind

        selected_parts = [("document", main_part)]
        selected_parts.extend(
            sorted(
                (
                    (relationship_kind, target)
                    for target, relationship_kind in visible_parts_by_target.items()
                ),
                key=lambda item: (_DOCX_VISIBLE_PART_ORDER[item[0]], item[1]),
            )
        )
        segments: list[_TextSegment] = []
        paragraph_count = 0
        for relationship_kind, part_name in selected_parts:
            root = _xml_root(
                _archive_read(archive, part_name),
                label=part_name,
                limits=limits,
                budget=xml_budget,
                xml_limits=xml_limits,
            )
            expected_root = (
                "document"
                if relationship_kind == "document"
                else _DOCX_VISIBLE_PART_ROOTS[relationship_kind]
            )
            if str(root.tag) != f"{{{_WORD_NAMESPACE}}}{expected_root}":  # type: ignore[union-attr]
                raise ValueError("Candidate DOCX visible text part has an invalid root")
            part_segments, paragraph_count = _docx_text_segments(
                root,
                part=part_name,
                current_paragraph_count=paragraph_count,
                limits=limits,
            )
            segments.extend(part_segments)
    text, locations = _segmented_text(
        segments,
        kind="paragraph",
        blank_line_between_segments=False,
        limits=limits,
    )
    return _result(
        path=path,
        content=content,
        input_format="docx",
        media_type=media_type,
        text=text,
        locations=locations,
        limits=limits,
    )


def extract_candidate_document(
    path: str | Path,
    *,
    limits: DocumentIntakeLimits | None = None,
) -> ExtractedCandidateDocument:
    """Extract bounded text and content-free provenance from one local career document."""
    selected_limits = limits or DocumentIntakeLimits()
    source = Path(path).expanduser()
    suffix = source.suffix.casefold()
    format_details = _FORMAT_BY_SUFFIX.get(suffix)
    if format_details is None:
        raise ValueError("Candidate document must be a .txt, .md, .pdf, or .docx file")
    content = _read_bounded_regular_file(source, selected_limits)
    return extract_document_bytes(content, filename=source.name, limits=selected_limits)


def extract_document_bytes(
    content: bytes,
    *,
    filename: str,
    limits: DocumentIntakeLimits | None = None,
    allow_safe_hyperlinks: bool = False,
) -> ExtractedCandidateDocument:
    """Inspect supplied bytes; output imports may opt into ordinary HTTP(S) links.

    The default retains candidate-source rejection of all external relationships.
    This function never opens the supplied filename or dereferences a hyperlink.
    """
    selected_limits = limits or DocumentIntakeLimits()
    if (
        not isinstance(content, bytes)
        or not 1 <= len(content) <= selected_limits.max_document_bytes
    ):
        raise ValueError("Candidate document bytes are empty or exceed the size limit")
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or "\\" in filename
        or len(filename) > 200
    ):
        raise ValueError("Candidate document filename must be a bounded basename")
    source = Path(filename)
    format_details = _FORMAT_BY_SUFFIX.get(source.suffix.casefold())
    if format_details is None:
        raise ValueError("Candidate document must be a .txt, .md, .pdf, or .docx file")
    input_format, media_type = format_details
    if input_format in {"text", "markdown"}:
        return _text_document(
            source,
            content,
            input_format=input_format,
            media_type=media_type,
            limits=selected_limits,
        )
    if input_format == "pdf":
        return _pdf_document(
            source,
            content,
            media_type=media_type,
            limits=selected_limits,
            allow_safe_hyperlinks=allow_safe_hyperlinks,
        )
    return _docx_document(
        source,
        content,
        media_type=media_type,
        limits=selected_limits,
        allow_safe_hyperlinks=allow_safe_hyperlinks,
    )


__all__ = [
    "DocumentFormat",
    "DocumentIntakeLimits",
    "DocumentLocation",
    "ExtractedCandidateDocument",
    "extract_candidate_document",
    "extract_document_bytes",
]
