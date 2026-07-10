"""
Checklist Loader Module
YAML 형식의 체크리스트 파일을 로드하고 관리합니다.
"""

import logging
import os
import yaml
from typing import Optional
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CheckItem:
    """개별 점검 항목"""
    question: str
    azure_check: dict = field(default_factory=dict)
    guidance: str = ""
    
    @property
    def check_type(self) -> str:
        """automated 또는 manual"""
        return self.azure_check.get("type", "manual")
    
    @property
    def check_method(self) -> Optional[str]:
        """자동화 점검 메서드 이름"""
        return self.azure_check.get("check_method")
    
    @property
    def resource_type(self) -> Optional[str]:
        """대상 리소스 타입"""
        return self.azure_check.get("resource_type")


@dataclass
class ChecklistItem:
    """체크리스트 세부 항목 (여러 CheckItem 포함)"""
    id: str
    name: str
    checks: list[CheckItem] = field(default_factory=list)


@dataclass 
class ChecklistCategory:
    """체크리스트 카테고리"""
    id: str
    name: str
    items: list[ChecklistItem] = field(default_factory=list)


@dataclass
class Checklist:
    """전체 체크리스트"""
    name: str
    version: str
    description: str
    categories: list[ChecklistCategory] = field(default_factory=list)
    applicable_resource_types: list[str] = field(default_factory=list)
    
    def get_all_checks(self) -> list[tuple[str, str, CheckItem]]:
        """
        모든 점검 항목을 플랫하게 반환합니다.
        
        Returns:
            (category_name, item_name, check_item) 튜플 리스트
        """
        all_checks = []
        for category in self.categories:
            for item in category.items:
                for check in item.checks:
                    all_checks.append((category.name, item.name, check))
        return all_checks
    
    def get_automated_checks(self) -> list[tuple[str, str, CheckItem]]:
        """자동화 가능한 점검 항목만 반환"""
        return [
            (cat, item, check) 
            for cat, item, check in self.get_all_checks()
            if check.check_type == "automated"
        ]
    
    def get_manual_checks(self) -> list[tuple[str, str, CheckItem]]:
        """수동 점검 항목만 반환"""
        return [
            (cat, item, check)
            for cat, item, check in self.get_all_checks()
            if check.check_type == "manual"
        ]


