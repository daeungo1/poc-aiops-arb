"""
Common helpers shared across tool modules.
"""

import os
import sys

# Add parent directory (aiops_resource_assessment/) to path for agent package imports
_parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from agent.search_query import SearchQueryClient
from agent.terraform_generator import TerraformGenerator
from agent.ai_foundry_config import get_ai_endpoint_from_env


def get_search_query_client() -> SearchQueryClient:
    """Create a SearchQueryClient from environment variables."""
    return SearchQueryClient(
        ai_endpoint=get_ai_endpoint_from_env(),
        deployment_name=os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5-mini"),
    )


def get_terraform_generator() -> TerraformGenerator:
    """Create a TerraformGenerator from environment variables."""
    return TerraformGenerator(
        ai_endpoint=get_ai_endpoint_from_env(),
        deployment_name=os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5-mini"),
    )


# Convenience: project parent dir for checklists/ and results/ paths
PROJECT_DIR = _parent_dir
