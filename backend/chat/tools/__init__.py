"""
AIOps Assessment Chatbot Tools package.

Tool functions are split by domain:
  - assessment: resource discovery & assessment pipeline
  - search: AI Search query & analysis
  - terraform: Terraform code generation

ALL_TOOLS aggregates every @tool function for the agent.
"""

from chat.tools.assessment import (
    get_subscription_info,
    list_azure_resources,
    list_checklists,
    get_checklist_detail,
    run_assessment,
)
from chat.tools.search import (
    get_latest_assessments,
    search_assessments,
    get_resource_detail,
)
from chat.tools.terraform import (
    generate_terraform_code,
    TERRAFORM_DOWNLOAD_BASE_URL,
)
from chat.tools.enterprise import (
    run_enterprise_assessment,
    get_enterprise_assessment,
    get_enterprise_finding,
    explain_enterprise_evidence,
)

# Re-export mutable config so main.py can set it via `chat.tools.TERRAFORM_DOWNLOAD_BASE_URL`
import chat.tools.terraform as _terraform_mod  # noqa: used by main.py

ALL_TOOLS = [
    # Assessment pipeline tools
    get_subscription_info,
    list_azure_resources,
    list_checklists,
    get_checklist_detail,
    run_assessment,
    # AI Search query tools
    get_latest_assessments,
    search_assessments,
    get_resource_detail,
    # Terraform generation
    generate_terraform_code,
    # Enterprise deterministic assessment tools
    run_enterprise_assessment,
    get_enterprise_assessment,
    get_enterprise_finding,
    explain_enterprise_evidence,
]

__all__ = [
    "ALL_TOOLS",
    "TERRAFORM_DOWNLOAD_BASE_URL",
    "get_subscription_info",
    "list_azure_resources",
    "list_checklists",
    "get_checklist_detail",
    "run_assessment",
    "get_latest_assessments",
    "search_assessments",
    "get_resource_detail",
    "generate_terraform_code",
    "run_enterprise_assessment",
    "get_enterprise_assessment",
    "get_enterprise_finding",
    "explain_enterprise_evidence",
]
