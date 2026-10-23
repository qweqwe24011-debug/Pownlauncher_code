"""
Pownlauncher — внедрение внутриигрового оверлея (PownOverlay.dll) в процесс Minecraft.

Работает только на Windows. Два способа инъекции:
  1. injectpy   (рекомендуется, см. README_OVERLAY.md):
         git clone https://github.com/Florararie/injectpy.git
         cd injectpy && pip install .
  2. Встроенный fallback-метод CreateRemoteThread + LoadLibraryW (ctypes),
     не требует сторонних пакетов.

DLL ищется в порядке:
  1. <base_dir>/overlay/PownOverlay.dll
  2. <base_dir>/PownOverlay.dll
  3. sys._MEIPASS/overlay/PownOverlay.dll (для PyInstaller-сборки)

После успешной инъекции создаётся sentinel-файл custom/<version>/.overlay_injected,
чтобы не инжектить повторно при перезапуске той же сборки.
"""

import ctypes
import ctypes.wintypes
import os
import sys
import threading
import time
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

# ---- WinAPI константы ----
PROCESS_CREATE_THREAD = 0x0002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_READ = 0x0010

MEM_COMMIT = 0x1000
MEM_RELEASE = 0x8000

PAGE_READWRITE = 0x04
INFINITE = 0xFFFFFFFF

WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102


class OverlayError(Exception):
    """Ошибка инъекции оверлея."""


