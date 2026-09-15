# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Focused tests for the backend scorer service."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.backend.models.scorers import CreateScorerRequest, ScoreAttackRequest
from pyrit.backend.services.scorer_service import ScorerConflictError, ScorerService
from pyrit.memory import CentralMemory
from pyrit.models import AttackResult, Conversation, Message, MessagePiece, Parameter
from pyrit.registry import ScorerRegistry, TargetRegistry
from pyrit.score import PlagiarismScorer, SelfAskScaleScorer, SubStringScorer, SystemPromptExtractionScorer
from unit.mocks import MockPromptTarget, get_mock_target_identifier

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.fixture(autouse=True)
def reset_registries() -> None:
    """Reset scorer and target registries between tests."""
    ScorerRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    yield
    ScorerRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()


def _persist_conversation(
    *,
    conversation_id: str,
    objective: str,
    messages: list[Message],
) -> AttackResult:
    """Persist a conversation and matching AttackResult into central memory."""
    memory = CentralMemory.get_memory_instance()
    memory.add_conversation_to_memory(
        conversation=Conversation(
            conversation_id=conversation_id,
            target_identifier=get_mock_target_identifier(),
        )
    )
    for message in messages:
        memory.add_message_to_memory(request=message)

    attack_result = AttackResult(
        conversation_id=conversation_id,
        objective=objective,
        attack_result_id=str(uuid.uuid4()),
    )
    memory.add_attack_results_to_memory(attack_results=[attack_result])
    return attack_result


def _message(*, role: str, value: str, conversation_id: str, sequence: int, response_error: str = "none") -> Message:
    """Build a persisted single-piece message for tests."""
    piece = MessagePiece(
        role=role,
        original_value=value,
        converted_value=value,
        original_value_data_type="text",
        converted_value_data_type="text",
        conversation_id=conversation_id,
        sequence=sequence,
        response_error=response_error,
    )
    return Message(message_pieces=[piece])


async def test_list_scorer_catalog_includes_target_choices_for_llm_scorers() -> None:
    """Catalog projects target references into selectable choices when targets are registered."""
    target = MockPromptTarget()
    TargetRegistry.get_registry_singleton().instances.register(target, name="judge-target")
    service = ScorerService()

    result = await service.list_scorer_catalog_async()

    entry = next(item for item in result.items if item.scorer_type == "SelfAskTrueFalseScorer")
    chat_target = next(parameter for parameter in entry.parameters if parameter.name == "chat_target")
    assert entry.is_llm_based is True
    assert entry.score_type == "true_false"
    assert chat_target.choices == ["judge-target"]
    assert chat_target.type_name == "str"


async def test_create_scorer_resolves_registered_chat_target_reference() -> None:
    """Creating an LLM scorer resolves chat_target by registered target name."""
    target = MockPromptTarget()
    TargetRegistry.get_registry_singleton().instances.register(target, name="judge-target")
    service = ScorerService()

    created = await service.create_scorer_async(
        request=CreateScorerRequest(
            type="SelfAskTrueFalseScorer",
            params={"chat_target": "judge-target"},
        )
    )

    scorer_obj = service.get_scorer_object(scorer_id=created.scorer_id)
    assert created.scorer_type == "SelfAskTrueFalseScorer"
    assert created.score_type == "true_false"
    assert scorer_obj is not None
    assert scorer_obj.get_chat_target() is target


async def test_create_scorer_invalid_params_raise_value_error() -> None:
    """Invalid scorer params are normalized to ValueError for route-friendly 400 handling."""
    service = ScorerService()

    with pytest.raises(ValueError, match="substring"):
        await service.create_scorer_async(
            request=CreateScorerRequest(
                type="SubStringScorer",
                params={},
            )
        )


async def test_scale_scorer_catalog_exposes_required_prompt_and_structured_rubric() -> None:
    """The catalog must describe the complete form needed to construct a scale scorer."""
    service = ScorerService()
    catalog = await service.list_scorer_catalog_async()
    entry = next(item for item in catalog.items if item.scorer_type == "SelfAskScaleScorer")
    parameters = {parameter.name: parameter for parameter in entry.parameters}
    assert parameters["system_prompt"].required
    assert parameters["system_prompt"].input_kind == "multiline"
    assert parameters["scale"].required
    assert parameters["scale"].input_kind == "json"
    assert parameters["scale"].json_schema is not None
    assert "minimum_value" in parameters["scale"].json_schema["properties"]
    assert parameters["response_handler"].input_kind == "unsupported"
    assert not parameters["response_handler"].required


