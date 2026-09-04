"""
Plauncher core — vanilla/Forge/Fabric/NeoForge/Quilt installer + prebuilt builds.
Fully offline (no Microsoft auth).

Этап 2 рефакторинга: шина событий (Event Bus). Ядро полностью развязано от UI.
mymain.py не трогается — внизу shim-слой с адаптерами callback → EventBus.
"""

import atexit
import ctypes
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid
import zipfile
from logging.handlers import RotatingFileHandler

import minecraft_launcher_lib as mll


# ============================================================
#  EventBus
# ============================================================

class EventBus:
    """Легковесная потокобезопасная шина событий."""

    def __init__(self):
        self._subs: dict[str, list[callable]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_name: str, callback: callable) -> None:
        with self._lock:
            self._subs.setdefault(event_name, []).append(callback)

    def unsubscribe(self, event_name: str, callback: callable) -> None:
        with self._lock:
            lst = self._subs.get(event_name, [])
            if callback in lst:
                lst.remove(callback)

    def emit(self, event_name: str, **kwargs) -> None:
        with self._lock:
            handlers = list(self._subs.get(event_name, []))
        for handler in handlers:
            try:
                handler(**kwargs)
            except Exception:
                logging.error(f"Error in event handler for '{event_name}'", exc_info=True)


# ============================================================
#  ConfigManager
# ============================================================

class ConfigManager:
    """Все пути, константы, TTL, настройки профиля."""

    def __init__(self, fs=None):
        self.fs = fs
        self.base_dir = self._resolve_base_dir()
        self.manifest_url = (
            "https://raw.githubusercontent.com/qweqwe24011-debug/"
            "Pownlauncher/refs/heads/main/builds.json"
        )
        self.manifest_cache_ttl = 3600
        self.versions_cache_ttl = 24 * 3600
        self.preserve_on_update = [
            "saves", "screenshots", "resourcepacks",
            "shaderpacks", "options.txt", "servers.dat",
        ]
        self.legacy_re = re.compile(r"^1\.(\d+)(?:\.\d+)*$")
        self.valid_nick_re = re.compile(r"^[a-zA-Z0-9_]{3,16}$")

    def _resolve_base_dir(self) -> str:
        if getattr(sys, "frozen", False):
            return os.path.dirname(os.path.abspath(sys.argv[0]))
        return os.path.dirname(os.path.abspath(__file__))

    def resource_path(self, filename: str) -> str:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return os.path.join(sys._MEIPASS, filename)
        return os.path.join(self.base_dir, filename)

    @property
    def manifest_cache_path(self) -> str:
        return os.path.join(self.base_dir, "builds_manifest.json")

    @property
    def versions_cache_file(self) -> str:
        return os.path.join(self.base_dir, "versions_cache.json")

    @property
    def settings_path(self) -> str:
        return os.path.join(self.base_dir, "profile.json")

    @property
    def playtime_path(self) -> str:
        return os.path.join(self.base_dir, "playtime.json")

    def custom_dir(self, name: str) -> str:
        return os.path.join(self.base_dir, "custom", name)

    def java_dir(self) -> str:
        return os.path.join(self.base_dir, "java")

    def log_path(self) -> str:
        p = os.path.join(self.base_dir, "plauncher.log")
        try:
            with open(p + ".tmp", "w") as f:
                f.write("test")
            os.remove(p + ".tmp")
            return p
        except OSError:
            pass
        if platform.system() == "Windows":
            appdata = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
            d = os.path.join(appdata, "Plauncher")
        else:
            d = os.path.expanduser("~/.local/share/plauncher")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "plauncher.log")

    def load_settings(self) -> dict:
        if self.fs is not None:
            return self.fs.json_read(self.settings_path) or {}
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_settings(self, data: dict) -> None:
        cur = self.load_settings()
        cur.update(data)
        if self.fs is not None:
            self.fs.json_write(self.settings_path, cur)
        else:
            os.makedirs(os.path.dirname(self.settings_path) or ".", exist_ok=True)
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=2)


# ============================================================
#  LoggerManager
# ============================================================

