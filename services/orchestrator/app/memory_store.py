import json
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.database import Base, engine
from app.models import Contact, CorrectionRecord, MemoryItem, new_id, utcnow

DEFAULT_CONTACTS = [
    {
        "name": "Rohan Sharma",
        "email": "rohan.sharma@example.com",
        "phone": "+91 98765 43210",
        "relationship": "colleague",
        "aliases": ["rohan", "rohan sharma", "rohan colleague"],
    },
    {
        "name": "Rohan Mehta",
        "email": "rohan.mehta@example.com",
        "phone": "+91 91234 56789",
        "relationship": "friend",
        "aliases": ["rohan mehta", "rohan college"],
    },
    {
        "name": "Professor Roy",
        "email": "prof.roy@university.edu",
        "relationship": "professor",
        "aliases": ["professor", "prof roy", "prof"],
    },
]

DEFAULT_STYLE_PROFILE = {
    "greeting": "Hi",
    "sign_off": "Best,",
    "formality": "semi_formal",
    "tone": "concise and polite",
    "use_hinglish": True,
}

DEFAULT_PREFERENCES = {
    "favorite_music_artists": ["Arijit Singh", "The Weeknd", "Coldplay"],
    "favorite_songs": {
        "arijit singh": "Tum Hi Ho",
        "the weeknd": "Starboy",
    },
    "default_coding_stack": "React 18 + TypeScript + Vite + Tailwind CSS",
    "default_browser": "chrome",
    "autonomy_mode": "balanced",  # "ask_first", "balanced", "autopilot"
}


class MemoryStore:
    """
    RALLY 3.0 Local Memory & Personalization Store.
    Maintains contacts, writing style profiles, app aliases, corrections,
    and user preferences entirely on local disk.
    """

    def initialize_defaults(self, db: Session) -> None:
        """Seeds default contacts and preference items if empty."""
        try:
            Base.metadata.create_all(bind=engine)
            if db.query(Contact).count() == 0:
                for c in DEFAULT_CONTACTS:
                    contact = Contact(
                        id=new_id("cnt"),
                        name=c["name"],
                        email=c.get("email"),
                        phone=c.get("phone"),
                        relationship=c.get("relationship", "colleague"),
                        aliases_json=json.dumps(c.get("aliases", [])),
                    )
                    db.add(contact)
                db.commit()

            if db.query(MemoryItem).filter(MemoryItem.category == "style_profile").count() == 0:
                item = MemoryItem(
                    id=new_id("mem"),
                    category="style_profile",
                    key="default",
                    value_json=json.dumps(DEFAULT_STYLE_PROFILE),
                )
                db.add(item)
                db.commit()

            if db.query(MemoryItem).filter(MemoryItem.category == "preference").count() == 0:
                for k, v in DEFAULT_PREFERENCES.items():
                    item = MemoryItem(
                        id=new_id("mem"),
                        category="preference",
                        key=k,
                        value_json=json.dumps(v),
                    )
                    db.add(item)
                db.commit()
        except Exception:
            db.rollback()

    # --- Contacts Resolution ---

    def list_contacts(self, db: Session) -> list[dict[str, Any]]:
        self.initialize_defaults(db)
        contacts = db.query(Contact).all()
        return [
            {
                "id": c.id,
                "name": c.name,
                "email": c.email,
                "phone": c.phone,
                "relationship": c.relationship,
                "aliases": json.loads(c.aliases_json or "[]"),
            }
            for c in contacts
        ]

    def find_contact(self, db: Session, query: str) -> list[dict[str, Any]]:
        """
        Fuzzy matches a spoken name against contacts and their aliases.
        Returns all matching contacts to detect ambiguities.
        """
        self.initialize_defaults(db)
        q = query.lower().strip()
        all_contacts = self.list_contacts(db)
        exact_matches = []
        partial_matches = []

        for c in all_contacts:
            name_low = c["name"].lower()
            aliases = [a.lower() for a in c.get("aliases", [])]
            if q == name_low or q in aliases:
                exact_matches.append(c)
            elif q in name_low or any(q in a for a in aliases):
                partial_matches.append(c)

        return exact_matches if exact_matches else partial_matches

    def add_contact(
        self,
        db: Session,
        name: str,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        relationship: str = "colleague",
        aliases: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        contact = Contact(
            id=new_id("cnt"),
            name=name,
            email=email,
            phone=phone,
            relationship=relationship,
            aliases_json=json.dumps(aliases or [name.lower()]),
        )
        db.add(contact)
        db.commit()
        return {
            "id": contact.id,
            "name": contact.name,
            "email": contact.email,
            "relationship": contact.relationship,
            "aliases": aliases or [name.lower()],
        }

    # --- Style Profile & Preferences ---

    def get_style_profile(self, db: Session) -> dict[str, Any]:
        self.initialize_defaults(db)
        item = db.query(MemoryItem).filter(MemoryItem.category == "style_profile", MemoryItem.key == "default").first()
        if item:
            return json.loads(item.value_json)
        return DEFAULT_STYLE_PROFILE

    def set_style_profile(self, db: Session, profile: dict[str, Any]) -> dict[str, Any]:
        item = db.query(MemoryItem).filter(MemoryItem.category == "style_profile", MemoryItem.key == "default").first()
        if not item:
            item = MemoryItem(id=new_id("mem"), category="style_profile", key="default", value_json=json.dumps(profile))
            db.add(item)
        else:
            item.value_json = json.dumps(profile)
        db.commit()
        return profile

    def get_preference(self, db: Session, key: str, default: Any = None) -> Any:
        self.initialize_defaults(db)
        item = db.query(MemoryItem).filter(MemoryItem.category == "preference", MemoryItem.key == key).first()
        if item:
            return json.loads(item.value_json)
        return DEFAULT_PREFERENCES.get(key, default)

    def set_preference(self, db: Session, key: str, value: Any) -> None:
        item = db.query(MemoryItem).filter(MemoryItem.category == "preference", MemoryItem.key == key).first()
        if not item:
            item = MemoryItem(id=new_id("mem"), category="preference", key=key, value_json=json.dumps(value))
            db.add(item)
        else:
            item.value_json = json.dumps(value)
        db.commit()

    # --- Autonomy Dial ---

    def get_autonomy_mode(self, db: Session) -> str:
        return self.get_preference(db, "autonomy_mode", "balanced")

    def set_autonomy_mode(self, db: Session, mode: str) -> str:
        if mode not in {"ask_first", "balanced", "autopilot"}:
            raise ValueError(f"Invalid autonomy mode '{mode}'. Choose ask_first, balanced, or autopilot.")
        self.set_preference(db, "autonomy_mode", mode)
        return mode

    # --- Corrections History ---

    def record_correction(self, db: Session, original: str, corrected: str, tags: Optional[list[str]] = None) -> dict[str, Any]:
        cor = CorrectionRecord(
            id=new_id("cor"),
            original_intent=original,
            corrected_action=corrected,
            context_tags_json=json.dumps(tags or []),
        )
        db.add(cor)
        db.commit()
        return {
            "id": cor.id,
            "original_intent": cor.original_intent,
            "corrected_action": cor.corrected_action,
            "created_at": cor.created_at.isoformat(),
        }

    def list_corrections(self, db: Session) -> list[dict[str, Any]]:
        rows = db.query(CorrectionRecord).order_by(CorrectionRecord.created_at.desc()).all()
        return [
            {
                "id": r.id,
                "original_intent": r.original_intent,
                "corrected_action": r.corrected_action,
                "context_tags": json.loads(r.context_tags_json or "[]"),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]


memory_store = MemoryStore()
