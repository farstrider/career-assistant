import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from career_assistant.artifacts import ArtifactError, extract_chunks, validate_artifact
from career_assistant.knowledge import (
    KnowledgeError,
    normalize_name,
    validate_entity_type,
    validate_relation_type,
)
from career_assistant.settings import SecuritySettings


def test_text_cv_chunks_keep_line_locators() -> None:
    chunks = extract_chunks("text/plain", b"Skills\nPython, PostgreSQL\n")
    assert [(chunk.locator, chunk.text) for chunk in chunks] == [
        ("line:1", "Skills"),
        ("line:2", "Python, PostgreSQL"),
    ]


def test_artifact_validation_rejects_mismatched_and_oversized_inputs() -> None:
    with pytest.raises(ArtifactError, match="invalid"):
        validate_artifact("cv.pdf", "application/pdf", b"not a pdf")
    with pytest.raises(ArtifactError, match="size limit"):
        validate_artifact("cv.txt", "text/plain", b"x" * (10 * 1024 * 1024 + 1))


def test_image_only_pdf_fails_without_ocr() -> None:
    with pytest.raises(ArtifactError):
        extract_chunks("application/pdf", b"%PDF-1.7\nnot a valid document")


def test_ontology_rejects_unregistered_types_and_normalizes_names() -> None:
    assert normalize_name("  Platform   Engineering ") == "platform engineering"
    assert validate_entity_type("Skill") == "Skill"
    assert validate_relation_type("USES") == "USES"
    with pytest.raises(KnowledgeError):
        validate_entity_type("Unknown")
    with pytest.raises(KnowledgeError):
        validate_relation_type("INVENTED")


def test_security_settings_accepts_a_test_artifact_key() -> None:
    key = Fernet.generate_key().decode("ascii")
    settings = SecuritySettings(artifact_key=SecretStr(key))
    assert settings.artifact_key is not None