class LoggerManager:
    """Инкапсуляция логгера с оптимизированным flush."""

    def __init__(self, config: ConfigManager):
        self.config = config
        self.event_bus: EventBus | None = None
        self._local = threading.local()
        self._logger = logging.getLogger("plauncher")
        self._logger.setLevel(logging.DEBUG)
        self._file_handler = RotatingFileHandler(
            config.log_path(), maxBytes=5 * 1024 * 1024,
            backupCount=3, encoding="utf-8",
        )
        self._file_handler.setLevel(logging.DEBUG)
        self._file_handler.setFormatter(logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        self._console_handler = logging.StreamHandler(sys.stdout)
        self._console_handler.setLevel(logging.INFO)
        self._console_handler.setFormatter(logging.Formatter(
            "[%(levelname)s] %(message)s"
        ))
        self._logger.addHandler(self._file_handler)
        self._logger.addHandler(self._console_handler)
        self._file_handler.flush()
        atexit.register(self._file_handler.flush)
        self.info(f"Plauncher started. Log path: {config.log_path()}")

    def set_event_bus(self, event_bus: EventBus) -> None:
        self.event_bus = event_bus

    def _emit_log(self, level: str, msg: str) -> None:
        if self.event_bus is None:
            return
        if getattr(self._local, "_is_emitting", False):
            return
        self._local._is_emitting = True
        try:
            self.event_bus.emit("log_message", level=level, message=msg)
        finally:
            self._local._is_emitting = False

    def info(self, msg: str) -> None:
        self._logger.info(msg)
        self._emit_log("info", msg)

    def debug(self, msg: str) -> None:
        self._logger.debug(msg)
        self._emit_log("debug", msg)

    def warning(self, msg: str) -> None:
        self._logger.warning(msg)
        self._file_handler.flush()
        self._emit_log("warning", msg)

    def error(self, msg: str) -> None:
        self._logger.error(msg)
        self._file_handler.flush()
        self._emit_log("error", msg)

    def exception(self, msg: str) -> None:
        self._logger.exception(msg)
        self._file_handler.flush()
        self._emit_log("error", msg)

    @property
    def log_path(self) -> str:
        return self.config.log_path()


# ============================================================
#  FileSystemManager
# ============================================================

class FileSystemManager:
    """Операции с диском: JSON, свободное место, безопасное удаление, zip."""

    def __init__(self, logger: LoggerManager, config: ConfigManager):
        self.logger = logger
        self.config = config
        self._write_lock = threading.Lock()

    def json_read(self, path: str) -> dict | None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.debug(f"Failed to read JSON {path}: {e}")
            return None

    def json_write(self, path: str, data: dict) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        temp_path = path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        # Retry logic for WinError 32 (file in use)
        for attempt in range(3):
            try:
                with self._write_lock:
                    os.replace(temp_path, path)
                return
            except (PermissionError, OSError) as e:
                self.logger.warning(f"json_write attempt {attempt + 1} failed for {path}: {e}")
                if attempt < 2:
                    time.sleep(0.1)
                else:
                    self.logger.error(f"json_write FAILED after 3 attempts: {path}")
                    # Clean up temp file to avoid leaving garbage
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

    def get_free_space_gb(self, path: str) -> float:
        try:
            if hasattr(shutil, "disk_usage"):
                return shutil.disk_usage(path).free / (1024 ** 3)
        except Exception as e:
            self.logger.debug(f"disk_usage failed: {e}")
        return -1.0

    def check_free_space(self, path: str, needed_gb: float, label: str = "") -> bool:
        free = self.get_free_space_gb(path)
        if free < 0:
            self.logger.warning(f"Cannot check free space for {label}")
            return True
        self.logger.debug(
            f"Free space at {path or '.'}: {free:.1f} GB, needed: {needed_gb:.1f} GB"
        )
        if free < needed_gb:
            self.logger.warning(
                f"Not enough space for {label}: {free:.1f} < {needed_gb:.1f} GB"
            )
            return False
        return True

    def safe_rmtree(self, path: str, max_retries: int = 3, delay: float = 0.5) -> bool:
        if not os.path.isdir(path):
            return True
        for attempt in range(1, max_retries + 1):
            try:
                shutil.rmtree(path)
                self.logger.debug(f"safe_rmtree succeeded: {path}")
                return True
            except Exception as e:
                self.logger.warning(f"safe_rmtree attempt {attempt} failed for {path}: {e}")
                if attempt < max_retries:
                    time.sleep(delay)
        self.logger.error(f"safe_rmtree FAILED after {max_retries} attempts: {path}")
        return False

    def safe_remove(self, path: str, max_retries: int = 3, delay: float = 0.5) -> bool:
        if not os.path.isfile(path):
            return True
        for attempt in range(1, max_retries + 1):
            try:
                os.remove(path)
                self.logger.debug(f"safe_remove succeeded: {path}")
                return True
            except Exception as e:
                self.logger.warning(f"safe_remove attempt {attempt} failed for {path}: {e}")
                if attempt < max_retries:
                    time.sleep(delay)
        self.logger.error(f"safe_remove FAILED after {max_retries} attempts: {path}")
        return False

    def copy_dir(self, src: str, dst: str) -> None:
        if os.path.isdir(dst):
            self.safe_rmtree(dst)
        shutil.copytree(src, dst)

    def copy_file(self, src: str, dst: str) -> None:
        shutil.copy2(src, dst)

    def extract_flat(self, zip_path: str, dest: str, skip: str) -> None:
        self.logger.info(f"Extracting {zip_path} -> {dest}")
        with zipfile.ZipFile(zip_path, "r") as z:
            for member in z.namelist():
                member_path = os.path.join(dest, member)
                if os.path.commonpath(
                    [os.path.abspath(dest), os.path.abspath(member_path)]
                ) != os.path.abspath(dest):
                    raise RuntimeError(f"Zip slip detected: {member}")
            z.extractall(dest)
        self.safe_remove(zip_path)
        entries = [e for e in os.listdir(dest) if e != os.path.basename(skip)]
        target_dir = None
        for e in entries:
            p = os.path.join(dest, e)
            if os.path.isdir(p):
                if os.path.isdir(os.path.join(p, "versions")) or os.path.isdir(os.path.join(p, "mods")):
                    target_dir = p
                    break
        if target_dir is not None:
            for item in os.listdir(target_dir):
                shutil.move(os.path.join(target_dir, item), os.path.join(dest, item))
            os.rmdir(target_dir)
        self.logger.info(f"Extraction complete: {dest}")

    def write_meta(self, d: str, name: str, version: str,
                   loader: str = "build", mc_version: str = "") -> None:
        self.json_write(
            os.path.join(d, ".build_meta.json"),
            {"display_name": name, "version": version,
             "loader": loader, "mc_version": mc_version}
        )


# ============================================================
#  DownloadManager
# ============================================================

class DownloadManager:
    """Сетевые запросы, кэш манифестов и версий с потокобезопасностью."""

    def __init__(self, logger: LoggerManager, config: ConfigManager,
                 fs: FileSystemManager, event_bus: EventBus):
        self.logger = logger
        self.config = config
        self.fs = fs
        self.event_bus = event_bus
        self._manifest_mem = None
        self._manifest_mem_time = 0
        self._versions_cache: dict[bool, list[str]] = {}
        self._manifest_lock = threading.Lock()
        self._versions_lock = threading.Lock()

    def download(self, url: str, dest: str, task_id: str | None = None,
                 max_retries: int = 3) -> None:
        self.logger.info(f"Downloading {url} -> {dest}")
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Plauncher"}
        )
        for attempt in range(1, max_retries + 1):
            try:
                downloaded = 0
                last_update = 0
                with urllib.request.urlopen(req, timeout=120) as resp:
                    total = int(resp.headers.get("Content-Length", 0))
                    if task_id:
                        self.event_bus.emit(
                            "download_progress",
                            task_id=task_id, current=0, total=total,
                            status_text="Downloading..."
                        )
                    with open(dest, "wb") as f:
                        while True:
                            chunk = resp.read(262144)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            now = time.time()
                            if now - last_update > 0.2 and task_id:
                                self.event_bus.emit(
                                    "download_progress",
                                    task_id=task_id, current=downloaded,
                                    total=total, status_text="Downloading..."
                                )
                                last_update = now
                self.logger.info(f"Download complete: {dest} ({downloaded} bytes)")
                return
            except urllib.error.HTTPError as e:
                if e.code in (404, 403, 410):
                    self.logger.error(f"Download failed (HTTP {e.code}): {url}")
                    raise RuntimeError(f"Файл не найден (HTTP {e.code}): {url}")
                if attempt < max_retries:
                    wait = 2 ** attempt
                    self.logger.warning(
                        f"Download attempt {attempt} failed (HTTP {e.code}). "
                        f"Retrying in {wait}s..."
                    )
                    time.sleep(wait)
                else:
                    self.logger.error(
                        f"Download failed after {max_retries} attempts (HTTP {e.code})"
                    )
                    raise
            except Exception as e:
                if attempt < max_retries:
                    wait = 2 ** attempt
                    self.logger.warning(
                        f"Download attempt {attempt} failed: {e}. "
                        f"Retrying in {wait}s..."
                    )
                    time.sleep(wait)
                else:
                    self.logger.error(
                        f"Download failed after {max_retries} attempts: {e}"
                    )
                    raise

    def fetch_manifest(self, force: bool = False) -> dict:
        now = time.time()
        if not force and self._manifest_mem is not None and \
           now - self._manifest_mem_time < self.config.manifest_cache_ttl:
            self.logger.debug("Using in-memory manifest cache")
            return self._manifest_mem

        cache_path = self.config.manifest_cache_path
        if not force and os.path.isfile(cache_path):
            try:
                mtime = os.path.getmtime(cache_path)
                if now - mtime < self.config.manifest_cache_ttl:
                    data = self.fs.json_read(cache_path)
                    if data is not None:
                        with self._manifest_lock:
                            self._manifest_mem = data
                            self._manifest_mem_time = now
                        self.logger.debug("Using disk manifest cache")
                        return data
            except OSError as e:
                self.logger.debug(f"Cache read error: {e}")

        data = None
        try:
            self.logger.info(f"Fetching manifest from {self.config.manifest_url}")
            req = urllib.request.Request(
                self.config.manifest_url,
                headers={"User-Agent": "Mozilla/5.0 Plauncher"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict):
                self.fs.json_write(cache_path, data)
        except Exception as e:
            self.logger.error(f"Failed to fetch manifest: {e}")

        if data is None:
            data = self.fs.json_read(cache_path)

        with self._manifest_lock:
            if not force and self._manifest_mem is not None and \
               now - self._manifest_mem_time < self.config.manifest_cache_ttl:
                self.logger.debug("Using in-memory manifest cache (post-fetch race)")
                return self._manifest_mem
            self._manifest_mem = data if data is not None else {}
            self._manifest_mem_time = now
            if not self._manifest_mem:
                self.logger.warning("No manifest available (offline and no cache)")
            return self._manifest_mem

    def manifest_info(self) -> dict:
        builds = self.fetch_manifest()
        cache_path = self.config.manifest_cache_path
        cached = os.path.isfile(cache_path)
        age = None
        if cached:
            try:
                age = time.time() - os.path.getmtime(cache_path)
            except OSError:
                pass
        return {
            "cached": cached,
            "age_seconds": age,
            "count": len(builds),
            "online": bool(cached and age is not None and age < self.config.manifest_cache_ttl),
        }

    def _load_versions_cache(self) -> list[str] | None:
        try:
            path = self.config.versions_cache_file
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list) and data:
                    return data
        except Exception as e:
            self.logger.debug(f"versions cache read failed: {e}")
        return None

    def _save_versions_cache(self, versions: list[str]) -> None:
        try:
            with open(self.config.versions_cache_file, "w", encoding="utf-8") as f:
                json.dump(versions, f)
        except Exception as e:
            self.logger.error(f"versions cache save failed: {e}")

    def get_available_versions(self, release_only: bool = True) -> list[str]:
        if release_only in self._versions_cache:
            return self._versions_cache[release_only]

        cached = self._load_versions_cache()
        if cached is not None:
            try:
                mtime = os.path.getmtime(self.config.versions_cache_file)
                age = time.time() - mtime
                if age < self.config.versions_cache_ttl:
                    with self._versions_lock:
                        self._versions_cache[release_only] = cached
                    self.logger.debug(f"versions cache hit (disk, age={int(age)}s)")
                    return cached
                self.logger.debug(f"versions cache stale (disk, age={int(age)}s), refreshing...")
            except OSError:
                pass

        result = None
        try:
            versions = mll.utils.get_version_list()
            if release_only:
                versions = [v for v in versions if v["type"] == "release"]
            ids = [v["id"] for v in versions]
            result = [
                vid for vid in ids
                if not (m := self.config.legacy_re.match(vid)) or int(m.group(1)) >= 6
            ]
            self._save_versions_cache(result)
            self.logger.info(f"versions fetched from network, count={len(result)}")
        except Exception as e:
            self.logger.error(f"versions fetch failed: {e}")

        with self._versions_lock:
            if release_only in self._versions_cache:
                return self._versions_cache[release_only]
            if result is not None:
                self._versions_cache[release_only] = result
                return result
            if cached is not None:
                self.logger.info("versions fallback to stale disk cache")
                self._versions_cache[release_only] = cached
                return cached
            fallback = ["1.20.1", "1.19.4", "1.18.2", "1.16.5", "1.12.2"]
            self._versions_cache[release_only] = fallback
            return fallback


