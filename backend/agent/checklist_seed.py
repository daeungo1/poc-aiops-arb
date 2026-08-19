"""시드 체크리스트 YAML 의 검증·탐색 — 대량(수백 건) 등록 파이프라인의 공통 부분."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Mapping

_YAML_SUFFIXES = (".yaml", ".yml")


class ChecklistValidationError(ValueError):
    """체크리스트 YAML 이 DB 적재 스키마를 만족하지 않을 때."""


def iter_checklist_files(root: Path) -> Iterator[Path]:
    """root 하위의 YAML 파일을 이름순으로 반환한다."""
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _YAML_SUFFIXES]
    yield from sorted(files, key=lambda p: p.name)


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ChecklistValidationError(f"{path} must be a mapping")
    return value


def _require_text(container: Mapping[str, Any], key: str, path: str) -> None:
    raw = container.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise ChecklistValidationError(f"{path}.{key} must be a non-empty string")


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ChecklistValidationError(f"{path} must be a non-empty list")
    return value


def validate_checklist_document(document: Any) -> None:
    """`upsert_from_yaml_content` 가 요구하는 키가 모두 있는지 확인한다.

    적재 도중 KeyError 로 중단되는 대신, 파일 단위로 원인을 알려 나머지 파일을 계속 처리하기 위함.
    """
    doc = _require_mapping(document, "document")

    metadata = _require_mapping(doc.get("metadata"), "metadata")
    _require_text(metadata, "name", "metadata")

    resource_types = metadata.get("applicable_resource_types", [])
    if not isinstance(resource_types, list):
        raise ChecklistValidationError("metadata.applicable_resource_types must be a list")

    categories = _require_list(doc.get("categories"), "categories")
    for cat_index, raw_category in enumerate(categories):
        cat_path = f"categories[{cat_index}]"
        category = _require_mapping(raw_category, cat_path)
        _require_text(category, "id", cat_path)
        _require_text(category, "name", cat_path)

        items = _require_list(category.get("items"), f"{cat_path}.items")
        for item_index, raw_item in enumerate(items):
            item_path = f"{cat_path}.items[{item_index}]"
            item = _require_mapping(raw_item, item_path)
            _require_text(item, "id", item_path)
            _require_text(item, "name", item_path)

            checks = _require_list(item.get("checks"), f"{item_path}.checks")
            for check_index, raw_check in enumerate(checks):
                check_path = f"{item_path}.checks[{check_index}]"
                check = _require_mapping(raw_check, check_path)
                _require_text(check, "question", check_path)


__all__ = [
    "ChecklistValidationError",
    "iter_checklist_files",
    "validate_checklist_document",
]