def default_dll_path(base_dir: str) -> str | None:
    """Найти PownOverlay.dll рядом с лаунчером (или внутри PyInstaller-архива)."""
    candidates = [
        os.path.join(base_dir, "overlay", "PownOverlay.dll"),
        os.path.join(base_dir, "PownOverlay.dll"),
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates += [
            os.path.join(meipass, "overlay", "PownOverlay.dll"),
            os.path.join(meipass, "PownOverlay.dll"),
        ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


# ============================================================
#  Способ 1: injectpy
# ============================================================

def _inject_with_injectpy(pid: int, dll_path: str) -> bool:
    try:
        from injectpy import Injector, InjectionResult
    except ImportError as e:
        raise OverlayError(f"injectpy не установлен: {e}")
    injector = Injector(verbose=False)
    # injectpy умеет искать по PID (process=None) или по имени процесса
    result = injector.inject(None, dll_path, pid)
    return result == InjectionResult.SUCCESS


# ============================================================
#  Способ 2: fallback — CreateRemoteThread + LoadLibraryW
# ============================================================

_kernel32 = None


def _k32():
    global _kernel32
    if _kernel32 is None:
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return _kernel32


def _inject_classic(pid: int, dll_path: str) -> bool:
    """Классическая инъекция: RemoteThread(LoadLibraryW(path))."""
    k32 = _k32()

    h_proc = k32.OpenProcess(
        PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION
        | PROCESS_VM_WRITE | PROCESS_VM_READ,
        False,
        pid,
    )
    if not h_proc:
        raise OverlayError(f"OpenProcess failed (err={ctypes.get_last_error()}, pid={pid}). "
                           f"Запустите лаунчер от администратора, если игра повышала привилегии.")

    path_bytes = os.path.abspath(dll_path).encode("utf-16-le") + b"\x00\x00"
    remote_buf = None
    try:
        remote_buf = k32.VirtualAllocEx(h_proc, None, len(path_bytes), MEM_COMMIT, PAGE_READWRITE)
        if not remote_buf:
            raise OverlayError(f"VirtualAllocEx failed (err={ctypes.get_last_error()})")

        written = ctypes.c_size_t(0)
        ok = k32.WriteProcessMemory(
            h_proc, remote_buf, path_bytes, len(path_bytes), ctypes.byref(written)
        )
        if not ok or written.value != len(path_bytes):
            raise OverlayError(f"WriteProcessMemory failed (err={ctypes.get_last_error()})")

        load_lib = k32.GetProcAddress(k32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
        if not load_lib:
            raise OverlayError("GetProcAddress(LoadLibraryW) failed")

        thread = k32.CreateRemoteThread(h_proc, None, 0, ctypes.c_void_p(load_lib),
                                        remote_buf, 0, None)
        if not thread:
            raise OverlayError(f"CreateRemoteThread failed (err={ctypes.get_last_error()})")
        try:
            wait_res = k32.WaitForSingleObject(thread, 15000)
            if wait_res == WAIT_TIMEOUT:
                raise OverlayError("Таймаут ожидания LoadLibraryW (15 c)")
            exit_code = wintypes.DWORD(0)
            k32.GetExitCodeThread(thread, ctypes.byref(exit_code))
            # exit_code — это HMODULE (32 бита). NULL => DLL не загрузилась.
            if exit_code.value == 0:
                raise OverlayError(
                    "LoadLibraryW вернул NULL: DLL не загрузилась "
                    "(проверьте разрядность x64 и зависимости DLL)"
                )
            return True
        finally:
            k32.CloseHandle(thread)
    finally:
        if remote_buf:
            k32.VirtualFreeEx(h_proc, remote_buf, 0, MEM_RELEASE)
        k32.CloseHandle(h_proc)


# ============================================================
#  Публичный API
# ============================================================

def inject_into_pid(pid: int, dll_path: str, prefer: str = "auto") -> str:
    """
    Внедрить DLL в процесс по PID. Возвращает имя использованного метода.
    prefer: "injectpy" | "classic" | "auto" (injectpy, затем classic).
    """
    if not IS_WINDOWS:
        raise OverlayError("Инъекция оверлея поддерживается только на Windows")
    if not os.path.isfile(dll_path):
        raise OverlayError(f"DLL не найдена: {dll_path}")

    errors = []
    if prefer in ("auto", "injectpy"):
        try:
            if _inject_with_injectpy(pid, dll_path):
                return "injectpy"
            errors.append("injectpy: InjectionResult != SUCCESS")
        except OverlayError as e:
            errors.append(f"injectpy: {e}")
        except Exception as e:
            errors.append(f"injectpy: {e}")
    if prefer in ("auto", "classic"):
        try:
            if _inject_classic(pid, dll_path):
                return "classic"
        except Exception as e:
            errors.append(f"classic: {e}")
    raise OverlayError("Все методы инъекции не удались: " + " | ".join(errors))


def process_alive(pid: int) -> bool:
    """Жив ли процесс (кроссплатформенная эвристика для Windows-потока)."""
    try:
        k32 = _k32()
        h = k32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
        if not h:
            return False
        code = wintypes.DWORD(0)
        still_running = k32.GetExitCodeThread(h, ctypes.byref(code))  # не критично
        k32.CloseHandle(h)
        return True
    except Exception:
        return False


def inject_overlay_async(proc, dll_path: str, logger=None,
                         wait_seconds: int = 10, stop_event: threading.Event | None = None,
                         sentinel_path: str | None = None) -> threading.Thread:
    """
    Фоновая инъекция после запуска Minecraft.

    proc          — subprocess.Popen процесса игры;
    wait_seconds  — пауза, чтобы JVM успела инициализировать OpenGL-контекст
                    (хук wglSwapBuffers ставится внутри DLL при первом свопе буфера);
    stop_event    — установить, если игра закрылась раньше времени (инъекция отменится);
    sentinel_path — файл-маркер «уже инжектили» (пишется при успехе).

    Возвращает запущенный daemon-поток.
    """
    def worker():
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if stop_event is not None and stop_event.is_set():
                return
            if proc.poll() is not None:
                if logger:
                    logger.info("[Overlay] Игра уже закрыта — инъекция отменена")
                return
            time.sleep(0.3)

        if stop_event is not None and stop_event.is_set():
            return

        pid = proc.pid
        if logger:
            logger.info(f"[Overlay] Ожидание {wait_seconds} сек. выполнено, инъекция в pid={pid}...")
        try:
            method = inject_into_pid(pid, dll_path)
            if logger:
                logger.info(f"[Overlay] DLL успешно внедрена (метод: {method})")
            if sentinel_path:
                try:
                    open(sentinel_path, "w").close()
                except OSError:
                    pass
        except Exception as e:
            if logger:
                logger.warning(f"[Overlay] Ошибка инъекции: {e}")

    t = threading.Thread(target=worker, daemon=True, name="OverlayInject")
    t.start()
    return t
