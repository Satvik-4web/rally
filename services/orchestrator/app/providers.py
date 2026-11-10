from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import quote_plus
import httpx
from app.config import settings
from app.schemas import MissionCreateRequest, MissionPlanSchema, StepPlanSpec


class QuotaExhaustedError(RuntimeError):
    pass


class PaidProviderBlockedError(PermissionError):
    pass


@dataclass
class ProviderHealth:
    provider_id: str
    display_name: str
    model_name: str
    billing_mode: str
    authenticated: bool
    available: bool
    rate_limited: bool
    status_message: str


KNOWN_SITES: dict[str, tuple[str, str]] = {
    "youtube": ("https://www.youtube.com", "youtube"),
    "google": ("https://www.google.com", "google"),
    "gmail": ("https://mail.google.com", "gmail"),
    "github": ("https://github.com", "github"),
    "stackoverflow": ("https://stackoverflow.com", "stack overflow"),
    "wikipedia": ("https://www.wikipedia.org", "wikipedia"),
    "chatgpt": ("https://chatgpt.com", "chatgpt"),
    "reddit": ("https://www.reddit.com", "reddit"),
    "instagram": ("https://www.instagram.com", "instagram"),
    "whatsapp web": ("https://web.whatsapp.com", "whatsapp"),
}


def parse_spotify_intent(objective: str) -> dict[str, str | bool | None]:
    cleaned = objective.strip()
    obj_low = cleaned.lower()
    if "spotify" not in obj_low:
        return {"matched": False}

    should_play = bool(re.search(r"\b(play|listen|search|find|put\s+on)\b", obj_low))
    query: str | None = None
    patterns = [
        r"(?:play|search|find|listen\s+to)\s+(?:for\s+)?(?:a\s+song\s+)?['\"]?(.+?)['\"]?\s+(?:on|in)\s+spotify\b",
        r"spotify.*?(?:and\s+)?(?:play|search|find|listen\s+to)\s+(?:for\s+)?['\"]?(.+?)['\"]?$",
    ]
    for pat in patterns:
        m = re.search(pat, cleaned, re.IGNORECASE)
        if m:
            candidate = re.sub(r"\s+(?:and\s+stop|on\s+spotify|please)\.?$", "", m.group(1), flags=re.IGNORECASE).strip(" .,'\"")
            if candidate and candidate.lower() not in {"spotify", "music", "a song", "song", "it"}:
                query = candidate
                break

    return {"matched": True, "query": query, "should_play": should_play}


def parse_youtube_or_web_search(objective: str) -> dict[str, str | bool | None]:
    cleaned = objective.strip()
    obj_low = cleaned.lower()

    is_yt = bool(re.search(r"\b(youtube|yt)\b", obj_low))
    is_google = bool(re.search(r"\bgoogle\b", obj_low)) and not is_yt and "google chrome" not in obj_low
    should_play = bool(re.search(r"\b(play|watch|listen)\b", obj_low))

    query: str | None = None
    patterns = [
        r"(?:search|find|look\s+up|look\s+for|play|watch|listen\s+to)\s+(?:for\s+)?(?:a\s+song\s+)?(?:called\s+|named\s+)?['\"]?(.+?)['\"]?\s+(?:on|in|at)\s+(?:youtube|yt|google)\b",
        r"\b(?:youtube|yt|google)\b.*?(?:search|find|look\s+up|play|watch)\s+(?:for\s+)?(?:a\s+song\s+)?['\"]?(.+?)['\"]?$",
        r"\b(?:on|in)\s+(?:youtube|yt|google)\s+(?:search|find|play)\s+(?:for\s+)?['\"]?(.+?)['\"]?$",
        r"(?:search|find|play)\s+(?:for\s+)?['\"]?(.+?)['\"]?$",
    ]

    if is_yt or is_google:
        for pat in patterns:
            m = re.search(pat, cleaned, re.IGNORECASE)
            if m:
                candidate = re.sub(r"^(?:on\s+|in\s+)?(?:youtube|yt|google)\s+(?:for\s+)?", "", m.group(1).strip(), flags=re.IGNORECASE).strip()
                candidate = re.sub(
                    r"\s+(?:and\s+stop|and\s+play\s+it|and\s+click\s+it|on\s+youtube|on\s+yt|in\s+chrome|please)\.?$",
                    "",
                    candidate,
                    flags=re.IGNORECASE,
                ).strip(" .,'\"")
                if candidate and candidate.lower() not in {"youtube", "yt", "google", "a song", "song"}:
                    query = candidate
                    break

    if is_yt:
        if query:
            encoded = quote_plus(query)
            return {
                "matched": True,
                "site": "YouTube",
                "url": f"https://www.youtube.com/results?search_query={encoded}",
                "query": query,
                "keyword": query.split()[0].lower(),
                "should_play": should_play,
            }
        return {
            "matched": True,
            "site": "YouTube",
            "url": "https://www.youtube.com",
            "query": None,
            "keyword": "youtube",
            "should_play": False,
        }

    if is_google and query:
        encoded = quote_plus(query)
        return {
            "matched": True,
            "site": "Google",
            "url": f"https://www.google.com/search?q={encoded}",
            "query": query,
            "keyword": query.split()[0].lower(),
            "should_play": False,
        }

    return {"matched": False}


