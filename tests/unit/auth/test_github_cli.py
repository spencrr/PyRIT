# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.auth.github_cli import github_cli_token_async


async def test_developer_login_is_disabled_by_default_without_running_gh_async() -> None:
    with (
        patch.dict("os.environ", {"PYRIT_ALLOW_GITHUB_CLI_LOGIN": "false"}),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn,
    ):
        with pytest.raises(ValueError, match="disabled"):
            await github_cli_token_async()
    spawn.assert_not_awaited()


async def test_missing_cli_is_actionable_async() -> None:
    with (
        patch.dict("os.environ", {"PYRIT_ALLOW_GITHUB_CLI_LOGIN": "true"}),
        patch("pyrit.auth.github_cli.shutil.which", return_value=None),
    ):
        with pytest.raises(ValueError, match="Install gh"):
            await github_cli_token_async()


@pytest.mark.parametrize(
    ("data", "code", "error"),
    [
        (b"gho_synthetic\n", 0, None),
        (b"", 0, "empty"),
        (b"two credentials\n", 0, "invalid"),
        (b"private-token-output", 1, "No usable"),
        (b"\xff", 0, "invalid"),
        (b"x" * 16385, 0, "oversized"),
    ],
)
async def test_login_resolution_is_bounded_and_never_includes_credential_in_errors_async(
    data: bytes,
    code: int,
    error: str | None,
) -> None:
    process = MagicMock(spec=asyncio.subprocess.Process)
    process.returncode = code
    process.stdout = asyncio.StreamReader()
    process.stdout.feed_data(data)
    process.stdout.feed_eof()
    process.wait = AsyncMock(return_value=code)
    with (
        patch.dict("os.environ", {"PYRIT_ALLOW_GITHUB_CLI_LOGIN": "true"}),
        patch("pyrit.auth.github_cli.shutil.which", return_value="gh.exe"),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process) as spawn,
    ):
        if error:
            with pytest.raises(ValueError, match=error) as caught:
                await github_cli_token_async()
            assert "private-token-output" not in str(caught.value)
        else:
            assert await github_cli_token_async() == "gho_synthetic"
        assert spawn.call_args.args == ("gh.exe", "auth", "token", "--hostname", "github.com")
        assert spawn.call_args.kwargs["stderr"] == asyncio.subprocess.DEVNULL


async def test_cancelled_login_lookup_kills_and_joins_only_its_child_async() -> None:
    process = MagicMock(spec=asyncio.subprocess.Process)
    process.returncode = None
    process.stdout = asyncio.StreamReader()
    process.kill.side_effect = process.stdout.feed_eof
    process.wait = AsyncMock(return_value=1)
    spawned = asyncio.Event()

    async def spawn_async(*args: Any, **kwargs: Any) -> MagicMock:
        spawned.set()
        return process

    with (
        patch.dict("os.environ", {"PYRIT_ALLOW_GITHUB_CLI_LOGIN": "true"}),
        patch("pyrit.auth.github_cli.shutil.which", return_value="gh.exe"),
        patch("asyncio.create_subprocess_exec", side_effect=spawn_async),
    ):
        task = asyncio.create_task(github_cli_token_async())
        await spawned.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


async def test_login_timeout_is_sanitized_and_joins_the_child_async() -> None:
    process = MagicMock(spec=asyncio.subprocess.Process)
    process.returncode = None
    process.stdout = MagicMock(spec=asyncio.StreamReader)
    process.stdout.readexactly = AsyncMock(side_effect=TimeoutError("private command diagnostics"))
    process.stdout.read = AsyncMock(return_value=b"")
    process.wait = AsyncMock(return_value=1)
    with (
        patch.dict("os.environ", {"PYRIT_ALLOW_GITHUB_CLI_LOGIN": "true"}),
        patch("pyrit.auth.github_cli.shutil.which", return_value="gh.exe"),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process),
    ):
        with pytest.raises(ValueError, match="timed out") as caught:
            await github_cli_token_async()
        assert "private command diagnostics" not in str(caught.value)
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


@pytest.mark.parametrize("returncode", [None, 0])
async def test_oversized_output_is_drained_before_joining_even_an_exited_child_async(returncode: int | None) -> None:
    process = MagicMock(spec=asyncio.subprocess.Process)
    process.returncode = returncode
    process.stdout = MagicMock(spec=asyncio.StreamReader)
    process.stdout.readexactly = AsyncMock(return_value=b"x" * 16385)
    process.stdout.read = AsyncMock(side_effect=[b"x" * 65536, b""])

    async def wait_async() -> int:
        assert process.stdout.read.await_count == 2
        return 0

    process.wait = AsyncMock(side_effect=wait_async)
    with (
        patch.dict("os.environ", {"PYRIT_ALLOW_GITHUB_CLI_LOGIN": "true"}),
        patch("pyrit.auth.github_cli.shutil.which", return_value="gh.exe"),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process),
    ):
        with pytest.raises(ValueError, match="oversized"):
            await github_cli_token_async()
    process.wait.assert_awaited_once()
    assert process.kill.call_count == (1 if returncode is None else 0)
