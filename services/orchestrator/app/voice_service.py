import asyncio
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from app.database import SessionLocal
from app.desktop_driver import desktop_driver
from app.events import event_bus
from app.mission_engine import mission_engine
from app.models import Mission, new_id
from app.providers import parse_generic_app_open_intent, parse_spotify_intent, parse_youtube_or_web_search

VOICE_STATES = {
    "INITIALIZING",
    "IDLE_LISTENING",
    "WAKE_WORD_DETECTED",
    "CAPTURING_COMMAND",
    "TRANSCRIBING",
    "PLANNING",
    "EXECUTING",
    "SPEAKING",
    "PAUSED",
    "ERROR",
}

IMPERATIVE_ACTION_VERBS = {
    "open", "launch", "start", "run", "search", "find", "play", "show", "check",
    "close", "terminate", "kill", "switch", "go", "browse", "visit", "type",
    "write", "draft", "send", "stop", "cancel", "halt", "pause", "resume",
    "observe", "summarize", "inspect", "help", "listen", "wake", "test", "verify",
    "create", "make", "build", "look", "what", "where", "how", "who", "tell",
    "set", "get", "bring", "minimize", "maximize", "focus", "read"
}


class VoiceCompanionService:
    """
    Implements Always-On Wake-Word Detection ("Hey RALLY"), VAD, Multi-Turn Conversational
    Session Management, Intelligent Command Routing, Windows TTS, and Startup Persistence.
    """

    def __init__(self) -> None:
        self.state: str = "IDLE_LISTENING"
        self.listening_enabled: bool = True
        self.wake_word: str = "hey rally"
        self.volume: int = 85
        self.selected_mic_index, self.selected_mic_name, self.selected_sample_rate = self._detect_best_microphone()
        self.conversation_timeout_sec: float = 25.0
        self.active_session_until: float = 0.0
        self.last_transcript: str = ""
        self.last_spoken_response: str = ""
        self.current_structured_task: dict[str, Any] | None = None
        self.current_action_label: str = "Idle"
        self.error_message: str | None = None
        self.session_history: list[dict[str, Any]] = []
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._hotkey_thread: threading.Thread | None = None
        self._sapi_proc: subprocess.Popen | None = None
        self._tts_lock = threading.Lock()

    def _detect_best_microphone(self) -> tuple[int | None, str, int]:
        # Return device_index=None so speech_recognition connects to Windows Default
        # Audio Device (Sound Mapper) which includes hardware noise suppression, AGC, and
        # proper acoustic sensitivity threshold (~118 dB rather than DirectSound raw 4456).
        mic_name = "Default Windows Microphone"
        try:
            import speech_recognition as sr
            mics = sr.Microphone.list_microphone_names()
            for idx, name in enumerate(mics):
                low = name.lower()
                if "realtek" in low or "array" in low or "microphone" in low:
                    mic_name = f"Default Microphone ({name})"
                    break
        except Exception:
            pass
        return None, mic_name, 44100

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def set_state(self, new_state: str, action_label: str | None = None, error: str | None = None) -> None:
        if new_state in VOICE_STATES:
            self.state = new_state
        if action_label is not None:
            self.current_action_label = action_label
        self.error_message = error
        try:
            with SessionLocal() as db:
                event_bus.emit(
                    db,
                    "voice.state.changed",
                    {
                        "state": self.state,
                        "action": self.current_action_label,
                        "last_transcript": self.last_transcript,
                        "last_spoken": self.last_spoken_response,
                        "in_conversation_window": time.time() < self.active_session_until,
                        "error": self.error_message,
                    },
                )
        except Exception:
            pass

    def play_activation_sound(self) -> None:
        if sys.platform == "win32":
            try:
                import winsound
                # Play crisp Windows chime through laptop speakers
                winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
                winsound.Beep(784, 90)
                winsound.Beep(1046, 120)
            except Exception:
                pass

    def wake_up(self, source: str = "Hotkey / Manual") -> dict[str, Any]:
        """Wakes up the voice companion into an active listening session immediately."""
        self.listening_enabled = True
        self.active_session_until = time.time() + self.conversation_timeout_sec
        self.play_activation_sound()
        self.set_state("CAPTURING_COMMAND", f"Live Voice Activated via {source}")
        return self.get_status()

    def speak(self, text: str) -> None:
        if not text:
            return
        self.last_spoken_response = text
        prev_state = self.state
        self.set_state("SPEAKING", f"Speaking: {text}")
        if (
            sys.platform == "win32"
            and not os.environ.get("PYTEST_CURRENT_TEST")
            and int(self.volume) > 0
        ):
            with self._tts_lock:
                try:
                    safe_text = text.replace("'", " ").replace('"', " ").replace("\n", " ")
                    ps_cmd = (
                        "Add-Type -AssemblyName System.Speech; "
                        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                        f"$s.Volume = {max(0, min(100, int(self.volume)))}; "
                        "$s.Rate = 1; "
                        f"$s.Speak('{safe_text}')"
                    )
                    subprocess.run(
                        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                        timeout=12,
                        check=False,
                    )
                except Exception:
                    pass
        if self.listening_enabled and prev_state != "PAUSED":
            self.set_state("IDLE_LISTENING", "Listening for wake word or follow-up")
        else:
            self.set_state("PAUSED", "Microphone paused")

    def list_microphones(self) -> list[dict[str, Any]]:
        mics: list[dict[str, Any]] = [{"index": None, "name": "Default Windows Microphone"}]
        try:
            import speech_recognition as sr
            for idx, name in enumerate(sr.Microphone.list_microphone_names()):
                mics.append({"index": idx, "name": name})
        except Exception:
            pass
        return mics

    def test_microphone(self) -> dict[str, Any]:
        mics = self.list_microphones()
        self.play_activation_sound()
        measured_energy = 0.0
        try:
            import speech_recognition as sr
            kwargs = {}
            if self.selected_mic_index is not None:
                kwargs["device_index"] = self.selected_mic_index
            if getattr(self, "selected_sample_rate", None):
                kwargs["sample_rate"] = self.selected_sample_rate
            with sr.Microphone(**kwargs) as src:
                r = sr.Recognizer()
                r.adjust_for_ambient_noise(src, duration=0.3)
                measured_energy = round(float(r.energy_threshold), 1)
        except Exception:
            pass

        return {
            "ok": len(mics) > 0,
            "selected_microphone": self.selected_mic_name,
            "measured_energy_threshold": measured_energy,
            "detected_devices": len(mics),
            "message": f"Microphone active ({self.selected_mic_name}, energy={measured_energy} dB). Ready to listen.",
        }

    # --- Windows Startup Registration (Section 1) ---
    def get_windows_startup_enabled(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as k:
                val, _ = winreg.QueryValueEx(k, "RALLY2_Assistant")
                return bool(val)
        except OSError:
            return False

    def set_windows_startup(self, enabled: bool) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
                if enabled:
                    py_exe = sys.executable
                    script_path = str((__file__).replace("voice_service.py", "tray_runner.py"))
                    winreg.SetValueEx(k, "RALLY2_Assistant", 0, winreg.REG_SZ, f'"{py_exe}" "{script_path}"')
                else:
                    try:
                        winreg.DeleteValue(k, "RALLY2_Assistant")
                    except OSError:
                        pass
            return self.get_windows_startup_enabled()
        except Exception:
            return False

    # --- Intelligent Command Routing (Section 5) ---
    def route_command_to_structured_task(self, command_text: str) -> dict[str, Any]:
        cmd = command_text.strip()
        low = cmd.lower()

        if any(p in low for p in ("stop", "cancel that", "pause", "halt", "abort")) and len(low.split()) <= 5:
            return {
                "intent": "interrupt_execution",
                "application": "RALLY System",
                "destination": None,
                "search_query": None,
                "execution_mode": "immediate_interrupt",
                "verification_required": False,
                "raw_command": cmd,
            }

        if any(p in low for p in ("what is on my screen", "what's on my screen", "summarize my screen", "observe my screen", "what windows are open")):
            return {
                "intent": "screen_observation",
                "application": "Windows Desktop",
                "destination": "Active Screen",
                "search_query": None,
                "execution_mode": "desktop_observation",
                "verification_required": True,
                "raw_command": cmd,
            }

        if any(p in low for p in ("how my project is progressing", "check my project", "mission status", "what have you been doing")):
            return {
                "intent": "mission_progress_check",
                "application": "RALLY Build Supervisor",
                "destination": "Mission Ledger",
                "search_query": None,
                "execution_mode": "supervisor_status",
                "verification_required": False,
                "raw_command": cmd,
            }

        yt = parse_youtube_or_web_search(cmd)
        if yt.get("matched"):
            return {
                "intent": "browser_search" if yt.get("query") else "browser_navigate",
                "application": "Google Chrome",
                "destination": str(yt.get("site") or "YouTube"),
                "search_query": yt.get("query"),
                "execution_mode": "desktop_or_browser",
                "verification_required": True,
                "raw_command": cmd,
            }

        sp = parse_spotify_intent(cmd)
        if sp.get("matched"):
            return {
                "intent": "media_control",
                "application": "Spotify",
                "destination": "Spotify Desktop",
                "search_query": sp.get("query"),
                "execution_mode": "desktop_automation",
                "verification_required": True,
                "raw_command": cmd,
            }

        if "email" in low or "professor" in low or "gmail" in low:
            return {
                "intent": "compose_email",
                "application": "Google Chrome / Mail",
                "destination": "Drafts",
                "search_query": None,
                "execution_mode": "browser_controlled",
                "verification_required": True,
                "raw_command": cmd,
            }

        app_open = parse_generic_app_open_intent(cmd)
        if app_open.get("app_name"):
            return {
                "intent": "launch_application",
                "application": str(app_open["app_name"]),
                "destination": None,
                "search_query": app_open.get("type_text"),
                "execution_mode": "desktop_automation",
                "verification_required": True,
                "raw_command": cmd,
            }

        return {
            "intent": "general_desktop_mission",
            "application": "Windows Desktop Operator",
            "destination": None,
            "search_query": None,
            "execution_mode": "desktop_or_browser",
            "verification_required": True,
            "raw_command": cmd,
        }

    # --- Process Utterance (Wake Word + Multi-Turn + Execution) ---
    async def process_utterance(self, raw_transcript: str, force_awake: bool = False) -> dict[str, Any]:
        cleaned = raw_transcript.strip()
        if not cleaned:
            return {"handled": False, "reason": "Empty transcript"}

        self.last_transcript = cleaned
        # Strip leading punctuation/symbols so "Hey, RALLY!" or ".Rally" matches smoothly
        low = re.sub(r"^[^\w]+", "", cleaned.lower())
        now = time.time()
        in_session = force_awake or (now < self.active_session_until) or self.listening_enabled

        # 1. Check Wake Word Detection with word-boundary punctuation support
        wake_variants = [
            self.wake_word.lower().strip(),
            "hey rally",
            "hi rally",
            "hello rally",
            "ok rally",
            "okay rally",
            "yo rally",
            "live rally",
            "wake up rally",
            "listen rally",
            "rally live",
            "rally listen",
            "rally wake up",
            "rally",
            "wake up",
            "start listening",
            "listen up",
            "hey riley",
            "hey alli",
            "hey rawley",
            "hey rollie",
            "hey raleigh",
            "hey rely",
        ]
        matched_wake = None
        for wv in [w for w in wake_variants if w]:
            if re.search(rf"\b{re.escape(wv)}\b", low):
                matched_wake = wv
                break

        if not matched_wake and not in_session:
            return {"handled": False, "reason": "Wake word not detected and no active conversation session."}

        # Strip wake word prefix and surrounding punctuation to isolate the command
        command_body = cleaned
        if matched_wake:
            command_body = re.sub(rf"^.*?\b{re.escape(matched_wake)}\b[\s,.:!\-]*", "", cleaned, flags=re.IGNORECASE).strip()

        # Filter out ambient background chatter when user did NOT use wake word, did NOT press hotkey/UI,
        # and is NOT in an active conversation session.
        if not matched_wake and not force_awake and now >= self.active_session_until:
            structured_task_check = self.route_command_to_structured_task(command_body)
            words = command_body.lower().split()
            first_word_clean = re.sub(r"[^\w]", "", words[0]) if words else ""
            has_action_verb = first_word_clean in IMPERATIVE_ACTION_VERBS
            has_known_intent = structured_task_check["intent"] != "general_desktop_mission"

            if not has_action_verb and not has_known_intent:
                print(f"[PASSIVE LISTENER FILTERED NOISE]: '{cleaned}' (ignored)", flush=True)
                return {
                    "handled": False,
                    "reason": "Passive audio ignored as ambient chatter (no wake word or action verb).",
                }

        # Emit the recognized command so the UI prompt input box types it in real time
        try:
            with SessionLocal() as db:
                event_bus.emit(
                    db,
                    "voice.live.transcript",
                    {
                        "text": cleaned,
                        "command": command_body,
                        "is_final": True,
                        "auto_execute": True,
                    },
                )
        except Exception:
            pass

        cmd_clean_low = command_body.lower().strip(" .,!?")

        # 2. Check immediate voice interruption ("Hey RALLY, stop." or "Stop.")
        if cmd_clean_low in {"stop", "cancel", "cancel that", "pause", "stop what you're doing", "abort"}:
            with SessionLocal() as db:
                stop_res = mission_engine.trigger_emergency_stop(db)
            spoken = "Stopped active execution."
            self._record_history(cleaned, spoken, {"intent": "interrupt_execution"}, None)
            await asyncio.to_thread(self.speak, spoken)
            return {"handled": True, "interrupted": True, "spoken": spoken, "stop_result": stop_res}

        self.set_state("WAKE_WORD_DETECTED", "Wake word recognized")
        self.play_activation_sound()
        self.active_session_until = time.time() + self.conversation_timeout_sec

        # TEST 1: User just said "Hey RALLY." with no instruction yet
        if not cmd_clean_low or cmd_clean_low in {"hey", "hi", "hello", "are you there"}:
            spoken = "Hey! What can I do for you?"
            self._record_history(cleaned, spoken, {"intent": "wake_greeting"}, None)
            await asyncio.to_thread(self.speak, spoken)
            self.set_state("CAPTURING_COMMAND", "Waiting for your instruction (Multi-turn active)")
            return {"handled": True, "woke_only": True, "spoken": spoken}

        # Route command into Structured Task (Section 5)
        self.set_state("PLANNING", f"Planning: {command_body}")
        structured_task = self.route_command_to_structured_task(command_body)
        self.current_structured_task = structured_task
        intent = structured_task["intent"]

        # Handle "What is on my screen?" (TEST 5)
        if intent == "screen_observation":
            self.set_state("EXECUTING", "Observing Windows desktop screen")
            wins = desktop_driver.list_windows()
            shot = desktop_driver.capture_screenshot("full")
            top_titles = [w["title"] for w in wins[:4]]
            spoken = (
                f"You have {len(wins)} windows open on your screen, including "
                + (", ".join(top_titles[:3]) if top_titles else "your desktop")
                + "."
            )
            self._record_history(cleaned, spoken, structured_task, None)
            await asyncio.to_thread(self.speak, spoken)
            self.active_session_until = time.time() + self.conversation_timeout_sec
            return {
                "handled": True,
                "spoken": spoken,
                "structured_task": structured_task,
                "window_count": len(wins),
                "screenshot": shot,
            }

        # Handle "Check how my project is progressing"
        if intent == "mission_progress_check":
            with SessionLocal() as db:
                latest = db.query(Mission).order_by(Mission.updated_at.desc()).first()
                if latest:
                    spoken = f"Your latest mission, {latest.title}, is currently {latest.status.lower()}."
                else:
                    spoken = "There are no active missions in the ledger right now."
            self._record_history(cleaned, spoken, structured_task, latest.id if latest else None)
            await asyncio.to_thread(self.speak, spoken)
            self.active_session_until = time.time() + self.conversation_timeout_sec
            return {"handled": True, "spoken": spoken, "structured_task": structured_task}

        # Normalize phrasing for email to professor if no draft/send keyword
        mission_objective = command_body
        if intent == "compose_email" and "draft" not in command_body.lower() and "send" not in command_body.lower():
            mission_objective = f"Draft an email: {command_body} and leave it unsent."

        # Speak concise pre-execution feedback
        ack_speech = self._build_ack_speech(structured_task)
        if ack_speech:
            await asyncio.to_thread(self.speak, ack_speech)

        # Create & Execute Real Desktop Mission
        self.set_state("EXECUTING", f"Executing: {mission_objective}")
        with SessionLocal() as db:
            m = Mission(
                id=new_id("mis"),
                title=f"Voice: {mission_objective[:48]}",
                objective=mission_objective,
                status="DRAFT",
                mission_type="general",
            )
            db.add(m)
            db.commit()
            await mission_engine.plan_mission(db, m)
            await mission_engine.execute_mission(db, m)
            db.refresh(m)
            final_status = m.status
            mission_id = m.id

        # Speak natural verified completion response
        completion_speech = self._build_completion_speech(structured_task, final_status)
        self._record_history(cleaned, completion_speech, structured_task, mission_id)
        await asyncio.to_thread(self.speak, completion_speech)
        self.active_session_until = time.time() + self.conversation_timeout_sec

        return {
            "handled": True,
            "mission_id": mission_id,
            "mission_status": final_status,
            "structured_task": structured_task,
            "spoken": completion_speech,
        }

    def _build_ack_speech(self, task: dict[str, Any]) -> str:
        intent = task.get("intent")
        app = task.get("application") or "application"
        query = task.get("search_query")
        if intent == "browser_search" and query:
            return f"Searching {task.get('destination', 'YouTube')} for {query}."
        if intent == "launch_application":
            return f"Opening {app}."
        if intent == "media_control":
            return f"Opening Spotify{f' for {query}' if query else ''}."
        if intent == "compose_email":
            return "Preparing your email draft."
        return "Working on that."

    def _build_completion_speech(self, task: dict[str, Any], status: str) -> str:
        if status == "AWAITING_APPROVAL":
            return "I need your approval on the dashboard before performing this external action."
        if status == "FAILED":
            return f"I encountered an issue completing that task on {task.get('application', 'your desktop')}."
        if status == "PAUSED":
            return "Task paused."
        intent = task.get("intent")
        app = task.get("application") or "the application"
        query = task.get("search_query")
        if intent == "browser_search" and query:
            return f"Done. I found the search results for {query}."
        if intent == "launch_application":
            return f"{app} is open."
        if intent == "media_control":
            return "Spotify is open and ready."
        if intent == "compose_email":
            return "I've prepared the email draft and left it unsent for your review."
        return "I've completed and verified the task."

    def is_in_conversation_window(self) -> bool:
        return time.time() < self.active_session_until

    def _record_history(self, transcript: str, response: str, task: dict[str, Any], mission_id: str | None) -> None:
        entry = {
            "id": new_id("vch"),
            "timestamp": self._now_iso(),
            "transcript": transcript,
            "spoken_response": response,
            "structured_task": task,
            "mission_id": mission_id,
        }
        self.session_history.insert(0, entry)
        self.session_history = self.session_history[:40]

    # --- Background Microphone Listening Thread (Section 1 & 2) ---
    def start_listening(self) -> dict[str, Any]:
        self.listening_enabled = True
        self._stop_event.clear()
        self.set_state("IDLE_LISTENING", f"Listening for '{self.wake_word}'")
        if not self._worker_thread or not self._worker_thread.is_alive():
            self._worker_thread = threading.Thread(target=self._microphone_loop, daemon=True)
            self._worker_thread.start()
        if not self._hotkey_thread or not self._hotkey_thread.is_alive():
            self._hotkey_thread = threading.Thread(target=self._win32_hotkey_loop, daemon=True)
            self._hotkey_thread.start()
        self._start_sapi_listener()
        return self.get_status()

    def pause_listening(self) -> dict[str, Any]:
        self.listening_enabled = False
        self.active_session_until = 0.0
        self.set_state("PAUSED", "Microphone listening paused by user")
        if self._sapi_proc:
            try:
                self._sapi_proc.terminate()
                self._sapi_proc = None
            except Exception:
                pass
        return self.get_status()

    def _start_sapi_listener(self) -> None:
        """Starts Windows SAPI dictation engine for native real-time word-by-word interim streaming."""
        if sys.platform != "win32":
            return
        if self._sapi_proc is not None:
            return

        ps_code = """
        Add-Type -AssemblyName System.Speech
        $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
        $grammar = New-Object System.Speech.Recognition.DictationGrammar
        $engine.LoadGrammar($grammar)
        $engine.SetInputToDefaultAudioDevice()

        Register-ObjectEvent -InputObject $engine -EventName SpeechHypothesized -Action {
            [Console]::WriteLine("HYPOTHESIS:" + $Event.SourceEventArgs.Result.Text)
        } | Out-Null

        Register-ObjectEvent -InputObject $engine -EventName SpeechRecognized -Action {
            [Console]::WriteLine("FINAL:" + $Event.SourceEventArgs.Result.Text)
        } | Out-Null

        [Console]::WriteLine("SAPI_READY")
        $engine.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
        while ($true) { Start-Sleep -Milliseconds 100 }
        """
        try:
            self._sapi_proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_code],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )

            def _sapi_reader():
                while not self._stop_event.is_set() and self._sapi_proc:
                    try:
                        line = self._sapi_proc.stdout.readline()
                        if not line:
                            break
                        line = line.strip()
                        if line.startswith("HYPOTHESIS:"):
                            txt = line[len("HYPOTHESIS:"):].strip()
                            if txt:
                                with SessionLocal() as db:
                                    event_bus.emit(
                                        db,
                                        "voice.live.transcript",
                                        {"text": txt, "is_final": False, "auto_execute": False},
                                    )
                        elif line.startswith("FINAL:"):
                            txt = line[len("FINAL:"):].strip()
                            if txt:
                                with SessionLocal() as db:
                                    event_bus.emit(
                                        db,
                                        "voice.live.transcript",
                                        {"text": txt, "is_final": True, "auto_execute": True},
                                    )
                                asyncio.run(self.process_utterance(txt, force_awake=self.is_in_conversation_window()))
                    except Exception:
                        pass

            t = threading.Thread(target=_sapi_reader, daemon=True)
            t.start()
        except Exception:
            pass

    async def transcribe_and_process_audio(self, audio_bytes: bytes, auto_execute: bool = True, force_awake: bool = False) -> dict[str, Any]:
        """
        Transcribes PCM/WAV audio uploaded directly from client-side Web Audio API in Electron,
        streams the text to prompt input in real time, and executes immediately.
        """
        if not audio_bytes or len(audio_bytes) < 400:
            return {"ok": False, "text": "", "reason": "Audio stream too short"}

        import io
        import speech_recognition as sr

        transcript = ""
        r = sr.Recognizer()
        try:
            with sr.AudioFile(io.BytesIO(audio_bytes)) as source:
                audio = r.record(source)
            try:
                transcript = r.recognize_google(audio, language="en-IN")
            except sr.UnknownValueError:
                try:
                    transcript = r.recognize_google(audio, language="en-US")
                except sr.UnknownValueError:
                    pass
        except Exception as exc:
            print(f"[TRANSCRIPTION ERROR]: {exc}", flush=True)

        if not transcript:
            return {"ok": False, "text": "", "reason": "No speech detected in audio"}

        print(f"[REALTIME CLIENT AUDIO HEARD]: {transcript}", flush=True)
        try:
            with SessionLocal() as db:
                event_bus.emit(
                    db,
                    "voice.live.transcript",
                    {"text": transcript, "is_final": True, "auto_execute": auto_execute},
                )
        except Exception:
            pass

        exec_res = None
        if auto_execute:
            exec_res = await self.process_utterance(transcript, force_awake=(force_awake or self.is_in_conversation_window()))

        return {
            "ok": True,
            "text": transcript,
            "auto_execute": auto_execute,
            "execution": exec_res,
        }

    def _microphone_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self.listening_enabled:
                time.sleep(0.4)
                continue

            try:
                import speech_recognition as sr
                recognizer = sr.Recognizer()
                recognizer.energy_threshold = 250
                recognizer.dynamic_energy_threshold = True
                recognizer.dynamic_energy_adjustment_damping = 0.15
                recognizer.pause_threshold = 0.6
                recognizer.non_speaking_duration = 0.3

                mic_kwargs = {}
                if self.selected_mic_index is not None:
                    mic_kwargs["device_index"] = self.selected_mic_index
                if getattr(self, "selected_sample_rate", None):
                    mic_kwargs["sample_rate"] = self.selected_sample_rate

                with sr.Microphone(**mic_kwargs) as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.3)
                    if recognizer.energy_threshold > 500:
                        recognizer.energy_threshold = 250
                    while self.listening_enabled and not self._stop_event.is_set():
                        in_conv = time.time() < self.active_session_until or self.listening_enabled
                        self.set_state(
                            "CAPTURING_COMMAND" if in_conv else "IDLE_LISTENING",
                            "Listening for follow-up command..." if in_conv else f"Listening for '{self.wake_word}'...",
                        )
                        try:
                            audio = recognizer.listen(source, timeout=3.0, phrase_time_limit=10.0)
                        except sr.WaitTimeoutError:
                            continue

                        if not self.listening_enabled:
                            break

                        self.set_state("TRANSCRIBING", "Transcribing speech...")
                        transcript = ""
                        try:
                            # Primary: Indian English for regional acoustic nuance
                            transcript = recognizer.recognize_google(audio, language="en-IN")
                        except sr.UnknownValueError:
                            try:
                                # Secondary: US English
                                transcript = recognizer.recognize_google(audio, language="en-US")
                            except sr.UnknownValueError:
                                continue
                        except Exception as exc:
                            self.set_state("ERROR", "Speech recognition error", str(exc))
                            time.sleep(0.5)
                            continue

                        if transcript:
                            print(f"[VOICE LISTENER HEARD]: {transcript}", flush=True)
                            try:
                                with SessionLocal() as db:
                                    event_bus.emit(
                                        db,
                                        "voice.live.transcript",
                                        {"text": transcript, "is_final": True, "auto_execute": True},
                                    )
                            except Exception:
                                pass
                            asyncio.run(self.process_utterance(transcript, force_awake=self.is_in_conversation_window()))

            except Exception as exc:
                # Reconnect gracefully if microphone or driver is unavailable (Section 1 & 10)
                if self.listening_enabled:
                    self.set_state("IDLE_LISTENING", f"Standby (Browser/SAPI voice ready; note: {exc})")
                time.sleep(3.0)

    def _win32_hotkey_loop(self) -> None:
        """
        Global hardware keystroke listener that works across all applications, games, and elevated windows.
        Listens on Default desktop for:
        - F9
        - F8
        - F7
        - Ctrl + Space / Alt + Space
        - Ctrl + Shift + Space
        - Ctrl + Shift + A
        - Ctrl + Shift + V
        - Ctrl + ~ (Tilde)
        """
        if sys.platform != "win32":
            return
        user32 = ctypes.windll.user32
        hdefault = None
        try:
            hdefault = user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if hdefault:
                user32.SetThreadDesktop(hdefault)
        except Exception:
            pass

        prev_f9 = False
        prev_f8 = False
        prev_f7 = False
        prev_space = False
        prev_tilde = False
        prev_a = False
        prev_v = False

        while not self._stop_event.is_set():
            try:
                if hdefault:
                    user32.SetThreadDesktop(hdefault)

                ctrl_down = bool(user32.GetAsyncKeyState(0x11) & 0x8000)
                shift_down = bool(user32.GetAsyncKeyState(0x10) & 0x8000)
                alt_down = bool(user32.GetAsyncKeyState(0x12) & 0x8000)
                f9_down = bool(user32.GetAsyncKeyState(0x78) & 0x8000)
                f8_down = bool(user32.GetAsyncKeyState(0x77) & 0x8000)
                f7_down = bool(user32.GetAsyncKeyState(0x76) & 0x8000)
                space_down = bool(user32.GetAsyncKeyState(0x20) & 0x8000)
                tilde_down = bool(user32.GetAsyncKeyState(0xC0) & 0x8000)
                a_down = bool(user32.GetAsyncKeyState(0x41) & 0x8000)
                v_down = bool(user32.GetAsyncKeyState(0x56) & 0x8000)

                triggered = None
                if f9_down and not prev_f9:
                    triggered = "F9"
                elif f8_down and not prev_f8:
                    triggered = "F8"
                elif f7_down and not prev_f7:
                    triggered = "F7"
                elif (ctrl_down or alt_down) and space_down and not prev_space:
                    triggered = "Ctrl/Alt+Space"
                elif ctrl_down and shift_down and a_down and not prev_a:
                    triggered = "Ctrl+Shift+A"
                elif ctrl_down and shift_down and v_down and not prev_v:
                    triggered = "Ctrl+Shift+V"
                elif ctrl_down and tilde_down and not prev_tilde:
                    triggered = "Ctrl+`"

                prev_f9 = f9_down
                prev_f8 = f8_down
                prev_f7 = f7_down
                prev_space = space_down
                prev_tilde = tilde_down
                prev_a = a_down
                prev_v = v_down

                if triggered:
                    print(f"[RALLY GLOBAL HOTKEY]: Triggered via {triggered}!", flush=True)
                    self.wake_up(source=f"Key: {triggered}")

            except Exception:
                pass
            time.sleep(0.04)

    def get_status(self) -> dict[str, Any]:
        conv_remaining = max(0.0, round(self.active_session_until - time.time(), 1))
        return {
            "state": self.state,
            "listening_enabled": self.listening_enabled,
            "wake_word": self.wake_word,
            "volume": self.volume,
            "selected_microphone": self.selected_mic_name,
            "selected_mic_index": self.selected_mic_index,
            "in_conversation_window": time.time() < self.active_session_until,
            "conversation_window_remaining_sec": conv_remaining,
            "conversation_Window_remaining_sec": conv_remaining,
            "last_transcript": self.last_transcript,
            "last_spoken_response": self.last_spoken_response,
            "current_action": self.current_action_label,
            "current_structured_task": self.current_structured_task,
            "windows_startup_enabled": self.get_windows_startup_enabled(),
            "error": self.error_message,
            "history": self.session_history[:15],
        }


voice_service = VoiceCompanionService()