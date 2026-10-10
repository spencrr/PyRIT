# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.backend.services import service_lifecycle
from pyrit.registry import TargetRegistry
from pyrit.registry.instance_registry import RegistryEntry
from tests.unit.mocks import MockPromptTarget


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("grouped", [False, True])
async def test_provider_cleanup_cancellation_does_not_skip_remaining_targets_async(grouped: bool) -> None:
    first, second = MockPromptTarget(), MockPromptTarget()
    error = (
        BaseExceptionGroup("SDK session cleanup", [ValueError("destroy failed"), asyncio.CancelledError()])
        if grouped
        else asyncio.CancelledError("SDK stop cancelled")
    )
    with (
        patch.object(service_lifecycle, "reset_scenario_run_service_async", new_callable=AsyncMock),
        patch.object(service_lifecycle, "close_agent_execution_manager_async", new_callable=AsyncMock),
        patch.object(
            TargetRegistry.get_registry_singleton().instances,
            "get_all_instances",
            return_value=[RegistryEntry(name="first", instance=first), RegistryEntry(name="second", instance=second)],
        ),
        patch.object(first, "cleanup_target_async", new_callable=AsyncMock, side_effect=error),
        patch.object(second, "cleanup_target_async", new_callable=AsyncMock) as cleanup,
        pytest.raises(ExceptionGroup, match="Target transport cleanup failed") as caught,
    ):
        await service_lifecycle.close_services_async()
    cleanup.assert_awaited_once()
    assert caught.value.exceptions[0].__cause__ is error


@pytest.mark.usefixtures("patch_central_database")
async def test_caller_cancellation_is_not_swallowed_async() -> None:
    target = MockPromptTarget()
    entered = asyncio.Event()

    async def wait_async() -> None:
        entered.set()
        await asyncio.Event().wait()

    with (
        patch.object(service_lifecycle, "reset_scenario_run_service_async", new_callable=AsyncMock),
        patch.object(service_lifecycle, "close_agent_execution_manager_async", new_callable=AsyncMock),
        patch.object(
            TargetRegistry.get_registry_singleton().instances,
            "get_all_instances",
            return_value=[RegistryEntry(name="native", instance=target)],
        ),
        patch.object(target, "cleanup_target_async", side_effect=wait_async),
    ):
        task = asyncio.create_task(service_lifecycle.close_services_async())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
