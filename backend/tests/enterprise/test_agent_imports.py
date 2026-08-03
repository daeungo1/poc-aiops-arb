import json
import subprocess
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _run_import_probe(code: str):
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return json.loads(result.stdout)


def test_importing_checklist_loader_does_not_import_foundry_or_agent_framework_modules():
    loaded = _run_import_probe(
        """
import json
import sys
import agent.checklist_loader

forbidden = {
    "agent.assessment_engine",
    "agent.foundry_llm",
    "agent.report_generator",
    "agent.search_query",
    "agent.terraform_generator",
}
print(json.dumps(sorted(
    name for name in sys.modules
    if name in forbidden or name.startswith("agent_framework")
)))
"""
    )

    assert loaded == []


def test_agent_package_preserves_lazy_checklist_loader_export():
    exported_module = _run_import_probe(
        """
import json
from agent import ChecklistLoader

print(json.dumps(ChecklistLoader.__module__))
"""
    )

    assert exported_module == "agent.checklist_loader"


def test_agent_package_preserves_all_public_exports_for_star_import():
    exported_names = _run_import_probe(
        """
import json
import agent

namespace = {}
exec("from agent import *", namespace)
print(json.dumps(sorted(name for name in agent.__all__ if name in namespace)))
"""
    )

    assert exported_names == sorted(
        [
            "AssessmentEngine",
            "AzureResourceReader",
            "ChecklistLoader",
            "LazyDefaultAzureCredential",
            "LazyDelegatedCredential",
            "ReportGenerator",
            "SearchQueryClient",
            "TerraformGenerator",
            "get_default_azure_credential",
            "get_effective_azure_credential",
            "get_resource_reader_azure_credential",
            "pop_cli_credential",
            "push_cli_credential",
        ]
    )