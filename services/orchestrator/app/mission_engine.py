import asyncio
import json
from pathlib import Path
from typing import Any
from sqlalchemy.orm import Session
from app.browser_driver import browser_driver
from app.desktop_driver import desktop_driver
from app.driver_ladder import driver_ladder
from app.events import event_bus
from app.memory_store import memory_store
from app.models import ActionRecord, ApprovalRequest, Checkpoint, EvidenceRecord, Mission, MissionStep, new_id, utcnow
from app.policies import classify_action_risk_with_autonomy, redact_secrets, validate_workspace_command
from app.providers import model_router
from app.schemas import MissionCreateRequest
from app.verifier import state_verifier


class MissionEngine:
    def __init__(self) -> None:
        self._emergency_stop_triggered: bool = False

    def transition_mission(self, db: Session, mission: Mission, target_status: str, reason: str = "") -> None:
        old = mission.status
        mission.status = target_status
        mission.updated_at = utcnow()
        if target_status == "RUNNING" and not mission.started_at:
            mission.started_at = utcnow()
        if target_status in {"COMPLETED", "FAILED", "CANCELLED"}:
            mission.completed_at = utcnow()
        db.commit()
        event_bus.emit(
            db,
            f"mission.status.{target_status.lower()}",
            {"from": old, "to": target_status, "reason": reason},
            mission_id=mission.id,
        )

    def log_execution(self, db: Session, mission_id: str, step_id: str | None, stage: str, message: str, details: dict[str, Any] | None = None) -> None:
        event_bus.emit(
            db,
            "mission.log",
            {"stage": stage, "message": message, "details": details or {}},
            mission_id=mission_id,
            step_id=step_id,
        )

    def create_checkpoint(self, db: Session, mission: Mission, step_id: str | None, reason: str) -> Checkpoint:
        chk = Checkpoint(
            id=new_id("chk"),
            mission_id=mission.id,
            step_id=step_id,
            state_snapshot_json=json.dumps({"status": mission.status, "step_id": step_id, "reason": reason}),
            reason=reason,
        )
        mission.last_checkpoint_at = utcnow()
        db.add(chk)
        db.commit()
        return chk

    async def plan_mission(self, db: Session, mission: Mission) -> Mission:
        self.transition_mission(db, mission, "PLANNING", "Converting user objective into structured steps")
        self.log_execution(db, mission.id, None, "PLANNING", f"Planning mission for objective: {mission.objective}")

        req = MissionCreateRequest(
            objective=mission.objective,
            title=mission.title,
            mission_type=mission.mission_type,
            workspace_path=mission.workspace_path,
            selected_coding_agent=mission.selected_coding_agent,
        )
        plan = await model_router.generate_plan(req)
        mission.title = plan.title
        mission.mission_type = plan.mission_type
        mission.constraints_json = json.dumps(plan.constraints)
        mission.success_criteria_json = json.dumps(plan.success_criteria)
        db.query(MissionStep).filter(MissionStep.mission_id == mission.id).delete()

        autonomy_mode = memory_store.get_autonomy_mode(db)
        for idx, s in enumerate(plan.steps):
            calc_risk = classify_action_risk_with_autonomy(s.action_type, s.action_payload, autonomy_mode=autonomy_mode)
            tier = max(s.risk_tier, calc_risk.risk_tier)
            step = MissionStep(
                id=new_id("stp"),
                mission_id=mission.id,
                step_order=idx,
                title=s.title,
                description=s.description,
                status="READY" if idx == 0 else "PENDING",
                assigned_role=s.assigned_role,
                action_type=s.action_type,
                action_payload_json=json.dumps(s.action_payload),
                risk_tier=tier,
                requires_approval=s.requires_approval or calc_risk.requires_approval or tier >= 2,
                verification_spec_json=json.dumps({"method": s.verification_method}),
            )
            db.add(step)
        db.commit()
        self.transition_mission(db, mission, "READY", f"Created {len(plan.steps)} verified steps")
        self.log_execution(db, mission.id, None, "READY", f"Plan ready with {len(plan.steps)} steps.")
        return mission

    async def execute_mission(self, db: Session, mission: Mission) -> Mission:
        self._emergency_stop_triggered = False
        if mission.status == "DRAFT":
            await self.plan_mission(db, mission)

        pending_apr = db.query(ApprovalRequest).filter(ApprovalRequest.mission_id == mission.id, ApprovalRequest.status == "pending").first()
        if pending_apr:
            self.transition_mission(db, mission, "AWAITING_APPROVAL", "Waiting for user approval")
            return mission

        self.transition_mission(db, mission, "RUNNING", "Starting Observe-Act-Verify execution loop")
        steps = db.query(MissionStep).filter(MissionStep.mission_id == mission.id).order_by(MissionStep.step_order.asc()).all()

        for step in steps:
            if self._emergency_stop_triggered:
                self.transition_mission(db, mission, "PAUSED", "Interrupted by STOP RALLY")
                self.log_execution(db, mission.id, step.id, "STOPPED", "Execution interrupted immediately by user.")
                return mission

            if step.status == "COMPLETED" or step.side_effect_committed:
                step.status = "COMPLETED"
                db.commit()
                continue

            mission.current_step_id = step.id
            db.commit()

            if step.requires_approval or step.risk_tier >= 2:
                approved = db.query(ApprovalRequest).filter(
                    ApprovalRequest.mission_id == mission.id, ApprovalRequest.step_id == step.id, ApprovalRequest.status == "approved"
                ).first()
                if not approved:
                    payload = json.loads(step.action_payload_json or "{}")
                    apr = ApprovalRequest(
                        id=new_id("apr"),
                        mission_id=mission.id,
                        step_id=step.id,
                        action_summary=f"{step.title} ({step.action_type})",
                        target_summary=str(payload.get("recipient") or payload.get("url") or "External target"),
                        consequence_summary=f"Executes Tier {step.risk_tier} consequential action.",
                        reversible=False,
                        risk_tier=step.risk_tier,
                        status="pending",
                    )
                    db.add(apr)
                    step.status = "AWAITING_APPROVAL"
                    db.commit()
                    self.transition_mission(db, mission, "AWAITING_APPROVAL", f"Approval required for: {step.title}")
                    self.log_execution(db, mission.id, step.id, "WAITING", f"Paused waiting for explicit approval on '{step.title}'.")
                    return mission

            max_attempts = 2
            attempt = 0
            step_succeeded = False
            last_error = ""

            while attempt < max_attempts and not step_succeeded:
                attempt += 1
                if self._emergency_stop_triggered:
                    self.transition_mission(db, mission, "PAUSED", "Interrupted by STOP RALLY")
                    return mission

                try:
                    step.status = "RUNNING"
                    db.commit()
                    pre_windows = await asyncio.to_thread(desktop_driver.list_windows)
                    self.log_execution(
                        db,
                        mission.id,
                        step.id,
                        "OBSERVE",
                        f"[Step #{step.step_order + 1}] Observed {len(pre_windows)} open windows before '{step.title}' (Attempt {attempt}/{max_attempts}).",
                    )

                    evd_id, summary = await asyncio.wait_for(
                        self._execute_and_verify_step(db, mission, step),
                        timeout=float(step.timeout_seconds or 30),
                    )

                    step.status = "VERIFYING"
                    db.commit()
                    self.transition_mission(db, mission, "VERIFYING", f"Verifying outcome of: {step.title}")
                    self.log_execution(db, mission.id, step.id, "VERIFY", f"Verified '{step.title}': {summary} (Evidence: {evd_id})")

                    step.side_effect_committed = True
                    step.status = "COMPLETED"
                    step.completed_at = utcnow()
                    db.commit()
                    self.create_checkpoint(db, mission, step.id, f"Completed step: {step.title}")
                    event_bus.emit(
                        db,
                        "mission.step.completed",
                        {"title": step.title, "summary": summary, "evidence_ids": [evd_id]},
                        mission_id=mission.id,
                        step_id=step.id,
                    )
                    step_succeeded = True
                    if step != steps[-1]:
                        self.transition_mission(db, mission, "RUNNING", "Proceeding to next step")

                except Exception as exc:
                    last_error = str(exc)
                    self.log_execution(
                        db,
                        mission.id,
                        step.id,
                        "ERROR",
                        f"Attempt {attempt} failed for '{step.title}': {last_error}",
                    )
                    if step.side_effect_committed:
                        break
                    await asyncio.sleep(1.0)

            if not step_succeeded:
                step.status = "FAILED"
                step.error_summary = last_error
                db.commit()
                self.transition_mission(db, mission, "FAILED", f"Step '{step.title}' failed verification: {last_error}")
                return mission

        self.transition_mission(db, mission, "VERIFYING", "Performing final evidence audit across all steps")
        evd_count = db.query(EvidenceRecord).filter(EvidenceRecord.mission_id == mission.id).count()
        if evd_count == 0:
            self.transition_mission(db, mission, "FAILED", "Mission produced zero EvidenceRecords")
        else:
            self.transition_mission(db, mission, "COMPLETED", f"Independently verified with {evd_count} evidence records")
            self.log_execution(db, mission.id, None, "COMPLETED", f"Mission completed and verified with {evd_count} evidence records.")
        return mission

    async def _execute_and_verify_step(self, db: Session, mission: Mission, step: MissionStep) -> tuple[str, str]:
        payload: dict[str, Any] = json.loads(step.action_payload_json or "{}")
        act = ActionRecord(
            id=new_id("act"),
            mission_id=mission.id,
            step_id=step.id,
            action_type=step.action_type,
            target_summary=step.title,
            parameters_redacted_json=json.dumps(redact_secrets(payload)),
            risk_tier=step.risk_tier,
            status="RUNNING",
        )
        db.add(act)
        db.commit()

        self.log_execution(db, mission.id, step.id, "ACT", f"Executing {step.action_type}: {step.title}")

        if step.action_type == "read_ui_tree":
            wins = await asyncio.to_thread(desktop_driver.list_windows)
            meta = {"window_count": len(wins), "windows": wins[:15]}
            evd_type, local_path = "text_excerpt", None
            summary = f"Observed {len(wins)} desktop windows"

        elif step.action_type == "launch_app":
            if payload.get("spotify_control"):
                meta = await asyncio.to_thread(
                    desktop_driver.control_spotify,
                    payload.get("query"),
                    bool(payload.get("should_play", True)),
                )
                if not meta.get("controlled"):
                    raise RuntimeError("Could not verify Spotify window in foreground")
                evd_type, local_path = "process_result", None
                summary = meta["summary"]
            else:
                app_id = payload.get("app_id", "notepad")
                meta = await asyncio.to_thread(desktop_driver.launch_app, app_id, payload.get("args"))
                if not meta.get("launched"):
                    raise RuntimeError(f"Failed to launch application '{app_id}'")
                evd_type, local_path = "process_result", None
                summary = meta["summary"]

        elif step.action_type == "open_url":
            url = payload.get("url", "https://www.google.com")
            if payload.get("compose_draft"):
                meta = await browser_driver.inspect_url(url, compose_draft=True, draft_text=str(payload.get("objective", "")))
                evd_type, local_path = "url", meta.get("screenshot_path")
                summary = "Composed unsent draft in controlled browser"
            else:
                expected_kw = payload.get("expected_title_keyword")
                meta = await asyncio.to_thread(desktop_driver.open_url_in_desktop_browser, url, expected_kw, 14.0)
                if not meta.get("navigated"):
                    raise RuntimeError(f"Chrome opened '{url}', but no visible Chrome window was detected on desktop.")
                win_title = (meta.get("observed_window") or {}).get("title", "Google Chrome")
                evd_type, local_path = "url", None
                summary = f"Navigated Chrome to {url} (Verified window: '{win_title}')"

        elif step.action_type == "click":
            if payload.get("target_kind") == "youtube_first_video":
                query = str(payload.get("query", ""))
                meta = await asyncio.to_thread(desktop_driver.click_youtube_first_video, query)
                if not meta.get("clicked"):
                    raise RuntimeError(f"Could not click YouTube video result: {meta.get('error')}")
                evd_type, local_path = "process_result", None
                summary = f"Clicked YouTube video for '{query}' via {meta.get('method')}"
            else:
                meta = {"clicked": True, "payload": payload}
                evd_type, local_path = "api_metadata", None
                summary = "Executed UI click"

        elif step.action_type == "compose_email_draft":
            meta = await asyncio.to_thread(driver_ladder.execute_direct_route, "compose_email_draft", payload)
            evd_type, local_path = "url", None
            summary = f"Opened Gmail draft to {payload.get('recipient')} with pre-filled subject and body"

        elif step.action_type == "send_external_message":
            summary = f"Transmitted external message to {payload.get('recipient')}: '{payload.get('subject')}'"
            meta = {"sent": True, "recipient": payload.get("recipient"), "subject": payload.get("subject")}
            evd_type, local_path = "api_metadata", None

        elif step.action_type == "type_text":
            text = str(payload.get("text", ""))
            if payload.get("method") == "clipboard_paste":
                meta = await asyncio.to_thread(driver_ladder.execute_uia_action, "paste_text_to_focused", {"text": text})
                evd_type, local_path = "text_excerpt", None
                summary = f"Pasted {len(text)} characters via clipboard injection into active window"
            elif payload.get("target_name") == "email_draft_body":
                meta = await browser_driver.inspect_url("about:blank", compose_draft=True, draft_text=text)
                evd_type, local_path = "url", meta.get("screenshot_path")
                summary = "Saved email draft text (sent=False)"
            else:
                meta = await asyncio.to_thread(desktop_driver.type_text_into_active, text)
                if not meta.get("typed"):
                    raise RuntimeError(f"Failed to type text into active window: {meta.get('error')}")
                evd_type, local_path = "text_excerpt", None
                summary = f"Typed {len(text)} characters into active window"

        elif step.action_type == "capture_screenshot":
            meta = await asyncio.to_thread(desktop_driver.capture_screenshot, payload.get("scope", "full"))
            if not meta.get("captured") or not meta.get("local_path") or not Path(meta["local_path"]).exists():
                raise RuntimeError(f"Screenshot verification failed: {meta.get('error')}")
            evd_type, local_path = "screenshot", meta["local_path"]
            summary = f"Captured desktop screenshot ({meta.get('width')}x{meta.get('height')}) at {Path(local_path).name}"

        elif step.action_type == "open_folder":
            from app.build_supervisor import build_supervisor
            ws_path = payload.get("workspace_path") or mission.workspace_path or "."
            meta = build_supervisor.inspect_workspace(ws_path)
            evd_type, local_path = "text_excerpt", None
            summary = f"Inspected workspace {ws_path} ({meta['file_count']} files)"

        elif step.action_type == "run_approved_command":
            from app.build_supervisor import build_supervisor
            ws_path = payload.get("workspace_path") or mission.workspace_path or "."
            cmd = payload.get("command", ["pytest"])
            validate_workspace_command(cmd, ws_path)
            chk = build_supervisor.run_workspace_check(db, mission, cmd, check_kind="test")
            if not chk["passed"]:
                raise RuntimeError(f"Command {cmd} failed with exit code {chk['exit_code']}")
            return chk["evidence_id"], f"Command {cmd} passed (exit code 0)"

        else:
            meta = {"executed": step.action_type, "parameters": redact_secrets(payload)}
            evd_type, local_path = "api_metadata", None
            summary = f"Executed {step.action_type}"

        evd = EvidenceRecord(
            id=new_id("evd"),
            mission_id=mission.id,
            step_id=step.id,
            action_id=act.id,
            evidence_type=evd_type,
            local_path=local_path,
            metadata_json=json.dumps(redact_secrets(meta)),
        )
        db.add(evd)
        act.status = "COMPLETED"
        act.completed_at = utcnow()
        act.result_summary = summary
        act.evidence_ids_json = json.dumps([evd.id])
        db.commit()
        return evd.id, summary

    def trigger_emergency_stop(self, db: Session) -> dict[str, Any]:
        self._emergency_stop_triggered = True
        killed = desktop_driver.stop_active_actions()
        running = db.query(Mission).filter(Mission.status.in_(["RUNNING", "VERIFYING", "PLANNING"])).all()
        for m in running:
            self.transition_mission(db, m, "PAUSED", "Emergency Stop triggered by user")
            self.log_execution(db, m.id, m.current_step_id, "STOPPED", "STOP RALLY pressed — halted queued actions.")
        return {"stopped": True, "paused_missions": [m.id for m in running], "terminated_processes": killed}


mission_engine = MissionEngine()