from __future__ import annotations

from pathlib import Path

import pytest

from enterprise.adapters.advisor import ADVISOR_API_VERSION, AdvisorAdapter
from enterprise.adapters.aprl import (
    RESOURCE_GRAPH_API_VERSION,
    AprlAdapter,
    resource_graph_source_version,
)
from enterprise.adapters import adapter_from_source
from enterprise.adapters.arm import ArmStorageAccountAdapter
from enterprise.adapters.base import CollectionContext, HttpResponse, HttpTransportError
from enterprise.adapters.defender import DEFENDER_API_VERSION, DefenderAdapter
from enterprise.adapters.policy import POLICY_STATES_API_VERSION, PolicyStatesAdapter
from enterprise.domain import EvidenceRecord, EvidenceStatus, VerdictState
from enterprise.evaluator import DeterministicEvaluator
from enterprise.registry import ControlRegistry

from .conftest import RESOURCE_ID, SUBSCRIPTION_ID, TENANT_ID, FakeTransport
from .test_arm import _account, _blob_service


ROOT = Path(__file__).resolve().parents[4]
SPIKE_ROOT = ROOT / "experiments/coverage_spike"
ADVISOR_SOURCE_REFERENCE = "synthetic.advisor.storage.redundancy"
ADVISOR_RECOMMENDATION_TYPE_ID = "synthetic-advisor-redundancy"
POLICY_DEFINITION_ID = "synthetic-policy-secure-transfer"
APRL_PROJECTION = {"resource_id": "resourceId", "resource_type": "resourceType"}


def _aprl_adapter(transport, *, query="Resources", source_kind="aprl", **kwargs):
    config = {
        "query": query,
        "projection": APRL_PROJECTION,
        "source_kind": source_kind,
        **kwargs,
    }
    if source_kind == "aprl":
        config.update(
            status_field="status",
            pass_status_values=("Passed", "pass"),
            fail_status_values=("Failed", "fail"),
        )
    return AprlAdapter(transport, **config)


def test_advisor_requires_explicit_source_identity():
    with pytest.raises(TypeError):
        AdvisorAdapter(FakeTransport())


def test_policy_requires_explicit_policy_definition_identity():
    with pytest.raises(TypeError):
        PolicyStatesAdapter(FakeTransport())


def test_defender_constructor_rejects_missing_assessment_names_even_for_default_source_reference():
    with pytest.raises(ValueError, match="assessment_names"):
        DefenderAdapter(FakeTransport())


def test_aprl_requires_explicit_projection_and_status_schema():
    with pytest.raises(TypeError):
        AprlAdapter(FakeTransport(), query="Resources")


@pytest.mark.parametrize(
    "factory",
    [
        lambda: AdvisorAdapter(
            FakeTransport(),
            source_reference=ADVISOR_SOURCE_REFERENCE,
            recommendation_type_id=ADVISOR_RECOMMENDATION_TYPE_ID,
            source_version="synthetic-v1",
        ),
        lambda: DefenderAdapter(FakeTransport(), source_version="synthetic-v1"),
        lambda: PolicyStatesAdapter(
            FakeTransport(),
            policy_definition_id="policy-definition",
            source_version="synthetic-v1",
        ),
    ],
)
def test_rest_adapter_rejects_source_version_that_hides_called_api_version(factory):
    with pytest.raises(ValueError, match="API version"):
        factory()


def test_aprl_rejects_source_version_that_hides_api_and_query_version():
    with pytest.raises(ValueError, match="Resource Graph API and query version"):
        AprlAdapter(
            FakeTransport(),
            query="Resources",
            projection={"resource_id": "resourceId", "resource_type": "resourceType"},
            status_field="status",
            pass_status_values=("Passed",),
            fail_status_values=("Failed",),
            source_version="synthetic-v1",
        )


