import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, Index
from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Mission(Base):
    __tablename__ = "missions"

    id = Column(String(32), primary_key=True, default=lambda: new_id("mis"))
    title = Column(String(255), nullable=False)
    objective = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="DRAFT")
    mission_type = Column(String(32), nullable=False, default="general")
    workspace_path = Column(Text, nullable=True)
    selected_coding_agent = Column(String(64), nullable=True)
    build_supervisor_state = Column(String(64), nullable=True)
    correction_iteration = Column(Integer, nullable=False, default=0)
    max_correction_iterations = Column(Integer, nullable=False, default=3)
    constraints_json = Column(Text, nullable=False, default="[]")
    success_criteria_json = Column(Text, nullable=False, default="[]")
    current_step_id = Column(String(32), nullable=True)
    priority = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    last_checkpoint_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)

    __table_args__ = (Index("ix_missions_status_updated", "status", "updated_at"),)


class MissionStep(Base):
    __tablename__ = "mission_steps"

    id = Column(String(32), primary_key=True, default=lambda: new_id("stp"))
    mission_id = Column(String(32), ForeignKey("missions.id"), nullable=False)
    step_order = Column(Integer, nullable=False, default=0)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="PENDING")
    assigned_role = Column(String(64), nullable=False, default="DesktopOperator")
    action_type = Column(String(64), nullable=False, default="read_ui_tree")
    action_payload_json = Column(Text, nullable=False, default="{}")
    dependencies_json = Column(Text, nullable=False, default="[]")
    inputs_json = Column(Text, nullable=False, default="{}")
    outputs_json = Column(Text, nullable=False, default="{}")
    risk_tier = Column(Integer, nullable=False, default=0)
    requires_approval = Column(Boolean, nullable=False, default=False)
    idempotency_key = Column(String(128), nullable=True)
    side_effect_committed = Column(Boolean, nullable=False, default=False)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=2)
    timeout_seconds = Column(Integer, nullable=False, default=60)
    verification_spec_json = Column(Text, nullable=False, default="{}")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_summary = Column(Text, nullable=True)

    __table_args__ = (Index("ix_mission_steps_mission_status", "mission_id", "status"),)


class ActionRecord(Base):
    __tablename__ = "action_records"

    id = Column(String(32), primary_key=True, default=lambda: new_id("act"))
    mission_id = Column(String(32), ForeignKey("missions.id"), nullable=False)
    step_id = Column(String(32), ForeignKey("mission_steps.id"), nullable=True)
    action_type = Column(String(64), nullable=False)
    target_summary = Column(Text, nullable=False)
    parameters_redacted_json = Column(Text, nullable=False, default="{}")
    risk_tier = Column(Integer, nullable=False, default=0)
    approval_id = Column(String(32), nullable=True)
    status = Column(String(32), nullable=False, default="PENDING")
    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    result_summary = Column(Text, nullable=True)
    error_code = Column(String(64), nullable=True)
    evidence_ids_json = Column(Text, nullable=False, default="[]")


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id = Column(String(32), primary_key=True, default=lambda: new_id("apr"))
    mission_id = Column(String(32), ForeignKey("missions.id"), nullable=False)
    step_id = Column(String(32), ForeignKey("mission_steps.id"), nullable=True)
    action_id = Column(String(32), nullable=True)
    action_summary = Column(Text, nullable=False)
    target_summary = Column(Text, nullable=False)
    consequence_summary = Column(Text, nullable=False)
    reversible = Column(Boolean, nullable=False, default=False)
    risk_tier = Column(Integer, nullable=False, default=2)
    status = Column(String(32), nullable=False, default="pending")
    requested_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(String(64), nullable=True)
    user_comment = Column(Text, nullable=True)


class EvidenceRecord(Base):
    __tablename__ = "evidence_records"

    id = Column(String(32), primary_key=True, default=lambda: new_id("evd"))
    mission_id = Column(String(32), ForeignKey("missions.id"), nullable=False)
    step_id = Column(String(32), nullable=True)
    action_id = Column(String(32), nullable=True)
    evidence_type = Column(String(64), nullable=False)
    local_path = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=False, default="{}")
    redaction_status = Column(String(32), nullable=False, default="redacted")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    retention_expires_at = Column(DateTime(timezone=True), nullable=True)


class Checkpoint(Base):
    __tablename__ = "checkpoints"

    id = Column(String(32), primary_key=True, default=lambda: new_id("chk"))
    mission_id = Column(String(32), ForeignKey("missions.id"), nullable=False)
    step_id = Column(String(32), nullable=True)
    state_snapshot_json = Column(Text, nullable=False)
    reason = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class LedgerEvent(Base):
    __tablename__ = "ledger_events"

    id = Column(String(32), primary_key=True, default=lambda: new_id("evt"))
    mission_id = Column(String(32), nullable=True)
    step_id = Column(String(32), nullable=True)
    event_type = Column(String(64), nullable=False)
    payload_json = Column(Text, nullable=False, default="{}")
    previous_hash = Column(String(64), nullable=True, default="GENESIS")
    entry_hash = Column(String(64), nullable=True)
    timestamp = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class DefectRecord(Base):
    __tablename__ = "defect_records"

    id = Column(String(32), primary_key=True, default=lambda: new_id("def"))
    mission_id = Column(String(32), ForeignKey("missions.id"), nullable=False)
    severity = Column(String(32), nullable=False)
    title = Column(String(255), nullable=False)
    expected_behavior = Column(Text, nullable=False)
    actual_behavior = Column(Text, nullable=False)
    affected_target = Column(String(255), nullable=True)
    corrective_prompt = Column(Text, nullable=False)
    evidence_id = Column(String(32), nullable=True)
    resolved = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"

    id = Column(String(32), primary_key=True, default=lambda: new_id("int"))
    provider = Column(String(64), unique=True, nullable=False)
    display_name = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False, default="disconnected")
    scopes_json = Column(Text, nullable=False, default="[]")
    credential_reference = Column(String(255), nullable=True)
    last_health_check_at = Column(DateTime(timezone=True), nullable=True)
    last_error_summary = Column(Text, nullable=True)


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(String(32), primary_key=True, default=lambda: new_id("cnt"))
    name = Column(String(128), nullable=False, index=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(64), nullable=True)
    relationship = Column(String(64), nullable=True, default="colleague")
    aliases_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class MemoryItem(Base):
    __tablename__ = "memory_items"

    id = Column(String(32), primary_key=True, default=lambda: new_id("mem"))
    category = Column(String(64), nullable=False, index=True)  # "style_profile", "preference", "app_alias"
    key = Column(String(128), nullable=False, index=True)
    value_json = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class CorrectionRecord(Base):
    __tablename__ = "corrections"

    id = Column(String(32), primary_key=True, default=lambda: new_id("cor"))
    original_intent = Column(Text, nullable=False)
    corrected_action = Column(Text, nullable=False)
    context_tags_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)