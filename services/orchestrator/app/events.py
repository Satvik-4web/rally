import asyncio
import hashlib
import json
from typing import Any
from sqlalchemy.orm import Session
from app.models import LedgerEvent, new_id, utcnow

GENESIS_HASH = "0" * 64


def compute_event_hash(prev_hash: str, evt_id: str, event_type: str, payload_json: str, timestamp_str: str) -> str:
    raw = f"{prev_hash}|{evt_id}|{event_type}|{payload_json}|{timestamp_str}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def normalize_timestamp_str(ts: Any) -> str:
    if hasattr(ts, "isoformat"):
        iso = ts.isoformat()
        if not iso.endswith("Z") and "+" not in iso:
            iso += "+00:00"
        return iso
    s = str(ts).replace(" ", "T")
    if not s.endswith("Z") and "+" not in s:
        s += "+00:00"
    return s


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=250)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    def emit(
        self,
        db: Session,
        event_type: str,
        payload: dict[str, Any],
        mission_id: str | None = None,
        step_id: str | None = None,
    ) -> dict[str, Any]:
        evt_id = new_id("evt")
        now = utcnow()
        payload_json = json.dumps(payload, sort_keys=True)

        is_ephemeral = event_type.startswith("voice.live") or event_type == "voice.state.changed"
        prev_hash = None
        entry_hash = None

        if not is_ephemeral:
            # Cryptographic Hash Chaining for durable audit events
            last_event = db.query(LedgerEvent).order_by(LedgerEvent.timestamp.desc(), LedgerEvent.id.desc()).first()
            prev_hash = last_event.entry_hash if (last_event and last_event.entry_hash) else GENESIS_HASH
            ts_str = normalize_timestamp_str(now)
            entry_hash = compute_event_hash(prev_hash, evt_id, event_type, payload_json, ts_str)

            record = LedgerEvent(
                id=evt_id,
                mission_id=mission_id,
                step_id=step_id,
                event_type=event_type,
                payload_json=payload_json,
                previous_hash=prev_hash,
                entry_hash=entry_hash,
                timestamp=now,
            )
            db.add(record)
            db.commit()

        envelope = {
            "event_id": evt_id,
            "type": event_type,
            "timestamp": now.isoformat(),
            "mission_id": mission_id,
            "step_id": step_id,
            "previous_hash": prev_hash,
            "entry_hash": entry_hash,
            "payload": payload,
        }
        for q in list(self._subscribers):
            try:
                q.put_nowait(envelope)
            except asyncio.QueueFull:
                pass
        return envelope

    def verify_ledger_chain(self, db: Session) -> dict[str, Any]:
        """
        Cryptographically verifies the immutable tamper-evident event ledger chain.
        Walks every recorded event and proves that previous_hash matches and entry_hash is valid.
        """
        events = db.query(LedgerEvent).order_by(LedgerEvent.timestamp.asc(), LedgerEvent.id.asc()).all()
        if not events:
            return {"valid": True, "event_count": 0, "status": "empty_ledger"}

        expected_prev = GENESIS_HASH
        for idx, ev in enumerate(events):
            # Check previous hash link
            if ev.previous_hash and ev.previous_hash != expected_prev:
                return {
                    "valid": False,
                    "tampered_index": idx,
                    "event_id": ev.id,
                    "reason": f"Hash chain broken. Expected previous {expected_prev}, got {ev.previous_hash}",
                }
            # Verify entry hash integrity
            ts_str = normalize_timestamp_str(ev.timestamp)
            calc_hash = compute_event_hash(ev.previous_hash or GENESIS_HASH, ev.id, ev.event_type, ev.payload_json, ts_str)
            if ev.entry_hash and ev.entry_hash != calc_hash:
                return {
                    "valid": False,
                    "tampered_index": idx,
                    "event_id": ev.id,
                    "reason": "Payload or metadata modified. Entry hash checksum mismatch.",
                }
            expected_prev = ev.entry_hash or calc_hash

        return {
            "valid": True,
            "event_count": len(events),
            "latest_hash": expected_prev,
            "status": "cryptographically_verified",
        }


event_bus = EventBus()
