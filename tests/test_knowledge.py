from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from career_assistant.artifacts import ArtifactError, extract_chunks, validate_artifact
from career_assistant.knowledge import (
    KnowledgeError,
    artifact_observation_key,
    extract_artifact_facts,
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


def test_pdf_cv_chunks_keep_page_line_locators(monkeypatch: pytest.MonkeyPatch) -> None:
    class Page:
        def __init__(self, text: str) -> None:
            self.text = text

        def extract_text(self) -> str:
            return self.text

    class Reader:
        pages = [
            Page("Skills\nPython, PostgreSQL\nExperience\nDirector of Engineering"),
            Page("Education\nComputer Science degree"),
        ]

    monkeypatch.setattr("career_assistant.artifacts.PdfReader", lambda *_args, **_kwargs: Reader())
    chunks = extract_chunks("application/pdf", b"%PDF-1.7")
    assert [(chunk.locator, chunk.text) for chunk in chunks] == [
        ("page:1:line:1", "Skills"),
        ("page:1:line:2", "Python, PostgreSQL"),
        ("page:1:line:3", "Experience"),
        ("page:1:line:4", "Director of Engineering"),
        ("page:2:line:1", "Education"),
        ("page:2:line:2", "Computer Science degree"),
    ]
    facts = extract_artifact_facts([(chunk.locator, chunk.text) for chunk in chunks])
    assert [(fact.predicate, fact.entity_type, fact.label) for fact in facts] == [
        ("POSSESSES", "Skill", "Python"),
        ("POSSESSES", "Skill", "PostgreSQL"),
        ("HELD_ROLE", "Role", "Director of Engineering"),
        ("STUDIED_AT", "Education", "Computer Science degree"),
    ]


def test_certification_heading_aliases_do_not_inherit_education() -> None:
    facts = extract_artifact_facts(
        [
            ("line:1", "Education"),
            ("line:2", "Computer Science"),
            ("line:3", "Licenses & Certifications"),
            ("line:4", "AWS Certified Solutions Architect"),
            ("line:5", "Affiliations"),
            ("line:6", "Institute of Electrical and Electronics Engineers"),
        ]
    )
    assert [(fact.predicate, fact.entity_type, fact.label) for fact in facts] == [
        ("STUDIED_AT", "Education", "Computer Science"),
        ("HOLDS", "Certification", "AWS Certified Solutions Architect"),
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


def test_artifact_observation_key_is_profile_stable_and_name_normalized() -> None:
    subject = UUID("019c0000-0000-7000-8000-000000000001")
    assert artifact_observation_key(subject, " Python ") == artifact_observation_key(
        subject, "python"
    )
    assert artifact_observation_key(subject, "Python", "POSSESSES") != artifact_observation_key(
        subject, "Python", "HELD_ROLE"
    )


def test_security_settings_accepts_a_test_artifact_key() -> None:
    key = Fernet.generate_key().decode("ascii")
    settings = SecuritySettings(artifact_key=SecretStr(key))
    assert settings.artifact_key is not None