# ============================================================
#  JavaManager
# ============================================================

class JavaManager:
    """Поиск, проверка и автозагрузка JRE."""

    def __init__(self, logger: LoggerManager, config: ConfigManager,
                 fs: FileSystemManager, dl: DownloadManager):
        self.logger = logger
        self.config = config
        self.fs = fs
        self.dl = dl

    def _java_major(self, exe: str) -> int | None:
        try:
            r = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=10)
            out = r.stderr or r.stdout
            m = re.search(r'version "(\d+)(?:\.(\d+))?"', out)
            if not m:
                return None
            major = int(m.group(1))
            return int(m.group(2)) if major == 1 and m.group(2) else major
        except Exception as e:
            self.logger.debug(f"Java check failed for {exe}: {e}")
            return None

    def _find_java(self, need: int) -> str | None:
        exe = "java.exe" if platform.system() == "Windows" else "java"
        candidates = []
        for p in os.environ.get("PATH", "").split(os.pathsep):
            c = os.path.join(p, exe)
            if os.path.isfile(c):
                candidates.append(c)
        jh = os.environ.get("JAVA_HOME", "")
        if jh:
            c = os.path.join(jh, "bin", exe)
            if os.path.isfile(c):
                candidates.append(c)
        jr = self.config.java_dir()
        if os.path.isdir(jr):
            # Сначала проверяем плоскую структуру (bin/ прямо в java_dir)
            flat_bin = os.path.join(jr, "bin", exe)
            if os.path.isfile(flat_bin):
                candidates.append(flat_bin)
            # Затем проверяем вложенную структуру (jdk*/bin/)
            for n in os.listdir(jr):
                c = os.path.join(jr, n, "bin", exe)
                if os.path.isfile(c):
                    candidates.append(c)
        for c in candidates:
            if self._java_major(c) == need:
                self.logger.debug(f"Found Java {need} at {c}")
                return c
        self.logger.debug(f"Java {need} not found in candidates")
        return None

    def java_for_mc(self, mc: str) -> int:
        m = re.match(r"^1\.(\d+)(?:\.(\d+))?", mc)
        if not m:
            return 17
        major = int(m.group(1))
        minor = int(m.group(2)) if m.group(2) else 0
        if major < 17:
            return 8
        if major == 20 and minor >= 5:
            return 21
        if major >= 21:
            return 21
        return 17

    def _adoptium_platform(self) -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()
        arch_map = {
            "amd64": "x64", "x86_64": "x64",
            "i386": "x86", "i686": "x86",
            "arm64": "aarch64", "aarch64": "aarch64",
            "armv7l": "arm", "armv6l": "arm",
        }
        arch = arch_map.get(machine, "x64")
        if system == "windows":
            return f"windows/{arch}"
        elif system == "linux":
            return f"linux/{arch}"
        elif system == "darwin":
            return f"mac/{arch}"
        else:
            self.logger.warning(f"Unknown platform {system}/{machine}, falling back to windows/x64")
            return "windows/x64"

    def get_java(self, need: int = 17) -> str:
        found = self._find_java(need)
        if found:
            return found
        plat = self._adoptium_platform()
        url = f"https://api.adoptium.net/v3/binary/latest/{need}/ga/{plat}/jre/hotspot/normal/eclipse"
        self.logger.info(f"Downloading Java {need} for {plat} from {url}")
        jr = self.config.java_dir()
        os.makedirs(jr, exist_ok=True)
        zp = os.path.join(jr, f"jre{need}.zip")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                with open(zp, "wb") as f:
                    f.write(r.read())
            self.logger.info(f"Java {need} downloaded ({os.path.getsize(zp)} bytes)")
        except Exception as e:
            self.logger.error(f"Failed to download Java {need}: {e}")
            raise RuntimeError(f"Не удалось скачать Java {need}: {e}")
        with zipfile.ZipFile(zp, "r") as z:
            z.extractall(jr)
        os.remove(zp)
        found = self._find_java(need)
        if not found:
            self.logger.error("Java downloaded but not found after extraction")
            raise RuntimeError("Java скачана, но не найдена после распаковки")
        self.logger.info(f"Java {need} ready at {found}")
        return found


