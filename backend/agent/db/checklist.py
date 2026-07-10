"""
PostgreSQL 기반 체크리스트 CRUD 헬퍼.

DB_HOST 환경 변수가 설정되어 있을 때만 활성화.
checklists + checklist_items 테이블을 대상으로 upsert / 조회 / 삭제를 제공한다.
"""
from __future__ import annotations

import json
from typing import Any

from .connection import get_conn, is_db_configured

__all__ = [
    "is_db_configured",
    "upsert_from_yaml_content",
    "delete_checklist",
    "get_raw_yaml",
    "get_summary",
    "get_detail",
]

# ── Priority 매핑 ──────────────────────────────────────────────────────────
_PRIORITY_MAP: dict[str, str] = {
    "MUST": "High", "MANDATORY": "High", "HIGH": "High",
    "CONDITIONAL": "Medium", "MEDIUM": "Medium",
    "LOW": "Low", "N/A": "Low",
}


def _map_priority(raw: str | None) -> str:
    if not raw:
        return "Medium"
    return _PRIORITY_MAP.get(str(raw).upper().strip(), "Medium")


def _parse_expected_value(expected: Any) -> str | None:
    if expected is None:
        return None
    if isinstance(expected, bool):
        return str(expected).lower()
    if isinstance(expected, (dict, list)):
        return json.dumps(expected, ensure_ascii=False)
    return str(expected)


def _parse_versions(val: Any) -> list:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(v) for v in val]
    return [str(val)]


# ── Upsert ─────────────────────────────────────────────────────────────────

def upsert_from_yaml_content(
    file_key: str,
    raw_yaml: str | bytes,
    login_id: str = "",
    user_name: str = "",
    sso_no: str = "",
) -> int:
    """YAML 텍스트를 파싱해 checklists + checklist_items upsert. checklist_id 반환."""
    import yaml as _yaml

    if isinstance(raw_yaml, bytes):
        raw_yaml = raw_yaml.decode("utf-8")

    data = _yaml.safe_load(raw_yaml)
    meta = data.get("metadata", {})

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO checklists
                        (file_key, name, version, description,
                         applicable_resource_types,
                         login_id, user_name, sso_no, raw_yaml)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (file_key) DO UPDATE
                        SET name                      = EXCLUDED.name,
                            version                   = EXCLUDED.version,
                            description               = EXCLUDED.description,
                            applicable_resource_types = EXCLUDED.applicable_resource_types,
                            login_id                  = EXCLUDED.login_id,
                            user_name                 = EXCLUDED.user_name,
                            sso_no                    = EXCLUDED.sso_no,
                            raw_yaml                  = EXCLUDED.raw_yaml,
                            updated_at                = NOW()
                    RETURNING id
                    """,
                    (
                        file_key,
                        meta.get("name", file_key),
                        str(meta.get("version", "1.0")),
                        meta.get("description"),
                        json.dumps(
                            meta.get("applicable_resource_types", []),
                            ensure_ascii=False,
                        ),
                        login_id,
                        user_name,
                        sso_no,
                        raw_yaml,
                    ),
                )
                checklist_id: int = cur.fetchone()[0]

                cur.execute(
                    "DELETE FROM checklist_items WHERE checklist_id = %s",
                    (checklist_id,),
                )

                global_order = 0
                for cat_order, cat in enumerate(data.get("categories", []), start=1):
                    cat_id: str = cat["id"]
                    cat_name: str = cat["name"]
                    for item in cat.get("items", []):
                        item_id: str = item["id"]
                        item_name: str = item["name"]
                        for check in item.get("checks", []):
                            az = check.get("azure_check") or {}
                            condition = az.get("condition") or {}
                            expected_raw = az.get("expected") or az.get("expected_value")
                            rec_ver = _parse_versions(
                                az.get("recommended_versions") or az.get("recommended")
                            )
                            exp_ver = _parse_versions(az.get("expected_versions"))

                            cur.execute(
                                """
                                INSERT INTO checklist_items (
                                    checklist_id,
                                    category_id,    category_name,   category_order,
                                    item_id,        item_name,
                                    question,       priority,        display_order,
                                    check_type,     check_method,    resource_type,
                                    expected_value, condition_field, condition_equals,
                                    policy_effect,  guidance,
                                    expected_versions, recommended_versions
                                ) VALUES (
                                    %s,
                                    %s, %s, %s,
                                    %s, %s,
                                    %s, %s, %s,
                                    %s, %s, %s,
                                    %s, %s, %s,
                                    %s, %s,
                                    %s, %s
                                )
                                """,
                                (
                                    checklist_id,
                                    cat_id,   cat_name,  cat_order,
                                    item_id,  item_name,
                                    check["question"],
                                    _map_priority(check.get("priority")),
                                    global_order,
                                    az.get("type", "manual"),
                                    az.get("check_method"),
                                    az.get("resource_type"),
                                    _parse_expected_value(expected_raw),
                                    condition.get("field"),
                                    condition.get("equals"),
                                    az.get("policy_effect"),
                                    az.get("guidance"),
                                    json.dumps(exp_ver, ensure_ascii=False),
                                    json.dumps(rec_ver, ensure_ascii=False),
                                ),
                            )
                            global_order += 1

                return checklist_id
    finally:
        conn.close()


# ── Delete ─────────────────────────────────────────────────────────────────

def delete_checklist(file_key: str) -> bool:
    """file_key에 해당하는 체크리스트 삭제 (checklist_items CASCADE). 삭제 여부 반환."""
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM checklists WHERE file_key = %s RETURNING id",
                    (file_key,),
                )
                return cur.fetchone() is not None
    finally:
        conn.close()


# ── Read ───────────────────────────────────────────────────────────────────

def get_raw_yaml(file_key: str) -> str | None:
    """저장된 원본 YAML 반환. 없으면 None."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT raw_yaml FROM checklists WHERE file_key = %s",
                (file_key,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def get_summary() -> dict:
    """checklists + checklist_items 집계. ChecklistLoader.get_summary()와 동일 포맷."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.file_key,
                    c.name,
                    c.version,
                    c.applicable_resource_types,
                    COUNT(i.id)                                            AS total_checks,
                    COUNT(i.id) FILTER (WHERE i.check_type = 'automated') AS automated_checks,
                    COUNT(i.id) FILTER (WHERE i.check_type = 'manual')    AS manual_checks,
                    COUNT(DISTINCT i.category_id)                         AS categories
                FROM checklists c
                LEFT JOIN checklist_items i ON i.checklist_id = c.id
                GROUP BY c.id
                ORDER BY c.name
                """
            )
            rows = cur.fetchall()

        total_checks = 0
        auto_total = 0
        manual_total = 0
        checklists = []

        for row in rows:
            (
                file_key, name, version,
                applicable_resource_types,
                tc, ac, mc, cats,
            ) = row
            total_checks += tc
            auto_total += ac
            manual_total += mc

            checklists.append({
                "id": file_key,
                "name": name,
                "version": version or "1.0",
                "total_checks": tc,
                "automated_checks": ac,
                "manual_checks": mc,
                "categories": cats,
                "applicable_resource_types": applicable_resource_types or [],
            })

        return {
            "total_checklists": len(checklists),
            "total_checks": total_checks,
            "automated_checks": auto_total,
            "manual_checks": manual_total,
            "checklists": checklists,
        }
    finally:
        conn.close()