@pytest.mark.asyncio
async def test_aprl_executes_configured_arg_query_and_normalizes_status(context):
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "data": [
                    {
                        "resourceId": RESOURCE_ID.lower(),
                        "resourceType": "Microsoft.Storage/storageAccounts",
                        "status": "Passed",
                    }
                ]
            },
            {},
        )
    )
    adapter = _aprl_adapter(
        transport,
        query="Resources | where type =~ 'microsoft.storage/storageaccounts'",
        source_reference="synthetic.aprl.storage.private_endpoint",
    )

    result = await adapter.collect(context)

    assert result.failures == ()
    assert result.evidence[0].resource_id == RESOURCE_ID
    assert result.evidence[0].payload["managed_status"] == "pass"
    assert result.evidence[0].payload["verdict"]["status"] == "pass"
    assert result.evidence[0].source_version == resource_graph_source_version(adapter.query)
    request = transport.requests[0]
    assert request["method"] == "POST"
    assert f"api-version={RESOURCE_GRAPH_API_VERSION}" in request["url"]
    assert f"api-version:{RESOURCE_GRAPH_API_VERSION}" in result.evidence[0].source_version
    assert request["json_body"]["query"] == adapter.query
    assert request["json_body"]["subscriptions"] == [context.subscription_id]


@pytest.mark.asyncio
async def test_aprl_malformed_projected_row_is_partial_unknown(context):
    result = await AprlAdapter(
        FakeTransport(
            HttpResponse(
                200,
                {
                    "data": [
                        {
                            "targetResourceId": RESOURCE_ID,
                            "targetResourceType": "Microsoft.Storage/storageAccounts",
                        }
                    ]
                },
                {},
            )
        ),
        query="Resources | project targetResourceId=id, targetResourceType=type",
        projection={
            "resource_id": "targetResourceId",
            "resource_type": "targetResourceType",
        },
        status_field="ruleResult",
        pass_status_values=("Pass",),
        fail_status_values=("Fail",),
    ).collect(context)

    assert result.partial is True
    assert result.failures[0].reason_code == "source_malformed"
    assert len(result.evidence) == 1
    assert result.evidence[0].resource_id == RESOURCE_ID
    assert result.evidence[0].status is EvidenceStatus.PARTIAL
    assert result.evidence[0].payload["managed_status"] == "unknown"


@pytest.mark.asyncio
async def test_aprl_continues_resource_graph_query_with_skip_token(context):
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "data": [
                    {
                        "resourceId": RESOURCE_ID,
                        "resourceType": "Microsoft.Storage/storageAccounts",
                        "status": "Passed",
                    }
                ],
                "resultTruncated": "true",
                "$skipToken": "continuation-token",
            },
            {},
        ),
        HttpResponse(200, {"data": [], "resultTruncated": "false"}, {}),
    )
    adapter = _aprl_adapter(transport)

    result = await adapter.collect(context)

    assert result.failures == ()
    assert len(transport.requests) == 2
    assert transport.requests[1]["json_body"]["options"]["$skipToken"] == "continuation-token"


@pytest.mark.asyncio
async def test_aprl_rejects_repeated_resource_graph_skip_token(context):
    page = {
        "data": [],
        "resultTruncated": "true",
        "$skipToken": "repeated-token",
    }
    transport = FakeTransport(HttpResponse(200, page, {}), HttpResponse(200, page, {}))

    result = await _aprl_adapter(transport).collect(context)

    assert result.partial is True
    assert result.failures[0].reason_code == "source_pagination_loop"


@pytest.mark.asyncio
async def test_aprl_skip_token_page_limit_preserves_prior_evidence(credential):
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "data": [
                    {
                        "resourceId": RESOURCE_ID,
                        "resourceType": "Microsoft.Storage/storageAccounts",
                        "status": "Passed",
                    }
                ],
                "resultTruncated": True,
                "$skipToken": "continuation-token",
            },
            {},
        ),
    )
    context = CollectionContext(
        tenant_id=TENANT_ID,
        subscription_id=SUBSCRIPTION_ID,
        resource_ids=(RESOURCE_ID,),
        credential=credential,
        max_pages=1,
    )

    result = await _aprl_adapter(transport).collect(context)

    assert len(transport.requests) == 1
    assert len(result.evidence) == 1
    assert result.evidence[0].status is EvidenceStatus.PARTIAL
    assert result.failures[0].reason_code == "source_page_limit"


