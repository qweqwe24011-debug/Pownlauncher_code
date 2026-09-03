import os
import time
import threading
import uuid
import webbrowser

import customtkinter as ctk
from PIL import Image

import clodmain

ctk.set_appearance_mode("dark")

# ============================================================
#  REAL MINECRAFT BLOCKS THEME (база #222223)
# ============================================================
BG = "#222223"                # Гладкий базальт — основной фон (нейтральный графит)
BG_CARD = "#2B2B2B"           # Уголь — карточки, блоки
BG_CARD_HOVER = "#383838"     # Светлее при наведении
BG_INPUT = "#1A1A1A"          # Обсидиан — поля ввода (глубина)

STONE_LIGHT = "#7F7F7F"       # Камень — светлые кнопки
STONE_DARK = "#4E4B4C"        # Гладкий базальт — тёмные кнопки
STONE_BORDER = "#3B3B3B"      # Рамка блоков

ACCENT = "#596436"            # Мох — основной акцент, кнопка ИГРАТЬ
ACCENT_HOVER = "#6B7A42"      # Светлее при наведении
ACCENT_DARK = "#46522A"       # Тёмный мох

DANGER = "#961A1A"            # Редстоуновый блок — кнопки удаления
DANGER_HOVER = "#B02020"      # Светлее при наведении
DANGER_DARK = "#701414"       # Тёмный редстоун

WARNING = "#D4A017"           # Золотой блок (приглушённый) — обновления
SUCCESS = "#2FCB54"           # Изумрудный блок — успех
ERROR = "#FF3B3B"             # Редстоун (яркий) — тексты ошибок

TEXT_PRIMARY = "#E5E0D0"      # Костный блок — основной текст
TEXT_SECONDARY = "#A0A0A0"    # Камень — вторичный текст
TEXT_MUTED = "#7A7A7A"        # Приглушённый текст
TEXT_SHADOW = "#100D17"       # Обсидиан — тень текста

BORDER = "#3B3B3B"            # Границы блоков

# Шрифт в стиле Minecraft (monospace bold)
MC_FONT = ("Consolas", 13, "bold")
MC_FONT_BIG = ("Consolas", 22, "bold")
MC_FONT_SMALL = ("Consolas", 11, "bold")
MC_FONT_TITLE = ("Consolas", 26, "bold")

LOADER_INFO = {
    "vanilla":  {"label": "Vanilla",   "color": "#7CBD6B", "bg": "#2A3B1A"},  # Трава
    "forge":    {"label": "Forge",     "color": "#D8D8D8", "bg": "#3A3A3A"},  # Железо
    "fabric":   {"label": "Fabric",    "color": "#62E5D8", "bg": "#1A3A3A"},  # Алмаз
    "neoforge": {"label": "NeoForge",  "color": "#B4653A", "bg": "#3A2A1A"},  # Медь
    "quilt":    {"label": "Quilt",     "color": "#A97DAB", "bg": "#2A1A2A"},  # Пурпур
    "build":    {"label": "Сборка",    "color": "#E5E0D0", "bg": "#3B3B3B"},  # Кость
}

_all_loaders = [("Vanilla", "vanilla"), ("Forge", "forge"), ("Fabric", "fabric"),
                ("NeoForge", "neoforge"), ("Quilt", "quilt")]
LOADERS = [(lbl, key) for lbl, key in _all_loaders
           if key not in ("neoforge", "quilt") or getattr(clodmain, f"_has_{key}")()]

settings = clodmain.load_settings()
username = settings.get("username", "Player")
ram_gb = settings.get("ram_gb", clodmain.get_recommended_ram())
TOTAL_RAM = int(clodmain.get_total_ram_gb())

MAX_VISIBLE_VERSIONS = 60

# ---------- icons ----------

def _load_icon(filename, size):
    try:
        return ctk.CTkImage(Image.open(clodmain.resource_path(filename)), size=size)
    except Exception as e:
        clodmain.log_error(f"Не удалось загрузить иконку {filename}: {e}")
        return None


_icon_img = _load_icon("icon.png", (64, 64))
_chest_img = _load_icon("chest.png", (32, 32))
_chest_big = _load_icon("chest.png", (48, 48))
_trash_img = _load_icon("trash.png", (20, 20))
_trash_img_big = _load_icon("trash.png", (32, 32))
_player_img = _load_icon("player.png", (36, 36))

# ---------- pre-fetch manifest & versions ----------
_manifest_cache = {"builds": {}, "ready": False}
_versions_cache_state = {"data": [], "ready": False}

def _prefetch_manifest():
    try:
        _manifest_cache["builds"] = clodmain.fetch_manifest()
    except Exception:
        pass
    _manifest_cache["ready"] = True

def _prefetch_versions():
    try:
        _versions_cache_state["data"] = clodmain.get_available_versions()
    except Exception:
        pass
    _versions_cache_state["ready"] = True

threading.Thread(target=_prefetch_manifest, daemon=True).start()
threading.Thread(target=_prefetch_versions, daemon=True).start()


# ---------- utils ----------

def _wheel(scrollable_frame, speed=6):
    canvas = scrollable_frame._parent_canvas
    def on_wheel(event):
        canvas.yview_scroll(int(-speed * (event.delta / 120)), "units")
        return "break"
    def bind_tree(widget):
        widget.bind("<MouseWheel>", on_wheel)
        for child in widget.winfo_children():
            bind_tree(child)
    bind_tree(scrollable_frame)


def _modal(parent, title, w, h):
    win = ctk.CTkToplevel(parent)
    win.geometry(f"{w}x{h}")
    win.title(title)
    win.resizable(False, False)
    win.configure(fg_color=BG)
    win.grab_set()
    return win


def _mc_button(parent, text, command=None, width=200, height=40,
               fg_color=STONE_LIGHT, hover_color=STONE_DARK,
               text_color=TEXT_PRIMARY, border_color=STONE_BORDER,
               font=None, **kwargs):
    if font is None:
        font = MC_FONT
    return ctk.CTkButton(
        parent, text=text, command=command,
        width=width, height=height,
        corner_radius=0,
        fg_color=fg_color,
        hover_color=hover_color,
        text_color=text_color,
        border_width=2,
        border_color=border_color,
        font=font,
        **kwargs
    )