# ============================================================
#  InstallationManager
# ============================================================

class InstallationManager:
    """Установка vanilla/modloader, распаковка, защита от Zip Slip."""

    def __init__(self, logger: LoggerManager, config: ConfigManager,
                 fs: FileSystemManager, dl: DownloadManager,
                 java: JavaManager, event_bus: EventBus):
        self.logger = logger
        self.config = config
        self.fs = fs
        self.dl = dl
        self.java = java
        self.event_bus = event_bus

    def _has_neoforge(self) -> bool:
        if not hasattr(mll, "mod_loader"):
            return False
        try:
            return "neoforge" in mll.mod_loader.list_mod_loader()
        except Exception as e:
            self.logger.debug(f"mod_loader.list_mod_loader() failed: {e}")
            return False

    def _has_quilt(self) -> bool:
        return hasattr(mll, "quilt")

    def _get_neoforge_loader(self):
        return mll.mod_loader.get_mod_loader("neoforge")

    def is_loader_supported(self, mc: str, loader: str) -> bool:
        if loader == "vanilla":
            return True
        if loader == "forge":
            return mll.forge.find_forge_version(mc) is not None
        m = self.config.legacy_re.match(mc)
        if not m:
            return False
        v = int(m.group(1))
        if loader == "fabric":
            return v >= 14
        if loader == "neoforge":
            if not self._has_neoforge():
                return False
            try:
                return self._get_neoforge_loader().is_minecraft_version_supported(mc)
            except Exception as e:
                self.logger.debug(f"NeoForge version check failed for {mc}: {e}")
                return False
        if loader == "quilt":
            if not self._has_quilt():
                return False
            return v >= 18
        return False

    def cleanup_broken(self) -> None:
        root = self.config.custom_dir("")
        if not os.path.isdir(root):
            return
        for n in os.listdir(root):
            p = os.path.join(root, n)
            if os.path.isdir(p) and os.path.isfile(os.path.join(p, ".installing")):
                self.logger.warning(f"Cleaning up broken install: {n}")
                self.fs.safe_rmtree(p)

    def is_custom_installed(self, name: str) -> bool:
        d = self.config.custom_dir(name)
        if not os.path.isdir(d) or os.path.isfile(os.path.join(d, ".installing")):
            return False
        if os.path.isfile(os.path.join(d, ".build_meta.json")):
            return True
        vd = os.path.join(d, "versions")
        return os.path.isdir(vd) and len(os.listdir(vd)) > 0

    def _mll_callback_adapter(self, task_id: str | None):
        """Адаптер: callback minecraft-launcher-lib → EventBus."""
        if task_id is None:
            return {"setStatus": lambda t: None,
                    "setProgress": lambda v: None,
                    "setMax": lambda v: None}
        def set_status(text):
            self.event_bus.emit(
                "download_progress", task_id=task_id,
                current=0, total=0, status_text=text
            )
        def set_progress(value):
            self.event_bus.emit(
                "download_progress", task_id=task_id,
                current=value, total=0, status_text=""
            )
        def set_max(value):
            pass
        return {"setStatus": set_status, "setProgress": set_progress, "setMax": set_max}

    def _install_core(self, d: str, mc: str, loader: str,
                      task_id: str | None = None) -> str:
        self.logger.info(f"Starting install: {mc} / {loader} -> {d}")
        try:
            os.makedirs(d, exist_ok=True)
            flag = os.path.join(d, ".installing")
            open(flag, "w").close()
            if not self.fs.check_free_space(self.config.base_dir, 2.0, f"{mc}-{loader}"):
                raise RuntimeError("Недостаточно свободного места на диске (нужно минимум 2 ГБ)")
            self.java.get_java(self.java.java_for_mc(mc))
            mll_cb = self._mll_callback_adapter(task_id)
            mll.install.install_minecraft_version(
                version=mc, minecraft_directory=d, callback=mll_cb
            )
            os.makedirs(os.path.join(d, "mods"), exist_ok=True)
            vd = os.path.join(d, "versions")
            if loader == "vanilla":
                if task_id:
                    self.event_bus.emit(
                        "download_progress", task_id=task_id,
                        current=100, total=100, status_text="Vanilla install complete"
                    )
                self.logger.info(f"Vanilla install complete: {mc}")
                if task_id:
                    self.event_bus.emit(
                        "task_finished", task_id=task_id,
                        success=True, message=f"Vanilla {mc} installed"
                    )
                return mc
            if loader == "forge":
                fv = mll.forge.find_forge_version(mc)
                if fv is None:
                    raise RuntimeError(f"No Forge for {mc}")
                if task_id:
                    self.event_bus.emit(
                        "download_progress", task_id=task_id,
                        current=0, total=0, status_text=f"Installing Forge {fv}..."
                    )
                self.logger.info(f"Installing Forge {fv}")
                mll.forge.install_forge_version(fv, d, callback=mll_cb)
                cands = [n for n in os.listdir(vd) if n != mc and mc in n]
                result = cands[0] if cands else fv
                if task_id:
                    self.event_bus.emit(
                        "task_finished", task_id=task_id,
                        success=True, message=f"Forge {mc} installed"
                    )
                return result
            if loader == "fabric":
                if task_id:
                    self.event_bus.emit(
                        "download_progress", task_id=task_id,
                        current=0, total=0, status_text=f"Installing Fabric for {mc}..."
                    )
                self.logger.info(f"Installing Fabric for {mc}")
                mll.fabric.install_fabric(mc, d, callback=mll_cb)
                cands = [n for n in os.listdir(vd) if n.startswith("fabric-loader") and mc in n]
                result = cands[0] if cands else next((n for n in os.listdir(vd) if n != mc), f"fabric-loader-{mc}")
                if task_id:
                    self.event_bus.emit(
                        "task_finished", task_id=task_id,
                        success=True, message=f"Fabric {mc} installed"
                    )
                return result
            if loader == "neoforge":
                if not self._has_neoforge():
                    raise RuntimeError(
                        "NeoForge не поддерживается установленной версией minecraft-launcher-lib "
                        "(нужна версия >= 8.0, использующая mod_loader). "
                        "Обновите: pip install --upgrade minecraft-launcher-lib"
                    )
                if task_id:
                    self.event_bus.emit(
                        "download_progress", task_id=task_id,
                        current=0, total=0, status_text=f"Installing NeoForge for {mc}..."
                    )
                self.logger.info(f"Installing NeoForge for {mc}")
                nf_loader = self._get_neoforge_loader()
                installed_vid = nf_loader.install(mc, d, callback=mll_cb)
                self.logger.info(f"NeoForge installed: {installed_vid}")
                if task_id:
                    self.event_bus.emit(
                        "task_finished", task_id=task_id,
                        success=True, message=f"NeoForge {mc} installed"
                    )
                return installed_vid
            if loader == "quilt":
                if not self._has_quilt():
                    raise RuntimeError(
                        "Quilt не поддерживается установленной версией minecraft-launcher-lib. "
                        "Обновите: pip install --upgrade minecraft-launcher-lib"
                    )
                if task_id:
                    self.event_bus.emit(
                        "download_progress", task_id=task_id,
                        current=0, total=0, status_text=f"Installing Quilt for {mc}..."
                    )
                self.logger.info(f"Installing Quilt for {mc}")
                mll.quilt.install_quilt(mc, d, callback=mll_cb)
                cands = [n for n in os.listdir(vd) if n.startswith("quilt-loader") and mc in n]
                result = cands[0] if cands else next((n for n in os.listdir(vd) if n != mc), f"quilt-loader-{mc}")
                if task_id:
                    self.event_bus.emit(
                        "task_finished", task_id=task_id,
                        success=True, message=f"Quilt {mc} installed"
                    )
                return result
            raise ValueError(f"Unknown loader: {loader}")
        except Exception as e:
            self.logger.exception(f"Install failed for {mc}/{loader}: {e}")
            self.fs.safe_rmtree(d)
            if task_id:
                self.event_bus.emit(
                    "task_finished", task_id=task_id,
                    success=False, message=str(e)
                )
            raise
        finally:
            flag = os.path.join(d, ".installing")
            if os.path.isfile(flag):
                os.remove(flag)

    def install_custom(self, mc: str, loader: str, name: str,
                       task_id: str | None = None) -> str:
        return self._install_core(self.config.custom_dir(name), mc, loader, task_id)

    def installed_build_version(self, name: str) -> str | None:
        meta = self.fs.json_read(os.path.join(self.config.custom_dir(name), ".build_meta.json"))
        return meta.get("version") if meta else None

    def is_build_update_available(self, name: str, manifest: dict | None = None) -> bool:
        builds = manifest if manifest is not None else self.dl.fetch_manifest()
        info = builds.get(name)
        if not info or not os.path.isdir(self.config.custom_dir(name)):
            return False
        inst = self.installed_build_version(name)
        return inst is None or inst != info.get("version", "")

    def download_build(self, name: str, url: str | None = None,
                       task_id: str | None = None) -> None:
        builds = self.dl.fetch_manifest()
        info = builds.get(name)
        if info is None:
            raise ValueError(f"Build '{name}' not found")
        url = url or info.get("url")
        if not url:
            raise ValueError(f"No URL for '{name}'")
        d = self.config.custom_dir(name)
        if os.path.isdir(d):
            raise RuntimeError("Already installed")
        if not self.fs.check_free_space(self.config.base_dir, 3.0, f"build {name}"):
            raise RuntimeError("Недостаточно свободного места на диске (нужно минимум 3 ГБ)")
        os.makedirs(d, exist_ok=True)
        flag = os.path.join(d, ".installing")
        open(flag, "w").close()
        self.logger.info(f"Downloading build '{name}' from {url}")
        try:
            if task_id:
                self.event_bus.emit(
                    "download_progress", task_id=task_id,
                    current=0, total=0, status_text="Downloading..."
                )
            zp = os.path.join(d, "_dl.zip")
            self.dl.download(url, zp, task_id=task_id)
            if task_id:
                self.event_bus.emit(
                    "download_progress", task_id=task_id,
                    current=50, total=100, status_text="Extracting..."
                )
            self.fs.extract_flat(zp, d, flag)
            self.fs.write_meta(
                d, name, info.get("version", ""),
                info.get("loader", "build"), info.get("mc_version", "")
            )
            self.logger.info(f"Build '{name}' installed successfully")
            if task_id:
                self.event_bus.emit(
                    "task_finished", task_id=task_id,
                    success=True, message=f"Build '{name}' installed"
                )
        except Exception as e:
            self.logger.exception(f"Build download failed: {e}")
            self.fs.safe_rmtree(d)
            if task_id:
                self.event_bus.emit(
                    "task_finished", task_id=task_id,
                    success=False, message=str(e)
                )
            raise
        finally:
            if os.path.isfile(flag):
                os.remove(flag)

    def update_build(self, name: str, task_id: str | None = None) -> None:
        builds = self.dl.fetch_manifest()
        info = builds.get(name)
        if not info:
            raise ValueError(f"Unknown build: {name}")
        d = self.config.custom_dir(name)
        if not os.path.isdir(d):
            raise RuntimeError("Not installed")
        url = info.get("url")
        if not url:
            raise ValueError(f"No URL for '{name}'")
        if not self.fs.check_free_space(self.config.base_dir, 3.0, f"update {name}"):
            raise RuntimeError("Недостаточно свободного места для обновления (нужно минимум 3 ГБ)")
        bak = d + "_bak"
        if os.path.isdir(bak):
            self.fs.safe_rmtree(bak)
        os.makedirs(bak, exist_ok=True)
        flag = os.path.join(d, ".installing")
        open(flag, "w").close()
        self.logger.info(f"Updating build '{name}'")
        try:
            if task_id:
                self.event_bus.emit(
                    "download_progress", task_id=task_id,
                    current=0, total=0, status_text="Backing up..."
                )
            for item in self.config.preserve_on_update:
                src = os.path.join(d, item)
                if not os.path.exists(src):
                    continue
                dst = os.path.join(bak, item)
                if os.path.isdir(src):
                    self.fs.copy_dir(src, dst)
                else:
                    self.fs.copy_file(src, dst)
            if task_id:
                self.event_bus.emit(
                    "download_progress", task_id=task_id,
                    current=10, total=100, status_text="Removing old..."
                )
            for e in os.listdir(d):
                if e == os.path.basename(flag):
                    continue
                p = os.path.join(d, e)
                if os.path.isdir(p):
                    self.fs.safe_rmtree(p)
                else:
                    self.fs.safe_remove(p)
            if task_id:
                self.event_bus.emit(
                    "download_progress", task_id=task_id,
                    current=20, total=100, status_text="Downloading..."
                )
            zp = os.path.join(d, "_dl.zip")
            self.dl.download(url, zp, task_id=task_id)
            if task_id:
                self.event_bus.emit(
                    "download_progress", task_id=task_id,
                    current=70, total=100, status_text="Extracting..."
                )
            self.fs.extract_flat(zp, d, flag)
            if task_id:
                self.event_bus.emit(
                    "download_progress", task_id=task_id,
                    current=80, total=100, status_text="Restoring..."
                )
            for item in self.config.preserve_on_update:
                src = os.path.join(bak, item)
                if not os.path.exists(src):
                    continue
                dst = os.path.join(d, item)
                if os.path.isdir(src):
                    self.fs.copy_dir(src, dst)
                else:
                    self.fs.copy_file(src, dst)
            self.fs.write_meta(
                d, name, info.get("version", ""),
                info.get("loader", "build"), info.get("mc_version", "")
            )
            self.logger.info(f"Build '{name}' updated successfully")
            if task_id:
                self.event_bus.emit(
                    "task_finished", task_id=task_id,
                    success=True, message=f"Build '{name}' updated"
                )
        except Exception as e:
            self.logger.exception(f"Build update failed: {e}")
            if task_id:
                self.event_bus.emit(
                    "download_progress", task_id=task_id,
                    current=0, total=0, status_text="Error, restoring backup..."
                )
            for item in self.config.preserve_on_update:
                src = os.path.join(bak, item)
                if not os.path.exists(src):
                    continue
                dst = os.path.join(d, item)
                try:
                    if os.path.isdir(src):
                        self.fs.copy_dir(src, dst)
                    else:
                        self.fs.copy_file(src, dst)
                except Exception as restore_err:
                    self.logger.error(f"Failed to restore {item}: {restore_err}")
            if task_id:
                self.event_bus.emit(
                    "task_finished", task_id=task_id,
                    success=False, message=str(e)
                )
            raise
        finally:
            self.fs.safe_rmtree(bak)
            if os.path.isfile(flag):
                os.remove(flag)


