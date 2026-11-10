import asyncio
import hmac
import json
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.build_supervisor import build_supervisor
from app.coding_agents import coding_agent_registry
from app.config import settings
from app.database import Base, engine, get_db
from app.desktop_driver import desktop_driver
from app.events import event_bus
from app.mission_engine import mission_engine
from app.models import ApprovalRequest, DefectRecord, EvidenceRecord, LedgerEvent, Mission, MissionStep, new_id, utcnow
from app.memory_store import memory_store
from app.providers import PaidProviderBlockedError, model_router
from app.schemas import ApprovalDecisionRequest, BuildInspectRequest, BuildMissionCreateRequest, MissionCreateRequest, RunCheckRequest
from app.security import (
    ALLOWED_ORIGINS,
    API_TOKEN,
    APPROVAL_SECRET,
    validate_host_and_origin,
    verify_approval_secret,
    verify_bearer_token,
)
from app.voice_service import voice_service


class VoiceCommandPayload(BaseModel):
    transcript: str
    force_awake: bool = False


class VoiceConfigPayload(BaseModel):
    wake_word: str | None = None
    volume: int | None = None
    mic_index: int | None = None
    mic_name: str | None = None
    windows_startup: bool | None = None


class ContactCreatePayload(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    relationship: str = "colleague"
    aliases: list[str] = []


class StyleProfilePayload(BaseModel):
    greeting: str | None = None
    sign_off: str | None = None
    formality: str | None = None
    tone: str | None = None
    use_hinglish: bool | None = None


class AutonomyPayload(BaseModel):
    mode: str


class CorrectionPayload(BaseModel):
    original_intent: str
    corrected_action: str
    tags: list[str] = []


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    voice_service.start_listening()
    yield


app = FastAPI(title="RALLY 2.0 Orchestrator API", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Allow CORS preflight requests
    if request.method == "OPTIONS":
        return await call_next(request)

    # Allow public endpoints (health check and OpenAPI specs)
    if request.url.path in {"/api/v1/health", "/docs", "/openapi.json", "/redoc"}:
        return await call_next(request)

    # 1. Enforce Host and Origin validation against DNS rebinding & cross-site hijacking
    try:
        validate_host_and_origin(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    # 2. Enforce Bearer token authentication
    auth_header = request.headers.get("authorization")
    token_param = request.query_params.get("token")
    extracted_token = None
    if auth_header and auth_header.startswith("Bearer "):
        extracted_token = auth_header[7:].strip()
    elif token_param:
        extracted_token = token_param.strip()

    if not extracted_token or not hmac.compare_digest(extracted_token, API_TOKEN):
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required: Missing or invalid RALLY bearer token."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await call_next(request)


def serialize_mission(db: Session, m: Mission) -> dict:
    steps = db.query(MissionStep).filter(MissionStep.mission_id == m.id).order_by(MissionStep.step_order.asc()).all()
    return {
        "id": m.id,
        "title": m.title,
        "objective": m.objective,
        "status": m.status,
        "mission_type": m.mission_type,
        "workspace_path": m.workspace_path,
        "selected_coding_agent": m.selected_coding_agent,
        "build_supervisor_state": m.build_supervisor_state,
        "correction_iteration": m.correction_iteration,
        "max_correction_iterations": m.max_correction_iterations,
        "constraints": json.loads(m.constraints_json or "[]"),
        "success_criteria": json.loads(m.success_criteria_json or "[]"),
        "steps": [
            {
                "id": s.id,
                "step_order": s.step_order,
                "title": s.title,
                "description": s.description,
                "status": s.status,
                "assigned_role": s.assigned_role,
                "action_type": s.action_type,
                "risk_tier": s.risk_tier,
                "requires_approval": s.requires_approval,
                "side_effect_committed": s.side_effect_committed,
            }
            for s in steps
        ],
    }


@app.get("/api/v1/health")
async def health_check():
    providers = await model_router.list_providers_health()
    return {
        "status": "ok",
        "spend_mode": settings.RALLY_SPEND_MODE,
        "zero_cost_enforced": settings.RALLY_SPEND_MODE == "zero_cost_only",
        "providers": [p.__dict__ for p in providers],
        "coding_agents": coding_agent_registry.discover_agents(),
        "voice": voice_service.get_status(),
    }


# --- Voice Activation & Conversational Endpoints ---

@app.get("/api/v1/voice/status")
def get_voice_status():
    return voice_service.get_status()


@app.get("/api/v1/voice/microphones")
def get_voice_microphones():
    return {"microphones": voice_service.list_microphones()}


@app.post("/api/v1/voice/start")
def start_voice_listening():
    return voice_service.start_listening()


@app.post("/api/v1/voice/wake")
def wake_voice_companion():
    return voice_service.wake_up("Hotkey / UI")


@app.post("/api/v1/voice/pause")
def pause_voice_listening():
    return voice_service.pause_listening()


@app.post("/api/v1/voice/test-mic")
def test_voice_microphone():
    return voice_service.test_microphone()


@app.post("/api/v1/voice/config")
def update_voice_config(cfg: VoiceConfigPayload):
    if cfg.wake_word is not None and cfg.wake_word.strip():
        voice_service.wake_word = cfg.wake_word.strip().lower()
    if cfg.volume is not None:
        voice_service.volume = max(0, min(100, cfg.volume))
    if cfg.mic_name is not None:
        voice_service.selected_mic_name = cfg.mic_name
        voice_service.selected_mic_index = cfg.mic_index
    if cfg.windows_startup is not None:
        voice_service.set_windows_startup(cfg.windows_startup)
    return voice_service.get_status()


@app.post("/api/v1/voice/utterance")
async def process_voice_utterance(payload: VoiceCommandPayload):
    return await voice_service.process_utterance(payload.transcript, force_awake=payload.force_awake)


@app.post("/api/v1/voice/transcribe")
async def transcribe_voice(request: Request, auto_execute: bool = True, force_awake: bool = False):
    audio_bytes = await request.body()
    return await voice_service.transcribe_and_process_audio(audio_bytes, auto_execute=auto_execute, force_awake=force_awake)


# --- Mission Endpoints ---

@app.post("/api/v1/missions")
async def create_mission(req: MissionCreateRequest, db: Session = Depends(get_db)):
    if req.selected_coding_agent:
        try:
            coding_agent_registry.validate_agent_selection(req.selected_coding_agent)
        except (ValueError, PaidProviderBlockedError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    m = Mission(
        id=new_id("mis"),
        title=req.title or req.objective[:60],
        objective=req.objective,
        status="DRAFT",
        mission_type=req.mission_type,
        workspace_path=req.workspace_path,
        selected_coding_agent=req.selected_coding_agent,
    )
    db.add(m)
    db.commit()
    await mission_engine.plan_mission(db, m)
    return serialize_mission(db, m)


@app.get("/api/v1/missions")
def list_missions(db: Session = Depends(get_db)):
    return [serialize_mission(db, m) for m in db.query(Mission).order_by(Mission.updated_at.desc()).all()]


@app.post("/api/v1/missions/{mission_id}/start")
async def start_mission(mission_id: str, db: Session = Depends(get_db)):
    m = db.query(Mission).filter(Mission.id == mission_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Mission not found")
    await mission_engine.execute_mission(db, m)
    return serialize_mission(db, m)


@app.post("/api/v1/missions/{mission_id}/pause")
def pause_mission(mission_id: str, db: Session = Depends(get_db)):
    m = db.query(Mission).filter(Mission.id == mission_id).first()
    mission_engine.transition_mission(db, m, "PAUSED", "Paused by user")
    return serialize_mission(db, m)


@app.post("/api/v1/missions/{mission_id}/resume")
async def resume_mission(mission_id: str, db: Session = Depends(get_db)):
    m = db.query(Mission).filter(Mission.id == mission_id).first()
    await mission_engine.execute_mission(db, m)
    return serialize_mission(db, m)


@app.post("/api/v1/missions/{mission_id}/cancel")
def cancel_mission(mission_id: str, db: Session = Depends(get_db)):
    m = db.query(Mission).filter(Mission.id == mission_id).first()
    mission_engine.transition_mission(db, m, "CANCELLED", "Cancelled by user")
    return serialize_mission(db, m)


@app.get("/api/v1/missions/{mission_id}/events")
def get_mission_events(mission_id: str, db: Session = Depends(get_db)):
    evts = db.query(LedgerEvent).filter(LedgerEvent.mission_id == mission_id).all()
    return [{"event_id": e.id, "type": e.event_type, "timestamp": e.timestamp.isoformat(), "payload": json.loads(e.payload_json)} for e in evts]


@app.get("/api/v1/missions/{mission_id}/evidence")
def get_mission_evidence(mission_id: str, db: Session = Depends(get_db)):
    items = db.query(EvidenceRecord).filter(EvidenceRecord.mission_id == mission_id).all()
    return [
        {
            "id": ev.id,
            "mission_id": ev.mission_id,
            "evidence_type": ev.evidence_type,
            "local_path": ev.local_path,
            "metadata": json.loads(ev.metadata_json or "{}"),
            "redaction_status": ev.redaction_status,
            "created_at": ev.created_at.isoformat(),
        }
        for ev in items
    ]


@app.get("/api/v1/evidence/{evidence_id}/artifact")
@app.get("/api/v1/missions/{mission_id}/evidence/{evidence_id}/artifact")
def get_evidence_artifact(evidence_id: str, mission_id: str | None = None, db: Session = Depends(get_db)):
    ev = db.query(EvidenceRecord).filter(EvidenceRecord.id == evidence_id).first()
    if not ev or not ev.local_path:
        raise HTTPException(status_code=404, detail="Evidence artifact not found")
    p = Path(ev.local_path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"Evidence artifact file missing: {ev.local_path}")
    media_type = "image/png" if p.suffix.lower() == ".png" else "application/octet-stream"
    return FileResponse(str(p), media_type=media_type)


@app.get("/api/v1/approvals")
def list_approvals(db: Session = Depends(get_db)):
    rows = db.query(ApprovalRequest).order_by(ApprovalRequest.requested_at.desc()).all()
    return [
        {
            "id": r.id,
            "mission_id": r.mission_id,
            "action_summary": r.action_summary,
            "target_summary": r.target_summary,
            "consequence_summary": r.consequence_summary,
            "reversible": r.reversible,
            "risk_tier": r.risk_tier,
            "status": r.status,
            "requested_at": r.requested_at.isoformat(),
        }
        for r in rows
    ]


@app.post("/api/v1/approvals/{approval_id}/approve")
async def approve_request(
    approval_id: str,
    decision: ApprovalDecisionRequest,
    _auth: str = Depends(verify_approval_secret),
    db: Session = Depends(get_db),
):
    apr = db.query(ApprovalRequest).filter(ApprovalRequest.id == approval_id).first()
    if not apr:
        raise HTTPException(status_code=404, detail="Approval request not found")
    apr.status = "approved"
    apr.resolved_at = utcnow()
    apr.resolved_by = decision.resolved_by
    db.commit()
    m = db.query(Mission).filter(Mission.id == apr.mission_id).first()
    if m and m.status == "AWAITING_APPROVAL":
        await mission_engine.execute_mission(db, m)
    return {"status": "approved", "mission_status": m.status if m else None}


@app.post("/api/v1/approvals/{approval_id}/reject")
def reject_request(
    approval_id: str,
    decision: ApprovalDecisionRequest,
    _auth: str = Depends(verify_approval_secret),
    db: Session = Depends(get_db),
):
    apr = db.query(ApprovalRequest).filter(ApprovalRequest.id == approval_id).first()
    if not apr:
        raise HTTPException(status_code=404, detail="Approval request not found")
    apr.status = "rejected"
    m = db.query(Mission).filter(Mission.id == apr.mission_id).first()
    if m:
        mission_engine.transition_mission(db, m, "BLOCKED", "User rejected approval")
    db.commit()
    return {"status": "rejected"}


@app.get("/api/v1/ledger/verify")
def verify_ledger(db: Session = Depends(get_db)):
    return event_bus.verify_ledger_chain(db)


# --- RALLY 3.0 Memory, Personalization & Autonomy Endpoints ---

@app.get("/api/v1/memory")
def get_memory_snapshot(db: Session = Depends(get_db)):
    return {
        "contacts": memory_store.list_contacts(db),
        "style_profile": memory_store.get_style_profile(db),
        "autonomy_mode": memory_store.get_autonomy_mode(db),
        "corrections": memory_store.list_corrections(db),
    }


@app.get("/api/v1/memory/contacts")
def list_contacts(db: Session = Depends(get_db)):
    return {"contacts": memory_store.list_contacts(db)}


@app.post("/api/v1/memory/contacts")
def create_contact(payload: ContactCreatePayload, db: Session = Depends(get_db)):
    return memory_store.add_contact(
        db,
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        relationship=payload.relationship,
        aliases=payload.aliases,
    )


@app.get("/api/v1/memory/style-profile")
def get_style_profile(db: Session = Depends(get_db)):
    return memory_store.get_style_profile(db)


@app.post("/api/v1/memory/style-profile")
def update_style_profile(payload: StyleProfilePayload, db: Session = Depends(get_db)):
    current = memory_store.get_style_profile(db)
    if payload.greeting is not None: current["greeting"] = payload.greeting
    if payload.sign_off is not None: current["sign_off"] = payload.sign_off
    if payload.formality is not None: current["formality"] = payload.formality
    if payload.tone is not None: current["tone"] = payload.tone
    if payload.use_hinglish is not None: current["use_hinglish"] = payload.use_hinglish
    return memory_store.set_style_profile(db, current)


@app.get("/api/v1/memory/autonomy")
def get_autonomy_mode(db: Session = Depends(get_db)):
    return {"mode": memory_store.get_autonomy_mode(db)}


@app.post("/api/v1/memory/autonomy")
def set_autonomy_mode(payload: AutonomyPayload, db: Session = Depends(get_db)):
    try:
        updated = memory_store.set_autonomy_mode(db, payload.mode)
        return {"mode": updated}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/v1/memory/corrections")
def list_corrections(db: Session = Depends(get_db)):
    return {"corrections": memory_store.list_corrections(db)}


@app.post("/api/v1/memory/corrections")
def add_correction(payload: CorrectionPayload, db: Session = Depends(get_db)):
    return memory_store.record_correction(db, payload.original_intent, payload.corrected_action, payload.tags)


@app.get("/api/v1/desktop/windows")
def get_desktop_windows():
    return {"windows": desktop_driver.list_windows()}


@app.post("/api/v1/desktop/stop")
def emergency_stop_endpoint(db: Session = Depends(get_db)):
    return mission_engine.trigger_emergency_stop(db)


@app.post("/api/v1/build/projects/inspect")
def inspect_build_project(req: BuildInspectRequest):
    return build_supervisor.inspect_workspace(req.workspace_path)


@app.post("/api/v1/build/missions")
async def create_build_mission(req: BuildMissionCreateRequest, db: Session = Depends(get_db)):
    try:
        coding_agent_registry.validate_agent_selection(req.selected_coding_agent)
        ws_info = build_supervisor.inspect_workspace(req.workspace_path)
    except (ValueError, PaidProviderBlockedError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    m = Mission(
        id=new_id("mis"),
        title=f"Build Supervision: {req.objective[:48]}",
        objective=req.objective,
        status="DRAFT",
        mission_type="build",
        workspace_path=ws_info["workspace_path"],
        selected_coding_agent=req.selected_coding_agent,
        build_supervisor_state="WORKSPACE_INSPECTION",
        max_correction_iterations=req.max_correction_iterations,
    )
    db.add(m)
    db.commit()
    await mission_engine.plan_mission(db, m)
    return {"mission": serialize_mission(db, m), "workspace_inspection": ws_info}


@app.post("/api/v1/build/missions/{mission_id}/run-check")
def run_build_check(mission_id: str, req: RunCheckRequest, db: Session = Depends(get_db)):
    m = db.query(Mission).filter(Mission.id == mission_id).first()
    cmd = req.custom_command or (["pytest"] if req.command_kind == "test" else ["python", "-m", "compileall", "."])
    return build_supervisor.run_workspace_check(db, m, cmd, check_kind=req.command_kind)


@app.get("/api/v1/build/missions/{mission_id}/defects")
def get_build_mission_defects(mission_id: str, db: Session = Depends(get_db)):
    defects = db.query(DefectRecord).filter(DefectRecord.mission_id == mission_id).all()
    return [
        {
            "id": d.id,
            "severity": d.severity,
            "title": d.title,
            "expected_behavior": d.expected_behavior,
            "actual_behavior": d.actual_behavior,
            "corrective_prompt": d.corrective_prompt,
            "resolved": d.resolved,
        }
        for d in defects
    ]


@app.post("/api/v1/build/missions/{mission_id}/request-correction")
async def trigger_build_correction(mission_id: str, db: Session = Depends(get_db)):
    m = db.query(Mission).filter(Mission.id == mission_id).first()
    return await build_supervisor.request_correction_cycle(db, m)


@app.get("/api/v1/build/missions/{mission_id}/report")
def get_build_mission_report(mission_id: str, db: Session = Depends(get_db)):
    m = db.query(Mission).filter(Mission.id == mission_id).first()
    return build_supervisor.generate_truthful_report(db, m)


@app.get("/api/v1/events/stream")
async def stream_events():
    queue = event_bus.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(evt)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            event_bus.unsubscribe(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")