@pytest.mark.asyncio
async def test_aprl_connection_failure_is_partial(context):
    result = await _aprl_adapter(
        FakeTransport(HttpTransportError("source connection failed")),
    ).collect(context)

    assert result.evidence == ()
    assert result.partial is True
    assert result.failures[0].reason_code == "source_transport_error"


@pytest.mark.asyncio
async def test_advisor_recommendation_presence_is_normalized_fail(context):
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "value": [
                    {
                        "id": f"{RESOURCE_ID}/providers/Microsoft.Advisor/recommendations/rec-1",
                        "type": "Microsoft.Advisor/recommendations",
                        "properties": {
                            "category": "HighAvailability",
                            "recommendationTypeId": ADVISOR_RECOMMENDATION_TYPE_ID,
                            "impactedField": "Microsoft.Storage/storageAccounts",
                            "resourceMetadata": {"resourceId": RESOURCE_ID.lower()},
                            "managedStatus": "pass",
                        },
                    }
                ]
            },
            {},
        )
    )

    result = await AdvisorAdapter(
        transport,
        source_reference=ADVISOR_SOURCE_REFERENCE,
        recommendation_type_id=ADVISOR_RECOMMENDATION_TYPE_ID,
    ).collect(context)

    assert result.evidence[0].resource_id == RESOURCE_ID
    assert result.evidence[0].payload["managed_status"] == "fail"
    assert result.evidence[0].payload["verdict"]["status"] == "fail"
    assert result.evidence[0].source_reference == ADVISOR_SOURCE_REFERENCE
    assert result.evidence[0].source_version == ADVISOR_API_VERSION == "2025-01-01"
    assert f"api-version={result.evidence[0].source_version}" in transport.requests[0]["url"]


@pytest.mark.asyncio
async def test_advisor_complete_absence_is_pass_only_for_requested_resource(context):
    result = await AdvisorAdapter(
        FakeTransport(
            HttpResponse(
                200,
                {
                    "value": [
                        {
                            "properties": {
                                "recommendationTypeId": "unrelated-recommendation",
                                "resourceMetadata": {"resourceId": RESOURCE_ID},
                            }
                        }
                    ]
                },
                {},
            )
        ),
        source_reference=ADVISOR_SOURCE_REFERENCE,
        recommendation_type_id=ADVISOR_RECOMMENDATION_TYPE_ID,
    ).collect(context)

    assert result.failures == ()
    assert result.partial is False
    assert len(result.evidence) == 1
    assert result.evidence[0].resource_id == RESOURCE_ID
    assert result.evidence[0].status is EvidenceStatus.COMPLETE
    assert result.evidence[0].payload["recommendation_type_id"] == ADVISOR_RECOMMENDATION_TYPE_ID
    assert result.evidence[0].payload["recommendation_present"] is False
    assert result.evidence[0].payload["managed_status"] == "pass"


@pytest.mark.asyncio
async def test_advisor_complete_absence_without_requested_resources_does_not_infer_pass(
    credential,
):
    context = CollectionContext(
        tenant_id=TENANT_ID,
        subscription_id=SUBSCRIPTION_ID,
        resource_ids=None,
        credential=credential,
    )

    result = await AdvisorAdapter(
        FakeTransport(HttpResponse(200, {"value": []}, {})),
        source_reference=ADVISOR_SOURCE_REFERENCE,
        recommendation_type_id=ADVISOR_RECOMMENDATION_TYPE_ID,
    ).collect(context)

    assert result.failures == ()
    assert result.evidence == ()


