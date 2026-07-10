"""구독별 DB/로컬 산출물 경로 식별자 유틸."""

from __future__ import annotations

import re

from .subscription_scope import normalize_subscription_id

# 구 API·구조(타임스탬프/파일 2단) 호환용 식별자.
LEGACY_STORAGE_SUBSCRIPTION_KEY = "legacy"


def subscription_scope_key(subscription_id: str) -> str:
    """DB/로컬 경로 스코프 키로 쓸 수 있는 정규화된 구독 ID."""
    s = normalize_subscription_id(subscription_id)
    if not s:
        raise ValueError("subscription_id is required")
    if "/" in subscription_id or "\\" in subscription_id or ".." in subscription_id:
        raise ValueError("invalid subscription_id")
    return s


def parse_assessment_run_folder(filename: str) -> str | None:
    """assessment_YYYYMMDD_HHMMSS.ext / assessment_report_*.ext → YYYYMMDD_HHMMSS"""
    for pat in (
        r"assessment_(\d{8}_\d{6})\.(md|json|html)$",
        r"assessment_report_(\d{8}_\d{6})\.(md|json|html)$",
    ):
        m = re.match(pat, filename, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def subscription_from_arm_resource_id(resource_id: str) -> str | None:
    """ARM resourceId에서 구독 GUID 추출 후 정규화."""
    rid = (resource_id or "").lower()
    key = "/subscriptions/"
    i = rid.find(key)
    if i < 0:
        return None
    rest = rid[i + len(key) :]
    part = rest.split("/", 1)[0].strip()
    return normalize_subscription_id(part) if part else None