def parse_generic_app_open_intent(objective: str) -> dict[str, str | None]:
    """
    Matches universal commands like:
    - 'Open Spotify.'
    - 'Launch Discord'
    - 'Open Calculator'
    - 'Open Antigravity'
    - 'Open Notepad and write hello world'
    """
    cleaned = objective.strip()
    m = re.match(
        r"^(?:please\s+)?(?:open|launch|start|activate|focus|switch\s+to|run)\s+(?:my\s+|the\s+)?([a-zA-Z0-9 _\-]+?)(?:\s+and\s+(?:write|type)\s+(.+?))?(?:\s+and\s+stop)?[.!]*$",
        cleaned,
        re.IGNORECASE,
    )
    if not m:
        return {"app_name": None, "type_text": None}

    app_name = m.group(1).strip(" .,'\"")
    text_to_type = m.group(2).strip(" .,'\"") if m.group(2) else None
    if text_to_type:
        text_to_type = re.sub(r"\s*do\s+not\s+save.*$", "", text_to_type, flags=re.IGNORECASE).strip(" .,'\"")

    return {"app_name": app_name, "type_text": text_to_type}


def parse_email_intent(objective: str) -> dict[str, Any]:
    cleaned = objective.strip()
    m = re.match(
        r"^(?:please\s+)?(?:mail|email|send\s+(?:an?\s+)?(?:email|message)\s+to|draft\s+(?:an?\s+)?email\s+to)\s+([a-zA-Z0-9 _\-]+?)(?:\s+(?:that|saying|about)\s+(.+?))?[.!]?$",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        recipient = m.group(1).strip(" .,'\"")
        message = m.group(2).strip(" .,'\"") if m.group(2) else "I will follow up shortly."
        return {"matched": True, "recipient": recipient, "message": message}
    return {"matched": False}


def parse_antigravity_prompt_intent(objective: str) -> dict[str, Any]:
    cleaned = objective.strip()
    obj_low = cleaned.lower()
    if "antigravity" in obj_low and ("prompt" in obj_low or "write" in obj_low or "gaming" in obj_low or "make" in obj_low):
        m = re.search(
            r"(?:prompt\s+(?:for|to\s+make|about)\s+|write\s+(?:a\s+)?prompt\s+(?:for|to\s+make|about)?\s*)(.+?)$",
            cleaned,
            re.IGNORECASE,
        )
        topic = m.group(1).strip(" .,'\"") if m else "gaming website"
        return {"matched": True, "topic": topic}
    return {"matched": False}


def extract_target_url_and_keyword(objective: str) -> tuple[str | None, str | None]:
    obj_low = objective.lower()
    explicit_url = re.search(r"(https?://[^\s,]+)", objective, re.IGNORECASE)
    if explicit_url:
        url = explicit_url.group(1).rstrip(".")
        domain = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].split(".")[0]
        return url, domain.lower()

    for site_key, (url, kw) in KNOWN_SITES.items():
        if site_key in obj_low:
            return url, kw

    domain_match = re.search(r"\b([a-zA-Z0-9-]+\.(com|org|io|dev|net|ai))\b", obj_low)
    if domain_match:
        dom = domain_match.group(1)
        return f"https://{dom}", dom.split(".")[0]

    return None, None