@pytest.mark.asyncio
async def test_advisor_malformed_identity_marks_collection_partial_and_never_infers_absence_pass(context):
    result = await AdvisorAdapter(
        FakeTransport(
            HttpResponse(
                200,
                {
                    "value": [
                        {
                            "properties": {
                                "resourceMetadata": {"resourceId": RESOURCE_ID},
                            }
                        }
                    ]
                },
                {},
            )
        ),
        source_reference=ADVISOR_SOURCE_REFERENCE,
        recommendation_type_id=ADVISOR_RECOMMENDATION_TYPE_ID,
    ).collect(context)

    assert result.partial is True
    assert any(failure.reason_code == "source_malformed" for failure in result.failures)
    assert len(result.evidence) == 1
    assert result.evidence[0].status is EvidenceStatus.PARTIAL
    assert result.evidence[0].payload["managed_status"] == "unknown"


@pytest.mark.asyncio
async def test_advisor_partial_absence_yields_unknown_through_evaluator(context):
    next_link = (
        f"https://management.azure.com/subscriptions/{context.subscription_id}/providers/"
        f"Microsoft.Advisor/recommendations?api-version={ADVISOR_API_VERSION}&$skiptoken=next"
    )
    advisor_result = await AdvisorAdapter(
        FakeTransport(
            HttpResponse(
                200,
                {
                    "value": [
                        {
                            "properties": {
                                "recommendationTypeId": "unrelated-recommendation",
                                "resourceMetadata": {"resourceId": RESOURCE_ID},
                            }
                        }
                    ],
                    "nextLink": next_link,
                },
                {},
            ),
            HttpResponse(500, {"error": {"code": "InternalServerError"}}, {}),
        ),
        source_reference=ADVISOR_SOURCE_REFERENCE,
        recommendation_type_id=ADVISOR_RECOMMENDATION_TYPE_ID,
    ).collect(context)
    arm_result = await ArmStorageAccountAdapter(
        FakeTransport(HttpResponse(200, _account(), {}), HttpResponse(200, _blob_service(), {})),
        resource_detail="all",
    ).collect(context)
    registry = ControlRegistry.load(
        SPIKE_ROOT / "checklists/azure_storage_production_readiness.yaml",
        SPIKE_ROOT / "mappings/azure_storage_production_readiness.yaml",
    )

    verdict = DeterministicEvaluator().evaluate(
        registry.get("storage.redundancy"),
        arm_result.evidence + advisor_result.evidence,
    )

    assert advisor_result.partial is True
    assert advisor_result.failures[0].reason_code == "source_http_error"
    assert len(advisor_result.evidence) == 1
    assert advisor_result.evidence[0].status is EvidenceStatus.PARTIAL
    assert advisor_result.evidence[0].payload["managed_status"] == "unknown"
    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "evidence_partial"


@pytest.mark.asyncio
async def test_advisor_filters_recommendations_by_configured_type_id(context):
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "value": [
                    {
                        "properties": {
                            "recommendationTypeId": "wanted",
                            "resourceMetadata": {"resourceId": RESOURCE_ID},
                        }
                    },
                    {
                        "properties": {
                            "recommendationTypeId": "unrelated",
                            "resourceMetadata": {"resourceId": RESOURCE_ID},
                        }
                    },
                ]
            },
            {},
        )
    )

    result = await AdvisorAdapter(
        transport,
        source_reference="advisor.wanted",
        recommendation_type_id="WANTED",
    ).collect(context)

    assert len(result.evidence) == 1
    assert result.evidence[0].payload["properties"]["recommendationTypeId"] == "wanted"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [("Healthy", "pass"), ("Unhealthy", "fail"), ("NotApplicable", "unknown")],
)
async def test_defender_normalizes_documented_assessment_statuses(context, raw_status, expected):
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "value": [
                    {
                        "id": f"{RESOURCE_ID}/providers/Microsoft.Security/assessments/a-1",
                        "type": "Microsoft.Security/assessments",
                        "properties": {
                            "resourceDetails": {
                                "source": "Azure",
                                "id": RESOURCE_ID.lower(),
                                "resourceType": "Microsoft.Storage/storageAccounts",
                            },
                            "status": {"code": raw_status},
                        },
                    }
                ]
            },
            {},
        )
    )

    result = await DefenderAdapter(transport, assessment_names=("a-1",)).collect(context)

    assert result.evidence[0].payload["managed_status"] == expected
    assert result.evidence[0].source_version == DEFENDER_API_VERSION == "2020-01-01"
    assert f"api-version={result.evidence[0].source_version}" in transport.requests[0]["url"]
    expected_evidence_status = EvidenceStatus.COMPLETE if expected != "unknown" else EvidenceStatus.PARTIAL
    assert result.evidence[0].status is expected_evidence_status


