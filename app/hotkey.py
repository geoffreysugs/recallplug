"""Global hotkey (default Ctrl+Alt+P) through the Win32 RegisterHotKey API.

RegisterHotKey delivers WM_HOTKEY to the message queue of the thread that
registered it, so the hotkey lives in its own background thread with its own
GetMessage loop. stop() posts WM_QUIT to that thread. The Win32 calls sit in
a small wrapper (Win32HotkeyApi) that tests replace with a fake.
"""
from __future__ import annotations

import ctypes
import sys
import threading
from typing import Callable, Optional

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_USER = 0x0400
PM_NOREMOVE = 0x0000
VK_P = 0x50
PAUSE_MODIFIERS = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
PAUSE_LABEL = "Ctrl+Alt+P"


class Win32HotkeyApi:
    """Thin ctypes wrapper. Every method must be called from the hotkey thread,
    except post_quit()."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("global hotkeys need Windows")
        from ctypes import wintypes
        self._wintypes = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u = self.user32
        u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        u.RegisterHotKey.restype = wintypes.BOOL
        u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        u.UnregisterHotKey.restype = wintypes.BOOL
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                  wintypes.UINT, wintypes.UINT]
        u.GetMessageW.restype = wintypes.BOOL
        u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT, wintypes.UINT]
        u.PeekMessageW.restype = wintypes.BOOL
        u.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                         wintypes.WPARAM, wintypes.LPARAM]
        u.PostThreadMessageW.restype = wintypes.BOOL
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def thread_id(self) -> int:
        return int(self.kernel32.GetCurrentThreadId())

    def ensure_queue(self) -> None:
        """Create this thread's message queue so post_quit() can always reach it."""
        msg = self._wintypes.MSG()
        self.user32.PeekMessageW(ctypes.byref(msg), None, WM_USER, WM_USER, PM_NOREMOVE)

    def register(self, hotkey_id: int, modifiers: int, vk: int) -> bool:
        return bool(self.user32.RegisterHotKey(None, hotkey_id, modifiers, vk))

    def unregister(self, hotkey_id: int) -> None:
        self.user32.UnregisterHotKey(None, hotkey_id)

    def get_message(self):
        """Blocks. (message, wParam), or None on WM_QUIT or an error."""
        msg = self._wintypes.MSG()
        result = self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if result == 0 or result == -1:
            return None
        return int(msg.message), int(msg.wParam)

    def post_quit(self, thread_id: int) -> bool:
        return bool(self.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0))


class GlobalHotkey:
    """Calls `callback()` (on the hotkey thread) each time the key is pressed."""

    def __init__(self, callback: Callable[[], None], modifiers: int = PAUSE_MODIFIERS,
                 vk: int = VK_P, hotkey_id: int = 1,
                 api_factory: Callable[[], object] = Win32HotkeyApi) -> None:
        self._callback = callback
        self._modifiers = modifiers
        self._vk = vk
        self._id = hotkey_id
        self._api_factory = api_factory
        self._api = None
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._registered = False
        self.error: Optional[str] = None

    @property
    def registered(self) -> bool:
        return self._registered

    def start(self, timeout: float = 2.0) -> bool:
        """True if the hotkey was registered. Never raises."""
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="RecallPlug-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self._registered

    def _run(self) -> None:
        try:
            api = self._api_factory()
            self._api = api
            self._thread_id = api.thread_id()
            api.ensure_queue()
            self._registered = api.register(self._id, self._modifiers, self._vk)
        except Exception as exc:
            self.error = type(exc).__name__
            self._registered = False
        finally:
            self._ready.set()
        if not self._registered:
            return
        try:
            while True:
                msg = api.get_message()
                if msg is None:
                    break
                if msg[0] == WM_HOTKEY and msg[1] == self._id:
                    try:
                        self._callback()
                    except Exception:
                        pass               # a failing handler must not end the hotkey loop
        finally:
            self._registered = False
            try:
                api.unregister(self._id)
            except Exception:
                pass

    def stop(self, timeout: float = 1.0) -> None:
        thread = self._thread
        if thread is None or not thread.is_alive():
            return
        if self._api is not None and self._thread_id:
            self._api.post_quit(self._thread_id)
        thread.join(timeout)


def beep(paused: bool) -> None:
    """Short sound: lower when pausing, higher when resuming. Silent off Windows."""
    try:
        import winsound
        winsound.Beep(600 if paused else 1000, 120)
    except Exception:
        pass
