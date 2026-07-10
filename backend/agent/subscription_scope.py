"""
구독 ID 기준으로 평가 리포트·Terraform 산출물 텍스트를 매칭합니다.
"""

from __future__ import annotations

import json
from pathlib import Path


HEAD_BYTES = 96 * 1024


def normalize_subscription_id(subscription_id: str) -> str:
    return subscription_id.strip().lower().replace("{", "").replace("}", "")


def text_contains_subscription_arm_path(text: str, subscription_id: str) -> bool:
    """ARM 경로 `/subscriptions/{guid}/` 포함 여부."""
    sid = normalize_subscription_id(subscription_id)
    if not sid:
        return True
    needle = f"/subscriptions/{sid}/"
    return needle in (text or "").lower()


def assessment_dict_matches_subscription(ass: dict, subscription_id: str | None) -> bool:
    """평가 항목(dict)의 resource_id가 해당 구독 범위인지 여부. subscription_id 없으면 통과."""
    if not subscription_id or not str(subscription_id).strip():
        return True
    rid = str(ass.get("resource_id") or "")
    return text_contains_subscription_arm_path(rid, subscription_id)


def report_content_matches_subscription(content: str, subscription_id: str) -> bool:
    """JSON 집계 리포트 또는 MD/HTML 등 본문에서 구독 범위 판별."""
    if not subscription_id:
        return True
    ct = (content or "").lstrip()
    if ct.startswith("{"):
        try:
            data = json.loads(ct)
            if isinstance(data, dict) and isinstance(data.get("assessments"), list):
                for a in data["assessments"]:
                    if not isinstance(a, dict):
                        continue
                    rid = str(a.get("resource_id") or "")
                    if text_contains_subscription_arm_path(rid, subscription_id):
                        return True
                return False
        except json.JSONDecodeError:
            pass
    return text_contains_subscription_arm_path(content, subscription_id)


def read_local_head(path: Path, max_bytes: int = HEAD_BYTES) -> str:
    with open(path, "rb") as f:
        return f.read(max_bytes).decode("utf-8", errors="ignore")