async def test_scorer_catalog_preserves_string_sequence_unions() -> None:
    """Category parameters accepting either one string or a sequence retain a list form."""
    catalog = await ScorerService().list_scorer_catalog_async()
    for scorer_type, name in [
        ("InsecureCodeScorer", "harm_categories"),
        ("SelfAskRefusalScorer", "score_category"),
    ]:
        entry = next(item for item in catalog.items if item.scorer_type == scorer_type)
        parameter = next(item for item in entry.parameters if item.name == name)
        assert parameter.input_kind == "field"
        assert parameter.is_list
        assert parameter.type_name == "list[str]"
        assert parameter.coerce_value(["category one", "category two"]) == ["category one", "category two"]


async def test_create_insecure_code_scorer_keeps_multiple_categories() -> None:
    """A catalog-shaped array reaches the canonical constructor as multiple categories."""
    TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="judge")
    service = ScorerService()
    created = await service.create_scorer_async(
        request=CreateScorerRequest(
            type="InsecureCodeScorer",
            params={
                "chat_target": "judge",
                "system_prompt": "Evaluate code for the specified categories.",
                "harm_categories": ["category one", "category two"],
            },
        )
    )
    scorer = service.get_scorer_object(scorer_id=created.scorer_id)
    assert scorer is not None
    assert list(scorer.get_identifier().params["harm_categories"]) == ["category one", "category two"]


async def test_create_and_use_scale_scorer_with_json_rubric() -> None:
    """JSON numeric-rubric input becomes the canonical model and yields a persisted numeric score."""
    target = MockPromptTarget()
    TargetRegistry.get_registry_singleton().instances.register(target, name="judge")
    service = ScorerService()
    created = await service.create_scorer_async(
        request=CreateScorerRequest(
            type="SelfAskScaleScorer",
            params={
                "chat_target": "judge",
                "system_prompt": "Return JSON with score_value from 0 to 10 and rationale.",
                "scale": {"minimum_value": 0, "maximum_value": 10, "category": "math"},
            },
        )
    )
    scorer = service.get_scorer_object(scorer_id=created.scorer_id)
    assert isinstance(scorer, SelfAskScaleScorer)
    assert scorer.get_identifier().params["scale"]["maximum_value"] == 10
    conversation_id = "scale-conversation"
    attack = _persist_conversation(
        conversation_id=conversation_id,
        objective="Talk about math",
        messages=[
            _message(role="assistant", value="Two plus two is four.", conversation_id=conversation_id, sequence=0),
        ],
    )

    async def judge_async(*, normalized_conversation: list[Message]) -> list[Message]:
        return [
            MessagePiece(
                role="assistant",
                original_value='{"score_value": 8, "rationale": "The response discusses arithmetic."}',
                conversation_id=normalized_conversation[-1].conversation_id,
            ).to_message()
        ]

    with patch.object(target, "_send_prompt_to_target_async", side_effect=judge_async):
        result = await service.score_attack_result_async(
            scorer_id=created.scorer_id,
            request=ScoreAttackRequest(
                attack_result_id=attack.attack_result_id,
                conversation_id=conversation_id,
                expected_scorer_hash=created.identifier_hash,
                objective="Talk about math",
            ),
        )
    assert result.status == "complete"
    assert result.scores[0].get_value() == 0.8
    assert CentralMemory.get_memory_instance().get_scores(score_ids=[str(result.scores[0].id)])


async def test_create_scale_scorer_rejects_invalid_rubric_before_construction() -> None:
    """Invalid numeric bounds are a useful input error rather than an attribute error later."""
    service = ScorerService()
    with pytest.raises(ValueError, match="minimum_value must be less"):
        await service.create_scorer_async(
            request=CreateScorerRequest(
                type="SelfAskScaleScorer",
                params={
                    "system_prompt": "Judge the response.",
                    "scale": {"minimum_value": 10, "maximum_value": 0, "category": "math"},
                },
            )
        )


