# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scorer API adapters for registry construction.

The registry owns constructor contracts and scalar string coercion. This module adds
bounded inline components and allowlisted JSON/YAML adapters for non-wire Python values.
"""

from __future__ import annotations

import copy
import inspect
import pathlib
import types
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Annotated, Any, Literal, get_args, get_origin, get_type_hints

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError, model_validator

from pyrit.analytics.text_matching import ApproximateTextMatching, ExactTextMatching, TextMatching
from pyrit.backend.models.common import SENSITIVE_FIELD_PATTERNS
from pyrit.backend.models.scorers import CreateScorerRequest, ParameterPreset, ScorerInstance, ScorerParameter
from pyrit.common.apply_defaults import REQUIRED_VALUE
from pyrit.models import (
    COMMON_JSON_SCHEMAS,
    JsonSchemaDefinition,
    JSONValue,
    Parameter,
    SeedPrompt,
    get_common_json_schema,
)
from pyrit.models.literals import (  # noqa: TC001  (runtime-required by Pydantic field annotations)
    ChatMessageRole,
    PromptDataType,
)
from pyrit.prompt_target import PromptShieldTarget, PromptTarget
from pyrit.registry import ScorerRegistry, TargetRegistry
from pyrit.score import Scorer
from pyrit.score.float_scale.float_scale_score_aggregator import (
    FloatScaleAggregatorFunc,
    FloatScaleScoreAggregator,
    FloatScaleScorerAllCategories,
    FloatScaleScorerByCategory,
)
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer, MessageFloatScaleScorer
from pyrit.score.float_scale.likert_scale import LikertScale
from pyrit.score.float_scale.numeric_scale import NumericRange, NumericRubric
from pyrit.score.response_handler import JsonSchemaResponseHandler, ResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.llamaguard_policy import LlamaGuardPolicy
from pyrit.score.true_false.llamaguard_scorer import LlamaGuardMessageRole
from pyrit.score.true_false.self_ask_category_scorer import ContentClassifier
from pyrit.score.true_false.self_ask_true_false_scorer import TrueFalseQuestion
from pyrit.score.true_false.shieldgemma_policy import ShieldGemmaGuideline, ShieldGemmaMessageRole
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc, TrueFalseScoreAggregator
from pyrit.score.true_false.true_false_scorer import MessageTrueFalseScorer, TrueFalseScorer

_MAX_COMPONENT_DEPTH = 6
_MAX_COMPONENT_COUNT = 32
_MAX_STRUCTURED_DEPTH = 20
_MAX_YAML_CHARS = 64 * 1024
_INLINE_SPEC_KEYS = frozenset({"type", "params"})
_JSON_SCHEMA_PARAMETER_KEYWORDS = ("schema",)
_SEED_PROMPT_KEYS = ("value", "response_json_schema", "response_json_schema_name", "parameters", "metadata", "name")
_JSON_SCHEMA_KEYWORDS = ("type", "properties", "oneOf", "anyOf", "allOf", "enum", "items", "$ref")
_JSON_SCHEMA_PARAMETER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "type": {
            "anyOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}, "minItems": 1},
            ]
        },
        "properties": {"type": "object", "additionalProperties": {"type": "object"}},
        "required": {"type": "array", "items": {"type": "string"}},
        "items": {"anyOf": [{"type": "object"}, {"type": "array"}]},
        "enum": {"type": "array"},
        "$ref": {"type": "string"},
        "additionalProperties": {"anyOf": [{"type": "boolean"}, {"type": "object"}]},
    },
}


class _InlineComponentSpec(BaseModel):
    """Trusted inline component specification accepted in scorer configs."""

    model_config = ConfigDict(extra="forbid")

    type: Annotated[str, StringConstraints(strip_whitespace=True)] = Field(..., min_length=1, max_length=200)
    params: dict[str, Any] = Field(default_factory=dict)


class _YamlPayload(BaseModel):
    """Explicit YAML wrapper for structured scorer parameter inputs."""

    model_config = ConfigDict(extra="forbid")

    yaml: str = Field(..., min_length=1, max_length=_MAX_YAML_CHARS)


class _SeedPromptInput(BaseModel):
    """Safe text-only subset of ``SeedPrompt`` accepted from scorer configs."""

    model_config = ConfigDict(extra="forbid")

    value: str
    data_type: Literal["text"] | None = None
    name: str | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)
    parameters: list[str] = Field(default_factory=list)
    response_json_schema: dict[str, Any] | None = None
    response_json_schema_name: str | None = None
    is_jinja_template: Literal[False] = False

    @model_validator(mode="after")
    def _validate_schema_inputs(self) -> _SeedPromptInput:
        if self.response_json_schema is not None and self.response_json_schema_name is not None:
            raise ValueError("Set only one of response_json_schema or response_json_schema_name.")
        return self


class _JsonResponseHandlerInput(BaseModel):
    """Serializable config for the supported response-handler backend adapter."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["json"] = "json"
    score_value_output_key: str = "score_value"
    rationale_output_key: str = "rationale"
    description_output_key: str = "description"
    metadata_output_key: str = "metadata"
    category_output_key: str = "category"
    response_schema: dict[str, Any] | None = None
    numeric_value: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize_schema_key(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if "type" not in normalized:
            normalized["type"] = "json"
        if "response_schema" not in normalized and "response_json_schema" in normalized:
            normalized["response_schema"] = normalized.pop("response_json_schema")
        return normalized


class _ScorerPromptValidatorInput(BaseModel):
    """Serializable config for a scorer prompt validator."""

    model_config = ConfigDict(extra="forbid")

    supported_data_types: list[PromptDataType] | None = None
    required_metadata: list[str] | None = None
    supported_roles: list[ChatMessageRole] | None = None
    max_pieces_in_response: int | None = Field(default=None, ge=1)
    max_text_length: int | None = Field(default=None, ge=1)
    enforce_all_pieces_valid: bool = False
    raise_on_no_valid_pieces: bool = False
    is_objective_required: bool = False


class _ExactTextMatchingInput(BaseModel):
    """Serializable config for exact substring matching."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["exact", "ExactTextMatching"] = "exact"
    case_sensitive: bool = False
    ignore_whitespace: bool = True


class _ApproximateTextMatchingInput(BaseModel):
    """Serializable config for approximate n-gram matching."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["approximate", "ApproximateTextMatching"] = "approximate"
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    n: int = Field(default=3, ge=1)
    case_sensitive: bool = False


_TextMatchingInput = Annotated[_ExactTextMatchingInput | _ApproximateTextMatchingInput, Field(discriminator="type")]

_TEXT_MATCHING_ADAPTER = TypeAdapter(_TextMatchingInput)
_STRING_LIST_ADAPTER = TypeAdapter(list[str])
_STRING_SET_ADAPTER = TypeAdapter(set[str])
_STRING_MAPPING_ADAPTER = TypeAdapter(dict[str, str])
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, Any])


