"""시드 체크리스트 문서 검증 — 대량 등록 전에 스키마 결함을 걸러낸다."""

import pytest

from agent.checklist_seed import ChecklistValidationError, iter_checklist_files, validate_checklist_document


def _valid_doc():
    return {
        "metadata": {"name": "샘플", "applicable_resource_types": ["Microsoft.Storage/storageAccounts"]},
        "categories": [
            {
                "id": "security",
                "name": "보안",
                "items": [
                    {
                        "id": "transport",
                        "name": "전송 보안",
                        "checks": [{"question": "HTTPS 전용인가?"}],
                    }
                ],
            }
        ],
    }


def test_valid_document_passes():
    validate_checklist_document(_valid_doc())


def test_missing_metadata_name_is_rejected():
    doc = _valid_doc()
    doc["metadata"]["name"] = ""

    with pytest.raises(ChecklistValidationError, match="metadata.name"):
        validate_checklist_document(doc)


def test_category_without_id_is_rejected():
    doc = _valid_doc()
    del doc["categories"][0]["id"]

    with pytest.raises(ChecklistValidationError, match="categories\\[0\\].id"):
        validate_checklist_document(doc)


def test_check_without_question_is_rejected():
    doc = _valid_doc()
    doc["categories"][0]["items"][0]["checks"][0] = {"priority": "HIGH"}

    with pytest.raises(ChecklistValidationError, match="question"):
        validate_checklist_document(doc)


def test_applicable_resource_types_must_be_a_list():
    doc = _valid_doc()
    doc["metadata"]["applicable_resource_types"] = "Microsoft.Storage/storageAccounts"

    with pytest.raises(ChecklistValidationError, match="applicable_resource_types"):
        validate_checklist_document(doc)


def test_empty_applicable_resource_types_is_allowed_as_universal():
    doc = _valid_doc()
    doc["metadata"]["applicable_resource_types"] = []

    validate_checklist_document(doc)


def test_iter_checklist_files_returns_sorted_yaml_files(tmp_path):
    (tmp_path / "b.yaml").write_text("b", encoding="utf-8")
    (tmp_path / "a.yml").write_text("a", encoding="utf-8")
    (tmp_path / "note.md").write_text("skip", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "c.yaml").write_text("c", encoding="utf-8")

    found = [p.name for p in iter_checklist_files(tmp_path)]

    assert found == ["a.yml", "b.yaml", "c.yaml"]
