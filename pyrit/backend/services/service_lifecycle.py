# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Explicit lifecycle for backend-owned cached services."""

from pyrit.agent.runtime import close_agent_execution_manager_async
from pyrit.backend.services.attack_service import get_attack_service
from pyrit.backend.services.converter_service import get_converter_service
from pyrit.backend.services.dataset_service import get_dataset_service
from pyrit.backend.services.initializer_service import get_initializer_service
from pyrit.backend.services.manual_send_scheduler import get_manual_send_scheduler
from pyrit.backend.services.message_send_service import get_message_send_service
from pyrit.backend.services.scenario_run_service import reset_scenario_run_service_async
from pyrit.backend.services.scenario_service import get_scenario_service
from pyrit.backend.services.scorer_service import get_scorer_service
from pyrit.backend.services.target_service import get_target_service
from pyrit.registry import TargetRegistry


def outstanding_estimates() -> int:
    """Return outstanding work without constructing unused services."""
    return get_scenario_service().outstanding_estimates() if get_scenario_service.cache_info().currsize else 0


def has_active_manual_sends() -> bool:
    """Return whether accepted sends remain, even after their HTTP requests have finished."""
    return bool(get_manual_send_scheduler.cache_info().currsize and get_manual_send_scheduler().has_active_work())


async def close_services_async() -> None:
    """
    Close existing resources, then invalidate cached registry and memory references.

    Raises:
        ExceptionGroup: One or more target transports could not be closed.
    """
    if get_manual_send_scheduler.cache_info().currsize:
        get_manual_send_scheduler().stop_admission()
    try:
        if get_message_send_service.cache_info().currsize:
            await get_message_send_service().shutdown_async()
    finally:
        try:
            if get_scenario_service.cache_info().currsize:
                await get_scenario_service().close_async()
            if get_converter_service.cache_info().currsize:
                await get_converter_service().close_async()
            await reset_scenario_run_service_async()
            await close_agent_execution_manager_async()
            errors: list[Exception] = []
            for entry in TargetRegistry.get_registry_singleton().instances.get_all_instances():
                try:
                    await entry.instance.cleanup_target_async()
                except Exception as error:
                    errors.append(error)
            if errors:
                raise ExceptionGroup("Target transport cleanup failed", errors)
        finally:
            for factory in (
                get_attack_service,
                get_converter_service,
                get_dataset_service,
                get_initializer_service,
                get_manual_send_scheduler,
                get_message_send_service,
                get_scenario_service,
                get_scorer_service,
                get_target_service,
            ):
                factory.cache_clear()