def _mc_frame(parent, fg_color=BG_CARD, border_color=STONE_BORDER, **kwargs):
    return ctk.CTkFrame(parent, corner_radius=0, fg_color=fg_color,
                        border_width=2, border_color=border_color, **kwargs)


def _mc_entry(parent, width=300, height=40, **kwargs):
    return ctk.CTkEntry(parent, width=width, height=height,
                        corner_radius=0,
                        fg_color=BG_INPUT,
                        border_color=STONE_BORDER,
                        border_width=2,
                        text_color=TEXT_PRIMARY,
                        font=MC_FONT,
                        **kwargs)


def _header(parent, icon_text, title):
    f = ctk.CTkFrame(parent, fg_color="transparent", height=60)
    f.pack(fill="x", padx=24, pady=(20, 0))
    ctk.CTkLabel(f, text=icon_text, font=("Consolas", 28, "bold")).pack(side="left", padx=(0, 12))
    ctk.CTkLabel(f, text=title, font=MC_FONT_BIG, text_color=TEXT_PRIMARY).pack(side="left")
    ctk.CTkFrame(parent, fg_color=STONE_BORDER, height=2).pack(fill="x", padx=24, pady=(12, 16))


# ============================================================
#  UITaskManager — централизованный менеджер задач (EventBus)
# ============================================================

class UITaskManager:
    """Подписка на события ядра с потокобезопасным обновлением UI."""

    def __init__(self, event_bus, window):
        self.event_bus = event_bus
        self.window = window
        self._handlers: dict[str, tuple[callable, callable]] = {}

    def start_task(self, task_id, on_progress, on_finished):
        """Подписаться на download_progress и task_finished для task_id."""
        target_task_id = task_id
        
        def progress_handler(task_id, current, total, status_text):
            if task_id != target_task_id:
                return
            self.window.after(0, lambda: on_progress(current, total, status_text))

        def finished_handler(task_id, success, message):
            if task_id != target_task_id:
                return
            self.window.after(0, lambda: on_finished(success, message))
            # Автоотписка
            self.cancel_task(target_task_id)

        self.event_bus.subscribe("download_progress", progress_handler)
        self.event_bus.subscribe("task_finished", finished_handler)
        self._handlers[target_task_id] = (progress_handler, finished_handler)

    def cancel_task(self, target_task_id):
        """Принудительная отписка по task_id."""
        handlers = self._handlers.pop(target_task_id, None)
        if handlers:
            prog, fin = handlers
            self.event_bus.unsubscribe("download_progress", prog)
            self.event_bus.unsubscribe("task_finished", fin)

    def cleanup(self):
        """Отписать все активные задачи (при закрытии окна)."""
        for target_task_id in list(self._handlers.keys()):
            self.cancel_task(target_task_id)


# ---------- main window ----------

win = ctk.CTk()
win.geometry("900x800")
win.title("Plauncher")
win.resizable(False, False)
win.configure(fg_color=BG)

# Иконка окна
try:
    from PIL import ImageTk
    icon_pil = Image.open(clodmain.resource_path("icon.png"))
    icon_tk = ImageTk.PhotoImage(icon_pil)
    win.iconphoto(True, icon_tk)
    win._icon_ref = icon_tk
except Exception:
    pass

selected_entry = {"data": None}
row_frames = []
updating_builds = {}
# БЛОКИРОВКА ПОВТОРНОГО ЗАПУСКА
running_process = {"popen": None}
launching = {"active": False}

# Глобальный task manager для главного окна (update_from_list)
_main_engine = clodmain._get_engine()
main_task_mgr = UITaskManager(_main_engine.event_bus, win)


# ---------- profile ----------

def prof():
    prwin = _modal(win, "Профиль", 380, 440)
    h = ctk.CTkFrame(prwin, fg_color="transparent", height=60)
    h.pack(fill="x", padx=24, pady=(20, 0))
    if _player_img:
        ctk.CTkLabel(h, image=_player_img, text="").pack(side="left", padx=(0, 12))
    ctk.CTkLabel(h, text="Профиль", font=MC_FONT_BIG, text_color=TEXT_PRIMARY).pack(side="left")
    ctk.CTkFrame(prwin, fg_color=STONE_BORDER, height=2).pack(fill="x", padx=24, pady=(12, 16))

    ctk.CTkLabel(prwin, text="НИК", font=MC_FONT_SMALL, text_color=TEXT_MUTED).pack(anchor="w", padx=28)
    prent = _mc_entry(prwin, width=320, height=42, justify="center")
    prent.insert(0, username)
    prent.pack(pady=(6, 18), padx=24)

    ctk.CTkLabel(prwin, text="ПАМЯТЬ ДЛЯ ИГРЫ", font=MC_FONT_SMALL, text_color=TEXT_MUTED).pack(anchor="w", padx=28)
    ram_val = ctk.CTkLabel(prwin, text=f"{ram_gb} ГБ из {TOTAL_RAM} ГБ",
                           font=MC_FONT, text_color=TEXT_SECONDARY)
    ram_val.pack(pady=(4, 8))

    ram_max = max(4, TOTAL_RAM)

    ram_row = ctk.CTkFrame(prwin, fg_color="transparent")
    ram_row.pack(pady=(0, 4), padx=24, fill="x")
    ctk.CTkLabel(ram_row, text="2", font=MC_FONT_SMALL, text_color=TEXT_MUTED, width=20).pack(side="left")
    ram_slider = ctk.CTkSlider(ram_row, from_=2, to=ram_max,
                                  number_of_steps=max(2, ram_max - 2),
                                  progress_color=ACCENT, button_color=ACCENT,
                                  button_hover_color=ACCENT_HOVER,
                                  command=lambda v: ram_val.configure(text=f"{int(float(v))} ГБ из {TOTAL_RAM} ГБ"))
    ram_slider.set(ram_gb)
    ram_slider.pack(side="left", fill="x", expand=True, padx=8)
    ctk.CTkLabel(ram_row, text=str(ram_max), font=MC_FONT_SMALL, text_color=TEXT_MUTED, width=24).pack(side="left")

    def open_logs():
        log_path = clodmain.LOG_PATH
        if os.path.isfile(log_path):
            if os.name == "nt":
                os.startfile(log_path)
            else:
                webbrowser.open(f"file://{log_path}")
        else:
            bottom_status.configure(text="Лог-файл еще не создан", text_color=WARNING)

    _mc_button(prwin, text="Открыть логи", command=open_logs, width=320, height=36,
               fg_color=STONE_DARK, hover_color=STONE_LIGHT, text_color=TEXT_SECONDARY,
               font=MC_FONT_SMALL).pack(pady=(8, 0))

    def save():
        global username, ram_gb
        new_nick = prent.get().strip()
        if not clodmain.is_valid_username(new_nick):
            return
        username = new_nick
        ram_gb = int(ram_slider.get())
        clodmain.save_settings({"username": username, "ram_gb": ram_gb})
        prwin.destroy()
        title_label.configure(text=f"Играете как {username}")

    _mc_button(prwin, text="Сохранить", command=save, width=320, height=44,
               fg_color=ACCENT, hover_color=ACCENT_HOVER, font=MC_FONT).pack(pady=(20, 20))


