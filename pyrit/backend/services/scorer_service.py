# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Service for backend scorer discovery, validation, instantiation, and scoring."""

from __future__ import annotations

import asyncio
import uuid
from functools import lru_cache
from typing import TYPE_CHECKING

from pyrit.backend.models.attacks import ScoreView
from pyrit.backend.models.scorers import (
    CreateScorerRequest,
    ExpectedResponsePiece,
    ScoreAttackRequest,
    ScoreAttackResponse,
    ScorerCatalogEntry,
    ScorerCatalogResponse,
    ScorerInstance,
    ScorerListResponse,
    ScorerParameter,
    ScorerValidationResponse,
)
from pyrit.backend.services.scorer_configuration import ScorerConfigurationManager
from pyrit.memory import CentralMemory
from pyrit.models import Message, MessageScorable, Parameter, Score, ScoreType, ScoringExpectation
from pyrit.registry import ScorerRegistry, TargetRegistry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.score import Scorer

__all__ = [
    "ScorerConflictError",
    "ScorerService",
    "get_scorer_service",
]


class ScorerConflictError(Exception):
    """Raised when scorer identity or stored evidence does not match the request."""


class ScorerService:
    """Service for scorer catalog, validation, instance management, and persisted-evidence scoring."""

    def __init__(self) -> None:
        """Initialize the scorer service."""
        self._registry = ScorerRegistry.get_registry_singleton()
        self._target_registry = TargetRegistry.get_registry_singleton()
        self._memory = CentralMemory.get_memory_instance()
        self._configuration_manager = ScorerConfigurationManager(
            scorer_registry=self._registry,
            target_registry=self._target_registry,
        )

    async def list_scorers_async(self) -> ScorerListResponse:
        """
        List all registered scorer instances.

        Returns:
            ScorerListResponse: Registered scorers from the instance registry.
        """
        items = [
            self._build_instance_from_object(scorer_id=entry.name, scorer_obj=entry.instance)
            for entry in self._registry.instances.get_all_instances()
        ]
        return ScorerListResponse(items=items)

    async def get_scorer_async(self, *, scorer_id: str) -> ScorerInstance | None:
        """
        Get a registered scorer instance by ID.

        Returns:
            ScorerInstance | None: Compact scorer instance metadata, or None when missing.
        """
        scorer = self._registry.instances.get(scorer_id)
        if scorer is None:
            return None
        return self._build_instance_from_object(scorer_id=scorer_id, scorer_obj=scorer)

    def get_scorer_object(self, *, scorer_id: str) -> Scorer | None:
        """
        Get the actual scorer object for internal use.

        Returns:
            Scorer | None: The registered scorer object, or None when missing.
        """
        return self._registry.instances.get(scorer_id)

    async def list_scorer_catalog_async(self) -> ScorerCatalogResponse:
        """
        List all available scorer types from the scorer registry.

        Returns:
            ScorerCatalogResponse: Catalog of constructible scorer classes.
        """
        metadata_items = await asyncio.to_thread(self._registry.get_all_registered_class_metadata)
        items = []
        for metadata in metadata_items:
            owner_cls = self._registry.get_class(metadata.class_name)
            items.append(
                ScorerCatalogEntry(
                    scorer_type=metadata.class_name,
                    score_type=self._score_type_for_class_name(class_name=metadata.class_name),
                    is_llm_based=metadata.is_llm_based,
                    parameters=self._catalog_parameters(parameters=metadata.parameters, owner_cls=owner_cls),
                    description=metadata.class_description or None,
                )
            )
        return ScorerCatalogResponse(items=items)

    async def validate_scorer_request_async(self, *, request: CreateScorerRequest) -> ScorerValidationResponse:
        """
        Validate a scorer configuration without registering it or calling provider models.

        Returns:
            ScorerValidationResponse: ``{"valid": true}`` when the scorer can be constructed safely.

        Raises:
            ValueError: If the scorer type or parameters are invalid.
        """
        try:
            await asyncio.to_thread(self._configuration_manager.build_scorer, request=request)
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(str(exc)) from None
        return ScorerValidationResponse()

    async def create_scorer_async(self, *, request: CreateScorerRequest) -> ScorerInstance:
        """
        Create and register a scorer instance.

        Args:
            request: Scorer type and constructor parameters.

        Returns:
            ScorerInstance: The created scorer's compact identity.

        Raises:
            ValueError: If the scorer type or parameters are invalid.
        """
        if request.type not in self._registry:
            raise ValueError(
                f"Scorer type '{request.type}' not found. Available types: {self._registry.get_class_names()}"
            )

        try:
            scorer = await asyncio.to_thread(self._configuration_manager.build_scorer, request=request)
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(str(exc)) from None

        scorer_id = str(uuid.uuid4())
        self._registry.instances.register(scorer, name=scorer_id)
        return self._build_instance_from_object(scorer_id=scorer_id, scorer_obj=scorer)

    async def score_attack_result_async(self, *, scorer_id: str, request: ScoreAttackRequest) -> ScoreAttackResponse:
        """
        Score persisted attack evidence with a registered scorer.

        Response scope scores the latest non-simulated assistant message only unless an
        anchored evidence sequence/piece-id pair is supplied, in which case the stored
        anchored message is scored exactly. Conversation scope scores each stored message
        in sequence, optionally truncating at the anchored sequence.

        Args:
            scorer_id: Registered scorer instance ID or alias.
            request: Attack/conversation identity plus scoring scope and expectation.

        Returns:
            ScoreAttackResponse: Persisted score results or a not-applicable marker.

        Raises:
            FileNotFoundError: If the scorer or attack result does not exist.
            ValueError: If the conversation has no stored messages to score.
            ScorerConflictError: If the expected scorer hash, attack conversation identity, or
                anchored evidence does not match the request.
        """
        scorer = self.get_scorer_object(scorer_id=scorer_id)
        if scorer is None:
            raise FileNotFoundError(f"Scorer '{scorer_id}' not found")

        scorer_hash = scorer.get_identifier().hash
        if scorer_hash.lower() != request.expected_scorer_hash.lower():
            raise ScorerConflictError(
                f"Scorer '{scorer_id}' no longer matches hash '{request.expected_scorer_hash}'. Refresh and retry."
            )

        attack_results = await asyncio.to_thread(
            self._memory.get_attack_results,
            attack_result_ids=[request.attack_result_id],
        )
        if not attack_results:
            raise FileNotFoundError(f"Attack '{request.attack_result_id}' not found")

        attack_result = attack_results[0]
        if request.conversation_id not in attack_result.get_active_conversation_ids():
            raise ScorerConflictError(
                f"Conversation '{request.conversation_id}' is not part of attack '{request.attack_result_id}'."
            )

        messages = list(
            await asyncio.to_thread(
                self._memory.get_conversation_messages,
                conversation_id=request.conversation_id,
            )
        )
        if not messages:
            raise ValueError(f"Conversation '{request.conversation_id}' has no stored messages to score.")

        anchored_message = self._resolve_anchored_message(messages=messages, request=request)
        expectation = ScoringExpectation(objective=request.objective)
        if request.scope == "response":
            response_message = anchored_message or self._select_latest_response_message(messages=messages)
            if response_message is None:
                return ScoreAttackResponse(
                    scorer_id=scorer_id,
                    scorer_hash=scorer_hash,
                    scores=[],
                    status="not_applicable",
                )
            scores = await scorer.score_async(
                scorable=MessageScorable.from_message(response_message),
                expectation=expectation,
            )
        else:
            scoped_messages = self._conversation_scope_messages(messages=messages, request=request)
            scores = await self._score_conversation_messages_async(
                scorer=scorer,
                messages=scoped_messages,
                expectation=expectation,
            )

        return ScoreAttackResponse(
            scorer_id=scorer_id,
            scorer_hash=scorer_hash,
            scores=[ScoreView.from_domain(score) for score in scores],
            status="complete" if scores else "not_applicable",
        )

    def _catalog_parameters(
        self,
        *,
        parameters: Sequence[Parameter],
        owner_cls: type | None = None,
    ) -> list[ScorerParameter]:
        """
        Project registry parameters into API-safe catalog parameters.

        Returns:
            list[ScorerParameter]: Complete constructor contract, including nested component metadata.
        """
        return self._configuration_manager.project_catalog_parameters(parameters=parameters, owner_cls=owner_cls)

    def _build_instance_from_object(self, *, scorer_id: str, scorer_obj: Scorer) -> ScorerInstance:
        """
        Build a compact scorer instance view from a registered scorer object.

        Returns:
            ScorerInstance: Compact scorer metadata for the API.
        """
        identifier = scorer_obj.get_identifier()
        return ScorerInstance(
            scorer_id=scorer_id,
            scorer_type=identifier.class_name or scorer_obj.__class__.__name__,
            identifier_hash=identifier.hash,
            score_type=scorer_obj.scorer_type,
        )

    async def _score_conversation_messages_async(
        self,
        *,
        scorer: Scorer,
        messages: list[Message],
        expectation: ScoringExpectation,
    ) -> list[Score]:
        """
        Score stored messages in sequence and preserve the returned score order.

        Returns:
            list[Score]: Flattened persisted scores from all applicable messages.
        """
        scores: list[Score] = []
        for message in messages:
            message_scores = await scorer.score_async(
                scorable=MessageScorable.from_message(message),
                expectation=expectation,
            )
            scores.extend(message_scores)
        return scores

    def _resolve_anchored_message(self, *, messages: list[Message], request: ScoreAttackRequest) -> Message | None:
        """
        Resolve and validate the anchored persisted message selected by the request.

        Returns:
            Message | None: The anchored message, or None when the request uses legacy latest-response semantics.

        Raises:
            ScorerConflictError: If the anchored sequence, piece IDs, or expected content do not
                match the persisted conversation state.
        """
        if request.evidence_sequence is None or request.evidence_message_piece_ids is None:
            return None

        anchored_candidates = [message for message in messages if message.sequence == request.evidence_sequence]
        if not anchored_candidates:
            raise ScorerConflictError(
                f"Conversation '{request.conversation_id}' no longer contains sequence {request.evidence_sequence}."
            )

        anchored_message = next(
            (
                message
                for message in anchored_candidates
                if [piece.id for piece in message.message_pieces] == request.evidence_message_piece_ids
            ),
            None,
        )
        if anchored_message is None:
            raise ScorerConflictError(
                "The stored response at the requested evidence anchor no longer matches the selected message pieces."
            )

        self._validate_expected_response(anchored_message=anchored_message, expected_response=request.expected_response)
        return anchored_message

    @staticmethod
    def _validate_expected_response(
        *,
        anchored_message: Message,
        expected_response: list[ExpectedResponsePiece] | None,
    ) -> None:
        """Reject stale anchored-response payloads whose stored converted content changed."""
        if expected_response is None:
            return
        actual_response = [
            ExpectedResponsePiece(
                id=piece.id,
                converted_value=piece.converted_value,
                converted_value_data_type=piece.converted_value_data_type,
            )
            for piece in anchored_message.message_pieces
        ]
        if actual_response != expected_response:
            raise ScorerConflictError("The stored response at the requested evidence anchor no longer matches.")

    @staticmethod
    def _conversation_scope_messages(*, messages: list[Message], request: ScoreAttackRequest) -> list[Message]:
        """Return the messages visible to the requested conversation-scoring scope."""
        if request.evidence_sequence is None:
            return messages
        return [message for message in messages if message.sequence <= request.evidence_sequence]

    @staticmethod
    def _select_latest_response_message(*, messages: list[Message]) -> Message | None:
        """
        Return the latest real assistant message in the conversation.

        Simulated assistant turns are prepended context, not the target's latest response,
        so response scope must not select them.

        Returns:
            Message | None: The latest non-simulated assistant message, or None if absent.
        """
        for message in reversed(messages):
            if message.api_role == "assistant" and not message.is_simulated:
                return message
        return None

    def _score_type_for_class_name(self, *, class_name: str) -> ScoreType:
        """
        Determine the score family for a registered scorer class.

        Returns:
            ScoreType: ``true_false``, ``float_scale``, or ``unknown``.
        """
        from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
        from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

        scorer_class = self._registry.get_class(class_name)
        if issubclass(scorer_class, TrueFalseScorer):
            return "true_false"
        if issubclass(scorer_class, FloatScaleScorer):
            return "float_scale"
        return "unknown"


@lru_cache(maxsize=1)
def get_scorer_service() -> ScorerService:
    """
    Get the global scorer service instance.

    Returns:
        ScorerService: The singleton scorer service.
    """
    return ScorerService()