def test_scorer_catalog_keeps_supported_list_parameters() -> None:
    """Lists must not disappear merely because they cannot be coerced from one scalar token."""
    parameters = ScorerService()._catalog_parameters(
        parameters=[
            Parameter(name="values", description="Values", param_type=list[str]),
        ]
    )
    assert parameters[0].is_list
    assert parameters[0].input_kind == "field"


async def test_score_attack_result_response_scope_persists_real_substring_score(sqlite_instance) -> None:
    """Response scope scores the latest stored assistant message and persists the result."""
    conversation_id = "conv-substring"
    attack_result = _persist_conversation(
        conversation_id=conversation_id,
        objective="Find WIN",
        messages=[
            _message(role="system", value="You are helpful.", conversation_id=conversation_id, sequence=0),
            _message(role="user", value="Say WIN now.", conversation_id=conversation_id, sequence=1),
            _message(role="assistant", value="WIN achieved.", conversation_id=conversation_id, sequence=2),
        ],
    )
    scorer = SubStringScorer(substring="WIN")
    service = ScorerService()
    service._registry.instances.register(scorer, name="substring")

    result = await service.score_attack_result_async(
        scorer_id="substring",
        request=ScoreAttackRequest(
            attack_result_id=attack_result.attack_result_id,
            conversation_id=conversation_id,
            expected_scorer_hash=scorer.get_identifier().hash,
            objective=attack_result.objective,
        ),
    )

    assert result.status == "complete"
    assert result.scorer_hash == scorer.get_identifier().hash
    assert len(result.scores) == 1
    score = result.scores[0]
    assert score.score_type == "true_false"
    assert str(score.score_value).lower() == "true"
    assert score.status == "complete"
    assert score.scorer_type == "SubStringScorer"
    assert score.scorable is not None
    persisted = sqlite_instance.get_scores(score_ids=[str(score.id)])
    assert len(persisted) == 1
    dumped = result.model_dump(mode="json")
    assert dumped["scores"][0]["scorer_type"] == "SubStringScorer"
    assert dumped["scores"][0]["status"] == "complete"


async def test_score_attack_result_response_scope_returns_float_scale_score() -> None:
    """Float scorers keep their score family and payload shape through the API response."""
    conversation_id = "conv-float"
    attack_result = _persist_conversation(
        conversation_id=conversation_id,
        objective="Assess similarity",
        messages=[
            _message(role="user", value="hello there", conversation_id=conversation_id, sequence=0),
            _message(role="assistant", value="hello there friend", conversation_id=conversation_id, sequence=1),
        ],
    )
    scorer = PlagiarismScorer(reference_text="hello there")
    service = ScorerService()
    service._registry.instances.register(scorer, name="plagiarism")

    result = await service.score_attack_result_async(
        scorer_id="plagiarism",
        request=ScoreAttackRequest(
            attack_result_id=attack_result.attack_result_id,
            conversation_id=conversation_id,
            expected_scorer_hash=scorer.get_identifier().hash,
        ),
    )

    assert result.status == "complete"
    assert len(result.scores) == 1
    assert result.scores[0].score_type == "float_scale"
    assert 0 <= float(str(result.scores[0].score_value)) <= 1


async def test_score_attack_result_response_scope_returns_undetermined_for_error_response() -> None:
    """Unreadable assistant errors remain scorer-owned undetermined results rather than route-level skips."""
    conversation_id = "conv-undetermined"
    attack_result = _persist_conversation(
        conversation_id=conversation_id,
        objective="Find WIN",
        messages=[
            _message(role="user", value="Say WIN now.", conversation_id=conversation_id, sequence=0),
            _message(
                role="assistant",
                value="transport failure",
                conversation_id=conversation_id,
                sequence=1,
                response_error="processing",
            ),
        ],
    )
    scorer = SubStringScorer(substring="WIN")
    service = ScorerService()
    service._registry.instances.register(scorer, name="substring")

    result = await service.score_attack_result_async(
        scorer_id="substring",
        request=ScoreAttackRequest(
            attack_result_id=attack_result.attack_result_id,
            conversation_id=conversation_id,
            expected_scorer_hash=scorer.get_identifier().hash,
        ),
    )

    assert result.status == "complete"
    assert len(result.scores) == 1
    assert result.scores[0].status == "undetermined"
    assert result.scores[0].score_value is None