# ---------- custom installer ----------

def open_custom_installer():
    clodmain.cleanup_broken()
    cwin = _modal(win, "Установить версию", 460, 660)
    _header(cwin, "⬇", "Установить версию")

    engine = clodmain._get_engine()
    task_mgr = UITaskManager(engine.event_bus, cwin)

    busy = {"active": False}

    ctk.CTkLabel(cwin, text="ЛОАДЕР", font=MC_FONT_SMALL, text_color=TEXT_MUTED).pack(anchor="w", padx=28)

    mode_var = ctk.StringVar(value="vanilla")
    loader_frame = ctk.CTkFrame(cwin, fg_color="transparent")
    loader_frame.pack(pady=(6, 12), padx=24)
    loader_btns = {}

    def select_loader(value):
        mode_var.set(value)
        status_lbl.configure(text="")
        for b in loader_btns.values():
            b.configure(fg_color=STONE_DARK, border_color=STONE_BORDER)
        loader_btns[value].configure(fg_color=ACCENT_DARK, border_color=ACCENT)

    for label, value in LOADERS:
        b = ctk.CTkButton(loader_frame, text=label, width=78, height=34, corner_radius=0,
                          fg_color=STONE_DARK, border_color=STONE_BORDER, border_width=2,
                          text_color=TEXT_PRIMARY, hover_color=STONE_LIGHT,
                          font=MC_FONT_SMALL,
                          command=lambda v=value: select_loader(v))
        b.pack(side="left", padx=3)
        loader_btns[value] = b

    ctk.CTkLabel(cwin, text="ВЕРСИЯ MINECRAFT", font=MC_FONT_SMALL, text_color=TEXT_MUTED).pack(anchor="w", padx=28)

    versions_holder = {"list": list(_versions_cache_state["data"]) if _versions_cache_state["ready"] else []}
    versions = versions_holder["list"]
    picked = ctk.StringVar(value=versions[0] if versions else "")

    ver_frame = _mc_frame(cwin, fg_color=BG_INPUT, height=42)
    ver_frame.pack(fill="x", padx=24, pady=(6, 6))
    ver_lbl = ctk.CTkLabel(ver_frame, text=picked.get() or "Загрузка версий...",
                           font=("Consolas", 15, "bold"), text_color=ACCENT)
    ver_lbl.pack(pady=8)

    search_var = ctk.StringVar()
    search_entry = _mc_entry(cwin, width=380, height=32, textvariable=search_var,
                              placeholder_text="Поиск версии...")
    search_entry.pack(pady=(0, 6), padx=24)

    ver_list = ctk.CTkScrollableFrame(cwin, width=380, height=180, corner_radius=0,
                                     fg_color=BG_INPUT, scrollbar_button_color=STONE_BORDER,
                                     scrollbar_button_hover_color=STONE_LIGHT)
    ver_list.pack(padx=24)

    visible_buttons = {}
    filter_job = {"id": None}

    def pick_version(v, btn):
        picked.set(v)
        ver_lbl.configure(text=v)
        status_lbl.configure(text="")
        for vv, b in visible_buttons.items():
            if vv == v:
                b.configure(fg_color=ACCENT_DARK, text_color=TEXT_PRIMARY)
            else:
                b.configure(fg_color="transparent", text_color=TEXT_SECONDARY)

    def render_versions(filtered):
        for w in ver_list.winfo_children():
            w.destroy()
        visible_buttons.clear()

        if not versions_holder["list"] and not _versions_cache_state["ready"]:
            ctk.CTkLabel(ver_list, text="Загрузка списка версий...",
                         font=MC_FONT_SMALL, text_color=TEXT_MUTED).pack(pady=6)
            return

        shown = filtered[:MAX_VISIBLE_VERSIONS]
        for v in shown:
            b = ctk.CTkButton(ver_list, text=v, height=28, fg_color="transparent", anchor="w",
                               corner_radius=0, text_color=TEXT_SECONDARY, hover_color=STONE_DARK,
                               font=MC_FONT_SMALL)
            b.configure(command=lambda v=v, b=b: pick_version(v, b))
            b.pack(fill="x", pady=1, padx=4)
            visible_buttons[v] = b
            if v == picked.get():
                b.configure(fg_color=ACCENT_DARK, text_color=TEXT_PRIMARY)

        remaining = len(filtered) - len(shown)
        if remaining > 0:
            ctk.CTkLabel(ver_list, text=f"… ещё {remaining}, уточните поиск",
                         font=MC_FONT_SMALL, text_color=TEXT_MUTED).pack(pady=6)
        elif not filtered:
            ctk.CTkLabel(ver_list, text="Ничего не найдено",
                         font=MC_FONT_SMALL, text_color=TEXT_MUTED).pack(pady=6)

        _wheel(ver_list)

    def do_filter():
        filter_job["id"] = None
        q = search_var.get().strip().lower()
        filtered = [v for v in versions_holder["list"] if q in v.lower()] if q else versions_holder["list"]
        render_versions(filtered)

    def on_search_change(*_):
        if filter_job["id"] is not None:
            cwin.after_cancel(filter_job["id"])
        filter_job["id"] = cwin.after(120, do_filter)

    search_var.trace_add("write", on_search_change)

    def _wait_versions():
        if not cwin.winfo_exists():
            return
        if _versions_cache_state["ready"]:
            versions_holder["list"] = list(_versions_cache_state["data"]) or ["1.20.1"]
            if not picked.get():
                picked.set(versions_holder["list"][0])
                ver_lbl.configure(text=picked.get())
            do_filter()
            return
        cwin.after(200, _wait_versions)

    status_lbl = ctk.CTkLabel(cwin, text="", font=MC_FONT, text_color=TEXT_SECONDARY, wraplength=400)
    status_lbl.pack(pady=(10, 4))
    bar = ctk.CTkProgressBar(cwin, width=380, height=12, corner_radius=0,
                              progress_color=ACCENT, mode="determinate", fg_color=STONE_DARK)
    bar.set(0)
    bar.pack(pady=4, padx=24)
    pct_lbl = ctk.CTkLabel(cwin, text="", font=MC_FONT_SMALL, text_color=TEXT_MUTED)
    pct_lbl.pack(pady=(0, 4))

    select_loader("vanilla")
    render_versions(versions_holder["list"])
    if not _versions_cache_state["ready"]:
        cwin.after(200, _wait_versions)

    install_btn = _mc_button(cwin, text="Установить", width=380, height=48,
                              fg_color=ACCENT, hover_color=ACCENT_HOVER, font=MC_FONT_BIG)

    def do_install(force=False):
        if busy["active"]:
            return
        mc = picked.get().strip()
        if not mc:
            status_lbl.configure(text="Дождитесь загрузки списка версий", text_color=WARNING)
            return
        loader = mode_var.get()
        folder = f"{mc}-{loader}"

        if not force and clodmain.is_custom_installed(folder):
            ask = _modal(cwin, "Переустановить?", 340, 180)
            ctk.CTkLabel(ask, text="⚠", font=("Consolas", 32, "bold"), text_color=WARNING).pack(pady=(16, 4))
            ctk.CTkLabel(ask, text=f"{folder} уже установлена.",
                         font=MC_FONT, text_color=TEXT_PRIMARY).pack()
            ctk.CTkLabel(ask, text="Удалить и установить заново?",
                         font=MC_FONT_SMALL, text_color=TEXT_MUTED).pack(pady=(0, 12))
            f = ctk.CTkFrame(ask, fg_color="transparent")
            f.pack()
            _mc_button(f, text="Отмена", command=ask.destroy, width=110, height=36,
                       fg_color=STONE_DARK, hover_color=STONE_LIGHT,
                       text_color=TEXT_SECONDARY, font=MC_FONT_SMALL).pack(side="left", padx=4)
            _mc_button(f, text="Переустановить", command=lambda: [ask.destroy(), do_install(force=True)],
                       width=140, height=36,
                       fg_color=DANGER, hover_color=DANGER_HOVER,
                       text_color=TEXT_PRIMARY, font=MC_FONT_SMALL).pack(side="left", padx=4)
            return

        task_id = f"ui:install:{folder}:{uuid.uuid4().hex[:6]}"

        def on_progress(current, total, status_text):
            if total > 0:
                p = min(current / total, 1.0)
                bar.set(p)
                pct_lbl.configure(text=f"{int(p * 100)}%", text_color=ACCENT)
            else:
                bar.set(0)
                if current > 0:
                    bar.set(min(current / 100, 1.0))
            status_lbl.configure(text=status_text, text_color=TEXT_SECONDARY)

        def on_finished(success, message):
            install_btn.configure(state="normal", text="Установить")
            busy["active"] = False
            if success:
                bar.set(1)
                pct_lbl.configure(text="Готово!", text_color=SUCCESS)
                status_lbl.configure(text="Установлено!", text_color=SUCCESS)
                refresh_custom_list()
            else:
                bar.set(0)
                pct_lbl.configure(text="Ошибка", text_color=ERROR)
                status_lbl.configure(text=message, text_color=ERROR)

        task_mgr.start_task(task_id, on_progress, on_finished)

        busy["active"] = True
        install_btn.configure(state="disabled", text="Установка...")
        bar.set(0)
        pct_lbl.configure(text="0%", text_color=TEXT_MUTED)
        status_lbl.configure(text="Подготовка...", text_color=TEXT_SECONDARY)

        def worker():
            try:
                if not clodmain.is_loader_supported(mc, loader):
                    raise RuntimeError(f"Версия {mc} не поддерживается лоадером {loader.capitalize()}")
                if force:
                    import shutil
                    d = clodmain.custom_dir(folder)
                    if os.path.isdir(d):
                        shutil.rmtree(d, ignore_errors=True)
                clodmain.install_custom(mc, loader, folder, task_id=task_id)
            except Exception as e:
                engine.event_bus.emit("task_finished", task_id=task_id, success=False, message=str(e))

        threading.Thread(target=worker, daemon=True).start()

    install_btn.configure(command=lambda: do_install(force=False))
    install_btn.pack(pady=(8, 24), padx=24)

    def on_close():
        task_mgr.cleanup()
        cwin.destroy()

    cwin.protocol("WM_DELETE_WINDOW", on_close)