@pytest.mark.asyncio
async def test_defender_filters_assessments_by_configured_name(context):
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "value": [
                    {
                        "name": "wanted",
                        "properties": {
                            "resourceDetails": {"source": "Azure", "id": RESOURCE_ID},
                            "status": {"code": "Healthy"},
                        },
                    },
                    {
                        "name": "unrelated",
                        "properties": {
                            "resourceDetails": {"source": "Azure", "id": RESOURCE_ID},
                            "status": {"code": "Unhealthy"},
                        },
                    },
                ]
            },
            {},
        )
    )

    result = await DefenderAdapter(transport, assessment_names=("WANTED",)).collect(context)

    assert len(result.evidence) == 1
    assert result.evidence[0].payload["name"] == "wanted"


@pytest.mark.asyncio
async def test_defender_unsupported_resource_details_variant_is_partial_unknown(context):
    result = await DefenderAdapter(
        FakeTransport(
            HttpResponse(
                200,
                {
                    "value": [
                        {
                            "name": "wanted",
                            "properties": {
                                "resourceDetails": {
                                    "source": "Aws",
                                    "id": RESOURCE_ID,
                                    "awsResourceId": "arn:aws:s3:::example",
                                },
                                "status": {"code": "Healthy"},
                            },
                        }
                    ]
                },
                {},
            )
        ),
        assessment_names=("wanted",),
    ).collect(context)

    assert result.partial is True
    assert result.failures[0].reason_code == "source_malformed"
    assert len(result.evidence) == 1
    assert result.evidence[0].resource_id == RESOURCE_ID
    assert result.evidence[0].status is EvidenceStatus.PARTIAL
    assert result.evidence[0].payload["managed_status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [("Compliant", "pass"), ("NonCompliant", "fail"), ("Conflict", "unknown")],
)
async def test_policy_states_normalize_compliance_state(context, raw_status, expected):
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "value": [
                    {
                        "resourceId": RESOURCE_ID.lower(),
                        "resourceType": "Microsoft.Storage/storageAccounts",
                        "policyDefinitionId": POLICY_DEFINITION_ID,
                        "complianceState": raw_status,
                    }
                ]
            },
            {},
        )
    )

    result = await PolicyStatesAdapter(
        transport,
        policy_definition_id=POLICY_DEFINITION_ID,
    ).collect(context)

    assert result.evidence[0].payload["managed_status"] == expected
    assert result.evidence[0].source_version == POLICY_STATES_API_VERSION == "2019-10-01"
    assert f"api-version={result.evidence[0].source_version}" in transport.requests[0]["url"]


@pytest.mark.asyncio
async def test_policy_states_filter_by_configured_definition_id(context):
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "value": [
                    {
                        "resourceId": RESOURCE_ID,
                        "policyDefinitionId": "/providers/Microsoft.Authorization/policyDefinitions/wanted",
                        "complianceState": "Compliant",
                    },
                    {
                        "resourceId": RESOURCE_ID,
                        "policyDefinitionId": "/providers/Microsoft.Authorization/policyDefinitions/unrelated",
                        "complianceState": "NonCompliant",
                    },
                ]
            },
            {},
        )
    )

    result = await PolicyStatesAdapter(
        transport,
        policy_definition_id="/PROVIDERS/MICROSOFT.AUTHORIZATION/POLICYDEFINITIONS/WANTED",
    ).collect(context)

    assert len(result.evidence) == 1
    assert result.evidence[0].payload["policyDefinitionId"].endswith("/wanted")


