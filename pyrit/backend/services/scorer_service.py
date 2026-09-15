# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Service for backend scorer discovery, instantiation, and scoring."""

from __future__ import annotations

import asyncio
import types
import uuid
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal, get_args, get_origin

from pyrit.backend.models.attacks import ScoreView
from pyrit.backend.models.common import SENSITIVE_FIELD_PATTERNS
from pyrit.backend.models.scorers import (
    CreateScorerRequest,
    ScoreAttackRequest,
    ScoreAttackResponse,
    ScorerCatalogEntry,
    ScorerCatalogResponse,
    ScorerInstance,
    ScorerListResponse,
)
from pyrit.memory import CentralMemory
from pyrit.models import ComponentType, Message, MessageScorable, Parameter, Score, ScoreType, ScoringExpectation
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
    """Raised when scorer identity or attack conversation identity does not match the request."""


class ScorerService:
    """Service for scorer catalog, instance management, and persisted-evidence scoring."""

    def __init__(self) -> None:
        """Initialize the scorer service."""
        self._registry = ScorerRegistry.get_registry_singleton()
        self._target_registry = TargetRegistry.get_registry_singleton()
        self._memory = CentralMemory.get_memory_instance()

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
        items = [
            ScorerCatalogEntry(
                scorer_type=metadata.class_name,
                score_type=self._score_type_for_class_name(class_name=metadata.class_name),
                is_llm_based=metadata.is_llm_based,
                parameters=self._catalog_parameters(parameters=metadata.parameters),
                description=metadata.class_description or None,
            )
            for metadata in metadata_items
        ]
        return ScorerCatalogResponse(items=items)

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
            scorer = await asyncio.to_thread(self._registry.create_instance, request.type, **dict(request.params))
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(str(exc)) from None

        scorer_id = str(uuid.uuid4())
        self._registry.instances.register(scorer, name=scorer_id)
        return self._build_instance_from_object(scorer_id=scorer_id, scorer_obj=scorer)

    async def score_attack_result_async(self, *, scorer_id: str, request: ScoreAttackRequest) -> ScoreAttackResponse:
        """
        Score persisted attack evidence with a registered scorer.

        Response scope scores the latest non-simulated assistant message only. Conversation
        scope scores each stored message in sequence, allowing the scorer's own role and
        evidence applicability contract to decide which turns matter.

        Args:
            scorer_id: Registered scorer instance ID or alias.
            request: Attack/conversation identity plus scoring scope and expectation.

        Returns:
            ScoreAttackResponse: Persisted score results or a not-applicable marker.

        Raises:
            FileNotFoundError: If the scorer or attack result does not exist.
            ValueError: If the conversation has no stored messages to score.
            ScorerConflictError: If the expected scorer hash or attack conversation identity does not match.
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

        expectation = ScoringExpectation(objective=request.objective)
        if request.scope == "response":
            response_message = self._select_latest_response_message(messages=messages)
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
            scores = await self._score_conversation_messages_async(
                scorer=scorer,
                messages=messages,
                expectation=expectation,
            )

        return ScoreAttackResponse(
            scorer_id=scorer_id,
            scorer_hash=scorer_hash,
            scores=[ScoreView.from_domain(score) for score in scores],
            status="complete" if scores else "not_applicable",
        )

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

    def _catalog_parameters(self, *, parameters: Sequence[Parameter]) -> list[Parameter]:
        """
        Project registry parameters into API-safe catalog parameters.

        Returns:
            list[Parameter]: Scalar-compatible and selectable-reference parameters.
        """
        projected: list[Parameter] = []
        for parameter in parameters:
            if parameter.is_string_coercible:
                projected.append(self._sanitize_parameter(parameter=parameter))
                continue
            if parameter.reference is None:
                continue
            projected.append(self._project_reference_parameter(parameter=parameter))
        return projected

    def _project_reference_parameter(self, *, parameter: Parameter) -> Parameter:
        """
        Render a registry-reference parameter as a selectable string or string-list field.

        Returns:
            Parameter: A wire-safe parameter whose choices mirror currently registered instances.
        """
        reference = parameter.reference
        if reference is None:
            raise ValueError(f"Parameter '{parameter.name}' is not a registry reference.")

        choices = self._reference_choices(component_type=reference.component_type)
        is_list = self._annotation_is_list(reference.annotation)
        param_type = self._choice_param_type(choices=choices, is_list=is_list)

        return Parameter(
            name=parameter.name,
            description=parameter.description,
            default=self._sanitize_default(name=parameter.name, value=parameter.default),
            param_type=param_type,
        )

    def _reference_choices(self, *, component_type: ComponentType) -> list[str]:
        """
        Return the currently registered instance names for a reference parameter.

        Returns:
            list[str]: Sorted instance names that can satisfy the reference.
        """
        if component_type is ComponentType.TARGET:
            return self._target_registry.instances.get_names()
        if component_type is ComponentType.SCORER:
            return self._registry.instances.get_names()
        return []

    @staticmethod
    def _annotation_is_list(annotation: Any) -> bool:
        """
        Determine whether a reference annotation accepts a list of registry names.

        Returns:
            bool: True when the annotation is list-shaped.
        """
        if annotation is None:
            return False
        origin = get_origin(annotation)
        if origin is list:
            return True
        if origin in {types.UnionType, getattr(types, "UnionType", types.UnionType)} or str(origin) == "typing.Union":
            non_none = [arg for arg in get_args(annotation) if arg is not type(None)]
            return len(non_none) == 1 and ScorerService._annotation_is_list(non_none[0])
        return False

    @staticmethod
    def _choice_param_type(*, choices: list[str], is_list: bool) -> Any:
        """
        Build a display param type that surfaces current choices on the wire.

        Returns:
            Any: ``str`` / ``list[str]`` when unconstrained, otherwise a Literal-based equivalent.
        """
        scalar_type = Literal.__getitem__(tuple(choices)) if choices else str
        return types.GenericAlias(list, scalar_type) if is_list else scalar_type

    @staticmethod
    def _sanitize_parameter(*, parameter: Parameter) -> Parameter:
        """
        Clone a catalog parameter with sensitive defaults removed.

        Returns:
            Parameter: A parameter safe to serialize to the API.
        """
        return Parameter(
            name=parameter.name,
            description=parameter.description,
            default=ScorerService._sanitize_default(name=parameter.name, value=parameter.default),
            param_type=parameter.param_type,
        )

    @staticmethod
    def _sanitize_default(*, name: str, value: Any) -> Any:
        """
        Remove secret-bearing defaults from catalog parameters.

        Returns:
            Any: The original default when safe, otherwise None.
        """
        lower_name = name.lower()
        if any(pattern in lower_name for pattern in SENSITIVE_FIELD_PATTERNS):
            return None
        return value

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
