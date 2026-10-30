"""Regenerate the deck fixtures by running the scripted ACP agent through the real PyRIT agent runtime.

Run from a PyRIT environment with the `agents` extra installed, on a branch of the agent stack:

    python fixtures/generate.py [--repo PATH]

Writes events.jsonl, record.json, projection.json and receipt.json next to this file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_REPO = Path(r"C:\Users\spencersch\src\PyRIT.worktrees\pyrt-agent-execution-environment")


async def main_async(repo: Path) -> None:
    from pyrit.agent.execution_manager import AgentExecutionManager
    from pyrit.models.agent_execution import AgentTargetConfiguration, EnvironmentTemplate, HarnessProfile

    agent = repo / "tests" / "integration" / "agent" / "scripted_acp_agent.py"
    configuration = AgentTargetConfiguration(
        harness_profile=HarnessProfile(harness_kind="acp", command=(sys.executable, str(agent)), permission_policy="allow_once"),
        environment_template=EnvironmentTemplate(
            environment="local",
            local_execution_acknowledged=True,
            fixture_directory=str(repo / "assets" / "agent_receipt"),
        ),
        artifact_paths=("receipt.json",),
    )
    root = Path(tempfile.mkdtemp(prefix="pyrit-deck-")) / "executions"
    async with AgentExecutionManager(root=root) as manager:
        common = {"configuration": configuration, "target_id": "deck-target", "conversation_id": "deck-conversation"}
        record, turn = await manager.send_async(request_id="deck-1", prompt="Create the receipt", has_history=False, **common)
        record, turn2 = await manager.send_async(request_id="deck-2", prompt="Update the receipt", has_history=True, **common)
        projection = await manager.tool_calls_async(execution_id=record.id, turn_id=turn.id)
        pieces = [json.loads(m.get_piece().original_value) for m in projection.messages]
        metadata = [m.get_piece().prompt_metadata for m in projection.messages]
        await manager.close_execution_async(execution_id=record.id)
        directory = manager.store.directory(record.id)
        shutil.copy(directory / "events.jsonl", HERE / "events.jsonl")
        shutil.copy(directory / "record.json", HERE / "record.json")
        (HERE / "projection.json").write_text(
            json.dumps({"pieces": pieces, "metadata": metadata, "omitted": projection.omitted}, indent=2, default=str),
            encoding="utf-8",
        )
        artifact = directory / "artifacts" / "receipt.json"
        if artifact.exists():
            shutil.copy(artifact, HERE / "receipt.json")
        print("turn 1", turn.status, repr(turn.response_text))
        print("turn 2", turn2.status, repr(turn2.response_text))
        print("events", record.event_count, "pieces", len(pieces), "omitted", projection.omitted)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    asyncio.run(main_async(parser.parse_args().repo))