@dataclass(frozen=True)
class _ComponentParameterInfo:
    """Resolved compatibility contract for a scorer or target reference parameter."""

    kind: Literal["scorer", "target"]
    accepted_base: type[Scorer] | type[PromptTarget]
    is_list: bool


@dataclass
class _BuildState:
    """Mutable recursion state for nested scorer/target construction."""

    component_count: int = 0
    active_container_ids: set[int] = field(default_factory=set)

    def check_component_limit(self) -> None:
        """Increment the component count and enforce the global cap."""
        self.component_count += 1
        if self.component_count > _MAX_COMPONENT_COUNT:
            raise ValueError(f"Nested scorer configuration exceeds the maximum of {_MAX_COMPONENT_COUNT} components.")

    def enter_container(self, value: Any) -> None:
        """Track one structured input container and reject cycles."""
        if not isinstance(value, (Mapping, list, tuple, set)):
            return
        container_id = id(value)
        if container_id in self.active_container_ids:
            raise ValueError("Nested scorer configuration contains a cycle.")
        self.active_container_ids.add(container_id)

    def exit_container(self, value: Any) -> None:
        """Remove one structured input container from the active traversal set."""
        if not isinstance(value, (Mapping, list, tuple, set)):
            return
        self.active_container_ids.discard(id(value))


