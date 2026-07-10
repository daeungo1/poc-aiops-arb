"""
Azure AI Foundry environment configuration helpers.
"""

import os


def get_ai_endpoint_from_env() -> str:
    """Return the Azure AI Foundry root endpoint from environment variables."""
    endpoint = os.environ.get("AZURE_AI_ENDPOINT", "").strip().rstrip("/")
    if not endpoint:
        raise ValueError("AZURE_AI_ENDPOINT is required")
    return endpoint


def get_ai_project_name_from_env() -> str:
    """Return the Azure AI Foundry project name from environment variables."""
    project_name = os.environ.get("AZURE_AI_PROJECT_NAME", "").strip().strip("/")
    if not project_name:
        raise ValueError("AZURE_AI_PROJECT_NAME is required")
    return project_name


def build_ai_project_endpoint(ai_endpoint: str, project_name: str) -> str:
    """Build the full Azure AI Foundry project endpoint."""
    return f"{ai_endpoint.strip().rstrip('/')}/api/projects/{project_name.strip().strip('/')}"


def get_ai_project_endpoint_from_env() -> str:
    """Return the full Azure AI Foundry project endpoint from split env vars."""
    return build_ai_project_endpoint(
        get_ai_endpoint_from_env(),
        get_ai_project_name_from_env(),
    )