# ============================================================
#  LauncherEngine
# ============================================================

class LauncherEngine:
    """Главный координатор (Facade). Создает и связывает все компоненты."""

    def __init__(self):
        self.event_bus = EventBus()
        self.config = ConfigManager()
        self.logger = LoggerManager(self.config)
        self.logger.set_event_bus(self.event_bus)
        self.fs = FileSystemManager(self.logger, self.config)
        self.config.fs = self.fs
        self.dl = DownloadManager(self.logger, self.config, self.fs, self.event_bus)
        self.java = JavaManager(self.logger, self.config, self.fs, self.dl)
        self.install = InstallationManager(
            self.logger, self.config, self.fs, self.dl, self.java, self.event_bus
        )

    def ensure_dependencies(self) -> str:
        if getattr(sys, "frozen", False):
            return ""
        has_neo = self.install._has_neoforge()
        has_quilt = self.install._has_quilt()
        if has_neo and has_quilt:
            return ""
        pip_error = None
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "minecraft-launcher-lib"],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                pip_error = (result.stderr or result.stdout or "неизвестная ошибка pip").strip()[-300:]
            else:
                import importlib
                importlib.reload(mll)
                for sub in ("mod_loader", "quilt", "forge", "fabric"):
                    try:
                        submod = importlib.import_module(f"minecraft_launcher_lib.{sub}")
                        setattr(mll, sub, submod)
                    except ImportError:
                        pass
        except subprocess.TimeoutExpired:
            pip_error = "pip install превысил таймаут (60с) — возможно, нет сети"
        except Exception as e:
            pip_error = str(e)
        has_neo = self.install._has_neoforge()
        has_quilt = self.install._has_quilt()
        if has_neo and has_quilt:
            return "minecraft-launcher-lib обновлен"
        missing = []
        if not has_neo:
            missing.append("NeoForge")
        if not has_quilt:
            missing.append("Quilt")
        installed_ver = getattr(mll, "__version__", "неизвестна")
        self.logger.error(f"ensure_dependencies: missing={missing}, pip_error={pip_error}, version={installed_ver}")
        msg = f"Внимание: {', '.join(missing)} не поддерживаются." + "\n"
        if pip_error:
            msg += f"Ошибка pip: {pip_error}" + "\n"
        msg += "Обновите вручную: pip install --upgrade minecraft-launcher-lib (нужна версия >= 8.0)"
        return msg

    def get_total_ram_gb(self) -> float:
        try:
            import psutil
            return psutil.virtual_memory().total / (1024 ** 3)
        except Exception:
            pass
        if platform.system() == "Windows":
            try:
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]
                s = MEMORYSTATUSEX()
                s.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
                return s.ullTotalPhys / (1024 ** 3)
            except Exception:
                pass
        return 8.0

    def get_recommended_ram(self, fraction: float = 0.75) -> int:
        return max(2, int(self.get_total_ram_gb() * fraction))

    def is_valid_username(self, u: str) -> bool:
        return bool(self.config.valid_nick_re.match(u))

    def _detect_vid(self, d: str, mc: str, loader: str) -> str:
        vd = os.path.join(d, "versions")
        if not os.path.isdir(vd):
            if loader == "build":
                meta = self.fs.json_read(os.path.join(d, ".build_meta.json"))
                if meta and meta.get("mc_version"):
                    return meta["mc_version"]
            raise RuntimeError("Not installed")
        cands = os.listdir(vd)
        if loader == "vanilla":
            return mc
        if loader == "build":
            plain = re.compile(r"^\d+(\.\d+)+$")
            modded = [c for c in cands if not plain.match(c)]
            return modded[0] if modded else cands[0]
        filters = {
            "forge": lambda n: n != mc and mc in n,
            "fabric": lambda n: n.startswith("fabric-loader") and mc in n,
            "neoforge": lambda n: "neoforge" in n.lower(),
            "quilt": lambda n: n.startswith("quilt-loader") and mc in n,
        }
        f = [n for n in cands if filters.get(loader, lambda _: False)(n)]
        if f:
            return f[0]
        if mc in cands:
            return mc
        raise RuntimeError("Not installed")

    def _launch(self, d: str, vid: str, mc: str, user: str, ram: int | None) -> subprocess.Popen:
        ram = ram if ram else self.get_recommended_ram()
        java = self.java.get_java(self.java.java_for_mc(mc))
        opts = {
            "username": user, "uuid": str(uuid.uuid4()), "token": "",
            "gameDirectory": d, "jvmArguments": [f"-Xmx{ram}G", "-Xmn128M"],
        }
        self.logger.info(f"Launching {mc} ({vid}) as {user} with {ram}GB RAM")
        cmd = mll.command.get_minecraft_command(version=vid, minecraft_directory=d, options=opts)
        java_bin = os.path.basename(cmd[0]).lower()
        if java_bin in ("java", "java.exe", "javaw.exe"):
            cmd[0] = java
        BAD_FLAGS = {"--sun-misc-unsafe-memory-access=allow", "--add-modules=jdk.incubator.vector"}
        cmd = [arg for arg in cmd if arg not in BAD_FLAGS]
        self.logger.debug(f"Launch command: {' '.join(cmd[:6])}...")
        popen_kwargs = {"cwd": d}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.Popen(cmd, **popen_kwargs)

    def launch_custom(self, name: str, mc: str, loader: str, user: str, ram: int | None = None) -> subprocess.Popen:
        d = self.config.custom_dir(name)
        if not self.is_valid_username(user):
            raise ValueError("Invalid username")
        return self._launch(d, self._detect_vid(d, mc, loader), mc, user, ram)

    def _load_playtime(self) -> dict:
        return self.fs.json_read(self.config.playtime_path) or {}

    def _save_playtime(self, data: dict) -> None:
        self.fs.json_write(self.config.playtime_path, data)

    def add_play_time(self, name: str, minutes: int) -> None:
        pt = self._load_playtime()
        pt[name] = pt.get(name, 0) + minutes
        self._save_playtime(pt)
        self.logger.info(f"Play time for {name}: +{minutes} min (total {pt[name]} min)")

    def _get_play_time(self, name: str) -> int:
        return self._load_playtime().get(name, 0)

    def list_installed(self) -> list[dict]:
        root = self.config.custom_dir("")
        if not os.path.isdir(root):
            return []
        self.install.cleanup_broken()
        manifest = self.dl.fetch_manifest()
        result = []
        for n in os.listdir(root):
            p = os.path.join(root, n)
            if not os.path.isdir(p):
                continue
            meta = self.fs.json_read(os.path.join(p, ".build_meta.json"))
            if meta:
                dn = meta.get("display_name", n)
                mc = meta.get("mc_version") or dn
                result.append({
                    "folder_name": n, "mc_version": mc, "display_name": dn,
                    "loader": meta.get("loader", "build"),
                    "version": meta.get("version"),
                    "update_available": self.install.is_build_update_available(n, manifest),
                    "play_time_minutes": self._get_play_time(n),
                })
                continue
            if "-" in n:
                mc, loader = n.rsplit("-", 1)
            else:
                mc, loader = n, "vanilla"
            result.append({
                "folder_name": n, "mc_version": mc, "loader": loader,
                "version": None, "update_available": False,
                "play_time_minutes": self._get_play_time(n),
            })

        def sort_key(entry):
            mc = entry.get("mc_version", "")
            parts = mc.split(".")
            try:
                ver_tuple = tuple(int(p) for p in parts if p.isdigit())
            except ValueError:
                ver_tuple = (0,)
            has_update = 1 if entry.get("update_available") else 0
            return (-has_update, -ver_tuple[0] if ver_tuple else 0,
                    -ver_tuple[1] if len(ver_tuple) > 1 else 0,
                    -ver_tuple[2] if len(ver_tuple) > 2 else 0)

        result.sort(key=sort_key)
        self.logger.debug(f"Installed versions: {len(result)}")
        return result

    def delete_custom(self, name: str, keep_worlds: bool = False) -> None:
        d = self.config.custom_dir(name)
        if not os.path.isdir(d):
            return
        self.logger.info(f"Deleting {name} (keep_worlds={keep_worlds})")
        if keep_worlds:
            saves_src = os.path.join(d, "saves")
            if os.path.isdir(saves_src):
                backup_dir = os.path.join(self.config.base_dir, "backups", "worlds", name)
                os.makedirs(backup_dir, exist_ok=True)
                backup_saves = os.path.join(backup_dir, "saves")
                if os.path.isdir(backup_saves):
                    self.fs.safe_rmtree(backup_saves)
                self.fs.copy_dir(saves_src, backup_saves)
                self.logger.info(f"Worlds backed up to {backup_saves}")
        self.fs.safe_rmtree(d)

    def open_instance_folder_path(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)
        if platform.system() == "Windows":
            os.startfile(path)
        elif platform.system() == "Darwin":
            subprocess.call(["open", path])
        else:
            subprocess.call(["xdg-open", path])


