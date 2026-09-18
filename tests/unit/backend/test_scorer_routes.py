# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Focused route tests for backend scorer endpoints."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.models.attacks import ScoreView
from pyrit.backend.models.scorers import (
    ScoreAttackResponse,
    ScorerCatalogResponse,
    ScorerInstance,
    ScorerListResponse,
    ScorerValidationResponse,
)
from pyrit.backend.services.scorer_service import ScorerConflictError, get_scorer_service
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, AttackResult, ComponentIdentifier, Conversation, Message, MessagePiece, Score
from pyrit.registry import ScorerRegistry, TargetRegistry

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def client() -> TestClient:
    """Create a test client for scorer route tests."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_scorer_state() -> Iterator[None]:
    """Reset scorer registries and service cache between route tests."""
    get_scorer_service.cache_clear()
    ScorerRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    yield
    get_scorer_service.cache_clear()
    ScorerRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()


def _scorer_instance(*, scorer_id: str = "scorer-1", scorer_type: str = "SubStringScorer") -> ScorerInstance:
    """Build a compact scorer instance for route mocks."""
    return ScorerInstance(
        scorer_id=scorer_id,
        scorer_type=scorer_type,
        identifier_hash="a" * 64,
        score_type="true_false",
    )


def _message(*, role: str, value: str, conversation_id: str, sequence: int) -> Message:
    """Build a persisted single-piece message for route tests."""
    return Message(
        message_pieces=[
            MessagePiece(
                role=role,
                original_value=value,
                converted_value=value,
                original_value_data_type="text",
                converted_value_data_type="text",
                conversation_id=conversation_id,
                sequence=sequence,
            )
        ]
    )


def test_list_scorers_returns_items(client: TestClient) -> None:
    """List route returns registered scorer instances."""
    with patch("pyrit.backend.routes.scorers.get_scorer_service") as mock_get_service:
        mock_service = MagicMock()
        mock_service.list_scorers_async = AsyncMock(return_value=ScorerListResponse(items=[_scorer_instance()]))
        mock_get_service.return_value = mock_service

        response = client.get("/api/scorers")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["items"][0]["scorer_id"] == "scorer-1"
        assert data["items"][0]["score_type"] == "true_false"


def test_list_scorer_catalog_returns_items(client: TestClient) -> None:
    """Catalog route returns scorer type metadata."""
    with patch("pyrit.backend.routes.scorers.get_scorer_service") as mock_get_service:
        mock_service = MagicMock()
        mock_service.list_scorer_catalog_async = AsyncMock(
            return_value=ScorerCatalogResponse(
                items=[
                    {
                        "scorer_type": "SubStringScorer",
                        "score_type": "true_false",
                        "is_llm_based": False,
                        "parameters": [],
                    }
                ]
            )
        )
        mock_get_service.return_value = mock_service

        response = client.get("/api/scorers/catalog")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["items"][0]["scorer_type"] == "SubStringScorer"
        assert data["items"][0]["score_type"] == "true_false"


def test_create_scorer_returns_created_instance(client: TestClient) -> None:
    """Create route returns the compact scorer instance shape."""
    with patch("pyrit.backend.routes.scorers.get_scorer_service") as mock_get_service:
        mock_service = MagicMock()
        mock_service.create_scorer_async = AsyncMock(return_value=_scorer_instance())
        mock_get_service.return_value = mock_service

        response = client.post("/api/scorers", json={"type": "SubStringScorer", "params": {"substring": "WIN"}})

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["scorer_type"] == "SubStringScorer"
        assert data["identifier_hash"] == "a" * 64


def test_validate_scorer_returns_valid_payload(client: TestClient) -> None:
    """Validate route should preflight scorer construction without registration side effects."""
    with patch("pyrit.backend.routes.scorers.get_scorer_service") as mock_get_service:
        mock_service = MagicMock()
        mock_service.validate_scorer_request_async = AsyncMock(return_value=ScorerValidationResponse())
        mock_get_service.return_value = mock_service

        response = client.post(
            "/api/scorers/validate",
            json={"type": "SubStringScorer", "params": {"substring": "WIN"}},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"valid": True}


@pytest.mark.usefixtures("patch_central_database")
def test_validate_route_does_not_register_real_scorer(client: TestClient) -> None:
    """Validate route should not add a scorer instance to the registry."""
    response = client.post(
        "/api/scorers/validate",
        json={"type": "SubStringScorer", "params": {"substring": "WIN"}},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"valid": True}

    list_response = client.get("/api/scorers")
    assert list_response.status_code == status.HTTP_200_OK
    assert list_response.json()["items"] == []


def test_get_scorer_not_found_returns_problem_detail(client: TestClient) -> None:
    """Missing scorers surface as 404 ProblemDetail responses."""
    with patch("pyrit.backend.routes.scorers.get_scorer_service") as mock_get_service:
        mock_service = MagicMock()
        mock_service.get_scorer_async = AsyncMock(return_value=None)
        mock_get_service.return_value = mock_service

        response = client.get("/api/scorers/missing")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert data["type"] == "/errors/not-found"
        assert data["title"] == "Not Found"
        assert "Scorer 'missing' not found" in data["detail"]


def test_score_route_conflict_returns_problem_detail(client: TestClient) -> None:
    """Scorer hash or conversation mismatches are translated to 409 responses."""
    with patch("pyrit.backend.routes.scorers.get_scorer_service") as mock_get_service:
        mock_service = MagicMock()
        mock_service.score_attack_result_async = AsyncMock(side_effect=ScorerConflictError("hash mismatch"))
        mock_get_service.return_value = mock_service

        response = client.post(
            "/api/scorers/scorer-1/score",
            json={
                "attack_result_id": "attack-1",
                "conversation_id": "conv-1",
                "expected_scorer_hash": "a" * 64,
            },
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        data = response.json()
        assert data["type"] == "/errors/conflict"
        assert data["title"] == "Conflict"
        assert data["detail"] == "hash mismatch"


def test_score_route_forbids_extra_fields(client: TestClient) -> None:
    """Strict score request models reject unexpected fields."""
    response = client.post(
        "/api/scorers/scorer-1/score",
        json={
            "attack_result_id": "attack-1",
            "conversation_id": "conv-1",
            "expected_scorer_hash": "a" * 64,
            "unexpected": True,
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    data = response.json()
    assert data["type"] == "/errors/validation-error"
    assert any(error["field"] == "body.unexpected" for error in data["errors"])


def test_score_route_returns_score_payload_shape(client: TestClient) -> None:
    """Score route serializes ScoreView payloads and top-level scorer fields."""
    score = Score(
        score_value="true",
        score_type="true_false",
        score_rationale="matched",
        scorer_class_identifier=ComponentIdentifier(class_name="SubStringScorer", class_module="pyrit.score"),
    )
    response_payload = ScoreAttackResponse(
        scorer_id="scorer-1",
        scorer_hash="a" * 64,
        scores=[ScoreView.from_domain(score)],
        status="complete",
    )

    with patch("pyrit.backend.routes.scorers.get_scorer_service") as mock_get_service:
        mock_service = MagicMock()
        mock_service.score_attack_result_async = AsyncMock(return_value=response_payload)
        mock_get_service.return_value = mock_service

        response = client.post(
            "/api/scorers/scorer-1/score",
            json={
                "attack_result_id": "attack-1",
                "conversation_id": "conv-1",
                "expected_scorer_hash": "a" * 64,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["scorer_id"] == "scorer-1"
        assert data["status"] == "complete"
        assert data["scores"][0]["score_type"] == "true_false"
        assert data["scores"][0]["scorer_type"] == "SubStringScorer"


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("anchored", [False, True])
def test_score_route_round_trips_real_substring_scorer(client: TestClient, sqlite_instance, anchored: bool) -> None:
    """Real app route flow supports create/list/score for a local deterministic scorer."""
    memory = CentralMemory.get_memory_instance()
    conversation_id = "route-conv"
    memory.add_conversation_to_memory(
        conversation=Conversation(
            conversation_id=conversation_id,
            target_identifier=ComponentIdentifier(class_name="MockTarget", class_module="tests.unit.backend"),
        )
    )
    memory.add_message_to_memory(
        request=_message(role="user", value="Say WIN", conversation_id=conversation_id, sequence=0)
    )
    response_message = _message(
        role="assistant", value="\n WIN is present \t", conversation_id=conversation_id, sequence=1
    )
    memory.add_message_to_memory(request=response_message)
    attack_result = AttackResult(
        conversation_id=conversation_id,
        objective="Find WIN",
        attack_result_id=str(uuid.uuid4()),
    )
    memory.add_attack_results_to_memory(attack_results=[attack_result])

    create_response = client.post(
        "/api/scorers",
        json={"type": "SubStringScorer", "params": {"substring": "WIN"}},
    )

    assert create_response.status_code == status.HTTP_201_CREATED
    created = create_response.json()
    assert created["scorer_type"] == "SubStringScorer"
    assert created["score_type"] == "true_false"

    list_response = client.get("/api/scorers")
    assert list_response.status_code == status.HTTP_200_OK
    assert any(item["scorer_id"] == created["scorer_id"] for item in list_response.json()["items"])

    manual_response = client.post(
        "/api/scores/manual",
        json={
            "attack_result_id": attack_result.attack_result_id,
            "message_id": str(response_message.get_piece().id),
            "value": False,
            "rationale": "Human review takes precedence.",
            "update_attack": True,
        },
    )
    assert manual_response.status_code == status.HTTP_201_CREATED
    manual_score = manual_response.json()
    assert manual_score["is_objective_score"] is True

    score_response = client.post(
        f"/api/scorers/{created['scorer_id']}/score",
        json={
            "attack_result_id": attack_result.attack_result_id,
            "conversation_id": conversation_id,
            "expected_scorer_hash": created["identifier_hash"],
            "objective": attack_result.objective,
            "scope": "response",
            **(
                {
                    "evidence_sequence": 1,
                    "evidence_message_piece_ids": [str(response_message.message_pieces[0].id)],
                    "expected_response": [
                        {
                            "id": str(response_message.message_pieces[0].id),
                            "converted_value": "\n WIN is present \t",
                            "converted_value_data_type": "text",
                        }
                    ],
                }
                if anchored
                else {}
            ),
        },
    )

    assert score_response.status_code == status.HTTP_200_OK
    scored = score_response.json()
    assert scored["scorer_id"] == created["scorer_id"]
    assert scored["scorer_hash"] == created["identifier_hash"]
    assert scored["status"] == "complete"
    assert len(scored["scores"]) == 1
    assert scored["scores"][0]["scorer_type"] == "SubStringScorer"
    assert scored["scores"][0]["score_type"] == "true_false"
    assert scored["scores"][0]["status"] == "complete"
    assert str(scored["scores"][0]["score_value"]).lower() == "true"
    persisted = sqlite_instance.get_scores(score_ids=[scored["scores"][0]["id"]])
    assert len(persisted) == 1
    assert scored["scores"][0]["is_objective_score"] is False
    updated_attack = memory.get_attack_results(attack_result_ids=[attack_result.attack_result_id])[0]
    assert updated_attack.human_score is not None
    assert str(updated_attack.human_score.id) == manual_score["id"]
    assert updated_attack.outcome == AttackOutcome.FAILURE
