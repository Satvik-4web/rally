import re
import shlex
from pathlib import Path
from typing import Any, NamedTuple
from app.config import settings

SECRET_PATTERNS = [
    re.compile(r"(sk-[a-zA-Z0-9]{16,})"),
    re.compile(r"(AIza[0-9A-Za-z\-_]{20,})"),
    re.compile(r"(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*['\"]?([^\s'\"]+)"),
]

PROMPT_INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior)\s+instructions"),
    re.compile(r"(?i)reveal\s+(your\s+)?(api\s+keys|secrets|system\s+prompt|passwords)"),
    re.compile(r"(?i)disable\s+(all\s+)?safety"),
]

ALLOWED_EXECUTABLE_PREFIXES = {"npm", "npx", "pnpm", "yarn", "node", "python", "pytest", "git", "ruff", "tsc"}
BLOCKED_SHELL_TOKENS = {";", "&&", "||", "|", "`", "$(", ">", "<", "rm -rf", "del /s", "format ", "shutdown"}
ALLOWED_APPLICATIONS = {
    "notepad": ["notepad.exe"],
    "calc": ["calc.exe"],
    "explorer": ["explorer.exe"],
    "chrome": ["chrome.exe"],
    "code": ["code"],
}


class ActionRisk(NamedTuple):
    risk_tier: int
    requires_approval: bool