# ============================================================
#  SHIM LAYER — обратная совместимость с mymain.py
# ============================================================

_engine: LauncherEngine | None = None
_engine_lock = threading.Lock()


def _get_engine() -> LauncherEngine:
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            _engine = LauncherEngine()
    return _engine


_shim_registry: dict[str, tuple[callable, callable]] = {}


def _shim_attach_callback(engine: LauncherEngine, task_id: str, callback: dict) -> None:
    def on_progress(task_id_evt, current, total, status_text):
        if task_id_evt != task_id:
            return
        if total:
            callback.get("setMax", lambda v: None)(total)
        callback.get("setProgress", lambda v: None)(current)
        callback.get("setStatus", lambda t: None)(status_text)

    def on_finished(task_id_evt, success, message):
        if task_id_evt != task_id:
            return

    engine.event_bus.subscribe("download_progress", on_progress)
    engine.event_bus.subscribe("task_finished", on_finished)
    _shim_registry[task_id] = (on_progress, on_finished)


def _shim_detach_callback(engine: LauncherEngine, task_id: str) -> None:
    handlers = _shim_registry.pop(task_id, None)
    if handlers:
        on_progress, on_finished = handlers
        engine.event_bus.unsubscribe("download_progress", on_progress)
        engine.event_bus.unsubscribe("task_finished", on_finished)


