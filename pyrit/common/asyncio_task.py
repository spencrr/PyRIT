# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Completion barriers for caller-owned cleanup and atomic I/O tasks."""

import asyncio
from typing import TypeVar

T = TypeVar("T")


async def await_task_completion_async(task: asyncio.Task[T]) -> T:
    """
    Join owned work even under repeated caller cancellation, then propagate cancellation.

    Returns:
        T: The completed task's result.

    Raises:
        asyncio.CancelledError: The caller or owned task was cancelled.
    """
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
