from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from career_assistant.settings import Settings

MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
ALLOWED_MEDIA_TYPES = {"text/plain", "application/pdf"}


class ArtifactError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TextChunk:
    locator: str
    text: str


def artifact_cipher(settings: Settings) -> Fernet:
    value = (
        settings.security.artifact_key.get_secret_value()
        if settings.security.artifact_key
        else None
    )
    if settings.security.artifact_key_file:
        try:
            value = settings.security.artifact_key_file.read_text(encoding="ascii").strip()
        except OSError as error:
            raise ArtifactError(
                "ARTIFACT_KEY_UNAVAILABLE", "Artifact encryption is unavailable"
            ) from error
    if not value:
        raise ArtifactError("ARTIFACT_KEY_UNAVAILABLE", "Artifact encryption is unavailable")
    try:
        return Fernet(value.encode("ascii"))
    except (ValueError, TypeError) as error:
        raise ArtifactError("ARTIFACT_KEY_INVALID", "Artifact encryption is unavailable") from error


def validate_artifact(filename: str, media_type: str, content: bytes) -> tuple[str, str]:
    if len(content) > MAX_ARTIFACT_BYTES:
        raise ArtifactError("ARTIFACT_TOO_LARGE", "Artifact exceeds the upload size limit")
    suffix = Path(filename).suffix.casefold()
    is_pdf = content.startswith(b"%PDF-")
    if media_type == "application/pdf" or suffix == ".pdf":
        if not is_pdf:
            raise ArtifactError("ARTIFACT_TYPE_REJECTED", "The uploaded PDF is invalid")
        return "application/pdf", "cv"
    if media_type != "text/plain" and suffix not in {".txt", ".text"}:
        raise ArtifactError("ARTIFACT_TYPE_REJECTED", "Only text and PDF artifacts are supported")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ArtifactError("ARTIFACT_TYPE_REJECTED", "Text artifacts must be UTF-8") from error
    return "text/plain", "cv"


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def extract_chunks(media_type: str, content: bytes) -> list[TextChunk]:
    if media_type == "text/plain":
        return [
            TextChunk(f"line:{number}", line.strip())
            for number, line in enumerate(content.decode("utf-8").splitlines(), 1)
            if line.strip()
        ]
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        chunks = [
            TextChunk(f"page:{number}", (page.extract_text() or "").strip())
            for number, page in enumerate(reader.pages, 1)
        ]
    except (PdfReadError, ValueError, IndexError) as error:
        raise ArtifactError("ARTIFACT_PARSE_FAILED", "The PDF could not be read") from error
    if not any(chunk.text for chunk in chunks):
        raise ArtifactError("ARTIFACT_NO_TEXT", "The PDF contains no extractable text")
    return [chunk for chunk in chunks if chunk.text]


def decrypt_artifact(settings: Settings, encrypted: bytes) -> bytes:
    try:
        return artifact_cipher(settings).decrypt(encrypted)
    except (InvalidToken, ArtifactError) as error:
        raise ArtifactError("ARTIFACT_DECRYPT_FAILED", "The artifact could not be read") from error
