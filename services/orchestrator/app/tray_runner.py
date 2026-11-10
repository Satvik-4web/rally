import ctypes
import os
import subprocess
import sys
import threading
import webbrowser
import uvicorn

from app.voice_service import voice_service


def acquire_single_instance_mutex() -> bool:
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, "Global\\RALLY_2_SINGLE_INSTANCE_MUTEX")
    last_error = kernel32.GetLastError()
    ERROR_ALREADY_EXISTS = 183
    return last_error != ERROR_ALREADY_EXISTS


def run_system_tray() -> None:
    try:
        import pystray
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (64, 64), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)
        draw.ellipse((12, 12, 52, 52), fill=(37, 99, 235), outline=(52, 211, 153), width=4)

        def on_open(icon, item):
            webbrowser.open("http://127.0.0.1:5173")

        def on_pause(icon, item):
            voice_service.pause_listening()

        def on_resume(icon, item):
            voice_service.start_listening()

        def on_exit(icon, item):
            voice_service.pause_listening()
            icon.stop()
            os._exit(0)

        menu = pystray.Menu(
            pystray.MenuItem("Open RALLY Dashboard", on_open, default=True),
            pystray.MenuItem("Resume Voice Listening ('Hey RALLY')", on_resume),
            pystray.MenuItem("Pause Voice Listening", on_pause),
            pystray.MenuItem("Exit RALLY", on_exit),
        )
        icon = pystray.Icon("RALLY_2", img, "RALLY 2.0 — AI Desktop Operator", menu)
        icon.run()
    except Exception:
        pass


if __name__ == "__main__":
    if not acquire_single_instance_mutex():
        print("RALLY 2.0 background service is already running.")
        sys.exit(0)

    voice_service.start_listening()
    tray_thread = threading.Thread(target=run_system_tray, daemon=True)
    tray_thread.start()

    uvicorn.run("app.main:app", host="127.0.0.1", port=8765, reload=False)