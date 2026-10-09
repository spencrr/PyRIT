# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Configuration and durable records for an ACP system under test."""

from datetime import UTC, datetime
from enum import Enum
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field, field_validator, model_validator


class AgentEnvironment(str, Enum):
    """Where the tested harness runs."""

    LOCAL = "local"
    DOCKER = "docker"


class AgentPermissionPolicy(str, Enum):
    """Deterministic permission responses, not a tool enforcement boundary."""

    DENY = "deny"
    ALLOW_ONCE = "allow_once"


class AgentConnectionState(str, Enum):
    """ACP session state, independent of the environment and browser subscription."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    READY = "ready"
    FAILED = "failed"


def _validate_command(value: tuple[str, ...]) -> tuple[str, ...]:
    if not value or any(not part or "\x00" in part for part in value):
        raise ValueError("command must contain a nonempty executable and valid arguments")
    return value


def _validate_credentials(value: tuple[str, ...]) -> tuple[str, ...]:
    if any(not name.isidentifier() for name in value):
        raise ValueError("credential_env must contain environment variable names, not values")
    return value


def _validate_artifacts(value: tuple[str, ...]) -> tuple[str, ...]:
    for path in value:
        if "\\" in path or ":" in path or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise ValueError("artifact_paths must be relative workspace file paths without '..'")
        if not path or path == ".":
            raise ValueError("artifact_paths must name files")
    return value


def _validate_environment(*, environment: AgentEnvironment, image: str | None, acknowledged: bool) -> None:
    if environment == AgentEnvironment.DOCKER and not image:
        raise ValueError("A Docker image is required")
    if environment == AgentEnvironment.LOCAL and not acknowledged:
        raise ValueError("Local execution is NOT sandboxed; set local_execution_acknowledged=True explicitly")


class AgentProfile(BaseModel):
    """Legacy flattened launch recipe, retained for existing execution records and callers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(default="copilot", min_length=1, max_length=100)
    command: tuple[str, ...] = ("copilot", "--acp", "--stdio")
    environment: AgentEnvironment = AgentEnvironment.DOCKER
    image: str | None = None
    fixture_directory: str | None = None
    expected_fixture_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    credential_env: tuple[str, ...] = ()
    authentication_method: str | None = None
    permission_policy: AgentPermissionPolicy = AgentPermissionPolicy.DENY
    model: str = ""
    local_execution_acknowledged: bool = False
    startup_timeout_seconds: float = Field(default=60, gt=0, le=600)
    turn_timeout_seconds: float = Field(default=180, gt=0, le=3600)
    idle_timeout_seconds: float = Field(default=300, gt=0, le=86400)
    lifetime_seconds: float = Field(default=900, gt=0, le=86400)
    cancellation_grace_seconds: float = Field(default=10, gt=0, le=60)
    max_evidence_bytes: int = Field(default=16 * 1024 * 1024, ge=4096, le=256 * 1024 * 1024)
    artifact_paths: tuple[str, ...] = ()
    max_artifact_bytes: int = Field(default=1024 * 1024, ge=1, le=16 * 1024 * 1024)
    docker_network: str = "bridge"
    docker_memory: str = "1g"
    docker_cpus: float = Field(default=1, gt=0, le=32)

    _command_validator = field_validator("command")(_validate_command)
    _artifact_validator = field_validator("artifact_paths")(_validate_artifacts)
    _credentials_validator = field_validator("credential_env")(_validate_credentials)

    @model_validator(mode="after")
    def _validate_environment(self) -> "AgentProfile":
        _validate_environment(
            environment=self.environment, image=self.image, acknowledged=self.local_execution_acknowledged
        )
        return self


class ModelBinding(BaseModel):
    """Harness-managed selection, or a capability-qualified registered target for BYOK."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    model: str = ""


class HarnessProfile(BaseModel):
    """Reusable harness behavior and authentication references, independent of placement."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    command: tuple[str, ...] = ("copilot", "--acp", "--stdio")
    credential_env: tuple[str, ...] = ()
    authentication_method: str | None = None
    permission_policy: AgentPermissionPolicy = AgentPermissionPolicy.DENY

    _command_validator = field_validator("command")(_validate_command)
    _credentials_validator = field_validator("credential_env")(_validate_credentials)