LOG_PATH = _get_engine().logger.log_path


def log_info(msg: str) -> None:
    _get_engine().logger.info(msg)


def log_debug(msg: str) -> None:
    _get_engine().logger.debug(msg)


def log_warning(msg: str) -> None:
    _get_engine().logger.warning(msg)


def log_error(msg: str) -> None:
    _get_engine().logger.error(msg)


def log_exception(msg: str) -> None:
    _get_engine().logger.exception(msg)


def resource_path(filename: str) -> str:
    return _get_engine().config.resource_path(filename)


def ensure_dependencies() -> str:
    return _get_engine().ensure_dependencies()


def fetch_manifest(force: bool = False) -> dict:
    return _get_engine().dl.fetch_manifest(force)


def manifest_info() -> dict:
    return _get_engine().dl.manifest_info()


def get_java(need: int = 17) -> str:
    return _get_engine().java.get_java(need)


def get_free_space_gb(path: str) -> float:
    return _get_engine().fs.get_free_space_gb(path)


def check_free_space(path: str, needed_gb: float, label: str = "") -> bool:
    return _get_engine().fs.check_free_space(path, needed_gb, label)


def get_total_ram_gb() -> float:
    return _get_engine().get_total_ram_gb()


def get_recommended_ram(fraction: float = 0.75) -> int:
    return _get_engine().get_recommended_ram(fraction)


