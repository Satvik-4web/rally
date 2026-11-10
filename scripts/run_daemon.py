import ctypes
import os
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]

class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]

def is_backend_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/api/v1/health", timeout=2.5) as resp:
            return resp.status == 200
    except Exception:
        return False

def is_process_alive(handle: wintypes.HANDLE | None) -> bool:
    if not handle:
        return False
    exit_code = wintypes.DWORD()
    kernel32 = ctypes.windll.kernel32
    if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
        return exit_code.value == 259  # STILL_ACTIVE = 259
    return False

def register_windows_startup():
    if sys.platform != "win32":
        return
    try:
        import winreg
        vbs_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "launch_rally_silent.vbs"))
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "RALLY2_AlwaysOn", 0, winreg.REG_SZ, f'wscript.exe "{vbs_path}"')
            print("[OK] Windows Boot Persistence enabled: RALLY will auto-start whenever you log in.", flush=True)
    except Exception as exc:
        print(f"[NOTE] Windows startup registration: {exc}", flush=True)

def launch_on_desktop(cmd: str, cwd: str) -> tuple[int, wintypes.HANDLE]:
    si = STARTUPINFO()
    si.cb = ctypes.sizeof(STARTUPINFO)
    si.lpDesktop = r"WinSta0\Default"

    pi = PROCESS_INFORMATION()
    kernel32 = ctypes.windll.kernel32
    # DETACHED_PROCESS (0x00000008) | CREATE_NEW_PROCESS_GROUP (0x00000200)
    flags = 0x00000008 | 0x00000200
    ok = kernel32.CreateProcessW(
        None,
        cmd,
        None,
        None,
        False,
        flags,
        None,
        cwd,
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not ok:
        err = kernel32.GetLastError()
        raise RuntimeError(f"CreateProcessW failed with error code {err} for command: {cmd}")
    print(f"[OK] Launched PID {pi.dwProcessId} on WinSta0\\Default: {cmd}", flush=True)
    return pi.dwProcessId, pi.hProcess

def main():
    print("=" * 60, flush=True)
    print("  RALLY 2.0 -- Always-On Sidecar Agent & Voice Supervisor  ", flush=True)
    print("=" * 60, flush=True)

    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    orch_dir = os.path.join(root_dir, "services", "orchestrator")

    # 1. Attach launcher thread to Default interactive desktop
    user32 = ctypes.windll.user32
    hdefault = user32.OpenDesktopW("Default", 0, False, 0x10000000)
    if hdefault:
        user32.SetThreadDesktop(hdefault)
        print("[OK] Attached supervisor to user's interactive 'Default' desktop.", flush=True)

    # 2. Launch Backend if not already running
    py_exe = os.path.join(orch_dir, ".venv", "Scripts", "python.exe")
    backend_cmd = f'"{py_exe}" -m uvicorn app.main:app --host 127.0.0.1 --port 8765'
    backend_pid, backend_handle = None, None

    if not is_backend_up():
        print("Starting RALLY Backend on :8765...", flush=True)
        backend_pid, backend_handle = launch_on_desktop(backend_cmd, orch_dir)
        for _ in range(30):
            time.sleep(0.3)
            if is_backend_up():
                print("[OK] Backend is live & healthy at http://127.0.0.1:8765", flush=True)
                break
    else:
        print("[OK] Backend is already running.", flush=True)

    # 3. Launch Electron Sidecar Agent on Default desktop
    electron_exe = os.path.join(root_dir, "node_modules", "electron", "dist", "electron.exe")
    electron_cmd = f'"{electron_exe}" apps/desktop'
    electron_pid, electron_handle = launch_on_desktop(electron_cmd, root_dir)

    # 4. Verify windows on Default desktop
    time.sleep(3.5)
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    visible_windows = []

    def enum_cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()
                if any(k in title.lower() for k in ("rally", "sidecar", "mission control")):
                    visible_windows.append((hwnd, title))
        return True

    user32.EnumDesktopWindows(hdefault, WNDENUMPROC(enum_cb), 0)
    print(f"[OK] Found {len(visible_windows)} RALLY window(s) on interactive desktop:", flush=True)
    for h, t in visible_windows:
        print(f"   [HWND {h}]: {t}", flush=True)

    # 5. Start voice listening loop
    try:
        req = urllib.request.Request("http://127.0.0.1:8765/api/v1/voice/start", method="POST")
        urllib.request.urlopen(req, timeout=2.0)
        print("[OK] Voice listening loop initialized.", flush=True)
    except Exception:
        pass

    print("=" * 60, flush=True)
    print("[RALLY READY] Sidecar Agent is ACTIVE on your screen edge.", flush=True)
    print("[RALLY READY] Hotkeys: F9, F8, F7, Ctrl+Space, Alt+Space.", flush=True)
    print("=" * 60, flush=True)

    # 6. Persistent Invincible Watchdog & Auto-Healing Loop
    register_windows_startup()
    consecutive_backend_failures = 0
    loop_count = 0

    while True:
        time.sleep(3.0)
        loop_count += 1

        # Check A: Is Backend API healthy?
        if not is_backend_up():
            consecutive_backend_failures += 1
            if consecutive_backend_failures >= 3:
                print("[WATCHDOG] Backend health check failed 3x. Auto-recovering backend...", flush=True)
                backend_pid, backend_handle = launch_on_desktop(backend_cmd, orch_dir)
                consecutive_backend_failures = 0
                time.sleep(3.0)
                try:
                    req = urllib.request.Request("http://127.0.0.1:8765/api/v1/voice/start", method="POST")
                    urllib.request.urlopen(req, timeout=2.0)
                except Exception:
                    pass
        else:
            consecutive_backend_failures = 0

        # Check B: Is Electron Sidecar alive?
        if not is_process_alive(electron_handle):
            print("[WATCHDOG] Sidecar window closed or crashed. Respawning on Default desktop...", flush=True)
            electron_pid, electron_handle = launch_on_desktop(electron_cmd, root_dir)
            time.sleep(2.0)

        if loop_count % 20 == 0:
            print(f"[WATCHDOG PULSE] All systems nominal. Backend: OK, Sidecar PID: {electron_pid}.", flush=True)

if __name__ == "__main__":
    main()