# ---------- build downloader ----------

def open_build_downloader():
    clodmain.cleanup_broken()
    bwin = _modal(win, "Скачать сборку", 460, 540)
    h = ctk.CTkFrame(bwin, fg_color="transparent", height=60)
    h.pack(fill="x", padx=24, pady=(20, 0))
    if _chest_img:
        ctk.CTkLabel(h, image=_chest_img, text="").pack(side="left", padx=(0, 12))
    ctk.CTkLabel(h, text="Готовые сборки", font=MC_FONT_BIG, text_color=TEXT_PRIMARY).pack(side="left")
    ctk.CTkFrame(bwin, fg_color=STONE_BORDER, height=2).pack(fill="x", padx=24, pady=(12, 16))

    engine = clodmain._get_engine()
    task_mgr = UITaskManager(engine.event_bus, bwin)
    build_tasks: dict[str, str] = {}

    busy = {"active": False}
    local_builds = {"data": _manifest_cache["builds"].copy()}
    local_loading = {"value": not _manifest_cache["ready"]}

    status_lbl = ctk.CTkLabel(bwin, text="", font=MC_FONT, text_color=TEXT_SECONDARY)
    bar = ctk.CTkProgressBar(bwin, width=380, height=12, corner_radius=0,
                              progress_color=ACCENT, mode="determinate", fg_color=STONE_DARK)
    bar.set(0)
    pct_lbl = ctk.CTkLabel(bwin, text="", font=MC_FONT_SMALL, text_color=TEXT_MUTED)

    manifest_lbl = ctk.CTkLabel(bwin, text="", font=MC_FONT_SMALL)
    manifest_lbl.pack(pady=(0, 4))

    btn_row = ctk.CTkFrame(bwin, fg_color="transparent")
    btn_row.pack(pady=(0, 8))

    refresh_btn = _mc_button(btn_row, text="🔄 Обновить список", width=180, height=32,
                              fg_color=STONE_DARK, hover_color=STONE_LIGHT,
                              text_color=TEXT_SECONDARY, font=MC_FONT_SMALL)
    refresh_btn.pack(side="left", padx=4)

    update_all_btn = _mc_button(btn_row, text="⬆ Обновить всё", width=140, height=32,
                                 fg_color=WARNING, hover_color="#d97706",
                                 text_color=BG, font=MC_FONT_SMALL)
    update_all_btn.pack(side="left", padx=4)

    rows_frame = ctk.CTkScrollableFrame(bwin, fg_color="transparent", width=400, height=280,
                                         scrollbar_button_color=STONE_BORDER, scrollbar_button_hover_color=STONE_LIGHT)
    rows_frame.pack(fill="both", expand=True, padx=20, pady=(0, 8))

    def update_status():
        if not bwin.winfo_exists():
            return
        info = clodmain.manifest_info()
        if info["online"]:
            manifest_lbl.configure(text=f"✓ Онлайн · {info['count']} сборок", text_color=SUCCESS)
        elif info["cached"]:
            age = int(info['age_seconds'] / 60) if info['age_seconds'] else "?"
            manifest_lbl.configure(text=f"⚠ Офлайн · кэш ({age} мин) · {info['count']} сборок", text_color=WARNING)
        else:
            manifest_lbl.configure(text="✗ Нет связи и нет кэша", text_color=ERROR)

    def _reset_button(btn, name):
        """Восстановить текст/цвет кнопки после ошибки."""
        if not bwin.winfo_exists():
            return
        installed = os.path.isdir(clodmain.custom_dir(name))
        has_update = installed and clodmain.is_build_update_available(name, local_builds["data"])
        if has_update:
            btn.configure(text="Обновить", fg_color=WARNING, hover_color="#d97706", state="normal")
        elif installed:
            btn.configure(text="Установлено", fg_color="#334155", hover_color="#334155", state="disabled")
        else:
            btn.configure(text="Скачать", fg_color=ACCENT, hover_color=ACCENT_HOVER, state="normal")

    def refresh_rows():
        if not bwin.winfo_exists():
            return
        for w in rows_frame.winfo_children():
            w.destroy()
        build_tasks.clear()

        builds = local_builds["data"]
        if not builds:
            if local_loading["value"]:
                for _ in range(3):
                    sk = _mc_frame(rows_frame, fg_color=BG_CARD, border_color=STONE_BORDER)
                    sk.pack(fill="x", padx=4, pady=5)
                    ctk.CTkLabel(sk, text="", height=44).pack(fill="x", padx=16)
            else:
                empty = ctk.CTkFrame(rows_frame, fg_color="transparent")
                empty.pack(pady=40)
                ctk.CTkLabel(empty, text="Нет доступных сборок", font=MC_FONT, text_color=TEXT_MUTED).pack()
            return

        for name, info in builds.items():
            if name.startswith("_"):
                continue
            row = _mc_frame(rows_frame, fg_color=BG_CARD, border_color=STONE_BORDER)
            row.pack(fill="x", padx=4, pady=5)

            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", padx=16, pady=12)
            ctk.CTkLabel(left, text=name, font=MC_FONT, text_color=TEXT_PRIMARY).pack(anchor="w")
            desc = info.get("description", "")
            if desc:
                ctk.CTkLabel(left, text=desc, font=MC_FONT_SMALL,
                             text_color=TEXT_MUTED).pack(anchor="w")
            ver = info.get("version", "")
            if ver:
                ctk.CTkLabel(left, text=f"версия {ver}", font=MC_FONT_SMALL,
                             text_color=TEXT_MUTED).pack(anchor="w")

            installed = os.path.isdir(clodmain.custom_dir(name))
            # Передаем манифест из кэша, чтобы не делать лишний сетевой запрос в главном потоке
            has_update = installed and clodmain.is_build_update_available(name, local_builds["data"])

            if has_update:
                text, color, hover, state = "Обновить", WARNING, "#d97706", "normal"
            elif installed:
                text, color, hover, state = "Установлено", "#334155", "#334155", "disabled"
            else:
                text, color, hover, state = "Скачать", ACCENT, ACCENT_HOVER, "normal"

            btn = _mc_button(row, text=text, width=100, height=30,
                              fg_color=color, hover_color=hover, state=state,
                              text_color=TEXT_PRIMARY, font=MC_FONT_SMALL)

            if has_update:
                def make_cmd(n=name, b=btn):
                    start_build_task(n, b, "update")
                btn.configure(command=make_cmd)
            elif not installed:
                url = info.get("url", "")
                if url:
                    def make_cmd(n=name, u=url, b=btn):
                        start_build_task(n, b, "download", u)
                    btn.configure(command=make_cmd)
            btn.pack(side="right", padx=12)

        _wheel(rows_frame)

    def start_build_task(name, btn, action, url=None):
        if busy.get("active", False):
            return
        task_id = f"ui:{action}:{name}:{uuid.uuid4().hex[:6]}"
        build_tasks[name] = task_id

        def on_progress(current, total, status_text):
            if total > 0:
                p = min(current / total, 1.0)
                bar.set(p)
                pct_lbl.configure(text=f"{int(p * 100)}%", text_color=ACCENT)
            status_lbl.configure(text=status_text, text_color=TEXT_SECONDARY)

        def on_finished(success, message):
            busy["active"] = False
            if not bwin.winfo_exists():
                return
            if success:
                bar.set(1)
                pct_lbl.configure(text="Готово!", text_color=SUCCESS)
                status_lbl.configure(text=message, text_color=SUCCESS)
                refresh_rows()
                refresh_custom_list()
            else:
                bar.set(0)
                pct_lbl.configure(text="Ошибка", text_color=ERROR)
                status_lbl.configure(text=message, text_color=ERROR)
                _reset_button(btn, name)

        task_mgr.start_task(task_id, on_progress, on_finished)
        busy["active"] = True
        btn.configure(state="disabled", text="...")
        bar.set(0)
        pct_lbl.configure(text="0%", text_color=TEXT_MUTED)

        def worker():
            try:
                if action == "update":
                    clodmain.update_build(name, task_id=task_id)
                else:
                    clodmain.download_build(name, url, task_id=task_id)
            except Exception as e:
                engine.event_bus.emit("task_finished", task_id=task_id, success=False, message=str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _wait_manifest_prefetch():
        if not bwin.winfo_exists():
            return
        if _manifest_cache["ready"]:
            if local_loading["value"]:
                local_builds["data"] = _manifest_cache["builds"].copy()
                local_loading["value"] = False
                update_status()
                refresh_rows()
            return
        bwin.after(200, _wait_manifest_prefetch)

    def load_manifest(force=False):
        local_loading["value"] = True
        refresh_btn.configure(state="disabled", text="Загрузка...")
        refresh_rows()

        def worker():
            try:
                builds = clodmain.fetch_manifest(force=force)
                local_builds["data"] = builds
                local_loading["value"] = False
                if bwin.winfo_exists():
                    bwin.after(0, lambda: [
                        refresh_btn.configure(state="normal", text="🔄 Обновить список"),
                        update_status(),
                        refresh_rows()
                    ])
            except Exception as e:
                local_loading["value"] = False
                err_msg = str(e)
                if bwin.winfo_exists():
                    bwin.after(0, lambda m=err_msg: [
                        refresh_btn.configure(state="normal", text="🔄 Обновить список"),
                        manifest_lbl.configure(text=f"Ошибка загрузки: {m}", text_color=ERROR),
                        refresh_rows()
                    ])

        threading.Thread(target=worker, daemon=True).start()

    def update_all_builds():
        if busy.get("active", False):
            return
        builds = local_builds["data"]
        to_update = [name for name, info in builds.items()
                     if not name.startswith("_")
                     and os.path.isdir(clodmain.custom_dir(name))
                     and clodmain.is_build_update_available(name, local_builds["data"])]
        if not to_update:
            manifest_lbl.configure(text="Все сборки актуальны", text_color=SUCCESS)
            return

        total = len(to_update)
        current_idx = [0]
        busy["active"] = True
        update_all_btn.configure(state="disabled", text="Обновление...")

        def update_next():
            if not bwin.winfo_exists():
                busy["active"] = False
                return
            if current_idx[0] >= total:
                manifest_lbl.configure(text=f"✓ Обновлено {total} сборок", text_color=SUCCESS)
                busy["active"] = False
                update_all_btn.configure(state="normal", text="⬆ Обновить всё")
                refresh_rows()
                refresh_custom_list()
                return

            name = to_update[current_idx[0]]
            current_idx[0] += 1
            task_id = f"ui:batch:{name}:{uuid.uuid4().hex[:6]}"

            def on_progress(current, dl_total, status_text):
                if not bwin.winfo_exists():
                    return
                manifest_lbl.configure(text=f"Обновление {current_idx[0]}/{total}: {name}...", text_color=WARNING)
                if dl_total > 0:
                    bar.set(min(current / dl_total, 1.0))
                    pct_lbl.configure(text=f"{int(min(current / dl_total, 1.0) * 100)}%", text_color=ACCENT)

            def on_finished(success, message):
                if not bwin.winfo_exists():
                    return
                if not success:
                    manifest_lbl.configure(text=f"Ошибка {name}: {message}", text_color=ERROR)
                bwin.after(100, update_next)

            task_mgr.start_task(task_id, on_progress, on_finished)

            def worker():
                try:
                    clodmain.update_build(name, task_id=task_id)
                except Exception as e:
                    engine.event_bus.emit("task_finished", task_id=task_id, success=False, message=str(e))

            threading.Thread(target=worker, daemon=True).start()

        update_next()

    refresh_btn.configure(command=lambda: load_manifest(force=True))
    update_all_btn.configure(command=update_all_builds)
    update_status()
    refresh_rows()
    if local_loading["value"]:
        bwin.after(200, _wait_manifest_prefetch)

    def on_close():
        task_mgr.cleanup()
        bwin.destroy()

    bwin.protocol("WM_DELETE_WINDOW", on_close)


# ---------- installed list ----------

def select_row(entry, row):
    selected_entry["data"] = entry
    clodmain.save_settings({"last_selected": entry["folder_name"]})
    for r in row_frames:
        r.configure(border_width=2, border_color=STONE_BORDER, fg_color=BG_CARD)
    row.configure(border_width=2, border_color=ACCENT, fg_color=BG_CARD_HOVER)
    label = LOADER_INFO.get(entry["loader"], {"label": entry["loader"]})["label"]
    bottom_status.configure(text=f"Выбрано: {entry['mc_version']} · {label}", text_color=SUCCESS)


def confirm_delete(entry):
    dwin = _modal(win, "Удалить версию", 420, 280)
    info = LOADER_INFO.get(entry["loader"], {"label": entry["loader"]})
    if _trash_img_big:
        ctk.CTkLabel(dwin, image=_trash_img_big, text="").pack(pady=(16, 4))
    ctk.CTkLabel(dwin, text=f"Удалить {entry['mc_version']} · {info['label']}?",
                 font=MC_FONT, text_color=TEXT_PRIMARY, wraplength=380).pack(pady=(4, 4))
    ctk.CTkLabel(dwin, text="Миры можно сохранить перед удалением.",
                 font=MC_FONT_SMALL, text_color=TEXT_MUTED, wraplength=360).pack(pady=(0, 12))

    def do_delete(keep_worlds=False):
        clodmain.delete_custom(entry["folder_name"], keep_worlds=keep_worlds)
        if selected_entry["data"] == entry:
            selected_entry["data"] = None
        dwin.destroy()
        refresh_custom_list()
        if keep_worlds:
            bottom_status.configure(text="Миры сохранены в backups/worlds/", text_color=SUCCESS)

    f = ctk.CTkFrame(dwin, fg_color="transparent")
    f.pack(pady=8)
    _mc_button(f, text="Отмена", command=dwin.destroy, width=110, height=36,
               fg_color=STONE_DARK, hover_color=STONE_LIGHT,
               text_color=TEXT_SECONDARY, font=MC_FONT_SMALL).pack(side="left", padx=4)
    _mc_button(f, text="Сохранить миры", command=lambda: do_delete(keep_worlds=True),
               width=140, height=36, fg_color=WARNING, hover_color="#d97706",
               text_color=BG, font=MC_FONT_SMALL).pack(side="left", padx=4)
    _mc_button(f, text="Удалить все", command=lambda: do_delete(keep_worlds=False),
               width=110, height=36, fg_color=DANGER, hover_color=DANGER_HOVER,
               text_color=TEXT_PRIMARY, font=MC_FONT_SMALL).pack(side="left", padx=4)


def update_from_list(entry):
    key = entry["folder_name"]
    if updating_builds.get(key):
        return
    updating_builds[key] = True

    task_id = f"ui:list_update:{key}:{uuid.uuid4().hex[:6]}"

    def on_progress(current, total, status_text):
        bottom_status.configure(text=status_text, text_color=WARNING)

    def on_finished(success, message):
        if success:
            bottom_status.configure(text="Сборка обновлена!", text_color=SUCCESS)
            refresh_custom_list()
        else:
            bottom_status.configure(text=f"Ошибка обновления: {message}", text_color=ERROR)

    main_task_mgr.start_task(task_id, on_progress, on_finished)

    def worker():
        try:
            if not win.winfo_exists():
                return
            clodmain.update_build(key, task_id=task_id)
        except Exception as e:
            _main_engine.event_bus.emit("task_finished", task_id=task_id, success=False, message=str(e))
        finally:
            updating_builds[key] = False

    threading.Thread(target=worker, daemon=True).start()


def refresh_custom_list():
    global row_frames
    for widget in custom_list_frame.winfo_children():
        widget.destroy()
    row_frames = []

    entries = clodmain.list_installed()

    row_h = 70
    new_height = min(460, max(140, len(entries) * row_h + 20))
    custom_list_frame.configure(height=new_height)

    if not entries:
        empty = ctk.CTkFrame(custom_list_frame, fg_color="transparent")
        empty.pack(pady=40)
        if _chest_big:
            ctk.CTkLabel(empty, image=_chest_big, text="").pack()
        ctk.CTkLabel(empty, text="Пока ничего не установлено",
                     text_color=TEXT_MUTED, font=MC_FONT).pack(pady=(12, 4))
        ctk.CTkLabel(empty, text="Нажмите «+ Установить версию» вверху",
                     text_color=TEXT_MUTED, font=MC_FONT_SMALL).pack()
        return

    for entry in entries:
        row = _mc_frame(custom_list_frame, fg_color=BG_CARD, border_color=STONE_BORDER)
        row.pack(fill="x", pady=5, padx=4)
        row_frames.append(row)

        info = LOADER_INFO.get(entry["loader"], {"label": entry["loader"],
                                                   "color": TEXT_SECONDARY, "bg": BG_CARD})

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", padx=16, pady=12)

        display = entry.get("display_name") or entry["mc_version"]
        title_lbl = ctk.CTkLabel(left, text=display,
                                  font=("Consolas", 15, "bold"), text_color=TEXT_PRIMARY)
        title_lbl.pack(side="left")

        badge = ctk.CTkLabel(left, text=info["label"], font=MC_FONT_SMALL,
                              fg_color=info["bg"], text_color=info["color"],
                              corner_radius=0, width=60, height=22)
        badge.pack(side="left", padx=(12, 0))

        if entry["loader"] == "build" and entry.get("update_available"):
            ctk.CTkLabel(left, text="есть обновление", font=MC_FONT_SMALL,
                         text_color=WARNING).pack(side="left", padx=(10, 0))

        # Статистика игры
        play_time = entry.get("play_time_minutes", 0)
        if play_time <= 0:
            time_str = "0 мин"
        elif play_time < 60:
            time_str = f"{play_time} мин"
        elif play_time < 1440:
            time_str = f"{play_time // 60} ч"
        else:
            time_str = f"{play_time // 1440} д"
        ctk.CTkLabel(left, text=f"· {time_str}", font=MC_FONT_SMALL,
                     text_color=TEXT_MUTED).pack(side="left", padx=(6, 0))

        for widget in (row, left, title_lbl, badge):
            widget.bind("<Button-1>", lambda e, en=entry, r=row: select_row(en, r))

        ctk.CTkButton(row, image=_trash_img, text="" if _trash_img else "✕", width=32, height=28, corner_radius=0,
                      fg_color="transparent", hover_color=STONE_DARK,
                      command=lambda e=entry: confirm_delete(e)).pack(side="right", padx=(4, 14))
        _mc_button(row, text="Папка", width=70, height=28,
                   fg_color=STONE_DARK, hover_color=STONE_LIGHT,
                   text_color=TEXT_SECONDARY, font=MC_FONT_SMALL,
                   command=lambda e=entry: clodmain.open_instance_folder_path(clodmain.custom_dir(e["folder_name"]))).pack(side="right", padx=4)
        if entry["loader"] == "build" and entry.get("update_available"):
            _mc_button(row, text="Обновить", width=80, height=28,
                       fg_color=WARNING, hover_color="#d97706",
                       text_color=TEXT_PRIMARY, font=MC_FONT_SMALL,
                       command=lambda e=entry: update_from_list(e)).pack(side="right", padx=4)

    _wheel(custom_list_frame)

    last = settings.get("last_selected", "")
    if len(entries) == 1:
        select_row(entries[0], row_frames[0])
    elif last and row_frames:
        for i, e in enumerate(entries):
            if e["folder_name"] == last:
                select_row(e, row_frames[i])
                break
        else:
            select_row(entries[0], row_frames[0])
    elif row_frames:
        select_row(entries[0], row_frames[0])


# БЛОКИРОВКА ПОВТОРНОГО ЗАПУСКА + СТАТУСЫ
def play_selected():
    entry = selected_entry["data"]
    if not entry:
        bottom_status.configure(text="Сначала выберите версию из списка", text_color=ERROR)
        return
    if not clodmain.is_valid_username(username):
        bottom_status.configure(text="Некорректный ник! Откройте профиль.", text_color=ERROR)
        return

    if running_process["popen"] is not None and running_process["popen"].poll() is None:
        bottom_status.configure(text="Minecraft уже запущен!", text_color=WARNING)
        return
    if launching["active"]:
        bottom_status.configure(text="Запуск уже выполняется...", text_color=WARNING)
        return
    launching["active"] = True

    def worker():
        win.after(0, lambda: bottom_status.configure(text="Запуск...", text_color=WARNING))
        start_time = time.time()
        try:
            proc = clodmain.launch_custom(entry["folder_name"], entry["mc_version"], entry["loader"], username, ram_gb)
            running_process["popen"] = proc
            launching["active"] = False
            win.after(0, lambda: bottom_status.configure(text="Minecraft запущен", text_color=SUCCESS))
            proc.wait()
            elapsed = int((time.time() - start_time) / 60)
            if elapsed > 0:
                clodmain.add_play_time(entry["folder_name"], elapsed)
            win.after(0, lambda: bottom_status.configure(text="Minecraft закрыт", text_color=TEXT_MUTED))
            win.after(0, refresh_custom_list)
        except Exception as e:
            err_msg = str(e)
            win.after(0, lambda m=err_msg: bottom_status.configure(text=f"Ошибка: {m}", text_color=ERROR))
        finally:
            running_process["popen"] = None
            launching["active"] = False

    threading.Thread(target=worker, daemon=True).start()


# ---------- header ----------

header = ctk.CTkFrame(win, fg_color="transparent", height=70)
header.pack(fill="x", padx=32, pady=(20, 4))

title_block = ctk.CTkFrame(header, fg_color="transparent")
title_block.pack(side="left")
if _icon_img:
    ctk.CTkLabel(title_block, image=_icon_img, text="").pack(side="left", padx=(0, 6))

ctk.CTkLabel(title_block, text="Plauncher", font=MC_FONT_TITLE, text_color=TEXT_PRIMARY).pack(anchor="w")
title_label = ctk.CTkLabel(title_block, text=f"Играете как {username}",
                            font=MC_FONT_SMALL, text_color=TEXT_MUTED)
title_label.pack(anchor="w")

ctk.CTkButton(header, command=prof, image=_player_img, text="" if _player_img else "👤",
              width=40, height=40, corner_radius=0,
              fg_color=BG_CARD, border_width=2, border_color=STONE_BORDER,
              hover_color=BG_CARD_HOVER).pack(side="right", padx=6)

ctk.CTkButton(header, command=open_build_downloader, image=_chest_img, text="" if _chest_img else "📦",
              width=40, height=40,
              corner_radius=0, fg_color=BG_CARD, border_width=2, border_color=STONE_BORDER,
              hover_color=BG_CARD_HOVER).pack(side="right", padx=6)

_mc_button(header, text="➕ Установить версию", command=open_custom_installer,
           width=200, height=40, fg_color=ACCENT, hover_color=ACCENT_HOVER,
           text_color=TEXT_PRIMARY, font=MC_FONT).pack(side="right", padx=6)

ctk.CTkLabel(win, text="ВАШИ ВЕРСИИ", font=MC_FONT_SMALL, text_color=TEXT_MUTED).pack(anchor="w", padx=38, pady=(20, 6))

custom_list_frame = ctk.CTkScrollableFrame(win, fg_color="transparent", height=460,
                                            scrollbar_button_color=STONE_BORDER, scrollbar_button_hover_color=STONE_LIGHT)
custom_list_frame.pack(fill="both", expand=True, padx=30, pady=(0, 10))

# ---------- footer ----------

bottom_status = ctk.CTkLabel(win, text="", font=MC_FONT, text_color=TEXT_SECONDARY, height=20)
bottom_status.pack(pady=(0, 6))

refresh_custom_list()

_mc_button(win, text="▶  ИГРАТЬ", command=play_selected, width=500, height=60,
           fg_color=ACCENT, hover_color=ACCENT_HOVER,
           text_color=TEXT_PRIMARY, font=("Consolas", 22, "bold")).pack(pady=(0, 24))

# Проверка зависимостей при старте (в фоне, чтобы не морозить окно на время pip install)
def _check_deps_bg():
    def worker():
        deps_status = clodmain.ensure_dependencies()
        if deps_status:
            win.after(0, lambda: bottom_status.configure(text=deps_status, text_color=WARNING))
    threading.Thread(target=worker, daemon=True).start()

win.after(200, _check_deps_bg)

win.mainloop()