def load_settings() -> dict:
    return _get_engine().config.load_settings()


def save_settings(data: dict) -> None:
    _get_engine().config.save_settings(data)


def is_valid_username(u: str) -> bool:
    return _get_engine().is_valid_username(u)


def _has_neoforge() -> bool:
    return _get_engine().install._has_neoforge()


def _has_quilt() -> bool:
    return _get_engine().install._has_quilt()


def is_loader_supported(mc: str, loader: str) -> bool:
    return _get_engine().install.is_loader_supported(mc, loader)


def get_available_versions(release_only: bool = True) -> list[str]:
    return _get_engine().dl.get_available_versions(release_only)


def custom_dir(name: str) -> str:
    return _get_engine().config.custom_dir(name)


def cleanup_broken() -> None:
    _get_engine().install.cleanup_broken()


def is_custom_installed(name: str) -> bool:
    return _get_engine().install.is_custom_installed(name)


def install_custom(mc: str, loader: str, name: str, callback=None, task_id=None) -> str:
    engine = _get_engine()
    if task_id is not None:
        # UI передал свой task_id — используем его напрямую без обвязки callback
        return engine.install.install_custom(mc, loader, name, task_id)
    if callback is None:
        return engine.install.install_custom(mc, loader, name, None)
    task_id = f"install:{name}:{uuid.uuid4().hex[:6]}"
    _shim_attach_callback(engine, task_id, callback)
    try:
        return engine.install.install_custom(mc, loader, name, task_id)
    finally:
        _shim_detach_callback(engine, task_id)


def launch_custom(name: str, mc: str, loader: str, user: str, ram: int | None = None) -> subprocess.Popen:
    return _get_engine().launch_custom(name, mc, loader, user, ram)


def installed_build_version(name: str) -> str | None:
    return _get_engine().install.installed_build_version(name)


def is_build_update_available(name: str, manifest: dict | None = None) -> bool:
    return _get_engine().install.is_build_update_available(name, manifest)


def download_build(name: str, url: str | None = None, callback=None, task_id=None) -> None:
    engine = _get_engine()
    if task_id is not None:
        # UI передал свой task_id — используем его напрямую без обвязки callback
        engine.install.download_build(name, url, task_id)
        return
    if callback is None:
        engine.install.download_build(name, url, None)
        return
    task_id = f"build:{name}:{uuid.uuid4().hex[:6]}"
    _shim_attach_callback(engine, task_id, callback)
    try:
        engine.install.download_build(name, url, task_id)
    finally:
        _shim_detach_callback(engine, task_id)


def update_build(name: str, callback=None, task_id=None) -> None:
    engine = _get_engine()
    if task_id is not None:
        # UI передал свой task_id — используем его напрямую без обвязки callback
        engine.install.update_build(name, task_id)
        return
    if callback is None:
        engine.install.update_build(name, None)
        return
    task_id = f"update:{name}:{uuid.uuid4().hex[:6]}"
    _shim_attach_callback(engine, task_id, callback)
    try:
        engine.install.update_build(name, task_id)
    finally:
        _shim_detach_callback(engine, task_id)


def list_installed() -> list[dict]:
    return _get_engine().list_installed()


def delete_custom(name: str, keep_worlds: bool = False) -> None:
    _get_engine().delete_custom(name, keep_worlds)


def open_instance_folder_path(path: str) -> None:
    _get_engine().open_instance_folder_path(path)


def add_play_time(name: str, minutes: int) -> None:
    _get_engine().add_play_time(name, minutes)