def redact_secrets(data: Any) -> Any:
    if isinstance(data, str):
        redacted = data
        for pattern in SECRET_PATTERNS:
            redacted = pattern.sub("[REDACTED_SECRET]", redacted)
        return redacted
    if isinstance(data, dict):
        return {
            k: ("[REDACTED_SECRET]" if any(s in k.lower() for s in ("key", "secret", "password", "token")) else redact_secrets(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact_secrets(i) for i in data]
    return data


def detect_prompt_injection(untrusted_text: str) -> tuple[bool, str | None]:
    for pat in PROMPT_INJECTION_PATTERNS:
        match = pat.search(untrusted_text)
        if match:
            return True, f"Untrusted content contained prohibited instruction pattern: '{match.group(0)}'"
    return False, None


DANGEROUS_EXECUTABLES = {
    "powershell", "powershell.exe", "cmd", "cmd.exe", "pwsh", "pwsh.exe",
    "bash", "bash.exe", "wscript", "wscript.exe", "cscript", "cscript.exe",
    "regedit", "regedit.exe", "rundll32", "rundll32.exe", "certutil", "certutil.exe",
    "mshta", "mshta.exe", "bitsadmin", "bitsadmin.exe", "vssadmin", "vssadmin.exe",
}

DANGEROUS_SHELL_FLAGS = {"-c", "-command", "/c", "-enc", "-encodedcommand", "iex", "invoke-expression", "curl", "wget", "rm", "del"}
DANGEROUS_URL_SCHEMES = ("file://", "javascript:", "data:", "vbscript:", "ms-appinstaller:")


def classify_action_risk(action_type: str, parameters: dict[str, Any]) -> ActionRisk:
    """
    Classifies an action's risk tier (0-3) and whether explicit human approval is required.
    Performs deep argument and target inspection:
    - Launching shells/interpreters (powershell, cmd) is escalated to Tier 2/3.
    - file:// or javascript: URLs are escalated to Tier 2.
    - Mutating commands, purchases, or transmissions require Tier 2 approval.
    """
    if action_type in {"read_ui_tree", "capture_screenshot", "wait_for_condition"}:
        return ActionRisk(0, False)

    target_text = str(parameters.get("target_name") or parameters.get("app_name") or "").lower().strip()
    action_args = str(parameters.get("args") or parameters.get("type_text") or parameters.get("command") or "").lower()

    # 1. Deep Inspection for launch_app
    if action_type == "launch_app":
        base_app = target_text.split()[0] if target_text.split() else ""
        if base_app in DANGEROUS_EXECUTABLES or any(sh in base_app for sh in ("powershell", "cmd.exe", "pwsh", "regedit")):
            if any(fl in action_args for fl in DANGEROUS_SHELL_FLAGS):
                return ActionRisk(3, True)  # Tier 3: Shell invocation with command execution arguments
            return ActionRisk(2, True)  # Tier 2: Shell / system utility launch requires explicit approval

    # 2. Deep Inspection for open_url
    if action_type == "open_url":
        url = str(parameters.get("url") or target_text).lower()
        if any(url.startswith(scheme) for scheme in DANGEROUS_URL_SCHEMES):
            return ActionRisk(2, True)  # Tier 2: file://, javascript:, or dangerous URI schemes require approval
        if any(w in url for w in ("admin", "delete", "reset", "logout", "purchase", "pay")):
            return ActionRisk(2, True)

    # 3. Deep Inspection for type_text into terminal / system windows
    if action_type in {"type_text", "press_key"}:
        win_title = str(parameters.get("window_title") or "").lower()
        if any(t in win_title for t in ("powershell", "cmd", "terminal", "command prompt")):
            return ActionRisk(2, True)

    # 4. Consequential / External keywords
    if any(w in target_text or w in action_args for w in ("send", "submit", "buy", "purchase", "delete", "pay", "format", "drop")):
        return ActionRisk(2, True)

    if action_type in {"send_external_message", "modify_workspace_files"}:
        return ActionRisk(2, True)

    if action_type in {"launch_app", "focus_window", "open_url", "compose_email_draft", "click", "type_text", "press_key", "scroll", "open_folder"}:
        return ActionRisk(1, False)

    if action_type == "run_approved_command":
        cmd_list = parameters.get("command", [])
        cmd_str = " ".join(cmd_list) if isinstance(cmd_list, list) else str(cmd_list)
        if any(w in cmd_str.lower() for w in ("push", "publish", "install", "clean", "reset", "del", "rmdir")):
            return ActionRisk(2, True)
        return ActionRisk(1, False)

    return ActionRisk(3, True)


ALWAYS_CONFIRM_ACTIONS = {
    "send_external_message",
    "send_email",
    "delete_file",
    "overwrite_file",
    "install_software",
    "purchase_item",
    "change_security_settings",
    "enter_password",
    "banking_action",
}


def classify_action_risk_with_autonomy(
    action_type: str,
    parameters: dict[str, Any],
    autonomy_mode: str = "balanced",
) -> ActionRisk:
    base_risk = classify_action_risk(action_type, parameters)

    # Check if target window or action is an immutable ALWAYS_CONFIRM item
    target_text = str(parameters.get("target_name") or parameters.get("app_name") or parameters.get("window_title") or "").lower()
    action_args = str(parameters.get("args") or parameters.get("type_text") or parameters.get("command") or "").lower()

    is_always_confirm = (
        action_type in ALWAYS_CONFIRM_ACTIONS
        or any(w in target_text or w in action_args for w in ("send", "submit", "pay", "buy", "purchase", "delete", "format", "password", "bank"))
        or any(w in target_text for w in ("bank", "paypal", "checkout", "payment"))
    )

    if is_always_confirm:
        return ActionRisk(max(2, base_risk.risk_tier), True)

    if autonomy_mode == "ask_first":
        # In ask_first mode, anything beyond read-only (Tier 0) requires explicit confirmation
        if base_risk.risk_tier > 0:
            return ActionRisk(base_risk.risk_tier, True)
        return base_risk

    elif autonomy_mode == "autopilot":
        # In autopilot mode, only always-confirm actions pause
        if not is_always_confirm:
            return ActionRisk(base_risk.risk_tier, False)
        return base_risk

    # Default "balanced" mode
    return base_risk


def sanitize_untrusted_content(untrusted_text: str) -> dict[str, Any]:
    """
    RALLY 3.0 Prompt Injection Defense:
    Text read from external web pages, emails, or screens is untrusted.
    Tags untrusted content and strips any embedded command escalation strings.
    """
    has_injection, reason = detect_prompt_injection(untrusted_text)
    sanitized = redact_secrets(untrusted_text)
    if has_injection:
        for pat in PROMPT_INJECTION_PATTERNS:
            sanitized = pat.sub("[PROMPT_INJECTION_BLOCKED]", sanitized)
    return {
        "text": sanitized,
        "is_untrusted": True,
        "has_injection": has_injection,
        "injection_reason": reason,
    }


def validate_workspace_command(command: list[str] | str, workspace_path: str) -> list[str]:
    ws = Path(workspace_path).resolve()
    if not ws.exists() or not ws.is_dir():
        raise ValueError(f"Workspace directory does not exist: {workspace_path}")

    raw_str = command if isinstance(command, str) else " ".join(command)
    for bad in BLOCKED_SHELL_TOKENS:
        if bad in raw_str.lower():
            raise PermissionError(f"Blocked shell token '{bad}' in command: {raw_str}")

    tokens = shlex.split(command, posix=False) if isinstance(command, str) else list(command)
    if not tokens:
        raise ValueError("Empty command is not allowed.")

    exe = Path(tokens[0]).stem.lower()
    if exe not in ALLOWED_EXECUTABLE_PREFIXES:
        raise PermissionError(f"Executable '{exe}' is not in the RALLY command allowlist.")

    if not settings.RALLY_ALLOW_ADMIN_COMMANDS and any(t.lower() in {"runas", "sudo", "powershell", "cmd"} for t in tokens):
        raise PermissionError("Administrator or nested shell escalation commands are prohibited.")

    return tokens