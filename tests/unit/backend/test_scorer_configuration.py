# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Focused tests for scorer catalog projection and nested scorer construction."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pyrit.backend.models.scorers import CreateScorerRequest
from pyrit.backend.services.scorer_configuration import ScorerConfigurationManager, _BuildState
from pyrit.models import SeedPrompt
from pyrit.prompt_target import PromptShieldTarget
from pyrit.registry import ScorerRegistry, TargetRegistry
from pyrit.score import AudioTrueFalseScorer, PlagiarismScorer, PromptShieldScorer, SelfAskScaleScorer, SubStringScorer
from unit.mocks import MockPromptTarget

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.fixture(autouse=True)
def reset_registries() -> Iterator[None]:
    """Reset scorer and target registries between configuration tests."""
    ScorerRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    yield
    ScorerRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()


def _configuration_manager() -> ScorerConfigurationManager:
    """Create a scorer configuration manager using the current singleton registries."""
    return ScorerConfigurationManager(
        scorer_registry=ScorerRegistry.get_registry_singleton(),
        target_registry=TargetRegistry.get_registry_singleton(),
    )


def _nested_inverter_spec(depth: int) -> dict[str, object]:
    """Create a nested inline scorer spec with a configurable inverter depth."""
    if depth == 0:
        return {"type": "SubStringScorer", "params": {"substring": "WIN"}}
    return {
        "type": "TrueFalseInverterScorer",
        "params": {
            "scorer": _nested_inverter_spec(depth - 1),
        },
    }


def test_catalog_marks_prompt_shield_target_as_inline_target_reference() -> None:
    """PromptShieldScorer should expose a filtered target-reference contract in the catalog."""
    target_registry = TargetRegistry.get_registry_singleton()
    target_registry.instances.register(
        PromptShieldTarget(endpoint="https://example.test", api_key="key"),
        name="prompt-shield",
    )
    target_registry.instances.register(MockPromptTarget(), name="chat-target")

    manager = _configuration_manager()
    metadata = ScorerRegistry.get_registry_singleton().get_registered_class_metadata("PromptShieldScorer")
    assert metadata is not None
    parameter = next(item for item in metadata.parameters if item.name == "prompt_shield_target")

    projected = manager.project_catalog_parameter(
        parameter=parameter,
        owner_cls=ScorerRegistry.get_registry_singleton().get_class("PromptShieldScorer"),
    )

    assert projected.reference_kind == "target"
    assert projected.accepts_inline is True
    assert projected.accepted_types == ["PromptShieldTarget"]
    assert projected.choices == ["prompt-shield"]


def test_catalog_filters_nested_scorer_choices_by_compatible_type() -> None:
    """Nested scorer parameters should expose only compatible registered scorer aliases."""
    scorer_registry = ScorerRegistry.get_registry_singleton()
    scorer_registry.instances.register(SubStringScorer(substring="WIN"), name="substring")
    scorer_registry.instances.register(PlagiarismScorer(reference_text="hello"), name="plagiarism")

    manager = _configuration_manager()
    metadata = scorer_registry.get_registered_class_metadata("AudioTrueFalseScorer")
    assert metadata is not None
    parameter = next(item for item in metadata.parameters if item.name == "text_capable_scorer")

    projected = manager.project_catalog_parameter(
        parameter=parameter,
        owner_cls=scorer_registry.get_class("AudioTrueFalseScorer"),
    )

    assert projected.reference_kind == "scorer"
    assert projected.accepts_inline is True
    assert "SubStringScorer" in projected.accepted_types
    assert "PlagiarismScorer" not in projected.accepted_types
    assert projected.choices == ["substring"]


def test_catalog_callable_defaults_use_stable_named_presets() -> None:
    """Callable defaults should serialize to stable allowlisted preset names, not repr addresses."""
    manager = _configuration_manager()
    scorer_registry = ScorerRegistry.get_registry_singleton()

    regex_metadata = scorer_registry.get_registered_class_metadata("RegexScorer")
    assert regex_metadata is not None
    regex_parameter = next(item for item in regex_metadata.parameters if item.name == "score_aggregator")
    regex_projected = manager.project_catalog_parameter(
        parameter=regex_parameter,
        owner_cls=scorer_registry.get_class("RegexScorer"),
    )

    threshold_metadata = scorer_registry.get_registered_class_metadata("FloatScaleThresholdScorer")
    assert threshold_metadata is not None
    threshold_parameter = next(item for item in threshold_metadata.parameters if item.name == "float_scale_aggregator")
    threshold_projected = manager.project_catalog_parameter(
        parameter=threshold_parameter,
        owner_cls=scorer_registry.get_class("FloatScaleThresholdScorer"),
    )

    assert regex_projected.model_dump(mode="json")["default"] == "TrueFalseScoreAggregator.OR"
    assert threshold_projected.model_dump(mode="json")["default"] == "FloatScaleScoreAggregator.MAX"


