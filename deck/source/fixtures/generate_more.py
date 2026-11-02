"""Capture the operator-approval journal and a real conformance report for the deck.

Run on a branch of the agent stack with the `agents` extra installed:

    python fixtures/generate_more.py [--repo PATH]

Writes ask_events.jsonl, ask_record.json, ask_turns.json and conformance_local.json next to this file.
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
LINE_ECHO = "import sys\nfor line in sys.stdin.buffer:\n    sys.stdout.buffer.write(line)\n    sys.stdout.buffer.flush()"


async def capture_ask_async(repo: Path) -> None:
    from pyrit.agent.execution_manager import AgentExecutionManager
    from pyrit.models.agent_execution import AgentTargetConfiguration, EnvironmentTemplate, HarnessProfile

    agent = repo / "tests" / "integration" / "agent" / "scripted_acp_agent.py"
    configuration = AgentTargetConfiguration(
        harness_profile=HarnessProfile(harness_kind="acp", command=(sys.executable, str(agent)), permission_policy="ask"),
        environment_template=EnvironmentTemplate(
            environment="local", local_execution_acknowledged=True, fixture_directory=str(repo / "assets" / "agent_receipt")
        ),
        artifact_paths=("receipt.json",),
    )
    root = Path(tempfile.mkdtemp(prefix="pyrit-deck-ask-")) / "executions"
    async with AgentExecutionManager(root=root) as manager:
        common = {"configuration": configuration, "target_id": "deck-target", "conversation_id": "deck-ask", "interactive": True}
        send = asyncio.create_task(manager.send_async(request_id="ask-1", prompt="approve", has_history=False, **common))
        async with asyncio.timeout(30):
            while not manager.records or not next(iter(manager.records.values())).approvals:
                await asyncio.sleep(0.02)
            record = next(iter(manager.records.values()))
            await asyncio.sleep(1.0)  # the operator reads the request
            await manager.decide_permission_async(
                execution_id=record.id, approval_id=record.approvals[0].id, allow=True, actor="operator:alex@contoso.example"
            )
            record, turn1 = await send
        record, turn2 = await manager.send_async(request_id="ask-2", prompt="Add one orange to the order", has_history=True, **common)
        projection = await manager.tool_calls_async(execution_id=record.id, turn_id=turn1.id)
        turns = [
            {"id": str(t.id), "prompt": t.prompt, "status": t.status.value, "stop_reason": t.stop_reason, "response_text": t.response_text}
            for t in (turn1, turn2)
        ]
        await manager.control_held_async(execution_id=record.id, action="close", actor="operator:alex@contoso.example") if record.state.value == "held" else None
        await manager.close_execution_async(execution_id=record.id)
        directory = manager.store.directory(record.id)
        shutil.copy(directory / "events.jsonl", HERE / "ask_events.jsonl")
        text = (directory / "record.json").read_text(encoding="utf-8")
        text = text.replace(str(root).replace("\\", "/"), "<tmp>/executions").replace(json.dumps(str(repo))[1:-1], "<repo>")
        (HERE / "ask_record.json").write_text(text, encoding="utf-8")
        (HERE / "ask_turns.json").write_text(
            json.dumps({"turns": turns, "pieces": [json.loads(m.get_piece().original_value) for m in projection.messages], "omitted": projection.omitted}, indent=2, default=str),
            encoding="utf-8",
        )
        artifact = directory / "artifacts" / "receipt.json"
        if artifact.exists():
            shutil.copy(artifact, HERE / "ask_receipt.json")
        print("ask turn 1", turn1.status, repr(turn1.response_text))
        print("ask turn 2", turn2.status, repr(turn2.response_text))
        print("approvals", [(a.decision, a.actor) for a in record.approvals])


async def capture_conformance_async() -> None:
    from pyrit.agent.conformance import verify_environment_provider_async
    from pyrit.agent.environments.local import LocalProvider
    from pyrit.models.agent_execution import EnvironmentTemplate

    directory = Path(tempfile.mkdtemp(prefix="pyrit-deck-conf-"))
    report = await verify_environment_provider_async(
        LocalProvider(), template=EnvironmentTemplate(environment="local"), echo_command=(sys.executable, "-u", "-c", LINE_ECHO), directory=directory
    )
    (HERE / "conformance_local.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print("conformance checks", len(report.checks), "failed", sum(not c.passed for c in report.checks))


async def main_async(repo: Path) -> None:
    await capture_ask_async(repo)
    await capture_conformance_async()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    asyncio.run(main_async(parser.parse_args().repo))
