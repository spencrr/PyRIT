# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from pyrit.agent.harness import HarnessAdapterRegistry
from tests.unit.agent.example_adapters import OpenCodeAdapter


@pytest.fixture
def opencode_adapter() -> Iterator[OpenCodeAdapter]:
    """Register the reference third-party adapter on an isolated registry for one test."""
    registry = HarnessAdapterRegistry()
    adapter = OpenCodeAdapter()
    registry.register(adapter)
    with patch.object(HarnessAdapterRegistry, "_singleton", registry):
        yield adapter
