# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from pyrit.backend.models.targets import CreateTargetRequest
from pyrit.backend.routes.targets import require_agent_target_admin
from pyrit.prompt_target import AgentTarget
from pyrit.registry import TargetRegistry


@pytest.mark.usefixtures("patch_central_database")
def test_executable_target_alias_requires_admin_before_construction() -> None:
    registry = TargetRegistry.get_registry_singleton()
    registry.register_class(AgentTarget, name="CustomHarnessAlias")
    request = Request({"type": "http", "headers": []})
    with (
        patch.dict("os.environ", {"PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": "false"}),
        pytest.raises(HTTPException) as caught,
    ):
        require_agent_target_admin(
            CreateTargetRequest(type="CustomHarnessAlias", params={}),
            request,
        )
    assert caught.value.status_code == 403
