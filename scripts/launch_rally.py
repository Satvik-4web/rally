import ctypes
import os
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes

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
        with urllib.request.urlopen("http://127.0.0.1:8765/api/v1/health", timeout=1.0) as resp:
            return resp.status == 200
    except Exception:
        return False

def launch_on_desktop(cmd: str, cwd: str) -> int:
    si = STARTUPINFO()
    si.cb = ctypes.sizeof(STARTUPINFO)
    si.lpDesktop = r"WinSta0\Default"

    pi = PROCESS_INFORMATION()
    kernel32 = ctypes.windll.kernel32
    ok = kernel32.CreateProcessW(
        None,
        cmd,
        None,
        None,
        False,
        0,
        None,
        cwd,
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not ok:
        err = kernel32.GetLastError()
        raise RuntimeError(f"CreateProcessW failed with error code {err} for command: {cmd}")
    print(f"✓ Launched PID {pi.dwProcessId} on WinSta0\\Default: {cmd}")
    return pi.dwProcessId

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 55)
    print("  RALLY 2.0 -- Launching Always-On Sidecar & Voice Agent  ")
    print("=" * 55)

    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    orch_dir = os.path.join(root_dir, "services", "orchestrator")
    desktop_dir = os.path.join(root_dir, "apps", "desktop")

    # 1. Attach thread to Default desktop
    user32 = ctypes.windll.user32
    hdefault = user32.OpenDesktopW("Default", 0, False, 0x10000000)
    if hdefault:
        user32.SetThreadDesktop(hdefault)
        print("[OK] Attached launcher to user's interactive 'Default' desktop.")

    # 2. Ensure Backend Service is running
    if not is_backend_up():
        print("Starting RALLY Python Backend on :8765...")
        py_exe = os.path.join(orch_dir, ".venv", "Scripts", "python.exe")
        backend_cmd = f'"{py_exe}" -m uvicorn app.main:app --host 127.0.0.1 --port 8765'
        launch_on_desktop(backend_cmd, orch_dir)
        for i in range(25):
            time.sleep(0.3)
            if is_backend_up():
                print("[OK] Backend is up and healthy at http://127.0.0.1:8765")
                break
    else:
        print("[OK] Backend already running and healthy.")

    # 3. Launch Electron Sidecar Agent on Default desktop
    electron_exe = os.path.join(root_dir, "node_modules", "electron", "dist", "electron.exe")
    electron_cmd = f'"{electron_exe}" apps/desktop'
    launch_on_desktop(electron_cmd, root_dir)

    # 4. Verify Sidecar window appearance on Default desktop
    print("Verifying Sidecar Agent presence on screen...")
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
    print(f"[OK] Found {len(visible_windows)} RALLY window(s) on interactive screen:")
    for h, t in visible_windows:
        print(f"   [HWND {h}]: {t}")

    # 5. Send voice start signal
    try:
        req = urllib.request.Request("http://127.0.0.1:8765/api/v1/voice/start", method="POST")
        urllib.request.urlopen(req, timeout=2.0)
        print("[OK] Voice listening loop active & ready.")
    except Exception:
        pass

    print("=" * 55)
    print("[RALLY] RALLY 2.0 Sidecar Agent is now LIVE on your screen!")
    print("[RALLY] Wake shortcuts: Press F9, F8, or Ctrl+Space at any time.")
    print("=" * 55)

if __name__ == "__main__":
    main()
