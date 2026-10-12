# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Explicit developer-only GitHub CLI credential resolution; no credential persistence."""

import asyncio
import os
import shutil

from pyrit.common.asyncio_task import await_task_completion_async


def github_cli_login_enabled() -> bool:
    """
    Check whether the backend operator permits developer login delegation.

    Returns:
        bool: Explicit runtime opt-in, never inferred from an installed CLI.
    """
    return os.getenv("PYRIT_ALLOW_GITHUB_CLI_LOGIN", "").lower() == "true"


async def github_cli_token_async() -> str:
    """
    Resolve the backend's github.com login without returning diagnostics containing secrets.

    Returns:
        str: Ephemeral credential for the explicitly selected execution.

    Raises:
        ValueError: Delegation is disabled, unavailable, or the login could not be resolved.
    """
    if not github_cli_login_enabled():
        raise ValueError(
            "Backend GitHub CLI login is disabled. For local development only, set "
            "PYRIT_ALLOW_GITHUB_CLI_LOGIN=true on the backend; hosted deployments should use credential references."
        )
    executable = shutil.which("gh")
    if executable is None:
        raise ValueError("GitHub CLI is not installed on the backend. Install gh and run gh auth login there.")
    process = await asyncio.create_subprocess_exec(
        executable,
        "auth",
        "token",
        "--hostname",
        "github.com",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        stdin=asyncio.subprocess.DEVNULL,
    )
    assert process.stdout is not None
    completed = False
    try:
        async with asyncio.timeout(15):
            try:
                await process.stdout.readexactly(16385)
                raise ValueError("GitHub CLI returned an oversized credential")
            except asyncio.IncompleteReadError as end:
                data = end.partial
            await process.wait()
            completed = True
    except TimeoutError:
        raise ValueError("GitHub CLI login lookup timed out; check gh auth status on the backend") from None
    finally:
        if not completed:
            await await_task_completion_async(asyncio.create_task(_stop_credential_process_async(process)))
    if process.returncode:
        raise ValueError("No usable backend GitHub CLI login. Run gh auth login --hostname github.com on the backend.")
    try:
        token = data.decode("ascii").strip()
    except UnicodeDecodeError:
        raise ValueError("GitHub CLI returned an invalid credential") from None
    if not token or any(character.isspace() for character in token):
        raise ValueError("GitHub CLI returned an empty or invalid credential")
    return token


async def _stop_credential_process_async(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    try:
        async with asyncio.timeout(5):
            assert process.stdout is not None
            # Drain even after exit: a paused pipe can prevent Process.wait() from finishing.
            while await process.stdout.read(65536):
                pass
            await process.wait()
    except TimeoutError:
        raise ValueError("GitHub CLI credential process did not finish cleanup within its deadline") from None