def test_build_audio_true_false_scorer_accepts_inline_nested_scorer() -> None:
    """Inline nested scorer specs should be canonicalized before constructing the parent scorer."""
    TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="judge")
    manager = _configuration_manager()

    scorer = manager.build_scorer(
        request=CreateScorerRequest(
            type="AudioTrueFalseScorer",
            params={
                "text_capable_scorer": {
                    "type": "SelfAskTrueFalseScorer",
                    "params": {"chat_target": "judge"},
                }
            },
        )
    )

    assert isinstance(scorer, AudioTrueFalseScorer)


def test_build_prompt_shield_scorer_accepts_inline_target() -> None:
    """Undeclared target-typed parameters should still accept safe inline target specs."""
    manager = _configuration_manager()

    scorer = manager.build_scorer(
        request=CreateScorerRequest(
            type="PromptShieldScorer",
            params={
                "prompt_shield_target": {
                    "type": "PromptShieldTarget",
                    "params": {
                        "endpoint": "https://example.test",
                        "api_key": "key",
                    },
                }
            },
        )
    )

    assert isinstance(scorer, PromptShieldScorer)
    assert isinstance(scorer.get_chat_target(), PromptShieldTarget)


def test_build_scorer_rejects_incompatible_inline_scorer_type() -> None:
    """Inline nested scorers must satisfy the declared scorer subtype contract."""
    manager = _configuration_manager()

    with pytest.raises(ValueError, match="MessageTrueFalseScorer"):
        manager.build_scorer(
            request=CreateScorerRequest(
                type="AudioTrueFalseScorer",
                params={
                    "text_capable_scorer": {
                        "type": "PlagiarismScorer",
                        "params": {"reference_text": "hello"},
                    }
                },
            )
        )


def test_build_scale_scorer_accepts_yaml_wrapped_rubric() -> None:
    """Structured YAML wrappers should validate into canonical rubric models."""
    TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="judge")
    manager = _configuration_manager()

    scorer = manager.build_scorer(
        request=CreateScorerRequest(
            type="SelfAskScaleScorer",
            params={
                "chat_target": "judge",
                "system_prompt": "Return JSON with a score and rationale.",
                "scale": {"yaml": "minimum_value: 0\nmaximum_value: 10\ncategory: math\n"},
            },
        )
    )

    assert isinstance(scorer, SelfAskScaleScorer)
    assert scorer.get_identifier().params["scale"]["maximum_value"] == 10


def test_build_seed_prompt_accepts_text_data_type_and_preserves_metadata() -> None:
    """Seed-prompt YAML adapters should accept explicit text type and keep name/metadata."""
    manager = _configuration_manager()

    prompt = manager._build_seed_prompt(
        value={"yaml": ("value: Judge carefully.\ndata_type: text\nname: local-seed\nmetadata:\n  purpose: test\n")},
        state=_BuildState(),
        parameter_name="system_prompt",
    )

    assert isinstance(prompt, SeedPrompt)
    assert prompt.data_type == "text"
    assert prompt.name == "local-seed"
    assert prompt.metadata == {"purpose": "test"}


def test_build_scale_scorer_accepts_yaml_wrapped_seed_prompt() -> None:
    """SelfAskScaleScorer should preflight when system_prompt YAML includes explicit text data_type."""
    TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="judge")
    manager = _configuration_manager()

    scorer = manager.build_scorer(
        request=CreateScorerRequest(
            type="SelfAskScaleScorer",
            params={
                "chat_target": "judge",
                "system_prompt": {
                    "yaml": ("value: Judge carefully.\ndata_type: text\nname: local-seed\nmetadata:\n  purpose: test\n")
                },
                "scale": {"minimum_value": 0, "maximum_value": 10, "category": "math"},
            },
        )
    )

    assert isinstance(scorer, SelfAskScaleScorer)


def test_build_seed_prompt_rejects_non_text_data_type() -> None:
    """Seed-prompt YAML adapters should continue rejecting non-text data types."""
    manager = _configuration_manager()

    with pytest.raises(ValueError, match="data_type"):
        manager._build_seed_prompt(
            value={"yaml": "value: Judge carefully.\ndata_type: image_path\n"},
            state=_BuildState(),
            parameter_name="system_prompt",
        )


def test_build_scorer_rejects_excessive_nested_depth() -> None:
    """Nested inline scorer specs should stop at the configured recursion depth."""
    manager = _configuration_manager()

    with pytest.raises(ValueError, match="maximum depth"):
        manager.build_scorer(
            request=CreateScorerRequest(
                type="TrueFalseInverterScorer",
                params={"scorer": _nested_inverter_spec(depth=6)},
            )
        )