class ChecklistLoader:
    """
    체크리스트 YAML 파일을 로드하고 관리하는 클래스
    """
    
    def __init__(self, checklist_dir: Optional[str] = None):
        """
        Args:
            checklist_dir: 체크리스트 YAML 파일이 있는 디렉토리 경로
        """
        if checklist_dir:
            self.checklist_dir = Path(checklist_dir)
        else:
            # 기본 경로: 현재 파일 기준 상위의 checklists 폴더
            self.checklist_dir = Path(__file__).parent.parent / "checklists"
        
        self.checklists: dict[str, Checklist] = {}
        
    def load_all(self) -> dict[str, Checklist]:
        """
        디렉토리 내 모든 YAML 체크리스트를 로드합니다.
        
        Returns:
            파일명을 키로 하는 Checklist 딕셔너리
        """
        if not self.checklist_dir.exists():
            raise FileNotFoundError(f"체크리스트 디렉토리를 찾을 수 없습니다: {self.checklist_dir}")
        
        for pattern in ("*.yaml", "*.yml"):
            for yaml_file in self.checklist_dir.glob(pattern):
                if not yaml_file.is_file():
                    continue
                checklist = self.load_file(yaml_file)
                self.checklists[yaml_file.stem] = checklist

        return self.checklists
    
    def load_file(self, filepath: Path | str) -> Checklist:
        """
        단일 YAML 파일을 로드합니다.
        
        Args:
            filepath: YAML 파일 경로
            
        Returns:
            Checklist 객체
        """
        filepath = Path(filepath)
        
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        
        return self._parse_checklist(data)
    
    def _parse_checklist(self, data: dict) -> Checklist:
        """YAML 데이터를 Checklist 객체로 파싱"""
        metadata = data.get("metadata", {})
        
        categories = []
        for cat_data in data.get("categories", []):
            items = []
            for item_data in cat_data.get("items", []):
                checks = []
                for check_data in item_data.get("checks", []):
                    check = CheckItem(
                        question=check_data.get("question", ""),
                        azure_check=check_data.get("azure_check", {}),
                        guidance=check_data.get("azure_check", {}).get("guidance", "")
                    )
                    checks.append(check)
                
                item = ChecklistItem(
                    id=item_data.get("id", ""),
                    name=item_data.get("name", ""),
                    checks=checks
                )
                items.append(item)
            
            category = ChecklistCategory(
                id=cat_data.get("id", ""),
                name=cat_data.get("name", ""),
                items=items
            )
            categories.append(category)
        
        return Checklist(
            name=metadata.get("name", ""),
            version=metadata.get("version", ""),
            description=metadata.get("description", ""),
            categories=categories,
            applicable_resource_types=[rt.lower() for rt in metadata.get("applicable_resource_types", [])]
        )
    
    def get_checklist_for_resource_type(
        self, 
        resource_type: str
    ) -> list[Checklist]:
        """
        특정 리소스 타입에 적용 가능한 체크리스트를 반환합니다.
        
        Args:
            resource_type: Azure 리소스 타입
            
        Returns:
            적용 가능한 Checklist 리스트
        """
        applicable = []
        resource_type_lower = resource_type.lower()
        
        for checklist in self.checklists.values():
            # 리소스 타입이 명시된 체크리스트
            if checklist.applicable_resource_types:
                for applicable_type in checklist.applicable_resource_types:
                    if applicable_type.lower() in resource_type_lower:
                        applicable.append(checklist)
                        break
            # 범용 체크리스트 (리소스 타입이 명시되지 않은 경우)
            elif not checklist.applicable_resource_types:
                applicable.append(checklist)
                
        return applicable

    def get_selected_checklists_for_resource_type(
        self,
        resource_type: str,
        checklist_keys: list[str],
    ) -> list[Checklist]:
        """
        사용자가 선택한 체크리스트 ID(파일 stem) 중, 해당 리소스 타입에 적용 가능한 것만 반환합니다.
        applicable_resource_types가 비어 있으면 범용 체크리스트로 모든 타입에 적용됩니다.
        """
        applicable: list[Checklist] = []
        resource_type_lower = resource_type.lower()
        seen_obj: set[int] = set()
        for raw_key in checklist_keys:
            key = (raw_key or "").strip()
            if not key:
                continue
            checklist = self.checklists.get(key)
            if checklist is None:
                continue
            oid = id(checklist)
            if oid in seen_obj:
                continue
            if checklist.applicable_resource_types:
                for at in checklist.applicable_resource_types:
                    if at.lower() in resource_type_lower:
                        applicable.append(checklist)
                        seen_obj.add(oid)
                        break
            else:
                applicable.append(checklist)
                seen_obj.add(oid)
        return applicable

    def get_all_check_items(self) -> list[dict]:
        """
        모든 체크리스트의 점검 항목을 플랫한 리스트로 반환합니다.
        LLM 컨텍스트로 사용하기 좋은 형태입니다.
        
        Returns:
            점검 항목 딕셔너리 리스트
        """
        all_items = []
        
        for checklist_name, checklist in self.checklists.items():
            for cat, item_name, check in checklist.get_all_checks():
                all_items.append({
                    "checklist": checklist.name,
                    "category": cat,
                    "item": item_name,
                    "question": check.question,
                    "check_type": check.check_type,
                    "check_method": check.check_method,
                    "resource_type": check.resource_type,
                    "guidance": check.guidance
                })
        
        return all_items
    
    def export_to_markdown(self, output_path: str):
        """
        모든 체크리스트를 Markdown 문서로 내보냅니다.
        
        Args:
            output_path: 출력 파일 경로
        """
        lines = ["# Architecture Review Board Checklists\n"]
        
        for checklist_name, checklist in self.checklists.items():
            lines.append(f"## {checklist.name}\n")
            lines.append(f"*Version: {checklist.version}*\n")
            lines.append(f"{checklist.description}\n")
            
            for category in checklist.categories:
                lines.append(f"### {category.id}. {category.name}\n")
                
                for item in category.items:
                    lines.append(f"#### {item.id}. {item.name}\n")
                    
                    for i, check in enumerate(item.checks, 1):
                        check_type_badge = "🤖" if check.check_type == "automated" else "👤"
                        lines.append(f"{i}. {check_type_badge} {check.question}")
                        if check.guidance:
                            lines.append(f"   - *가이드: {check.guidance}*")
                        lines.append("")
                    
                    lines.append("")
            
            lines.append("---\n")
        
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    
    def load_from_db(self) -> dict[str, "Checklist"]:
        """
        DB(checklists + checklist_items)에서 체크리스트를 로드합니다.

        Returns:
            file_key를 키로 하는 Checklist 딕셔너리
        """
        from .db.connection import get_conn

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, file_key, name, version, description,
                       applicable_resource_types
                FROM checklists
                ORDER BY id
                """
            )
            rows = cur.fetchall()

            for cl_id, file_key, name, version, description, applicable_rt in rows:
                import json as _json
                if isinstance(applicable_rt, str):
                    try:
                        applicable_rt = _json.loads(applicable_rt)
                    except Exception:
                        applicable_rt = []
                applicable_rt = applicable_rt or []

                cur.execute(
                    """
                    SELECT category_id, category_name, category_order,
                           item_id, item_name,
                           question, check_type, check_method,
                           resource_type, guidance
                    FROM checklist_items
                    WHERE checklist_id = %s
                    ORDER BY category_order, display_order
                    """,
                    (cl_id,),
                )
                item_rows = cur.fetchall()

                # categories → items → checks 재구성
                from collections import OrderedDict
                cat_map: OrderedDict = OrderedDict()
                for (cat_id, cat_name, cat_order,
                     item_id, item_name,
                     question, check_type, check_method,
                     resource_type, guidance) in item_rows:

                    if cat_id not in cat_map:
                        cat_map[cat_id] = {
                            "id": cat_id, "name": cat_name or "",
                            "items": OrderedDict(),
                        }
                    items_map = cat_map[cat_id]["items"]
                    if item_id not in items_map:
                        items_map[item_id] = {
                            "id": item_id, "name": item_name or "",
                            "checks": [],
                        }
                    items_map[item_id]["checks"].append(CheckItem(
                        question=question or "",
                        azure_check={
                            "type": check_type or "manual",
                            "check_method": check_method,
                            "resource_type": resource_type,
                            "guidance": guidance or "",
                        },
                        guidance=guidance or "",
                    ))

                categories: list[ChecklistCategory] = []
                for cat_data in cat_map.values():
                    items: list[ChecklistItem] = []
                    for item_data in cat_data["items"].values():
                        items.append(ChecklistItem(
                            id=item_data["id"],
                            name=item_data["name"],
                            checks=item_data["checks"],
                        ))
                    categories.append(ChecklistCategory(
                        id=cat_data["id"],
                        name=cat_data["name"],
                        items=items,
                    ))

                self.checklists[file_key] = Checklist(
                    name=name or file_key,
                    version=str(version or "1.0"),
                    description=description or "",
                    categories=categories,
                    applicable_resource_types=[rt.lower() for rt in applicable_rt],
                )

            cur.close()
            return self.checklists
        finally:
            conn.close()

    def get_summary(self) -> dict:
        """
        로드된 체크리스트의 요약 정보를 반환합니다.
        
        Returns:
            요약 정보 딕셔너리
        """
        total_checks = 0
        automated_checks = 0
        manual_checks = 0
        
        checklist_summaries = []
        
        for name, checklist in self.checklists.items():
            all_checks = checklist.get_all_checks()
            auto = checklist.get_automated_checks()
            manual = checklist.get_manual_checks()
            
            total_checks += len(all_checks)
            automated_checks += len(auto)
            manual_checks += len(manual)
            
            checklist_summaries.append({
                "id": name,
                "name": checklist.name,
                "version": checklist.version,
                "total_checks": len(all_checks),
                "automated_checks": len(auto),
                "manual_checks": len(manual),
                "categories": len(checklist.categories),
                "applicable_resource_types": checklist.applicable_resource_types,
            })
        
        return {
            "total_checklists": len(self.checklists),
            "total_checks": total_checks,
            "automated_checks": automated_checks,
            "manual_checks": manual_checks,
            "checklists": checklist_summaries
        }


def get_configured_checklist_loader(project_dir: Path | str) -> ChecklistLoader:
    """체크리스트 로드 진입점. DB(checklists 테이블)에서만 로드합니다."""
    from .db.checklist import is_db_configured
    if not is_db_configured():
        raise RuntimeError("DB가 설정되지 않았습니다. DB_HOST 환경 변수를 설정하세요.")

    loader = ChecklistLoader()
    loader.load_from_db()
    if loader.checklists:
        logger.info("Loaded %d checklists from DB", len(loader.checklists))
        return loader

    raise RuntimeError("DB에 체크리스트가 없습니다. 체크리스트 화면에서 YAML 체크리스트를 추가하세요.")