async def test_score_attack_result_response_scope_ignores_simulated_assistant_history() -> None:
    """Response scope does not treat prepended simulated assistant turns as the latest response."""
    conversation_id = "conv-simulated"
    attack_result = _persist_conversation(
        conversation_id=conversation_id,
        objective="Find WIN",
        messages=[
            _message(role="user", value="Original question", conversation_id=conversation_id, sequence=0),
            _message(
                role="simulated_assistant",
                value="Historic assistant answer with WIN",
                conversation_id=conversation_id,
                sequence=1,
            ),
            _message(role="user", value="Pending follow-up", conversation_id=conversation_id, sequence=2),
        ],
    )
    scorer = SubStringScorer(substring="WIN")
    service = ScorerService()
    service._registry.instances.register(scorer, name="substring")

    result = await service.score_attack_result_async(
        scorer_id="substring",
        request=ScoreAttackRequest(
            attack_result_id=attack_result.attack_result_id,
            conversation_id=conversation_id,
            expected_scorer_hash=scorer.get_identifier().hash,
        ),
    )

    assert result.status == "not_applicable"
    assert result.scores == []


async def test_score_attack_result_wrong_conversation_fails_before_scoring() -> None:
    """Conversation identity mismatch returns conflict without invoking the scorer."""
    conversation_id = "conv-owned"
    attack_result = _persist_conversation(
        conversation_id=conversation_id,
        objective="Find WIN",
        messages=[
            _message(role="user", value="Say WIN", conversation_id=conversation_id, sequence=0),
            _message(role="assistant", value="WIN", conversation_id=conversation_id, sequence=1),
        ],
    )
    _persist_conversation(
        conversation_id="conv-other",
        objective="Other attack",
        messages=[_message(role="assistant", value="WIN", conversation_id="conv-other", sequence=0)],
    )
    scorer = SubStringScorer(substring="WIN")
    scorer.score_async = AsyncMock(return_value=[])
    service = ScorerService()
    service._registry.instances.register(scorer, name="substring")

    with pytest.raises(ScorerConflictError, match="is not part of attack"):
        await service.score_attack_result_async(
            scorer_id="substring",
            request=ScoreAttackRequest(
                attack_result_id=attack_result.attack_result_id,
                conversation_id="conv-other",
                expected_scorer_hash=scorer.get_identifier().hash,
            ),
        )

    scorer.score_async.assert_not_awaited()


async def test_score_attack_result_hash_mismatch_fails_before_scoring() -> None:
    """Stale scorer hashes are rejected before any judge invocation occurs."""
    conversation_id = "conv-hash"
    attack_result = _persist_conversation(
        conversation_id=conversation_id,
        objective="Find WIN",
        messages=[
            _message(role="user", value="Say WIN", conversation_id=conversation_id, sequence=0),
            _message(role="assistant", value="WIN", conversation_id=conversation_id, sequence=1),
        ],
    )
    scorer = SubStringScorer(substring="WIN")
    scorer.score_async = AsyncMock(return_value=[])
    service = ScorerService()
    service._registry.instances.register(scorer, name="substring")

    with pytest.raises(ScorerConflictError, match="no longer matches hash"):
        await service.score_attack_result_async(
            scorer_id="substring",
            request=ScoreAttackRequest(
                attack_result_id=attack_result.attack_result_id,
                conversation_id=conversation_id,
                expected_scorer_hash="0" * 64,
            ),
        )

    scorer.score_async.assert_not_awaited()


async def test_score_attack_result_conversation_scope_not_applicable_for_unread_roles() -> None:
    """Conversation scope reports not_applicable when every stored message is outside scorer applicability."""
    conversation_id = "conv-not-applicable"
    attack_result = _persist_conversation(
        conversation_id=conversation_id,
        objective="Assess leak",
        messages=[_message(role="user", value="hello there", conversation_id=conversation_id, sequence=0)],
    )
    scorer = SystemPromptExtractionScorer()
    service = ScorerService()
    service._registry.instances.register(scorer, name="system-prompt")

    result = await service.score_attack_result_async(
        scorer_id="system-prompt",
        request=ScoreAttackRequest(
            attack_result_id=attack_result.attack_result_id,
            conversation_id=conversation_id,
            expected_scorer_hash=scorer.get_identifier().hash,
            scope="conversation",
        ),
    )

    assert result.status == "not_applicable"
    assert result.scores == []
