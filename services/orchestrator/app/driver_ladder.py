import os
import sys
import time
import subprocess
import urllib.parse
from typing import Any, Optional
from app.desktop_driver import desktop_driver
from app.browser_driver import browser_driver

GMAIL_COMPOSE_BASE = "https://mail.google.com/mail/?view=cm&fs=1"


class DriverLadder:
    """
    RALLY 3.0 Driver Ladder.
    Rung 1: Direct routes (URLs, URIs, CLI, Start Menu, APIs) — Highest reliability
    Rung 2: Tool servers (MCP) — High reliability
    Rung 3: Browser control (Playwright DOM) — High reliability
    Rung 4: Windows UI Automation (Accessibility tree + clipboard injection) — Medium-High
    Rung 5: Vision fallback (Screenshot + coordinate clicks) — Medium, slowest
    """

    # --- Rung 1: Direct Routes ---

    def execute_direct_route(self, route_type: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Executes an action via the highest-reliability direct OS / protocol route.
        0 pixel clicks required.
        """
        if route_type == "open_url":
            url = params.get("url")
            if not url:
                raise ValueError("Direct route open_url requires 'url' parameter.")
            res = desktop_driver.open_url(url)
            return {"rung": 1, "method": "direct_url", "url": url, "result": res}

        elif route_type == "launch_app":
            app_name = params.get("app_name") or params.get("target_name")
            args = params.get("args")
            res = desktop_driver.launch_app(app_name, args=args)
            return {"rung": 1, "method": "direct_app_launch", "app": app_name, "result": res}

        elif route_type == "play_youtube":
            # Direct route for YouTube playback
            query = params.get("query", "")
            video_id = params.get("video_id")
            if video_id:
                url = f"https://www.youtube.com/watch?v={video_id}"
            else:
                encoded_q = urllib.parse.quote_plus(query)
                url = f"https://www.youtube.com/results?search_query={encoded_q}"
            res = desktop_driver.open_url(url)
            return {"rung": 1, "method": "youtube_direct_route", "url": url, "result": res}

        elif route_type == "play_spotify":
            query = params.get("query", "")
            if query:
                encoded_q = urllib.parse.quote(query)
                uri = f"spotify:search:{encoded_q}"
            else:
                uri = "spotify:"
            try:
                if sys.platform == "win32":
                    os.startfile(uri)
                else:
                    desktop_driver.launch_app("spotify")
                return {"rung": 1, "method": "spotify_uri_protocol", "uri": uri}
            except Exception:
                # Fallback to desktop app launch
                res = desktop_driver.launch_app("spotify")
                return {"rung": 1, "method": "spotify_app_launch", "result": res}

        elif route_type == "compose_email_draft":
            recipient = params.get("recipient", "")
            subject = params.get("subject", "")
            body = params.get("body", "")

            # Generate direct Gmail Web compose URL
            query_params = {
                "to": recipient,
                "su": subject,
                "body": body,
            }
            gmail_url = f"{GMAIL_COMPOSE_BASE}&{urllib.parse.urlencode(query_params)}"
            res = desktop_driver.open_url(gmail_url)
            return {
                "rung": 1,
                "method": "gmail_compose_url",
                "recipient": recipient,
                "subject": subject,
                "body": body,
                "url": gmail_url,
                "result": res,
            }

        elif route_type == "run_cli":
            command = params.get("command")
            cwd = params.get("cwd", ".")
            proc = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                shell=False,
                timeout=params.get("timeout", 30),
            )
            return {
                "rung": 1,
                "method": "cli_command",
                "exit_code": proc.returncode,
                "stdout": proc.stdout[:2000],
                "stderr": proc.stderr[:2000],
            }

        raise NotImplementedError(f"Direct route type '{route_type}' is not supported.")

    # --- Rung 3: Browser DOM Control ---

    async def execute_browser_dom(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Controls browser using DOM tree rather than pixel clicks."""
        if action == "navigate":
            url = params.get("url")
            res = await browser_driver.navigate(url)
            return {"rung": 3, "method": "playwright_dom_navigate", "url": url, "result": res}

        elif action == "click_element":
            selector = params.get("selector")
            text = params.get("text")
            res = await browser_driver.click(selector, text=text)
            return {"rung": 3, "method": "playwright_dom_click", "selector": selector, "result": res}

        elif action == "fill_input":
            selector = params.get("selector")
            value = params.get("value", "")
            res = await browser_driver.type_text(selector, value)
            return {"rung": 3, "method": "playwright_dom_fill", "selector": selector, "result": res}

        raise NotImplementedError(f"Browser DOM action '{action}' is not supported.")

    # --- Rung 4: Windows UI Automation (UIA) & Clipboard Injection ---

    def execute_uia_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Interacts with native desktop controls using Windows UI Automation tree.
        Uses safe clipboard pasting with previous clipboard backup & restore.
        """
        if action == "paste_text_to_focused":
            text = params.get("text", "")
            backup_clipboard = self._get_clipboard_text()
            try:
                self._set_clipboard_text(text)
                # Send Ctrl+V using desktop driver
                desktop_driver.press_key("ctrl+v")
                time.sleep(0.15)
                return {"rung": 4, "method": "clipboard_paste", "length": len(text)}
            finally:
                # Restore user's previous clipboard after short delay
                if backup_clipboard is not None:
                    time.sleep(0.2)
                    self._set_clipboard_text(backup_clipboard)

        elif action == "focus_element":
            window_id = params.get("window_id")
            res = desktop_driver.focus_window(window_id)
            return {"rung": 4, "method": "uia_focus", "result": res}

        raise NotImplementedError(f"UIA action '{action}' is not supported.")

    # --- Rung 5: Vision Fallback ---

    def execute_vision_fallback(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Last resort: captures screenshot and triggers action."""
        shot = desktop_driver.capture_screenshot()
        return {"rung": 5, "method": "vision_fallback", "screenshot_id": shot.get("id")}

    # --- Helper methods ---

    def _get_clipboard_text(self) -> Optional[str]:
        if sys.platform != "win32":
            return None
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                    data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                    return data
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass
        return None

    def _set_clipboard_text(self, text: str) -> None:
        if sys.platform != "win32":
            return
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass


driver_ladder = DriverLadder()