def get_detail(file_key: str) -> dict | None:
    """체크리스트 상세 (categories → items → checks 계층) 반환. 없으면 None."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, version, description, applicable_resource_types
                FROM checklists
                WHERE file_key = %s
                """,
                (file_key,),
            )
            row = cur.fetchone()
            if not row:
                return None
            checklist_id, name, version, description, art = row

            cur.execute(
                """
                SELECT
                    category_id, category_name, category_order,
                    item_id,     item_name,
                    question,    priority,       check_type,     check_method,
                    resource_type, expected_value,
                    condition_field, condition_equals,
                    policy_effect, guidance,
                    expected_versions, recommended_versions
                FROM checklist_items
                WHERE checklist_id = %s
                ORDER BY display_order
                """,
                (checklist_id,),
            )
            items = cur.fetchall()

        # 계층 구조로 재조립
        categories: dict[str, dict] = {}
        cat_order_map: dict[str, int] = {}

        for item in items:
            (
                cat_id, cat_name, cat_order,
                item_id, item_name,
                question, priority, check_type, check_method,
                resource_type, expected_value,
                cond_field, cond_eq,
                policy_effect, guidance,
                exp_ver, rec_ver,
            ) = item

            if cat_id not in categories:
                categories[cat_id] = {"id": cat_id, "name": cat_name, "items": {}}
                cat_order_map[cat_id] = cat_order

            item_key = f"{cat_id}::{item_id}"
            if item_key not in categories[cat_id]["items"]:
                categories[cat_id]["items"][item_key] = {
                    "id": item_id,
                    "name": item_name,
                    "checks": [],
                }

            categories[cat_id]["items"][item_key]["checks"].append({
                "question": question,
                "priority": priority,
                "check_type": check_type or "manual",
                "guidance": guidance or "",
                "azure_check": {
                    "type": check_type,
                    "check_method": check_method,
                    "resource_type": resource_type,
                    "expected_value": expected_value,
                    "condition": {"field": cond_field, "equals": cond_eq},
                    "policy_effect": policy_effect,
                    "guidance": guidance,
                    "expected_versions": exp_ver or [],
                    "recommended_versions": rec_ver or [],
                },
            })

        sorted_cats = sorted(
            categories.values(),
            key=lambda c: cat_order_map.get(c["id"], 0),
        )
        for cat in sorted_cats:
            cat["items"] = list(cat["items"].values())
            cat.pop("order", None)

        return {
            "name": name,
            "version": version,
            "description": description,
            "applicable_resource_types": art or [],
            "categories": sorted_cats,
        }
    finally:
        conn.close()
