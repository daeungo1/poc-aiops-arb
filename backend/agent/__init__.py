# AIOps Resource Assessment Agent
# Azure Architecture Review Board 기반 리소스 진단 에이전트

from importlib import import_module
from typing import Any


_EXPORTS = {
    "LazyDefaultAzureCredential": ("azure_credential", "LazyDefaultAzureCredential"),
    "LazyDelegatedCredential": ("azure_credential", "LazyDelegatedCredential"),
    "get_default_azure_credential": ("azure_credential", "get_default_azure_credential"),
    "get_effective_azure_credential": ("azure_credential", "get_effective_azure_credential"),
    "get_resource_reader_azure_credential": ("azure_credential", "get_resource_reader_azure_credential"),
    "push_cli_credential": ("azure_credential", "push_cli_credential"),
    "pop_cli_credential": ("azure_credential", "pop_cli_credential"),
    "AzureResourceReader": ("azure_resource_reader", "AzureResourceReader"),
    "ChecklistLoader": ("checklist_loader", "ChecklistLoader"),
    "AssessmentEngine": ("assessment_engine", "AssessmentEngine"),
    "ReportGenerator": ("report_generator", "ReportGenerator"),
    "SearchQueryClient": ("search_query", "SearchQueryClient"),
    "TerraformGenerator": ("terraform_generator", "TerraformGenerator"),
}

__all__ = [
    "LazyDefaultAzureCredential",
    "LazyDelegatedCredential",
    "get_default_azure_credential",
    "get_effective_azure_credential",
    "get_resource_reader_azure_credential",
    "push_cli_credential",
    "pop_cli_credential",
    "AzureResourceReader",
    "ChecklistLoader",
    "AssessmentEngine",
    "ReportGenerator",
    "SearchQueryClient",
    "TerraformGenerator",
]

__version__ = "1.0.0"


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(f".{module_name}", __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
