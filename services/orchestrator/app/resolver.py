import json
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.memory_store import memory_store


class GoalResolver:
    """
    RALLY 3.0 Entity & Goal Resolver.
    Fills in vague parts of user commands using local memory, style profile,
    contact book, and app aliases before the planner creates steps.
    """

    def resolve_goal(self, intent: str, raw_params: dict[str, Any], db: Session) -> dict[str, Any]:
        """
        Translates structured intent and parameters into fully-resolved concrete targets.
        """
        resolved = dict(raw_params)
        resolved["intent"] = intent
        resolved["requires_clarification"] = False
        resolved["clarification_question"] = None

        if intent in {"play_media", "youtube_search", "browser_search"}:
            self._resolve_media_goal(resolved, db)

        elif intent in {"send_message", "email_draft", "compose_email"}:
            self._resolve_email_goal(resolved, db)

        elif intent in {"open_app", "launch_app", "focus_window"}:
            self._resolve_app_goal(resolved, db)

        elif intent in {"code_prompt", "antigravity_prompt", "agent_prompt"}:
            self._resolve_coding_prompt_goal(resolved, db)

        return resolved

    def _resolve_media_goal(self, params: dict[str, Any], db: Session) -> None:
        query = str(params.get("search_query") or params.get("query") or params.get("artist") or "").strip()
        query_low = query.lower()

        # Check user favorite song mappings in memory
        fav_songs = memory_store.get_preference(db, "favorite_songs", {})
        for artist, song in fav_songs.items():
            if artist.lower() in query_low:
                params["resolved_song"] = song
                params["resolved_artist"] = artist.title()
                if "song" not in query_low or query_low.endswith("song") or query_low == artist.lower():
                    params["target_query"] = f"{artist.title()} {song}"
                else:
                    params["target_query"] = query
                return

        # Default resolution for Arijit Singh
        if "arijit" in query_low:
            params["resolved_artist"] = "Arijit Singh"
            params["resolved_song"] = "Tum Hi Ho"
            params["target_query"] = "Arijit Singh Tum Hi Ho"
        elif "weeknd" in query_low:
            params["resolved_artist"] = "The Weeknd"
            params["resolved_song"] = "Starboy"
            params["target_query"] = "The Weeknd Starboy"
        else:
            params["target_query"] = query or "Trending Music"

    def _resolve_email_goal(self, params: dict[str, Any], db: Session) -> None:
        raw_recipient = str(params.get("recipient") or params.get("contact") or "").strip()
        style = memory_store.get_style_profile(db)

        # 1. Resolve Contact
        contacts = memory_store.find_contact(db, raw_recipient)
        if len(contacts) == 1:
            matched = contacts[0]
            params["resolved_recipient_name"] = matched["name"]
            params["resolved_recipient_email"] = matched.get("email") or f"{raw_recipient.lower()}@example.com"
        elif len(contacts) > 1:
            # Ambiguity detected
            names = [c["name"] for c in contacts]
            params["requires_clarification"] = True
            params["clarification_question"] = f"Did you mean {' or '.join(names)}?"
            params["candidate_contacts"] = contacts
            return
        else:
            # New or unresolved contact name
            params["resolved_recipient_name"] = raw_recipient.title() if raw_recipient else "Colleague"
            params["resolved_recipient_email"] = f"{raw_recipient.lower().replace(' ', '.')}@example.com" if raw_recipient else ""

        # 2. Draft Subject and Body in user's Style Profile
        message_topic = str(params.get("message") or params.get("topic") or "I'll be running late today").strip()
        recipient_first = params["resolved_recipient_name"].split()[0]
        greeting = f"{style.get('greeting', 'Hi')} {recipient_first},"
        sign_off = style.get("sign_off", "Best,")

        if "late" in message_topic.lower():
            subject = "Running a bit late today"
            body = (
                f"{greeting}\n\n"
                f"I wanted to let you know that I'll be running a bit late today. "
                f"I will be online and available shortly.\n\n"
                f"{sign_off}\nUser"
            )
        else:
            subject = f"Update regarding {message_topic[:30]}"
            body = (
                f"{greeting}\n\n"
                f"{message_topic}\n\n"
                f"{sign_off}\nUser"
            )

        params["resolved_subject"] = subject
        params["resolved_body"] = body

    def _resolve_app_goal(self, params: dict[str, Any], db: Session) -> None:
        raw_app = str(params.get("app_name") or params.get("target_name") or "").strip()
        aliases = memory_store.get_preference(db, "app_aliases", {
            "code": "VS Code",
            "vscode": "VS Code",
            "visual studio code": "VS Code",
            "chrome": "Google Chrome",
            "antigravity": "Antigravity",
        })
        resolved_app = aliases.get(raw_app.lower(), raw_app)
        params["resolved_app_name"] = resolved_app

    def _resolve_coding_prompt_goal(self, params: dict[str, Any], db: Session) -> None:
        tech_stack = memory_store.get_preference(db, "default_coding_stack", "React 18 + TypeScript + Vite + Tailwind CSS")
        user_request = str(params.get("prompt") or params.get("topic") or "gaming website").strip()

        expanded_prompt = (
            f"Build a complete, responsive {user_request} using {tech_stack}. "
            f"Include modern interactive UI components, dark theme styling with subtle glowing accents, "
            f"modular architecture, clean TypeScript interfaces, and end-to-end user flows. "
            f"Ensure zero mock placeholders and provide verifiable tests."
        )
        params["expanded_prompt"] = expanded_prompt
        params["target_agent"] = "Antigravity"


goal_resolver = GoalResolver()
