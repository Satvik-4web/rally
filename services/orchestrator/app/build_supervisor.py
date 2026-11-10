import json
import subprocess
from pathlib import Path
from typing import Any
from sqlalchemy.orm import Session
from app.coding_agents import CodingTaskRequest, coding_agent_registry
from app.events import event_bus
from app.models import DefectRecord, EvidenceRecord, Mission, new_id
from app.policies import detect_prompt_injection, redact_secrets, validate_workspace_command


class BuildSupervisor:
    def inspect_workspace(self, workspace_path: str) -> dict[str, Any]:
        ws = Path(workspace_path).resolve()
        if not ws.exists() or not ws.is_dir():
            raise ValueError(f"Workspace path does not exist: {workspace_path}")
        files = [str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts][:100]
        framework = "react" if (ws / "package.json").exists() else "python"
        return {
            "workspace_path": str(ws),
            "exists": True,
            "framework": framework,
            "file_count": len(files),
            "sample_files": files[:25],
            "is_git_repo": (ws / ".git").exists(),
        }

    def run_workspace_check(self, db: Session, mission: Mission, command: list[str] | str, check_kind: str = "test") -> dict[str, Any]:
        ws = mission.workspace_path or "."
        validated_tokens = validate_workspace_command(command, ws)
        mission.build_supervisor_state = "QUALITY_CHECKS"
        db.commit()

        proc = subprocess.run(validated_tokens, cwd=str(Path(ws).resolve()), capture_output=True, text=True, timeout=90, check=False)
        combined_output = redact_secrets(f"{proc.stdout}\n{proc.stderr}".strip())

        evd = EvidenceRecord(
            id=new_id("evd"),
            mission_id=mission.id,
            evidence_type="test_result" if check_kind == "test" else "process_result",
            metadata_json=json.dumps({"check_kind": check_kind, "command": validated_tokens, "exit_code": proc.returncode, "output_excerpt": combined_output[:1500]}),
        )
        db.add(evd)

        defect_created = None
        if proc.returncode != 0:
            mission.build_supervisor_state = "DEFECT_TRIAGE"
            defect = DefectRecord(
                id=new_id("def"),
                mission_id=mission.id,
                severity="blocker" if check_kind == "build" else "major",
                title=f"{check_kind.upper()} command failed with exit code {proc.returncode}",
                expected_behavior=f"Command {' '.join(validated_tokens)} exits with 0.",
                actual_behavior=combined_output[:600] or f"Exit code {proc.returncode}",
                affected_target=ws,
                corrective_prompt=f"Fix failing {check_kind} in {ws}:\n{combined_output[:1000]}",
                evidence_id=evd.id,
                resolved=False,
            )
            db.add(defect)
            db.commit()
            defect_created = {"id": defect.id, "severity": defect.severity, "title": defect.title, "corrective_prompt": defect.corrective_prompt}
        else:
            mission.build_supervisor_state = "FINAL_REPORT"
            db.commit()

        event_bus.emit(db, "build.check.completed", {"check_kind": check_kind, "exit_code": proc.returncode}, mission_id=mission.id)
        return {"check_kind": check_kind, "exit_code": proc.returncode, "passed": proc.returncode == 0, "evidence_id": evd.id, "defect": defect_created}

    async def request_correction_cycle(self, db: Session, mission: Mission) -> dict[str, Any]:
        if mission.correction_iteration >= mission.max_correction_iterations:
            mission.build_supervisor_state = "BLOCKED"
            mission.status = "PARTIALLY_COMPLETED"
            db.commit()
            return {"dispatched": False, "reason": "Max correction iterations reached."}

        open_defects = db.query(DefectRecord).filter(DefectRecord.mission_id == mission.id, DefectRecord.resolved.is_(False)).all()
        if not open_defects:
            return {"dispatched": False, "reason": "No open defects."}

        primary = open_defects[0]
        mission.correction_iteration += 1
        mission.build_supervisor_state = "CODING_AGENT_RUNNING"
        db.commit()

        res = await coding_agent_registry.dispatch_corrective_task(
            mission.selected_coding_agent or "ollama_cli",
            CodingTaskRequest(mission.id, mission.current_step_id, mission.workspace_path or ".", primary.corrective_prompt, []),
        )
        return {"dispatched": True, "iteration": mission.correction_iteration, "defect_id": primary.id, "agent_result": res}

    def generate_truthful_report(self, db: Session, mission: Mission) -> dict[str, Any]:
        evidences = db.query(EvidenceRecord).filter(EvidenceRecord.mission_id == mission.id).all()
        defects = db.query(DefectRecord).filter(DefectRecord.mission_id == mission.id).all()
        tests_verified = any(e.evidence_type == "test_result" and json.loads(e.metadata_json).get("exit_code") == 0 for e in evidences)
        unresolved = [d for d in defects if not d.resolved]
        return {
            "mission_id": mission.id,
            "status": mission.status,
            "verification_matrix": {
                "tests_verified": tests_verified,
                "acceptance_criteria_verified": tests_verified and len(unresolved) == 0,
            },
            "unresolved_defects": [{"id": d.id, "title": d.title, "severity": d.severity} for d in unresolved],
        }


build_supervisor = BuildSupervisor()