class ZeroCostModelRouter:
    def __init__(self) -> None:
        self._rate_limited_providers: set[str] = set()
        self._registry: dict[str, dict[str, str]] = {
            "deterministic_local": {
                "display_name": "RALLY Universal Desktop & Web Planner (Zero-Cost / Local)",
                "model_name": "rally-universal-planner-v2",
                "billing_mode": "local",
            },
            "ollama": {
                "display_name": "Ollama Local Runtime",
                "model_name": settings.RALLY_MODEL_NAME,
                "billing_mode": "local",
            },
            "paid_cloud_example": {
                "display_name": "Commercial Paid Cloud API",
                "model_name": "paid-frontier-v1",
                "billing_mode": "paid_api",
            },
        }

    def mark_rate_limited(self, provider_id: str) -> None:
        self._rate_limited_providers.add(provider_id)

    def clear_rate_limit(self, provider_id: str) -> None:
        self._rate_limited_providers.discard(provider_id)

    def assert_cost_policy(self, provider_id: str) -> None:
        info = self._registry.get(provider_id)
        if not info:
            raise PaidProviderBlockedError(f"Unknown model provider '{provider_id}' blocked by zero-cost policy.")
        billing = info["billing_mode"]
        if settings.RALLY_SPEND_MODE == "zero_cost_only" and billing not in {"local", "free_quota"}:
            raise PaidProviderBlockedError(
                f"Provider '{provider_id}' has billing_mode='{billing}' and is disabled in zero_cost_only mode."
            )
        if provider_id in self._rate_limited_providers:
            raise QuotaExhaustedError(f"Free quota for '{provider_id}' is exhausted. Pausing task.")

    async def list_providers_health(self) -> list[ProviderHealth]:
        results: list[ProviderHealth] = []
        for pid, meta in self._registry.items():
            rate_limited = pid in self._rate_limited_providers
            billing = meta["billing_mode"]
            allowed = (billing in {"local", "free_quota"}) or settings.RALLY_SPEND_MODE != "zero_cost_only"
            if pid == "ollama":
                avail = False
                msg = "Ollama server not detected at 127.0.0.1:11434"
                try:
                    async with httpx.AsyncClient(timeout=1.0) as client:
                        resp = await client.get(f"{settings.RALLY_MODEL_BASE_URL}/api/tags")
                        if resp.status_code == 200:
                            avail = True
                            msg = "Connected to local Ollama daemon"
                except Exception:
                    pass
                results.append(ProviderHealth(pid, meta["display_name"], meta["model_name"], billing, True, avail and not rate_limited, rate_limited, msg))
            else:
                results.append(
                    ProviderHealth(
                        pid,
                        meta["display_name"],
                        meta["model_name"],
                        billing,
                        True,
                        allowed and not rate_limited,
                        rate_limited,
                        "Ready" if allowed else "Blocked by zero_cost_only policy",
                    )
                )
        return results

    async def generate_plan(self, req: MissionCreateRequest, active_provider: str | None = None) -> MissionPlanSchema:
        provider_id = active_provider or settings.RALLY_MODEL_PROVIDER
        self.assert_cost_policy(provider_id)
        obj_lower = req.objective.lower()

        # 1. Antigravity Coding Prompt Workflow
        ag_intent = parse_antigravity_prompt_intent(req.objective)
        if ag_intent.get("matched"):
            from app.database import SessionLocal
            from app.resolver import goal_resolver
            with SessionLocal() as db_session:
                res_goal = goal_resolver.resolve_goal("code_prompt", {"topic": ag_intent["topic"]}, db_session)
            expanded_prompt = res_goal.get("expanded_prompt", f"Build a modern {ag_intent['topic']} using React 18, TypeScript, and Tailwind CSS.")
            steps = [
                StepPlanSpec(
                    title="Launch or focus Antigravity IDE",
                    description="Activate Antigravity on Windows desktop station.",
                    assigned_role="DesktopOperator",
                    action_type="launch_app",
                    action_payload={"app_id": "antigravity"},
                    risk_tier=1,
                ),
                StepPlanSpec(
                    title="Inject expanded prompt into Antigravity",
                    description="Paste detailed coding prompt via safe clipboard injection.",
                    assigned_role="DesktopOperator",
                    action_type="type_text",
                    action_payload={"text": expanded_prompt, "method": "clipboard_paste", "target_app": "antigravity"},
                    risk_tier=1,
                ),
                StepPlanSpec(
                    title="Capture verification screenshot of Antigravity",
                    description="Record visual proof of prompt delivery in Antigravity.",
                    assigned_role="Verifier",
                    action_type="capture_screenshot",
                    action_payload={"scope": "full"},
                    risk_tier=0,
                ),
            ]
            return MissionPlanSchema(
                title=req.title or f"Antigravity: {ag_intent['topic']}",
                mission_type="desktop",
                success_criteria=["Antigravity active in foreground", "Prompt pasted via clipboard", "Evidence screenshot recorded"],
                constraints=["Preserve user clipboard"],
                steps=steps,
            )

        # 2. Email draft/send workflow (with contact resolution and style profile)
        mail_intent = parse_email_intent(req.objective)
        if mail_intent.get("matched") or (("email" in obj_lower or "gmail" in obj_lower) and ("draft" in obj_lower or "send" in obj_lower or "reply" in obj_lower)):
            from app.database import SessionLocal
            from app.resolver import goal_resolver
            rec_raw = mail_intent.get("recipient") if mail_intent.get("matched") else "Rohan"
            msg_raw = mail_intent.get("message") if mail_intent.get("matched") else req.objective
            with SessionLocal() as db_session:
                res_goal = goal_resolver.resolve_goal("send_message", {"recipient": rec_raw, "message": msg_raw}, db_session)

            rec_name = res_goal.get("resolved_recipient_name", rec_raw)
            rec_email = res_goal.get("resolved_recipient_email", "rohan.sharma@example.com")
            subject = res_goal.get("resolved_subject", "Running a bit late today")
            body = res_goal.get("resolved_body", "Hi Rohan,\n\nI wanted to let you know that I'll be running a bit late today.\n\nBest,\nUser")

            steps = [
                StepPlanSpec(
                    title="Observe desktop state before email drafting",
                    description="Check active browser windows before composing email.",
                    assigned_role="DesktopOperator",
                    action_type="read_ui_tree",
                    action_payload={},
                    risk_tier=0,
                ),
                StepPlanSpec(
                    title=f"Open Gmail compose draft for {rec_name} ({rec_email})",
                    description=f"Prepare email draft in user's writing style via direct Gmail compose URL.",
                    assigned_role="DesktopOperator",
                    action_type="compose_email_draft",
                    action_payload={
                        "recipient": rec_email,
                        "subject": subject,
                        "body": body,
                        "compose_draft": True,
                    },
                    risk_tier=1,
                ),
                StepPlanSpec(
                    title=f"Send email to {rec_name} ({rec_email})",
                    description="Transmit drafted email externally (Requires Tier 2 explicit human approval).",
                    assigned_role="DesktopOperator",
                    action_type="send_external_message",
                    action_payload={
                        "recipient": rec_email,
                        "subject": subject,
                        "body": body,
                    },
                    risk_tier=2,
                    requires_approval=True,
                ),
                StepPlanSpec(
                    title="Capture verification screenshot of email draft",
                    description="Capture desktop screenshot of Gmail draft.",
                    assigned_role="Verifier",
                    action_type="capture_screenshot",
                    action_payload={"scope": "full"},
                    risk_tier=0,
                ),
            ]
            return MissionPlanSchema(
                title=req.title or f"Email Draft: {rec_name} ({subject})",
                mission_type="browser",
                success_criteria=[f"Draft prepared for {rec_name}", "Halt for Tier 2 approval before transmission"],
                constraints=["Draft, don't send without approval", "Write in user's style profile"],
                steps=steps,
            )

        # 3. Spotify Desktop App Control (Open Spotify / Play song on Spotify)
        sp_intent = parse_spotify_intent(req.objective)
        if sp_intent.get("matched"):
            sp_query = sp_intent.get("query")
            sp_play = bool(sp_intent.get("should_play"))
            steps = [
                StepPlanSpec(
                    title="Observe desktop state before Spotify action",
                    description="Check running windows and taskbar processes for Spotify.",
                    assigned_role="DesktopOperator",
                    action_type="read_ui_tree",
                    action_payload={},
                    risk_tier=0,
                ),
                StepPlanSpec(
                    title=f"Open & Focus Spotify Desktop App" + (f" (Search: '{sp_query}')" if sp_query else ""),
                    description="Activate or launch the Windows Spotify application and bring it to the foreground.",
                    assigned_role="DesktopOperator",
                    action_type="launch_app",
                    action_payload={
                        "app_id": "spotify",
                        "spotify_control": True,
                        "query": sp_query,
                        "should_play": sp_play,
                    },
                    risk_tier=1,
                ),
                StepPlanSpec(
                    title="Capture verification screenshot of Spotify",
                    description="Capture desktop screenshot confirming Spotify is open in the foreground.",
                    assigned_role="Verifier",
                    action_type="capture_screenshot",
                    action_payload={"scope": "full"},
                    risk_tier=0,
                ),
            ]
            return MissionPlanSchema(
                title=req.title or (f"Spotify: {sp_query}" if sp_query else "Open Spotify Desktop App"),
                mission_type="desktop",
                success_criteria=["Spotify window opened and brought to foreground", "Evidence screenshot captured"],
                constraints=["Local desktop execution"],
                steps=steps,
            )

        # 4. YouTube / Google Search & Play Workflows
        yt_search = parse_youtube_or_web_search(req.objective)
        if yt_search.get("matched"):
            from app.database import SessionLocal
            from app.resolver import goal_resolver
            query_str = yt_search["query"]
            final_query = query_str
            if query_str:
                with SessionLocal() as db_session:
                    res_goal = goal_resolver.resolve_goal("play_media", {"query": query_str}, db_session)
                    if res_goal.get("target_query"):
                        final_query = res_goal["target_query"]
            encoded = quote_plus(final_query) if final_query else ""
            url_to_open = (
                f"https://www.youtube.com/results?search_query={encoded}"
                if yt_search["site"] == "YouTube"
                else f"https://www.google.com/search?q={encoded}"
            )
            kw_to_verify = final_query.split()[0].lower() if final_query else "youtube"
            query_str = final_query
            should_play = bool(yt_search["should_play"])
            should_play = bool(yt_search["should_play"])

            steps = [
                StepPlanSpec(
                    title="Observe current Windows desktop state",
                    description="Enumerate visible windows before controlling Google Chrome.",
                    assigned_role="DesktopOperator",
                    action_type="read_ui_tree",
                    action_payload={},
                    risk_tier=0,
                ),
                StepPlanSpec(
                    title=(
                        f"Search {yt_search['site']} for '{query_str}'"
                        if query_str
                        else f"Open {yt_search['site']} in Google Chrome"
                    ),
                    description=f"Navigate Google Chrome to {url_to_open} and verify window state.",
                    assigned_role="DesktopOperator",
                    action_type="open_url",
                    action_payload={
                        "url": url_to_open,
                        "expected_title_keyword": kw_to_verify,
                        "search_query": query_str,
                        "use_desktop_chrome": True,
                    },
                    risk_tier=1,
                ),
            ]
            if query_str and should_play:
                steps.append(
                    StepPlanSpec(
                        title=f"Select and play '{query_str}' video on YouTube",
                        description="Use Windows UI Automation on Google Chrome to click the top matching video result.",
                        assigned_role="DesktopOperator",
                        action_type="click",
                        action_payload={"target_kind": "youtube_first_video", "query": query_str},
                        risk_tier=1,
                    )
                )
            steps.append(
                StepPlanSpec(
                    title="Capture independent verification screenshot",
                    description="Capture visible desktop screenshot confirming search results / playback in Chrome.",
                    assigned_role="Verifier",
                    action_type="capture_screenshot",
                    action_payload={"scope": "full"},
                    risk_tier=0,
                )
            )
            return MissionPlanSchema(
                title=req.title or (f"YouTube Search: {query_str}" if query_str else f"Chrome Navigation: {url_to_open}"),
                mission_type="desktop",
                success_criteria=[f"Opened {yt_search['site']} in Chrome", "Verified final desktop state"],
                constraints=["Zero-cost local execution"],
                steps=steps,
            )

        # 4. Generic URL / Website Navigation
        target_url, target_kw = extract_target_url_and_keyword(req.objective)
        if "navigate" in obj_lower or "website" in obj_lower or target_url:
            url_to_open = target_url or "https://www.google.com"
            kw_to_verify = target_kw or "chrome"
            steps = [
                StepPlanSpec(
                    title="Observe current Windows desktop state",
                    description="Enumerate visible windows before launching or activating Google Chrome.",
                    assigned_role="DesktopOperator",
                    action_type="read_ui_tree",
                    action_payload={},
                    risk_tier=0,
                ),
                StepPlanSpec(
                    title=f"Launch/Activate Google Chrome & Navigate to {url_to_open}",
                    description=f"Open real Windows Google Chrome application and load {url_to_open}.",
                    assigned_role="DesktopOperator",
                    action_type="open_url",
                    action_payload={"url": url_to_open, "expected_title_keyword": kw_to_verify, "use_desktop_chrome": True},
                    risk_tier=1,
                ),
                StepPlanSpec(
                    title="Capture independent verification screenshot",
                    description="Capture visible desktop screenshot confirming target page is loaded.",
                    assigned_role="Verifier",
                    action_type="capture_screenshot",
                    action_payload={"scope": "full"},
                    risk_tier=0,
                ),
            ]
            return MissionPlanSchema(
                title=req.title or f"Chrome Navigation: {url_to_open}",
                mission_type="desktop",
                success_criteria=[f"Navigated to {url_to_open}"],
                constraints=["Stop after verification"],
                steps=steps,
            )

        # 5. Universal Windows App Open / Focus / Type (ANY App on the user's PC!)
        generic_app = parse_generic_app_open_intent(req.objective)
        if generic_app["app_name"]:
            app_name = str(generic_app["app_name"])
            text_to_type = generic_app["type_text"]
            steps = [
                StepPlanSpec(
                    title=f"Launch or Focus '{app_name}' on Windows Desktop",
                    description=f"Locate '{app_name}' across open taskbar windows, Windows Start Menu (Get-StartApps), URI protocols, or Program Files, and bring it to the foreground.",
                    assigned_role="DesktopOperator",
                    action_type="launch_app",
                    action_payload={"app_id": app_name},
                    risk_tier=1,
                )
            ]
            if text_to_type or ("notepad" in app_name.lower() and "write" in obj_lower):
                steps.append(
                    StepPlanSpec(
                        title=f"Type requested text into {app_name}",
                        description="Send keystrokes via Windows UI Automation.",
                        assigned_role="DesktopOperator",
                        action_type="type_text",
                        action_payload={"text": text_to_type or "RALLY test\nTyped by RALLY 2.0 Desktop Operator."},
                        risk_tier=1,
                    )
                )
            steps.append(
                StepPlanSpec(
                    title=f"Capture verification screenshot of {app_name}",
                    description=f"Capture visible desktop state proving {app_name} is open and active.",
                    assigned_role="Verifier",
                    action_type="capture_screenshot",
                    action_payload={"scope": "full"},
                    risk_tier=0,
                )
            )
            return MissionPlanSchema(
                title=req.title or f"Open Application: {app_name}",
                mission_type="desktop",
                success_criteria=[f"'{app_name}' launched or focused in foreground", "Verification screenshot recorded"],
                constraints=["Verify real process/window exists"],
                steps=steps,
            )

        # 6. Build Supervisor missions
        if req.mission_type == "build" or "build" in obj_lower or req.workspace_path:
            steps = [
                StepPlanSpec(
                    title="Inspect workspace and Git baseline",
                    description="Verify project directory, package manifest, and Git working tree.",
                    assigned_role="BuildSupervisor",
                    action_type="open_folder",
                    action_payload={"workspace_path": req.workspace_path or "."},
                    risk_tier=0,
                ),
                StepPlanSpec(
                    title="Execute quality checks",
                    description="Run project-local test/build command and capture exit codes.",
                    assigned_role="BuildSupervisor",
                    action_type="run_approved_command",
                    action_payload={"workspace_path": req.workspace_path or ".", "command": ["pytest"]},
                    risk_tier=1,
                ),
            ]
            return MissionPlanSchema(
                title=req.title or "Autonomous Build & Verification Mission",
                mission_type="build",
                success_criteria=["Workspace inspected", "Build/tests verified with evidence"],
                constraints=["One writer per workspace", "Zero-cost coding agent only"],
                steps=steps,
            )

        # 7. Fallback desktop observation
        steps = [
            StepPlanSpec(
                title="Observe active desktop windows",
                description="Inspect visible open windows on the Windows desktop.",
                assigned_role="DesktopOperator",
                action_type="read_ui_tree",
                action_payload={},
                risk_tier=0,
            ),
            StepPlanSpec(
                title="Capture desktop verification screenshot",
                description="Record visual evidence of current desktop state.",
                assigned_role="Verifier",
                action_type="capture_screenshot",
                action_payload={"scope": "full"},
                risk_tier=0,
            ),
        ]
        return MissionPlanSchema(
            title=req.title or f"Mission: {req.objective[:48]}",
            mission_type=req.mission_type,
            success_criteria=["Desktop state observed"],
            constraints=["Conservative execution policy"],
            steps=steps,
        )


model_router = ZeroCostModelRouter()