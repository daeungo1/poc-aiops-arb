"""시드 체크리스트 YAML 을 checklists / checklist_items 테이블에 일괄 등록한다.

수백 건 규모를 전제로 파일 단위로 검증·보고하며, 한 파일이 실패해도 나머지는 계속 처리한다.
등록 경로는 앱과 동일한 `upsert_from_yaml_content()` 이므로 raw_yaml 원문과 3단 평탄화가 함께 저장된다.

사용:
  # 로컬 DB(.env 의 DB_* 사용)로 직접 등록
  cd backend && uv run python scripts/seed_checklists.py

  # 배포된 인스턴스에 HTTP 로 등록 (DB 직접 접근이 불가한 환경)
  cd backend && uv run python scripts/seed_checklists.py --api-base https://<host>

  # 검증만
  cd backend && uv run python scripts/seed_checklists.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.checklist_seed import (  # noqa: E402
    ChecklistValidationError,
    iter_checklist_files,
    validate_checklist_document,
)

DEFAULT_SOURCE = BACKEND_ROOT / "seeds" / "checklists"


def _register_via_db(file_key: str, content: bytes) -> None:
    from agent.db.checklist import upsert_from_yaml_content

    upsert_from_yaml_content(
        file_key,
        content,
        login_id="seed",
        user_name="checklist-seeder",
        sso_no="",
    )


def _register_via_api(api_base: str, file_key: str, content: bytes, timeout: float) -> None:
    url = f"{api_base.rstrip('/')}/api/checklists/{file_key}"
    body = json.dumps({"content": content.decode("utf-8")}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 운영자가 지정한 base URL
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="시드 체크리스트 YAML 일괄 등록")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="YAML 디렉터리")
    parser.add_argument("--api-base", default="", help="지정 시 DB 대신 REST API(PUT)로 등록")
    parser.add_argument("--timeout", type=float, default=120.0, help="API 모드 요청 타임아웃(초)")
    parser.add_argument("--dry-run", action="store_true", help="검증만 수행하고 등록하지 않음")
    args = parser.parse_args(argv)

    source: Path = args.source
    if not source.is_dir():
        print(f"source directory not found: {source}", file=sys.stderr)
        return 2

    files = list(iter_checklist_files(source))
    if not files:
        print(f"no checklist YAML under {source}", file=sys.stderr)
        return 2

    registered = 0
    failures: list[tuple[str, str]] = []

    for index, path in enumerate(files, start=1):
        file_key = path.stem
        try:
            content = path.read_bytes()
            validate_checklist_document(yaml.safe_load(content))
        except (ChecklistValidationError, yaml.YAMLError, OSError) as exc:
            failures.append((file_key, f"invalid: {exc}"))
            print(f"[{index}/{len(files)}] {file_key} - INVALID: {exc}")
            continue

        if args.dry_run:
            print(f"[{index}/{len(files)}] {file_key} - ok (dry-run)")
            continue

        try:
            if args.api_base:
                _register_via_api(args.api_base, file_key, content, args.timeout)
            else:
                _register_via_db(file_key, content)
        except (urllib.error.URLError, RuntimeError, Exception) as exc:  # noqa: BLE001 - 파일 단위로 계속 진행
            failures.append((file_key, f"register failed: {exc}"))
            print(f"[{index}/{len(files)}] {file_key} - FAILED: {exc}")
            continue

        registered += 1
        print(f"[{index}/{len(files)}] {file_key} - registered")

    print(
        f"\nsummary: total={len(files)} registered={registered} "
        f"failed={len(failures)} dry_run={args.dry_run}"
    )
    for file_key, reason in failures:
        print(f"  - {file_key}: {reason}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