@pytest.mark.asyncio
async def test_policy_missing_configured_identity_fields_is_partial_unknown(context):
    definition_id = "/providers/Microsoft.Authorization/policyDefinitions/wanted"
    result = await PolicyStatesAdapter(
        FakeTransport(
            HttpResponse(
                200,
                {
                    "value": [
                        {
                            "resourceId": RESOURCE_ID,
                            "resourceType": "Microsoft.Storage/storageAccounts",
                            "policyDefinitionId": definition_id,
                            "complianceState": "Compliant",
                        }
                    ]
                },
                {},
            )
        ),
        policy_definition_id=definition_id,
        assignment_id="/subscriptions/example/providers/Microsoft.Authorization/policyAssignments/assignment",
        definition_reference_id="storage-secure-transfer",
    ).collect(context)

    assert result.partial is True
    assert result.failures[0].reason_code == "source_malformed"
    assert len(result.evidence) == 1
    assert result.evidence[0].status is EvidenceStatus.PARTIAL
    assert result.evidence[0].payload["managed_status"] == "unknown"


@pytest.mark.asyncio
async def test_policy_ignores_unrelated_definition_before_optional_identity_field_checks(context):
    wanted_definition = "/providers/Microsoft.Authorization/policyDefinitions/wanted"
    result = await PolicyStatesAdapter(
        FakeTransport(
            HttpResponse(
                200,
                {
                    "value": [
                        {
                            "resourceId": RESOURCE_ID,
                            "policyDefinitionId": "/providers/Microsoft.Authorization/policyDefinitions/unrelated",
                            "complianceState": "NonCompliant",
                        }
                    ]
                },
                {},
            )
        ),
        policy_definition_id=wanted_definition,
        assignment_id="/subscriptions/example/providers/Microsoft.Authorization/policyAssignments/assignment",
        definition_reference_id="ref-1",
    ).collect(context)

    assert result.failures == ()
    assert result.evidence == ()


@pytest.mark.asyncio
async def test_policy_exact_matches_all_configured_identities(context):
    definition_id = "/providers/Microsoft.Authorization/policyDefinitions/wanted"
    assignment_id = "/subscriptions/example/providers/Microsoft.Authorization/policyAssignments/wanted"
    result = await PolicyStatesAdapter(
        FakeTransport(
            HttpResponse(
                200,
                {
                    "value": [
                        {
                            "resourceId": RESOURCE_ID,
                            "policyDefinitionId": definition_id,
                            "policyAssignmentId": assignment_id + "-other",
                            "policyDefinitionReferenceId": "secure-transfer",
                            "complianceState": "NonCompliant",
                        },
                        {
                            "resourceId": RESOURCE_ID,
                            "policyDefinitionId": definition_id.upper(),
                            "policyAssignmentId": assignment_id.upper(),
                            "policyDefinitionReferenceId": "SECURE-TRANSFER",
                            "complianceState": "Compliant",
                        },
                    ]
                },
                {},
            )
        ),
        policy_definition_id=definition_id,
        assignment_id=assignment_id,
        definition_reference_id="secure-transfer",
    ).collect(context)

    assert result.failures == ()
    assert len(result.evidence) == 1
    assert result.evidence[0].payload["managed_status"] == "pass"


@pytest.mark.asyncio
async def test_policy_normalizes_leading_slash_resource_type(context):
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "value": [
                    {
                        "resourceId": RESOURCE_ID,
                        "resourceType": "/Microsoft.Storage/storageAccounts",
                        "policyDefinitionId": POLICY_DEFINITION_ID,
                        "complianceState": "Compliant",
                    }
                ]
            },
            {},
        )
    )

    result = await PolicyStatesAdapter(
        transport,
        policy_definition_id=POLICY_DEFINITION_ID,
    ).collect(context)

    assert result.evidence[0].payload["resource_type"] == "Microsoft.Storage/storageAccounts"


