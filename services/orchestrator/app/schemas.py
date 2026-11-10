from typing import Any, Literal
from pydantic import BaseModel, Field

AllowedActionType = Literal[
    "launch_app",
    "focus_window",
    "capture_screenshot",
    "click",
    "double_click",
    "type_text",
    "press_key",
    "scroll",
    "open_url",
    "select_text",
    "copy_text",
    "paste_text",
    "read_ui_tree",
    "wait_for_condition",
    "run_approved_command",
    "open_folder",
    "send_external_message",
    "compose_email_draft",
    "modify_workspace_files",
]


class MissionCreateRequest(BaseModel):
    objective: str = Field(..., min_length=3)
    title: str | None = None
    mission_type: Literal["general", "desktop", "browser", "build"] = "general"
    workspace_path: str | None = None
    selected_coding_agent: str | None = None
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)


class MissionPatchRequest(BaseModel):
    title: str | None = None
    objective: str | None = None
    constraints: list[str] | None = None
    success_criteria: list[str] | None = None


class StepPlanSpec(BaseModel):
    title: str
    description: str
    assigned_role: str = "DesktopOperator"
    action_type: AllowedActionType
    action_payload: dict[str, Any] = Field(default_factory=dict)
    risk_tier: int = Field(default=0, ge=0, le=3)
    requires_approval: bool = False
    verification_method: str = "process_or_observation_check"


class MissionPlanSchema(BaseModel):
    title: str
    mission_type: Literal["general", "desktop", "browser", "build"]
    success_criteria: list[str]
    constraints: list[str]
    steps: list[StepPlanSpec]


class ApprovalDecisionRequest(BaseModel):
    resolved_by: str = "local_user"
    comment: str | None = None


class DesktopActionRequest(BaseModel):
    mission_id: str | None = None
    type: AllowedActionType
    target: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str = "Explicit user or step action"
    expected_result: str = "Observable state change"


class BuildInspectRequest(BaseModel):
    workspace_path: str


class BuildMissionCreateRequest(BaseModel):
    workspace_path: str
    objective: str
    selected_coding_agent: str = "ollama_cli"
    required_routes: list[str] = Field(default_factory=lambda: ["/"])
    max_correction_iterations: int = Field(default=3, ge=1, le=5)


class RunCheckRequest(BaseModel):
    command_kind: Literal["build", "test", "lint"] = "test"
    custom_command: list[str] | None = None