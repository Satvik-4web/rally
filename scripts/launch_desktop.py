import ctypes
import os
import sys
import time
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

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Attach to user's interactive desktop
    hdefault = user32.OpenDesktopW("Default", 0, False, 0x10000000)
    if hdefault:
        user32.SetThreadDesktop(hdefault)
        print("[OK] Attached to Default desktop.")

    si = STARTUPINFO()
    si.cb = ctypes.sizeof(STARTUPINFO)
    si.lpDesktop = r"WinSta0\Default"

    pi = PROCESS_INFORMATION()

    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    electron_exe = os.path.join(root_dir, "node_modules", "electron", "dist", "electron.exe")
    cmd = f'"{electron_exe}" apps/desktop'

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
        root_dir,
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not ok:
        err = kernel32.GetLastError()
        print(f"[ERROR] CreateProcessW failed with error {err}")
        return

    print(f"[OK] Launched Electron PID {pi.dwProcessId} on Default desktop.")

    # Wait 4 seconds for window to render and check
    time.sleep(4.0)

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    found = []

    def enum_cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()
                if any(k in title.lower() for k in ("rally", "sidecar", "mission control")):
                    found.append((hwnd, title))
        return True

    user32.EnumDesktopWindows(hdefault, WNDENUMPROC(enum_cb), 0)
    print(f"[OK] Active RALLY windows visible on interactive screen: {len(found)}")
    for h, t in found:
        print(f"  [HWND {h}]: {t}")

if __name__ == "__main__":
    main()
