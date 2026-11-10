import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from app.config import settings
from app.providers import PaidProviderBlockedError


@dataclass
class CodingAgentInfo:
    agent_id: str
    display_name: str
    executable: str
    installed: bool
    version: str | None
    authenticated: bool
    billing_mode: str
    allowed_in_zero_cost: bool
    status_note: str


@dataclass
class CodingTaskRequest:
    mission_id: str
    step_id: str | None
    workspace_path: str
    prompt: str
    acceptance_criteria: list[str]


class CodingAgentRegistry:
    def __init__(self) -> None:
        self._active_workspace_writers: dict[str, str] = {}

    def discover_agents(self) -> list[dict[str, Any]]:
        specs = [
            {
                "agent_id": "ollama_cli",
                "display_name": "Ollama Local Coding CLI",
                "executable": "ollama",
                "billing_mode": "local",
                "authenticated": True,
                "note": "Runs local open-weight coding models on laptop GPU/CPU (Zero-Cost).",
            },
            {
                "agent_id": "aider_local",
                "display_name": "Aider CLI (Local Ollama Backend)",
                "executable": "aider",
                "billing_mode": "local",
                "authenticated": True,
                "note": "CLI repository editor scoped to local model endpoint.",
            },
            {
                "agent_id": "unverified_cloud_agent",
                "display_name": "Paid / Unverified Cloud Coding Agent",
                "executable": "claude",
                "billing_mode": "paid_api",
                "authenticated": False,
                "note": "Disabled automatically in zero_cost_only mode.",
            },
        ]
        discovered: list[dict[str, Any]] = []
        for s in specs:
            installed = shutil.which(s["executable"]) is not None
            version_str = "detected" if installed else None
            allowed = s["billing_mode"] in {"local", "free_quota"} or settings.RALLY_SPEND_MODE != "zero_cost_only"
            discovered.append(
                asdict(
                    CodingAgentInfo(
                        agent_id=s["agent_id"],
                        display_name=s["display_name"],
                        executable=s["executable"],
                        installed=installed,
                        version=version_str,
                        authenticated=s["authenticated"],
                        billing_mode=s["billing_mode"],
                        allowed_in_zero_cost=allowed,
                        status_note=s["note"],
                    )
                )
            )
        return discovered

    def validate_agent_selection(self, agent_id: str) -> dict[str, Any]:
        agents = {a["agent_id"]: a for a in self.discover_agents()}
        if agent_id not in agents:
            raise ValueError(f"Unknown coding agent '{agent_id}'.")
        agent = agents[agent_id]
        if settings.RALLY_SPEND_MODE == "zero_cost_only" and not agent["allowed_in_zero_cost"]:
            raise PaidProviderBlockedError(
                f"Coding agent '{agent_id}' has billing_mode='{agent['billing_mode']}' and cannot run in zero_cost_only mode."
            )
        return agent

    async def dispatch_corrective_task(self, agent_id: str, request: CodingTaskRequest) -> dict[str, Any]:
        agent = self.validate_agent_selection(agent_id)
        ws = str(Path(request.workspace_path).resolve())
        brief_file = Path(ws) / ".rally_corrective_task.md"
        brief_content = (
            f"# RALLY 2.0 Supervised Coding Task\n"
            f"- Mission ID: {request.mission_id}\n"
            f"- Selected Agent: {agent['display_name']} ({agent['billing_mode']})\n\n"
            f"## Corrective Instruction\n{request.prompt}\n"
        )
        brief_file.write_text(brief_content, encoding="utf-8")
        return {
            "status": "dispatched_to_connector" if agent["installed"] else "manual_handoff_prepared",
            "agent_id": agent_id,
            "brief_path": str(brief_file),
        }


coding_agent_registry = CodingAgentRegistry()