class EnvironmentTemplate(BaseModel):
    """Reusable starting filesystem and resource/access policy; never a running environment."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    environment: AgentEnvironment = AgentEnvironment.DOCKER
    image: str | None = None
    fixture_directory: str | None = None
    expected_fixture_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    local_execution_acknowledged: bool = False
    docker_network: str = "bridge"
    docker_memory: str = "1g"
    docker_cpus: float = Field(default=1, gt=0, le=32)

    @model_validator(mode="after")
    def _validate_template(self) -> "EnvironmentTemplate":
        _validate_environment(
            environment=self.environment, image=self.image, acknowledged=self.local_execution_acknowledged
        )
        return self


class AgentTargetConfiguration(BaseModel):
    """Resolved system-under-test configuration; construction does not provision resources."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(default="copilot", min_length=1, max_length=100)
    model_binding: ModelBinding = Field(default_factory=ModelBinding)
    harness_profile: HarnessProfile = Field(default_factory=HarnessProfile)
    environment_template: EnvironmentTemplate
    startup_timeout_seconds: float = Field(default=60, gt=0, le=600)
    turn_timeout_seconds: float = Field(default=180, gt=0, le=3600)
    idle_timeout_seconds: float = Field(default=300, gt=0, le=86400)
    lifetime_seconds: float = Field(default=900, gt=0, le=86400)
    cancellation_grace_seconds: float = Field(default=10, gt=0, le=60)
    max_evidence_bytes: int = Field(default=16 * 1024 * 1024, ge=4096, le=256 * 1024 * 1024)
    artifact_paths: tuple[str, ...] = ()
    max_artifact_bytes: int = Field(default=1024 * 1024, ge=1, le=16 * 1024 * 1024)

    _artifact_validator = field_validator("artifact_paths")(_validate_artifacts)

    def to_profile(self) -> AgentProfile:
        """
        Resolve into the original immutable launch representation.

        Returns:
            AgentProfile: Backward-compatible recipe with identical target identity.
        """
        values = self.model_dump(exclude={"model_binding", "harness_profile", "environment_template"})
        values.update(self.model_binding.model_dump())
        values.update(self.harness_profile.model_dump())
        values.update(self.environment_template.model_dump())
        return AgentProfile.model_validate(values)

    @classmethod
    def from_profile(cls, profile: AgentProfile) -> "AgentTargetConfiguration":
        """
        Project a legacy recipe into separate authoring concepts without changing its behavior.

        Returns:
            AgentTargetConfiguration: Immutable composed configuration.
        """
        values = profile.model_dump()
        groups: dict[str, dict[str, JsonValue]] = {}
        for name, model in (
            ("model_binding", ModelBinding),
            ("harness_profile", HarnessProfile),
            ("environment_template", EnvironmentTemplate),
        ):
            groups[name] = {key: values.pop(key) for key in model.model_fields if key in values}
        return cls.model_validate({**values, **groups})


class AgentExecutionState(str, Enum):
    """Resource status, separate from turn outcome and attack score."""

    STARTING = "starting"
    IDLE = "idle"
    WORKING = "working"
    CLOSING = "closing"
    CLOSED = "closed"
    CLEANUP_FAILED = "cleanup_failed"


class AgentTurnStatus(str, Enum):
    """The observed terminal outcome of a single attempted prompt."""

    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNKNOWN = "unknown"


class AgentTurn(BaseModel):
    """Prepared input is durable before dispatch; completion is explicit."""

    id: UUID = Field(default_factory=uuid4)
    request_id: str
    prompt: str
    status: AgentTurnStatus = AgentTurnStatus.RUNNING
    stop_reason: str | None = None
    response_text: str = ""
    capture_complete: bool = False
    error: str | None = None


class AgentExecution(BaseModel):
    """One allocation. A recreated environment always has a new execution identity."""

    id: UUID = Field(default_factory=uuid4)
    owner_id: str
    conversation_id: str
    target_id: str
    profile: AgentProfile
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_activity_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    state: AgentExecutionState = AgentExecutionState.STARTING
    connection_state: AgentConnectionState = AgentConnectionState.DISCONNECTED
    last_event_at: datetime | None = None
    transcript_revision: int = 0
    session_id: str | None = None
    provider_id: str | None = None
    process_id: int | None = None
    process_created_at: float | None = None
    image_id: str | None = None
    fixture_sha256: str | None = None
    agent_info: dict[str, JsonValue] = Field(default_factory=dict)
    turns: list[AgentTurn] = Field(default_factory=list)
    event_count: int = 0
    evidence_bytes: int = 0
    capture_error: str | None = None
    close_reason: str | None = None
    cleanup_error: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    artifact_errors: list[str] = Field(default_factory=list)
    source_coverage: str = "ACP-exposed events only; internal tool coverage is not guaranteed."

    @computed_field
    @property
    def configuration(self) -> AgentTargetConfiguration:
        """The resolved configuration, independent of this execution's mutable state."""
        return AgentTargetConfiguration.from_profile(self.profile)


class AgentExecutionEvent(BaseModel):
    """An ordered, retained protocol or lifecycle event."""

    execution_id: UUID
    sequence: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    turn_id: UUID | None = None
    direction: str
    payload: dict[str, JsonValue]


class AgentEventPage(BaseModel):
    """Cursor-based access to recorded events."""

    events: list[AgentExecutionEvent]
    next_cursor: int
