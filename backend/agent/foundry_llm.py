"""Azure AI Foundry Responses API 공용 헬퍼.

챗봇(chat/agent.py)과 동일하게 agent_framework의 AzureOpenAIResponsesClient
(Responses API · 프로젝트 엔드포인트)를 사용한다. 평가·Terraform·검색 모듈은
동기 컨텍스트(ThreadPoolExecutor 워커 / asyncio.to_thread 워커 / CLI 메인스레드)에서
호출되므로, 공개 함수는 동기로 두고 내부에서 asyncio.run 으로 async 클라이언트를 호출한다.

주의: 러닝 이벤트 루프가 있는 스레드에서 호출하면 asyncio.run 이 실패한다.
현재 호출부는 모두 비-루프 컨텍스트라 안전하다.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from agent_framework import Message
from agent_framework.azure import AzureOpenAIResponsesClient

from .ai_foundry_config import get_ai_project_endpoint_from_env
from .azure_credential import LazyDefaultAzureCredential


def _make_client(deployment_name: str) -> AzureOpenAIResponsesClient:
    return AzureOpenAIResponsesClient(
        project_endpoint=get_ai_project_endpoint_from_env(),
        deployment_name=deployment_name,
        credential=LazyDefaultAzureCredential(),
    )


async def _aresponse(
    deployment_name: str,
    instructions: str,
    user_prompt: str,
    response_format: Optional[dict[str, Any]] = None,
) -> str:
    client = _make_client(deployment_name)
    messages = [Message("system", [instructions]), Message("user", [user_prompt])]
    options = {"response_format": response_format} if response_format else None
    response = await client.get_response(messages, options=options)
    return response.text or ""


def responses_text(
    deployment_name: str,
    instructions: str,
    user_prompt: str,
    response_format: Optional[dict[str, Any]] = None,
) -> str:
    """동기 컨텍스트에서 Responses API를 호출하고 응답 텍스트를 반환한다."""
    return asyncio.run(_aresponse(deployment_name, instructions, user_prompt, response_format))


def responses_json(
    deployment_name: str,
    instructions: str,
    user_prompt: str,
    response_format: dict[str, Any],
) -> dict:
    """구조화 출력(json_object / json_schema)을 요청하고 파싱한 dict를 반환한다."""
    return json.loads(responses_text(deployment_name, instructions, user_prompt, response_format))
