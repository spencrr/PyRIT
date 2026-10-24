# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from unittest.mock import patch

import pyrit.setup.configuration_loader as configuration_module
from pyrit.registry import InitializerRegistry
from pyrit.setup.configuration_loader import ConfigurationLoader


def test_agent_demo_configuration_overrides_unrelated_shared_bootstrap(tmp_path: Path) -> None:
    shared = tmp_path / ".pyrit_conf"
    shared_content = (
        "memory_db_type: azure_sql\n"
        "initializers: [simple]\n"
        "initialization_scripts: [missing_initializer.py]\n"
        "allow_custom_initializers: true\n"
        "enable_live_reinitialization: true\n"
        "env_files: [missing.env]\n"
        "env_akv_ref: [https://example.vault.azure.net/secrets/bootstrap]\n"
        "extensions: {unrelated: true}\n"
    )
    shared.write_text(shared_content, encoding="utf-8")
    demo = Path(__file__).resolve().parents[3] / "docker" / "agent_receipt" / "pyrit.yaml"
    with patch.object(configuration_module, "DEFAULT_CONFIG_PATH", shared):
        config = ConfigurationLoader.load_with_overrides(config_file=demo, strict=True)

    assert config.memory_db_type == "sqlite"
    assert [item.name for item in config.initializer_configs] == ["technique"]
    assert config.resolve_initialization_scripts() == []
    assert config.resolve_env_files() == []
    assert config.resolve_env_akv_ref() == []
    assert not config.allow_custom_initializers
    assert not config.enable_live_reinitialization
    assert config.max_concurrent_scenario_runs == 1
    assert config.extensions == {}
    assert len(config.resolve_initializers(registry=InitializerRegistry())) == 1
    assert shared.read_text(encoding="utf-8") == shared_content