@pytest.mark.asyncio
async def test_policy_rejects_cross_subscription_record_as_scope_conflict(context):
    other_resource_id = RESOURCE_ID.replace(
        context.subscription_id,
        "99999999-8888-7777-6666-555555555555",
    )
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "value": [
                    {
                        "resourceId": other_resource_id,
                        "resourceType": "Microsoft.Storage/storageAccounts",
                        "policyDefinitionId": POLICY_DEFINITION_ID,
                        "complianceState": "Compliant",
                    }
                ]
            },
            {},
        )
    )

    result = await PolicyStatesAdapter(
        transport,
        policy_definition_id=POLICY_DEFINITION_ID,
    ).collect(context)

    assert result.evidence == ()
    assert result.partial is True
    assert result.failures[0].reason_code == "source_scope_conflict"


def test_evaluator_matches_equivalent_resource_ids_case_insensitively():
    registry = ControlRegistry.load(
        SPIKE_ROOT / "checklists/azure_storage_production_readiness.yaml",
        SPIKE_ROOT / "mappings/azure_storage_production_readiness.yaml",
    )
    control = registry.get("storage.secure_transfer")
    primary = EvidenceRecord.create(
        source_kind="arm",
        source_reference="arm.storage_account.resource",
        source_version="2023-05-01",
        resource_id=RESOURCE_ID,
        status=EvidenceStatus.COMPLETE,
        payload={
            "resource_type": "Microsoft.Storage/storageAccounts",
            "properties": {"supportsHttpsTrafficOnly": True},
        },
    )
    corroborating = EvidenceRecord.create(
        source_kind="azure_policy",
        source_reference="synthetic.azure_policy.storage.secure_transfer",
        source_version=POLICY_STATES_API_VERSION,
        resource_id=RESOURCE_ID.swapcase(),
        status=EvidenceStatus.COMPLETE,
        payload={
            "resource_type": "Microsoft.Storage/storageAccounts",
            "verdict": {"status": "pass"},
        },
    )

    verdict = DeterministicEvaluator().evaluate(control, (primary, corroborating))

    assert primary.resource_id == RESOURCE_ID
    assert corroborating.resource_id == RESOURCE_ID.swapcase()
    assert verdict.state is VerdictState.PASS


@pytest.mark.asyncio
async def test_missing_managed_status_is_partial_unknown(context):
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "value": [
                    {
                            "name": "wanted",
                        "properties": {
                            "resourceDetails": {
                                "source": "Azure",
                                "id": RESOURCE_ID,
                                "resourceType": "Microsoft.Storage/storageAccounts",
                            }
                        }
                    }
                ]
            },
            {},
        )
    )

    result = await DefenderAdapter(transport, assessment_names=("wanted",)).collect(context)

    evidence = result.evidence[0]
    assert evidence.status is EvidenceStatus.PARTIAL
    assert evidence.payload["managed_status"] == "unknown"
    assert evidence.payload["verdict"]["status"] == "unknown"


