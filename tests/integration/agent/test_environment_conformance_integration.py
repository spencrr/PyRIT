# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Real-process conformance of the first-party local provider."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("psutil")

from pyrit.agent.conformance import verify_environment_provider_async
from pyrit.agent.environments.local import LocalProvider
from pyrit.models.agent_execution import EnvironmentTemplate

pytestmark = pytest.mark.run_only_if_all_tests

LINE_ECHO = (
    "import sys\nfor line in sys.stdin.buffer:\n    sys.stdout.buffer.write(line)\n    sys.stdout.buffer.flush()"
)


async def test_local_provider_conforms_with_a_real_process_async(tmp_path: Path) -> None:
    report = await verify_environment_provider_async(
        LocalProvider(),
        template=EnvironmentTemplate(environment="local"),
        echo_command=(sys.executable, "-u", "-c", LINE_ECHO),
        directory=tmp_path,
    )
    report.raise_for_failures()