class ScorerConfigurationManager:
    """Build scorer catalog metadata and scorer instances from safe API payloads."""

    def __init__(
        self,
        *,
        scorer_registry: ScorerRegistry | None = None,
        target_registry: TargetRegistry | None = None,
    ) -> None:
        """Initialize the manager with the scorer and target registries."""
        self._scorer_registry = scorer_registry or ScorerRegistry.get_registry_singleton()
        self._target_registry = target_registry or TargetRegistry.get_registry_singleton()
        self._type_hints_cache: dict[type, dict[str, Any]] = {}

    def project_catalog_parameter(
        self,
        *,
        parameter: Parameter,
        owner_cls: type | None = None,
    ) -> ScorerParameter:
        """
        Project one registry parameter into a backend scorer-parameter contract.

        Returns:
            ScorerParameter: The API-safe scorer parameter metadata for the catalog.
        """
        resolved = self._resolved_parameter(parameter=parameter, owner_cls=owner_cls)
        annotation = resolved.param_type
        base = ScorerParameter(
            name=resolved.name,
            description=resolved.description,
            default=self._sanitize_default(name=resolved.name, value=resolved.default),
            param_type=resolved.param_type,
            accepts_text=self._accepts_text_input(annotation),
        )

        component_info = self._component_parameter_info(parameter=resolved)
        if component_info is not None:
            accepted_types = self._compatible_component_types(component_info=component_info)
            choices = self._compatible_instance_names(component_info=component_info)
            choice_type = self._choice_param_type(choices=choices, is_list=component_info.is_list)
            return base.model_copy(
                update={
                    "param_type": choice_type,
                    "reference_kind": component_info.kind,
                    "accepts_inline": True,
                    "accepted_types": accepted_types,
                }
            )

        callable_choices = self._callable_choice_names(owner_cls=owner_cls, parameter_name=resolved.name)
        if callable_choices:
            callable_default = self._callable_default_name(
                owner_cls=owner_cls,
                parameter_name=resolved.name,
                value=resolved.default,
            )
            return base.model_copy(
                update={
                    "default": resolved.default if resolved.required else callable_default,
                    "param_type": self._choice_param_type(choices=callable_choices, is_list=False),
                    "accepts_text": False,
                    "presets": [ParameterPreset(name=name, value=name) for name in callable_choices],
                }
            )

        enum_annotation = self._enum_string_union(annotation)
        if enum_annotation is not None:
            return base.model_copy(update={"param_type": enum_annotation})

        if self._is_text_matching_annotation(annotation):
            return base.model_copy(
                update={
                    "input_kind": "json",
                    "json_schema": _TEXT_MATCHING_ADAPTER.json_schema(),
                    "supports_yaml": True,
                    "accepts_text": False,
                    "presets": self._text_matching_presets(),
                }
            )

        if self._is_response_handler_annotation(annotation):
            return base.model_copy(
                update={
                    "input_kind": "json",
                    "json_schema": _JsonResponseHandlerInput.model_json_schema(),
                    "supports_yaml": True,
                    "accepts_text": False,
                    "presets": self._response_handler_presets(owner_cls=owner_cls),
                }
            )

        if self._is_validator_annotation(annotation):
            return base.model_copy(
                update={
                    "input_kind": "json",
                    "json_schema": _ScorerPromptValidatorInput.model_json_schema(),
                    "supports_yaml": True,
                    "accepts_text": False,
                }
            )

        if self._is_seed_prompt_annotation(annotation):
            return base.model_copy(
                update={
                    "input_kind": "multiline",
                    "json_schema": _SeedPromptInput.model_json_schema(),
                    "supports_yaml": True,
                    "accepts_text": True,
                }
            )

        if self._is_json_schema_parameter(parameter=resolved):
            return base.model_copy(
                update={
                    "input_kind": "json",
                    "json_schema": copy.deepcopy(_JSON_SCHEMA_PARAMETER_SCHEMA),
                    "supports_yaml": True,
                    "accepts_text": False,
                    "presets": self._json_schema_presets(),
                }
            )

        if self._is_generic_json_mapping_annotation(annotation):
            return base.model_copy(
                update={
                    "input_kind": "json",
                    "json_schema": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "supports_yaml": True,
                    "accepts_text": False,
                }
            )

        model_type = self._base_model_annotation(annotation)
        if model_type is not None:
            return base.model_copy(
                update={
                    "input_kind": "json",
                    "json_schema": model_type.model_json_schema(),
                    "supports_yaml": True,
                    "accepts_text": False,
                }
            )

        if self._is_string_mapping_annotation(annotation):
            return base.model_copy(
                update={
                    "input_kind": "json",
                    "json_schema": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                    "supports_yaml": True,
                    "accepts_text": False,
                }
            )

        if self._is_string_set_annotation(annotation):
            return base.model_copy(
                update={
                    "input_kind": "json",
                    "json_schema": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                    "supports_yaml": True,
                    "accepts_text": False,
                }
            )

        sequence_type = self._scalar_sequence_param_type(annotation)
        if sequence_type is not None:
            return base.model_copy(
                update={
                    "param_type": sequence_type,
                    "accepts_text": self._accepts_sequence_text(annotation),
                }
            )

        if self._multiline_text_parameter(parameter=resolved):
            return base.model_copy(update={"input_kind": "multiline"})

        string_fallback = self._plain_string_union(annotation)
        if string_fallback:
            return base.model_copy(update={"param_type": str, "accepts_text": True})

        if resolved.is_string_coercible:
            return base

        return base.model_copy(
            update={
                "input_kind": "unsupported",
                "default": resolved.default if resolved.required else None,
                "accepts_text": False,
            }
        )

    def project_catalog_parameters(
        self,
        *,
        parameters: Sequence[Parameter],
        owner_cls: type | None = None,
    ) -> list[ScorerParameter]:
        """
        Project a full constructor-parameter list for one scorer class.

        Returns:
            list[ScorerParameter]: The API-safe scorer parameter metadata for the class.
        """
        return [self.project_catalog_parameter(parameter=parameter, owner_cls=owner_cls) for parameter in parameters]

    def build_scorer(self, *, request: CreateScorerRequest) -> Scorer:
        """
        Build one scorer instance from a validated API request without registering it.

        Returns:
            Scorer: The constructed scorer instance.
        """
        spec = _InlineComponentSpec(type=request.type, params=copy.deepcopy(request.params))
        state = _BuildState()
        built = self._build_component_from_spec(
            kind="scorer",
            spec=spec,
            state=state,
            depth=1,
        )
        if not isinstance(built, Scorer):
            raise TypeError("Expected a scorer instance.")
        return built

    def build_scorer_instance(self, *, request: CreateScorerRequest, scorer_id: str) -> tuple[Scorer, ScorerInstance]:
        """
        Build a scorer and its response-safe instance metadata without registering it.

        Returns:
            tuple[Scorer, ScorerInstance]: The constructed scorer and the validated response payload.
        """
        scorer = self.build_scorer(request=request)
        return scorer, self._build_scorer_instance(scorer_id=scorer_id, scorer=scorer)

    def _build_component_from_spec(
        self,
        *,
        kind: Literal["scorer", "target"],
        spec: _InlineComponentSpec,
        state: _BuildState,
        depth: int,
    ) -> Scorer | PromptTarget:
        if depth > _MAX_COMPONENT_DEPTH:
            raise ValueError(f"Nested scorer configuration exceeds the maximum depth of {_MAX_COMPONENT_DEPTH}.")

        registry = self._registry_for_kind(kind)
        state.check_component_limit()
        if spec.type not in registry:
            available = registry.get_class_names()
            raise ValueError(f"{kind.capitalize()} type '{spec.type}' not found. Available types: {available}")

        cls = registry.get_class(spec.type)
        metadata = registry.get_registered_class_metadata(spec.type)
        if metadata is None:
            raise ValueError(f"{kind.capitalize()} type '{spec.type}' is unavailable.")

        parameters_by_name = {parameter.name: parameter for parameter in metadata.parameters}
        unknown = sorted(set(spec.params) - set(parameters_by_name))
        if unknown:
            raise ValueError(
                f"Unknown parameter(s) {unknown} for '{cls.__name__}'. Valid parameters: {sorted(parameters_by_name)}"
            )

        resolved_args: dict[str, Any] = {}
        for name, value in spec.params.items():
            resolved_args[name] = self._resolve_parameter_value(
                owner_cls=cls,
                parameter=parameters_by_name[name],
                value=value,
                state=state,
                depth=depth,
            )

        return registry.create_instance(spec.type, **resolved_args)

    def _resolve_parameter_value(
        self,
        *,
        owner_cls: type,
        parameter: Parameter,
        value: Any,
        state: _BuildState,
        depth: int,
    ) -> Any:
        self._validate_structure(value=value, state=state, depth=0)
        registry_parameter = parameter
        parameter = self._resolved_parameter(parameter=parameter, owner_cls=owner_cls)
        annotation = parameter.param_type

        if value is None:
            if parameter.required:
                raise ValueError(f"Parameter '{parameter.name}' of '{owner_cls.__name__}' is required.")
            if self._annotation_allows_none(annotation):
                return None
            raise ValueError(f"Parameter '{parameter.name}' of '{owner_cls.__name__}' does not accept null.")

        component_info = self._component_parameter_info(parameter=parameter)
        if component_info is not None:
            return self._resolve_component_parameter_value(
                parameter=parameter,
                component_info=component_info,
                value=value,
                state=state,
                depth=depth,
                owner_name=owner_cls.__name__,
            )

        callable_options = self._callable_options(owner_cls=owner_cls, parameter_name=parameter.name)
        if callable_options:
            if callable(value):
                return value
            if not isinstance(value, str):
                raise ValueError(
                    f"Parameter '{parameter.name}' of '{owner_cls.__name__}' must be one of "
                    f"{sorted(self._callable_choice_names(owner_cls=owner_cls, parameter_name=parameter.name))}."
                )
            resolved_callable = callable_options.get(value)
            if resolved_callable is None:
                raise ValueError(
                    f"Parameter '{parameter.name}' of '{owner_cls.__name__}' expected one of "
                    f"{sorted(self._callable_choice_names(owner_cls=owner_cls, parameter_name=parameter.name))}, "
                    f"got {value!r}."
                )
            return resolved_callable

        enum_annotation = self._enum_string_union(annotation)
        if enum_annotation is not None:
            return Parameter(
                name=parameter.name,
                description=parameter.description,
                default=parameter.default,
                param_type=enum_annotation,
            ).coerce_value(value)

        if self._is_text_matching_annotation(annotation):
            return self._build_text_matching(value=value, state=state)

        if self._is_response_handler_annotation(annotation):
            return self._build_response_handler(value=value, state=state)

        if self._is_validator_annotation(annotation):
            return self._build_validator(value=value, state=state)

        if self._is_seed_prompt_annotation(annotation):
            return self._build_seed_prompt(value=value, state=state, parameter_name=parameter.name)

        if self._is_json_schema_parameter(parameter=parameter):
            return self._coerce_json_schema(value=value, state=state)

        if self._is_generic_json_mapping_annotation(annotation):
            return self._coerce_generic_json_mapping(value=value, state=state, parameter_name=parameter.name)

        model_type = self._base_model_annotation(annotation)
        if model_type is not None:
            loaded = self._load_yaml_wrapper(value=value, state=state, param_name=parameter.name)
            if isinstance(loaded, model_type):
                return loaded
            if not isinstance(loaded, Mapping):
                raise ValueError(
                    f"Parameter '{parameter.name}' of '{owner_cls.__name__}' must be an object compatible with "
                    f"{model_type.__name__}."
                )
            try:
                return model_type.model_validate(loaded)
            except ValidationError as exc:
                raise ValueError(f"Invalid scorer parameter '{parameter.name}': {exc}") from None

        if self._is_string_mapping_annotation(annotation):
            loaded = self._load_yaml_wrapper(value=value, state=state, param_name=parameter.name)
            try:
                return _STRING_MAPPING_ADAPTER.validate_python(loaded)
            except ValidationError as exc:
                raise ValueError(f"Invalid scorer parameter '{parameter.name}': {exc}") from None

        if self._is_string_set_annotation(annotation):
            loaded = self._load_yaml_wrapper(value=value, state=state, param_name=parameter.name)
            try:
                string_list = _STRING_LIST_ADAPTER.validate_python(loaded)
            except ValidationError as exc:
                raise ValueError(f"Invalid scorer parameter '{parameter.name}': {exc}") from None
            if len(set(string_list)) != len(string_list):
                raise ValueError(f"Parameter '{parameter.name}' must contain unique strings.")
            return _STRING_SET_ADAPTER.validate_python(string_list)

        if self._is_scalar_sequence_union(annotation):
            return self._coerce_scalar_sequence_union(parameter=parameter, value=value, owner_name=owner_cls.__name__)

        if parameter.is_string_coercible or self._is_simple_list_annotation(annotation):
            if isinstance(value, str) and registry_parameter.is_string_coercible:
                return value
            try:
                return parameter.coerce_value(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid scorer parameter '{parameter.name}': {exc}") from None

        if self._plain_string_union(annotation):
            if isinstance(value, str):
                return value
            raise ValueError(f"Parameter '{parameter.name}' of '{owner_cls.__name__}' must be provided as a string.")

        if self._is_path_annotation(annotation):
            raise ValueError(
                f"Parameter '{parameter.name}' of '{owner_cls.__name__}' does not accept server file paths."
            )

        return value

    def _resolve_component_parameter_value(
        self,
        *,
        parameter: Parameter,
        component_info: _ComponentParameterInfo,
        value: Any,
        state: _BuildState,
        depth: int,
        owner_name: str,
    ) -> Any:
        if component_info.is_list:
            if not isinstance(value, list):
                raise ValueError(
                    f"{owner_name}.{parameter.name}: expected a list of component aliases or inline specs."
                )
            return [
                self._resolve_component_item(
                    parameter=parameter,
                    component_info=component_info,
                    value=item,
                    state=state,
                    depth=depth + 1,
                    owner_name=owner_name,
                )
                for item in value
            ]

        if isinstance(value, list):
            raise ValueError(f"{owner_name}.{parameter.name}: expected a single component alias or inline spec.")
        return self._resolve_component_item(
            parameter=parameter,
            component_info=component_info,
            value=value,
            state=state,
            depth=depth + 1,
            owner_name=owner_name,
        )

    def _resolve_component_item(
        self,
        *,
        parameter: Parameter,
        component_info: _ComponentParameterInfo,
        value: Any,
        state: _BuildState,
        depth: int,
        owner_name: str,
    ) -> Scorer | PromptTarget:
        expected_type = component_info.accepted_base
        registry = self._registry_for_kind(component_info.kind)
        if isinstance(value, expected_type):
            state.check_component_limit()
            return value
        if isinstance(value, str):
            state.check_component_limit()
            instance = registry.instances.get(value)
            if instance is None:
                available = self._compatible_instance_names(component_info=component_info)
                if not available:
                    raise ValueError(
                        f"{owner_name}.{parameter.name}: '{value}' not found. No compatible registered "
                        f"{component_info.kind} instances are available."
                    )
                raise ValueError(
                    f"{owner_name}.{parameter.name}: '{value}' not found. Compatible registered "
                    f"{component_info.kind} instances: {available}"
                )
            if not isinstance(instance, expected_type):
                raise ValueError(
                    f"{owner_name}.{parameter.name}: '{value}' is a {type(instance).__name__}, expected "
                    f"{expected_type.__name__}."
                )
            return instance
        if self._looks_like_inline_spec(value):
            spec = _InlineComponentSpec.model_validate(value)
            built = self._build_component_from_spec(
                kind=component_info.kind,
                spec=spec,
                state=state,
                depth=depth,
            )
            if not isinstance(built, expected_type):
                raise ValueError(
                    f"{owner_name}.{parameter.name}: inline {component_info.kind} '{spec.type}' is not compatible "
                    f"with expected type {expected_type.__name__}."
                )
            return built

        raise ValueError(
            f"{owner_name}.{parameter.name}: expected a registered {component_info.kind} alias or "
            "an inline {'type': ..., 'params': ...} spec."
        )

    def _build_text_matching(self, *, value: Any, state: _BuildState) -> TextMatching:
        if isinstance(value, (ExactTextMatching, ApproximateTextMatching)):
            return value
        loaded = self._load_yaml_wrapper(value=value, state=state, param_name="text_matcher")
        if isinstance(loaded, str):
            loaded = {"type": loaded}
        try:
            config = _TEXT_MATCHING_ADAPTER.validate_python(loaded)
        except ValidationError as exc:
            raise ValueError(f"Invalid scorer parameter 'text_matcher': {exc}") from None
        if isinstance(config, _ApproximateTextMatchingInput):
            return ApproximateTextMatching(
                threshold=config.threshold,
                n=config.n,
                case_sensitive=config.case_sensitive,
            )
        return ExactTextMatching(
            case_sensitive=config.case_sensitive,
            ignore_whitespace=config.ignore_whitespace,
        )

    def _build_response_handler(self, *, value: Any, state: _BuildState) -> ResponseHandler:
        if isinstance(value, ResponseHandler):
            return value
        loaded = self._load_yaml_wrapper(value=value, state=state, param_name="response_handler")
        if isinstance(loaded, str):
            loaded = {"type": loaded}
        try:
            config = _JsonResponseHandlerInput.model_validate(loaded)
        except ValidationError as exc:
            raise ValueError(f"Invalid scorer parameter 'response_handler': {exc}") from None
        response_schema = (
            self._coerce_json_schema(value=config.response_schema, state=state) if config.response_schema else None
        )
        return JsonSchemaResponseHandler(
            score_value_output_key=config.score_value_output_key,
            rationale_output_key=config.rationale_output_key,
            description_output_key=config.description_output_key,
            metadata_output_key=config.metadata_output_key,
            category_output_key=config.category_output_key,
            response_schema=response_schema,
            numeric_value=config.numeric_value,
        )

    def _build_validator(self, *, value: Any, state: _BuildState) -> ScorerPromptValidator:
        if isinstance(value, ScorerPromptValidator):
            return value
        loaded = self._load_yaml_wrapper(value=value, state=state, param_name="validator")
        try:
            config = _ScorerPromptValidatorInput.model_validate(loaded)
        except ValidationError as exc:
            raise ValueError(f"Invalid scorer parameter 'validator': {exc}") from None
        return ScorerPromptValidator(**config.model_dump(exclude_none=True))

    def _build_seed_prompt(self, *, value: Any, state: _BuildState, parameter_name: str) -> SeedPrompt | str:
        if isinstance(value, SeedPrompt):
            return value
        if isinstance(value, str):
            return value
        loaded = self._load_yaml_wrapper(value=value, state=state, param_name=parameter_name)
        try:
            prompt_input = _SeedPromptInput.model_validate(loaded)
        except ValidationError as exc:
            raise ValueError(f"Invalid scorer parameter '{parameter_name}': {exc}") from None
        response_schema: dict[str, Any] | None = None
        if prompt_input.response_json_schema_name is not None:
            response_schema = get_common_json_schema(prompt_input.response_json_schema_name)
        elif prompt_input.response_json_schema is not None:
            response_schema = self._coerce_json_schema(value=prompt_input.response_json_schema, state=state)
        return SeedPrompt(
            value=prompt_input.value,
            data_type=prompt_input.data_type or "text",
            is_jinja_template=False,
            name=prompt_input.name,
            metadata=copy.deepcopy(prompt_input.metadata),
            parameters=list(prompt_input.parameters),
            response_json_schema=response_schema,
        )

    def _coerce_json_schema(self, *, value: Any, state: _BuildState) -> dict[str, Any]:
        loaded = self._load_yaml_wrapper(value=value, state=state, param_name="response_json_schema")
        try:
            schema = _JSON_OBJECT_ADAPTER.validate_python(loaded)
        except ValidationError as exc:
            raise ValueError(f"Invalid JSON schema definition: {exc}") from None
        self._validate_structure(value=schema, state=state, depth=0)
        if not any(keyword in schema for keyword in _JSON_SCHEMA_KEYWORDS):
            raise ValueError("JSON schema definitions must declare at least one standard schema keyword.")
        schema_type = schema.get("type")
        if isinstance(schema_type, list) and not all(isinstance(item, str) for item in schema_type):
            raise ValueError("JSON schema 'type' lists must contain only strings.")
        if schema_type is not None and not isinstance(schema_type, (str, list)):
            raise ValueError("JSON schema 'type' must be a string or list of strings.")
        properties = schema.get("properties")
        if properties is not None and not isinstance(properties, Mapping):
            raise ValueError("JSON schema 'properties' must be an object.")
        required = schema.get("required")
        if required is not None and (
            not isinstance(required, list) or not all(isinstance(item, str) for item in required)
        ):
            raise ValueError("JSON schema 'required' must be a list of strings.")
        additional_properties = schema.get("additionalProperties")
        if additional_properties is not None and not isinstance(additional_properties, (bool, Mapping)):
            raise ValueError("JSON schema 'additionalProperties' must be a boolean or object.")
        return copy.deepcopy(schema)

    def _coerce_generic_json_mapping(
        self,
        *,
        value: Any,
        state: _BuildState,
        parameter_name: str,
    ) -> dict[str, Any]:
        """
        Validate a generic JSON object mapping without applying schema-specific rules.

        Returns:
            dict[str, Any]: The validated JSON object mapping.
        """
        loaded = self._load_yaml_wrapper(value=value, state=state, param_name=parameter_name)
        try:
            mapping = _JSON_OBJECT_ADAPTER.validate_python(loaded)
        except ValidationError as exc:
            raise ValueError(f"Invalid scorer parameter '{parameter_name}': {exc}") from None
        self._validate_structure(value=mapping, state=state, depth=0)
        return copy.deepcopy(mapping)

    def _coerce_scalar_sequence_union(self, *, parameter: Parameter, value: Any, owner_name: str) -> Any:
        annotation = parameter.param_type
        scalar_member = next(
            (member for member in self._non_none_union_members(annotation) if self._is_scalar_like_annotation(member)),
            None,
        )
        sequence_member = next(
            (
                member
                for member in self._non_none_union_members(annotation)
                if self._sequence_element_type(member) is not None
            ),
            None,
        )
        if scalar_member is None or sequence_member is None:
            raise ValueError(f"{owner_name}.{parameter.name}: unsupported scalar/sequence union.")

        if isinstance(value, list):
            element_type = self._sequence_element_type(sequence_member)
            assert element_type is not None
            try:
                return [
                    Parameter(
                        name=parameter.name,
                        description=parameter.description,
                        default=parameter.default,
                        param_type=element_type,
                    ).coerce_value(item)
                    for item in value
                ]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid scorer parameter '{parameter.name}': {exc}") from None

        try:
            return Parameter(
                name=parameter.name,
                description=parameter.description,
                default=parameter.default,
                param_type=scalar_member,
            ).coerce_value(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid scorer parameter '{parameter.name}': {exc}") from None

    def _resolved_parameter(self, *, parameter: Parameter, owner_cls: type | None) -> Parameter:
        """Return the parameter with its trusted live annotation substituted when available."""
        if owner_cls is None:
            return parameter
        annotation = self._trusted_type_hints(owner_cls).get(parameter.name)
        if annotation is None:
            return parameter
        return parameter.model_copy(update={"param_type": annotation})

    def _trusted_type_hints(self, owner_cls: type) -> dict[str, Any]:
        cached = self._type_hints_cache.get(owner_cls)
        if cached is not None:
            return cached
        module = inspect.getmodule(owner_cls)
        if module is None:
            self._type_hints_cache[owner_cls] = {}
            return {}
        try:
            type_hints = get_type_hints(
                owner_cls.__init__,
                globalns={**module.__dict__, **self._trusted_annotation_namespace()},
                localns=self._trusted_annotation_namespace(),
                include_extras=True,
            )
        except (NameError, TypeError, AttributeError):
            type_hints = {}
        self._type_hints_cache[owner_cls] = type_hints
        return type_hints

    def _trusted_annotation_namespace(self) -> dict[str, Any]:
        """Return the explicit allowlisted annotation namespace used for trusted type resolution."""
        namespace = self._base_trusted_annotation_namespace()
        for name in self._scorer_registry.get_class_names():
            namespace[name] = self._scorer_registry.get_class(name)
        for name in self._target_registry.get_class_names():
            namespace[name] = self._target_registry.get_class(name)
        return namespace

    @staticmethod
    @lru_cache(maxsize=1)
    def _base_trusted_annotation_namespace() -> dict[str, Any]:
        """Return the non-registry portion of the trusted annotation namespace."""
        return {
            "Any": Any,
            "Awaitable": Awaitable,
            "Callable": Callable,
            "Mapping": Mapping,
            "Sequence": Sequence,
            "pathlib": pathlib,
            "PromptTarget": PromptTarget,
            "PromptShieldTarget": PromptShieldTarget,
            "Scorer": Scorer,
            "TrueFalseScorer": TrueFalseScorer,
            "MessageTrueFalseScorer": MessageTrueFalseScorer,
            "FloatScaleScorer": FloatScaleScorer,
            "MessageFloatScaleScorer": MessageFloatScaleScorer,
            "ResponseHandler": ResponseHandler,
            "JsonSchemaResponseHandler": JsonSchemaResponseHandler,
            "ScorerPromptValidator": ScorerPromptValidator,
            "SeedPrompt": SeedPrompt,
            "JsonSchemaDefinition": JsonSchemaDefinition,
            "NumericRange": NumericRange,
            "NumericRubric": NumericRubric,
            "LikertScale": LikertScale,
            "TrueFalseQuestion": TrueFalseQuestion,
            "ContentClassifier": ContentClassifier,
            "LlamaGuardPolicy": LlamaGuardPolicy,
            "LlamaGuardMessageRole": LlamaGuardMessageRole,
            "ShieldGemmaGuideline": ShieldGemmaGuideline,
            "ShieldGemmaMessageRole": ShieldGemmaMessageRole,
            "TextMatching": TextMatching,
            "TrueFalseAggregatorFunc": TrueFalseAggregatorFunc,
            "FloatScaleAggregatorFunc": FloatScaleAggregatorFunc,
        }

    def _compatible_component_types(self, *, component_info: _ComponentParameterInfo) -> list[str]:
        """Return compatible scorer/target class names for a reference-like parameter."""
        registry = self._registry_for_kind(component_info.kind)
        compatible: list[str] = []
        for name in registry.get_class_names():
            candidate = registry.get_class(name)
            if inspect.isabstract(candidate):
                continue
            if issubclass(candidate, component_info.accepted_base):
                compatible.append(name)
        return compatible

    def _compatible_instance_names(self, *, component_info: _ComponentParameterInfo) -> list[str]:
        """Return registered scorer/target instance names compatible with a parameter."""
        registry = self._registry_for_kind(component_info.kind)
        return [
            entry.name
            for entry in registry.instances.get_all_instances()
            if isinstance(entry.instance, component_info.accepted_base)
        ]

    def _registry_for_kind(
        self,
        kind: Literal["scorer", "target"],
    ) -> ScorerRegistry | TargetRegistry:
        """Return the scorer or target registry for the requested component kind."""
        if kind == "target":
            return self._target_registry
        return self._scorer_registry

    @staticmethod
    def _component_parameter_info(parameter: Parameter) -> _ComponentParameterInfo | None:
        """Return the resolved component compatibility contract for a parameter, if any."""
        annotation = parameter.param_type
        members = ScorerConfigurationManager._non_none_union_members(annotation)
        if len(members) == 1:
            member = members[0]
            component_element_type = ScorerConfigurationManager._component_sequence_element_type(member)
            if component_element_type is not None:
                component_kind = ScorerConfigurationManager._component_kind_for_type(component_element_type)
                if component_kind is not None:
                    kind, base_type = component_kind
                    return _ComponentParameterInfo(kind=kind, accepted_base=base_type, is_list=True)
            element_type = ScorerConfigurationManager._sequence_element_type(member)
            if element_type is not None:
                component_kind = ScorerConfigurationManager._component_kind_for_type(element_type)
                if component_kind is not None:
                    kind, base_type = component_kind
                    return _ComponentParameterInfo(kind=kind, accepted_base=base_type, is_list=True)
            component_kind = ScorerConfigurationManager._component_kind_for_type(member)
            if component_kind is not None:
                kind, base_type = component_kind
                return _ComponentParameterInfo(kind=kind, accepted_base=base_type, is_list=False)

        if parameter.reference is not None:
            if parameter.reference.component_type.value == "target":
                return _ComponentParameterInfo(kind="target", accepted_base=PromptTarget, is_list=parameter.is_list)
            if parameter.reference.component_type.value == "scorer":
                return _ComponentParameterInfo(kind="scorer", accepted_base=Scorer, is_list=parameter.is_list)
        return None

    @staticmethod
    def _component_kind_for_type(
        annotation: Any,
    ) -> tuple[Literal["scorer", "target"], type[Scorer] | type[PromptTarget]] | None:
        """Return the component family and accepted base type for one annotation member."""
        if isinstance(annotation, type) and issubclass(annotation, PromptTarget):
            return "target", annotation
        if isinstance(annotation, type) and issubclass(annotation, Scorer):
            return "scorer", annotation
        return None

    @staticmethod
    def _is_text_matching_annotation(annotation: Any) -> bool:
        """Return True when the annotation accepts a text-matching strategy object."""
        return TextMatching in ScorerConfigurationManager._union_members(annotation)

    @staticmethod
    def _is_response_handler_annotation(annotation: Any) -> bool:
        """Return True when the annotation accepts a response-handler object."""
        return ResponseHandler in ScorerConfigurationManager._union_members(annotation)

    @staticmethod
    def _is_validator_annotation(annotation: Any) -> bool:
        """Return True when the annotation accepts a scorer prompt validator."""
        return ScorerPromptValidator in ScorerConfigurationManager._union_members(annotation)

    @staticmethod
    def _is_seed_prompt_annotation(annotation: Any) -> bool:
        """Return True when the annotation accepts a ``SeedPrompt`` and/or plain string."""
        members = ScorerConfigurationManager._union_members(annotation)
        return SeedPrompt in members and str in members

    @staticmethod
    def _is_json_schema_parameter(*, parameter: Parameter) -> bool:
        """Return True when a parameter is a JSON schema rather than a generic JSON mapping."""
        if not ScorerConfigurationManager._is_generic_json_mapping_annotation(parameter.param_type):
            return False
        lower_name = parameter.name.lower()
        return any(keyword in lower_name for keyword in _JSON_SCHEMA_PARAMETER_KEYWORDS)

    @staticmethod
    def _is_generic_json_mapping_annotation(annotation: Any) -> bool:
        """Return True when the annotation accepts an arbitrary ``dict[str, Any]``-style object."""
        for member in ScorerConfigurationManager._union_members(annotation):
            origin = get_origin(member)
            if origin not in {dict, Mapping}:
                continue
            key_type, value_type = get_args(member) or (None, None)
            if key_type is str and value_type is Any:
                return True
        return False

    @staticmethod
    def _base_model_annotation(annotation: Any) -> type[BaseModel] | None:
        """Return the concrete BaseModel subclass accepted by an annotation, if any."""
        members = ScorerConfigurationManager._non_none_union_members(annotation)
        if len(members) != 1:
            return None
        candidate = members[0]
        if candidate is SeedPrompt:
            return None
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
        return None

    @staticmethod
    def _is_string_mapping_annotation(annotation: Any) -> bool:
        """Return True when the annotation is a ``dict[str, str]`` mapping."""
        members = ScorerConfigurationManager._non_none_union_members(annotation)
        if len(members) != 1:
            return False
        member = members[0]
        if get_origin(member) is not dict:
            return False
        key_type, value_type = get_args(member) or (None, None)
        return key_type is str and value_type is str

    @staticmethod
    def _is_string_set_annotation(annotation: Any) -> bool:
        """Return True when the annotation is a ``set[str]``."""
        members = ScorerConfigurationManager._non_none_union_members(annotation)
        if len(members) != 1:
            return False
        member = members[0]
        if get_origin(member) is not set:
            return False
        args = get_args(member)
        return bool(args) and args[0] is str

    @staticmethod
    def _is_path_annotation(annotation: Any) -> bool:
        """Return True when the annotation accepts a filesystem path object."""
        return any(member is pathlib.Path for member in ScorerConfigurationManager._union_members(annotation))

    @staticmethod
    def _annotation_allows_none(annotation: Any) -> bool:
        """Return True when the annotation explicitly accepts ``None`` or is unconstrained."""
        return annotation in {Any, None} or type(None) in ScorerConfigurationManager._union_members(annotation)

    @staticmethod
    def _is_scalar_sequence_union(annotation: Any) -> bool:
        """Return True when the annotation accepts either one scalar or a scalar sequence."""
        members = ScorerConfigurationManager._non_none_union_members(annotation)
        if len(members) < 2:
            return False
        has_scalar = any(ScorerConfigurationManager._is_scalar_like_annotation(member) for member in members)
        has_sequence = any(ScorerConfigurationManager._sequence_element_type(member) is not None for member in members)
        return has_scalar and has_sequence

    @staticmethod
    def _is_simple_list_annotation(annotation: Any) -> bool:
        """Return True when the annotation is a ``list`` of scalar-like values."""
        members = ScorerConfigurationManager._non_none_union_members(annotation)
        if len(members) != 1:
            return False
        return ScorerConfigurationManager._sequence_element_type(members[0]) is not None

    @staticmethod
    def _plain_string_union(annotation: Any) -> bool:
        """Return True when the annotation safely accepts a plain string input."""
        members = ScorerConfigurationManager._non_none_union_members(annotation)
        return str in members and not ScorerConfigurationManager._is_seed_prompt_annotation(annotation)

    @staticmethod
    def _enum_string_union(annotation: Any) -> type[Enum] | None:
        """Return an enum type when a union accepts the enum and its serialized string value."""
        members = ScorerConfigurationManager._non_none_union_members(annotation)
        enum_members = [member for member in members if isinstance(member, type) and issubclass(member, Enum)]
        if len(enum_members) != 1 or str not in members:
            return None
        if len(members) != 2:
            return None
        return enum_members[0]

    @staticmethod
    def _scalar_sequence_param_type(annotation: Any) -> Any | None:
        """Return a list display type for scalar/sequence unions shown in the catalog."""
        members = ScorerConfigurationManager._non_none_union_members(annotation)
        for member in members:
            element_type = ScorerConfigurationManager._sequence_element_type(member)
            if element_type is not None:
                return types.GenericAlias(list, element_type)
        return None

    @staticmethod
    def _sequence_element_type(annotation: Any) -> Any | None:
        """Return the scalar element type for list/sequence annotations, or None."""
        origin = get_origin(annotation)
        if origin not in {list, Sequence, tuple}:
            return None
        args = get_args(annotation)
        element_type = args[0] if args else str
        if ScorerConfigurationManager._is_scalar_like_annotation(element_type):
            return element_type
        return None

    @staticmethod
    def _component_sequence_element_type(annotation: Any) -> type[Scorer] | type[PromptTarget] | None:
        """Return the component element type for list/sequence annotations, or None."""
        origin = get_origin(annotation)
        if origin not in {list, Sequence, tuple}:
            return None
        args = get_args(annotation)
        element_type = args[0] if args else None
        if isinstance(element_type, type) and issubclass(element_type, (Scorer, PromptTarget)):
            return element_type
        return None

    @staticmethod
    def _is_scalar_like_annotation(annotation: Any) -> bool:
        """Return True when the annotation is one scalar value or one constrained scalar."""
        if annotation in {str, int, float, bool}:
            return True
        if get_origin(annotation) is Literal:
            return True
        return isinstance(annotation, type) and issubclass(annotation, Enum)

    @staticmethod
    def _union_members(annotation: Any) -> tuple[Any, ...]:
        """Return the members of a union annotation, or a 1-tuple containing the annotation."""
        origin = get_origin(annotation)
        if origin in (types.UnionType, getattr(types, "UnionType", types.UnionType)) or str(origin) == "typing.Union":
            return get_args(annotation)
        return (annotation,)

    @staticmethod
    def _non_none_union_members(annotation: Any) -> list[Any]:
        """Return the non-``None`` members of a union annotation."""
        return [member for member in ScorerConfigurationManager._union_members(annotation) if member is not type(None)]

    @staticmethod
    def _choice_param_type(*, choices: list[str], is_list: bool) -> Any:
        """Return a wire-safe type whose serialized choices mirror current registry instances."""
        scalar_type = Literal.__getitem__(tuple(choices)) if choices else str
        return types.GenericAlias(list, scalar_type) if is_list else scalar_type

    @staticmethod
    def _sanitize_default(*, name: str, value: Any) -> Any:
        if value is REQUIRED_VALUE:
            return value
        if callable(value):
            return None
        lower_name = name.lower()
        if any(pattern in lower_name for pattern in SENSITIVE_FIELD_PATTERNS):
            return None
        return value

    @staticmethod
    def _accepts_text_input(annotation: Any) -> bool:
        """Return True when a parameter can safely accept a raw plain-text value."""
        if ScorerConfigurationManager._enum_string_union(annotation) is not None:
            return False
        members = ScorerConfigurationManager._non_none_union_members(annotation)
        return str in members or members == [str]

    @staticmethod
    def _accepts_sequence_text(annotation: Any) -> bool:
        """Return True when a scalar/sequence union also accepts one plain string."""
        return str in ScorerConfigurationManager._union_members(annotation)

    @staticmethod
    def _multiline_text_parameter(*, parameter: Parameter) -> bool:
        if str not in ScorerConfigurationManager._union_members(parameter.param_type):
            return False
        lower_name = parameter.name.lower()
        return any(token in lower_name for token in ("prompt", "template", "reference_text"))

    @staticmethod
    def _looks_like_inline_spec(value: Any) -> bool:
        """Return True when a mapping has the trusted inline component spec shape."""
        return isinstance(value, Mapping) and "type" in value and set(value).issubset(_INLINE_SPEC_KEYS)

    @staticmethod
    def _load_yaml_wrapper(*, value: Any, state: _BuildState, param_name: str) -> Any:
        if not (isinstance(value, Mapping) and set(value) == {"yaml"}):
            return value
        try:
            payload = _YamlPayload.model_validate(value)
        except ValidationError as exc:
            raise ValueError(f"Invalid scorer parameter '{param_name}': {exc}") from None
        try:
            loaded = yaml.safe_load(payload.yaml)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid scorer parameter '{param_name}': {exc}") from None
        ScorerConfigurationManager._validate_structure(value=loaded, state=state, depth=0)
        return loaded

    @staticmethod
    def _validate_structure(*, value: Any, state: _BuildState, depth: int) -> None:
        """Enforce depth limits and cycle detection for nested structured values."""
        if depth > _MAX_STRUCTURED_DEPTH:
            raise ValueError(f"Nested scorer configuration exceeds the maximum data depth of {_MAX_STRUCTURED_DEPTH}.")
        if not isinstance(value, (Mapping, list, tuple, set)):
            return
        state.enter_container(value)
        try:
            items = list(value.values()) if isinstance(value, Mapping) else list(value)
            for item in items:
                ScorerConfigurationManager._validate_structure(value=item, state=state, depth=depth + 1)
        finally:
            state.exit_container(value)

    @staticmethod
    def _text_matching_presets() -> list[ParameterPreset]:
        """Return the built-in safe text-matching presets exposed by the catalog."""
        return [
            ParameterPreset(name="exact", value={"type": "exact"}),
            ParameterPreset(name="approximate", value={"type": "approximate"}),
        ]

    @staticmethod
    def _response_handler_presets(*, owner_cls: type | None) -> list[ParameterPreset]:
        """Return safe response-handler presets for the catalog."""
        presets = [ParameterPreset(name="json", value={"type": "json"})]
        if owner_cls is not None and issubclass(owner_cls, FloatScaleScorer):
            presets.append(ParameterPreset(name="numeric-json", value={"type": "json", "numeric_value": True}))
        return presets

    @staticmethod
    def _json_schema_presets() -> list[ParameterPreset] | None:
        """Return the vetted bundled JSON schemas exposed as catalog presets."""
        schema_presets = [
            ParameterPreset(name=name, value=copy.deepcopy(schema))
            for name, schema in sorted(COMMON_JSON_SCHEMAS.items())
        ]
        return schema_presets or None

    def _callable_choice_names(self, *, owner_cls: type | None, parameter_name: str) -> list[str]:
        """Return the stable display names for one supported callable-valued parameter."""
        if owner_cls is None:
            return []
        options = self._callable_options(owner_cls=owner_cls, parameter_name=parameter_name)
        return sorted(name for name in options if "." in name)

    def _callable_default_name(self, *, owner_cls: type | None, parameter_name: str, value: Any) -> str | None:
        """Return the stable preset name for one callable default, or None when not expressible."""
        if owner_cls is None or not callable(value):
            return None
        options = self._callable_options(owner_cls=owner_cls, parameter_name=parameter_name)
        for name in self._callable_choice_names(owner_cls=owner_cls, parameter_name=parameter_name):
            if options.get(name) is value:
                return name
        return None

    def _callable_options(self, *, owner_cls: type, parameter_name: str) -> dict[str, Callable]:
        """Return the allowlisted callable options for one supported scorer parameter."""
        if parameter_name == "float_scale_aggregator":
            return self._namespace_callable_options(
                namespaces=(("FloatScaleScoreAggregator", FloatScaleScoreAggregator),)
            )
        if parameter_name not in {"aggregator", "score_aggregator"}:
            return {}
        if issubclass(owner_cls, TrueFalseScorer):
            return self._namespace_callable_options(
                namespaces=(("TrueFalseScoreAggregator", TrueFalseScoreAggregator),)
            )
        if issubclass(owner_cls, FloatScaleScorer):
            return self._namespace_callable_options(
                namespaces=(
                    ("FloatScaleScoreAggregator", FloatScaleScoreAggregator),
                    ("FloatScaleScorerByCategory", FloatScaleScorerByCategory),
                    ("FloatScaleScorerAllCategories", FloatScaleScorerAllCategories),
                )
            )
        return {}

    @staticmethod
    def _namespace_callable_options(*, namespaces: Sequence[tuple[str, type]]) -> dict[str, Callable]:
        """
        Expose qualified presets only; generated function names are not unique.

        Returns:
            dict[str, Callable]: Qualified names mapped to their allowlisted implementations.
        """
        return {
            f"{namespace_name}.{attr_name}": value
            for namespace_name, namespace in namespaces
            for attr_name, value in vars(namespace).items()
            if not attr_name.startswith("_") and callable(value)
        }

    @staticmethod
    def _build_scorer_instance(*, scorer_id: str, scorer: Scorer) -> ScorerInstance:
        """
        Build response-safe scorer metadata, forcing identifier construction before registration.

        Returns:
            ScorerInstance: The validated scorer-instance response payload.
        """
        identifier = scorer.get_identifier()
        return ScorerInstance(
            scorer_id=scorer_id,
            scorer_type=identifier.class_name or scorer.__class__.__name__,
            identifier_hash=identifier.hash,
            score_type=scorer.scorer_type,
        )