@pytest.mark.asyncio
async def test_adapter_evidence_is_compatible_with_all_six_sample_controls(context):
    registry = ControlRegistry.load(
        SPIKE_ROOT / "checklists/azure_storage_production_readiness.yaml",
        SPIKE_ROOT / "mappings/azure_storage_production_readiness.yaml",
    )
    unique_sources = {
        (source.source_kind, source.reference): source
        for control in registry.controls.values()
        for source in control.sources
    }

    collected: list[EvidenceRecord] = []
    for source in unique_sources.values():
        if source.source_kind in {"arm", "storage_service"}:
            transport = FakeTransport(HttpResponse(200, _account(), {})) if source.source_kind == "arm" else FakeTransport(HttpResponse(200, _blob_service(), {}))
        elif source.source_kind == "arg":
            transport = FakeTransport(
                HttpResponse(
                    200,
                    {
                        "data": [
                            {
                                "resourceId": RESOURCE_ID,
                                "resourceType": "Microsoft.Storage/storageAccounts",
                                "relationships": {"privateEndpoint": {"id": "pe-1"}},
                            }
                        ]
                    },
                    {},
                )
            )
        elif source.source_kind == "aprl":
            transport = FakeTransport(
                HttpResponse(
                    200,
                    {
                        "data": [
                            {
                                "resourceId": RESOURCE_ID,
                                "resourceType": "Microsoft.Storage/storageAccounts",
                                "status": "pass",
                            }
                        ]
                    },
                    {},
                )
            )
        elif source.source_kind == "azure_policy":
            transport = FakeTransport(
                HttpResponse(
                    200,
                    {
                        "value": [
                            {
                                "resourceId": RESOURCE_ID,
                                "resourceType": "Microsoft.Storage/storageAccounts",
                                "policyDefinitionId": source.adapter_config["policy_definition_id"],
                                "policyAssignmentId": source.adapter_config["assignment_id"],
                                "policyDefinitionReferenceId": source.adapter_config["definition_reference_id"],
                                "complianceState": "Compliant",
                            }
                        ]
                    },
                    {},
                )
            )
        elif source.source_kind == "defender":
            assessment_name = source.adapter_config["assessment_names"][0]
            transport = FakeTransport(
                HttpResponse(
                    200,
                    {
                        "value": [
                            {
                                "name": assessment_name,
                                "properties": {
                                    "resourceDetails": {
                                        "source": "Azure",
                                        "id": RESOURCE_ID,
                                        "resourceType": "Microsoft.Storage/storageAccounts",
                                    },
                                    "status": {"code": "Healthy"},
                                },
                            }
                        ]
                    },
                    {},
                )
            )
        elif source.source_kind == "advisor":
            transport = FakeTransport(HttpResponse(200, {"value": []}, {}))
        else:
            raise AssertionError(f"unsupported source in test fixture: {source.source_kind}")

        adapter = adapter_from_source(source, transport)
        result = await adapter.collect(context)
        if source.source_kind in {"arm", "storage_service"}:
            assert len(transport.requests) == 1
            assert {record.source_kind for record in result.evidence} == {source.source_kind}
        collected.extend(result.evidence)

    evidence = tuple(collected)

    verdicts = {
        key: DeterministicEvaluator().evaluate(control, evidence)
        for key, control in registry.controls.items()
    }

    assert set(verdicts) == set(registry.controls)
    assert all(verdict.state is VerdictState.PASS for verdict in verdicts.values())


def test_adapter_factory_constructs_expected_adapter_types_for_all_sample_sources():
    registry = ControlRegistry.load(
        SPIKE_ROOT / "checklists/azure_storage_production_readiness.yaml",
        SPIKE_ROOT / "mappings/azure_storage_production_readiness.yaml",
    )
    transport = FakeTransport()

    built = {}
    for control in registry.controls.values():
        for source in control.sources:
            adapter = adapter_from_source(source, transport)
            built[(source.source_kind, source.reference)] = (
                adapter.__class__.__name__,
                getattr(adapter, "resource_detail", None),
            )

    assert built[("arm", "arm.storage_account.resource")] == ("ArmStorageAccountAdapter", "account")
    assert built[("storage_service", "arm.storage_account.blob_service")] == ("ArmStorageAccountAdapter", "blob_service")
    assert built[("arg", "arg.storage_account.private_endpoints")] == ("AprlAdapter", None)
    assert built[("aprl", "synthetic.aprl.storage.private_endpoint")] == ("AprlAdapter", None)
    assert built[("advisor", "synthetic.advisor.storage.redundancy")] == ("AdvisorAdapter", None)
    assert built[("defender", "synthetic.defender.storage.public_network")] == ("DefenderAdapter", None)
    assert built[("azure_policy", "synthetic.azure_policy.storage.secure_transfer")] == ("PolicyStatesAdapter", None)