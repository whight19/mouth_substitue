"""Tkinter 图形界面。

整体是左右两栏：左边输入与朗读，右边选择输出设备、音色和语调。
界面里的所有耗时工作（网络、音频、音色列表）都在后台线程，主线程只处理
队列事件，因此窗口不会卡住。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Callable

from . import __app_name__, __version__, audio, config, voices
from .pipeline import (
    CONNECTING,
    ERROR,
    IDLE,
    SPEAKING,
    STOPPED,
    SYNTHESIZING,
    TTSPipeline,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配色与字体
# ---------------------------------------------------------------------------

BG = "#0d1017"
PANEL = "#151925"
PANEL_ALT = "#1b2030"
PANEL_SOFT = "#111624"
BORDER = "#262d40"
BORDER_SOFT = "#202637"
TEXT = "#e9edf8"
MUTED = "#8b94aa"
FAINT = "#5d657a"
ACCENT = "#8093ff"
ACCENT_HOVER = "#9aa9ff"
ACCENT_DIM = "#5b6bd6"
GREEN = "#3ddc97"
AMBER = "#f0b45a"
RED = "#ff6f86"
PLACEHOLDER = "#59627a"

FONT = "Microsoft YaHei UI"
MONO = "Cascadia Mono"

CUSTOM_PRESET = "（自定义）"
DEFAULT_DEVICE_LABEL = "系统默认设备"


def _point(size: float, weight: str = "normal") -> tuple[str, int, str]:
    return (FONT, int(round(size)), weight)


def apply_dark_titlebar(window: tk.Misc) -> None:
    """把窗口标题栏染成深色（Windows 11）。"""
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetAncestor(window.winfo_id(), 2)
        if not hwnd:
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            return
        enabled = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled)
        )
        color = ctypes.c_int(0x00251915)  # 0x00BBGGRR: #151925
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 35, ctypes.byref(color), ctypes.sizeof(color)
        )
        text_color = ctypes.c_int(0x00F8EDE9)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 36, ctypes.byref(text_color), ctypes.sizeof(text_color)
        )
    except Exception:
        pass


class StatusChip(tk.Frame):
    def __init__(self, master: tk.Misc, px: Callable[[int], int]) -> None:
        super().__init__(
            master,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1,
            bd=0,
        )
        self._px = px
        self.dot = tk.Canvas(
            self, width=px(9), height=px(9), bg=PANEL, highlightthickness=0
        )
        self.dot.pack(side="left", padx=(px(11), px(6)), pady=px(7))
        self._dot = self.dot.create_oval(
            px(1), px(1), px(8), px(8), fill=MUTED, outline=""
        )
        self.label = tk.Label(
            self, text="连接中…", bg=PANEL, fg=MUTED, font=_point(9)
        )
        self.label.pack(side="left", padx=(0, px(11)))

    def set(self, text: str, color: str) -> None:
        self.label.config(text=text, fg=color)
        self.dot.itemconfig(self._dot, fill=color)


class PlaceholderText(tk.Text):
    def __init__(
        self,
        master: tk.Misc,
        placeholder: str,
        px: Callable[[int], int],
        **kwargs: Any,
    ) -> None:
        super().__init__(master, **kwargs)
        self._placeholder = placeholder
        self._showing = False
        self._px = px
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        self._show_placeholder()

    def _show_placeholder(self) -> None:
        if self.get("1.0", "end-1c").strip():
            return
        if self.get("1.0", "end-1c"):
            self.delete("1.0", "end")
        self._showing = True
        self.config(fg=PLACEHOLDER)
        self.insert("1.0", self._placeholder)
        self.mark_set("insert", "1.0")

    def _focus_in(self, _event=None) -> None:
        if self._showing:
            self.delete("1.0", "end")
            self.config(fg=TEXT)
            self._showing = False

    def _focus_out(self, _event=None) -> None:
        if not self.get("1.0", "end-1c").strip():
            self._show_placeholder()

    def get_text(self) -> str:
        if self._showing:
            return ""
        return self.get("1.0", "end-1c")

    def set_text(self, text: str) -> None:
        self.delete("1.0", "end")
        self._showing = False
        self.config(fg=TEXT)
        if text:
            self.insert("1.0", text)
            self.edit_modified(False)
        else:
            self._show_placeholder()


class VoiceDialog(tk.Toplevel):
    """完整音色库：搜索、过滤、选择，也支持直接填音色 ID。"""

    def __init__(
        self,
        app: "MouthApp",
        current: str,
        on_select: Callable[[str], None],
    ) -> None:
        super().__init__(app.root)
        self.app = app
        self.px = app.px
        self.on_select = on_select
        self.current = current
        self.voices: list[dict[str, Any]] = list(app.voices)
        self.rows: dict[str, dict[str, Any]] = {}

        self.title("音色库")
        self.configure(bg=BG)
        self.transient(app.root)
        self.resizable(True, True)
        width, height = self.px(820), self.px(600)
        self.minsize(self.px(680), self.px(480))
        self._center(app.root, width, height)
        self.grab_set()

        self._build()
        self._populate()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(80, self._focus_search)
        self.after(120, lambda: apply_dark_titlebar(self))

    def _center(self, parent: tk.Misc, width: int, height: int) -> None:
        parent.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build(self) -> None:
        px = self.px
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=px(22), pady=(px(18), px(10)))
        tk.Label(
            header, text="音色库", bg=BG, fg=TEXT, font=_point(15, "bold")
        ).pack(side="left")
        self.count_label = tk.Label(
            header, text="", bg=BG, fg=MUTED, font=_point(9)
        )
        self.count_label.pack(side="left", padx=(px(10), 0), pady=(px(4), 0))

        search_row = tk.Frame(self, bg=BG)
        search_row.pack(fill="x", padx=px(22), pady=(0, px(10)))
        search_box = tk.Frame(
            search_row,
            bg=PANEL_ALT,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        search_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            search_box, text="搜索", bg=PANEL_ALT, fg=FAINT, font=_point(9)
        ).pack(side="left", padx=(px(10), 0))
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            search_box,
            textvariable=self.search_var,
            bg=PANEL_ALT,
            fg=TEXT,
            insertbackground=ACCENT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=_point(10),
        )
        self.search_entry.pack(
            side="left", fill="x", expand=True, padx=px(8), pady=px(7)
        )
        self.search_var.trace_add("write", lambda *_: self._populate())

        self.refresh_btn = app_button(
            search_row,
            "刷新在线列表",
            self._refresh,
            self.px,
            kind="secondary",
            compact=True,
        )
        self.refresh_btn.pack(side="left", padx=(px(8), 0))

        table = tk.Frame(self, bg=BG)
        table.pack(fill="both", expand=True, padx=px(22))
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table,
            columns=("name", "locale", "gender", "tags", "short"),
            show="headings",
            style="Voices.Treeview",
            selectmode="browse",
        )
        headings = {
            "name": ("音色", 190),
            "locale": ("语言", 82),
            "gender": ("性别", 48),
            "tags": ("特点", 180),
            "short": ("音色 ID", 220),
        }
        for key, (title, width) in headings.items():
            self.tree.heading(key, text=title, anchor="w")
            self.tree.column(
                key,
                width=self.px(width),
                minwidth=self.px(44),
                stretch=(key in ("name", "tags", "short")),
                anchor="w",
            )
        scroll = ttk.Scrollbar(
            table, orient="vertical", command=self.tree.yview, style="Vertical.TScrollbar"
        )
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", lambda _e: self._choose())
        self.tree.bind("<Return>", lambda _e: self._choose())

        custom_row = tk.Frame(self, bg=BG)
        custom_row.pack(fill="x", padx=px(22), pady=(px(12), 0))
        tk.Label(
            custom_row, text="自定义音色 ID", bg=BG, fg=MUTED, font=_point(9)
        ).pack(side="left")
        self.custom_var = tk.StringVar()
        custom_entry = tk.Entry(
            custom_row,
            textvariable=self.custom_var,
            bg=PANEL_ALT,
            fg=TEXT,
            insertbackground=ACCENT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=_point(9),
        )
        custom_entry.pack(
            side="left", fill="x", expand=True, padx=(px(10), px(8)), ipady=px(4)
        )
        custom_entry.bind("<Return>", lambda _e: self._use_custom())
        app_button(
            custom_row,
            "使用",
            self._use_custom,
            self.px,
            kind="secondary",
            compact=True,
        ).pack(side="left")

        self.status_label = tk.Label(
            self, text="", bg=BG, fg=MUTED, font=_point(9), anchor="w"
        )
        self.status_label.pack(fill="x", padx=px(22), pady=(px(8), 0))

        footer = tk.Frame(self, bg=BG)
        footer.pack(fill="x", padx=px(22), pady=(px(10), px(16)))
        app_button(
            footer,
            "取消",
            self.destroy,
            self.px,
            kind="ghost",
            compact=True,
        ).pack(side="right")
        app_button(
            footer,
            "使用此音色",
            self._choose,
            self.px,
            kind="primary",
            compact=True,
        ).pack(side="right", padx=(0, px(8)))

    def _focus_search(self) -> None:
        if self.winfo_exists():
            self.search_entry.focus_set()

    def _populate(self) -> None:
        if not self.winfo_exists():
            return
        query = self.search_var.get().strip().lower()
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.rows.clear()
        shown = 0
        selected_iid = ""
        for voice in self.voices:
            short = str(voice.get("ShortName", ""))
            if not short:
                continue
            name = voices.display_name(voice)
            locale = str(voice.get("Locale", ""))
            locale_text = voices.locale_label(locale)
            gender = voices.gender_label(str(voice.get("Gender", "")))
            tags = voices.voice_tags(voice)
            haystack = f"{short} {name} {locale} {locale_text} {gender} {tags}".lower()
            if query and query not in haystack:
                continue
            iid = short
            if iid in self.rows:
                iid = f"{short}#{shown}"
            self.rows[iid] = voice
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(name, locale_text, gender, tags, short),
            )
            if short == self.current:
                selected_iid = iid
            shown += 1
        self.count_label.config(text=f"{shown} / {len(self.voices)} 个音色")
        if selected_iid:
            self.tree.selection_set(selected_iid)
            self.tree.see(selected_iid)

    def _choose(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        voice = self.rows.get(selection[0])
        if voice:
            short = str(voice.get("ShortName", ""))
            self.on_select(short)
            self.destroy()

    def _use_custom(self) -> None:
        short = self.custom_var.get().strip()
        if not short:
            self.status_label.config(text="请先填写音色 ID，例如 zh-CN-XiaoxiaoNeural")
            return
        if "-" not in short:
            self.status_label.config(text="音色 ID 格式看起来不对，应以语言开头，如 zh-CN-…")
            return
        self.on_select(short)
        self.destroy()

    def _refresh(self) -> None:
        self.refresh_btn.config(state="disabled", text="获取中…")
        self.status_label.config(text="正在从微软语音服务获取音色列表…")
        self.app.run_bg(
            voices.fetch_voices,
            self._on_fetched,
            self._on_fetch_error,
        )

    def _on_fetched(self, result: Any) -> None:
        if not self.winfo_exists():
            return
        self.refresh_btn.config(state="normal", text="刷新在线列表")
        try:
            fetched = [v for v in result if isinstance(v, dict)]
        except TypeError:
            fetched = []
        if not fetched:
            self.status_label.config(text="没有获取到音色，继续使用本地缓存")
            return
        self.app.voices = voices.merge_curated(fetched)
        self.voices = list(self.app.voices)
        try:
            config.save_voice_cache(self.app.voices)
            self.app.cfg["voice_cache_ts"] = time.time()
            config.save(self.app.cfg)
        except Exception:
            log.debug("保存音色缓存失败", exc_info=True)
        self._populate()
        self.status_label.config(text=f"已更新，共 {len(self.voices)} 个音色")

    def _on_fetch_error(self, exc: BaseException) -> None:
        if not self.winfo_exists():
            return
        self.refresh_btn.config(state="normal", text="刷新在线列表")
        self.status_label.config(
            text=f"获取失败：{exc}。可以继续使用本地缓存。"
        )


class MouthApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = config.load()
        self.voices: list[dict[str, Any]] = voices.merge_curated(
            config.load_voice_cache()
        )
        self.events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self.ui_results: queue.Queue[tuple[Callable | None, Any]] = queue.Queue()
        self.pipeline = TTSPipeline(self._emit)

        self._voice_short = self.cfg["voice"]
        self._voice_options: list[tuple[str, str]] = []
        self._voice_map: dict[str, str] = {}
        self._device_options: list[audio.OutputDevice | None] = []
        self._suspend = True
        self._save_job: str | None = None
        self._notice_job: str | None = None
        self._status = IDLE
        self._closing = False

        self.root.title(f"{__app_name__} · 文字转语音")
        self.root.configure(bg=BG)
        try:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            min_w = min(self.px(940), max(self.px(720), screen_w - self.px(40)))
            min_h = min(self.px(660), max(self.px(520), screen_h - self.px(80)))
        except Exception:
            min_w, min_h = self.px(940), self.px(660)
        self.root.minsize(min_w, min_h)
        self._apply_dark_titlebar()
        self._set_scaling()
        self._build_styles()
        self._build()
        self._apply_config()
        self._restore_geometry()
        self._bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(40, self._poll)

        self.pipeline.start()
        self._maybe_refresh_voice_cache()
        self.root.after(80, self._apply_dark_titlebar)

    # -- DPI / 样式 -------------------------------------------------------

    def _set_scaling(self) -> None:
        try:
            dpi = float(self.root.winfo_fpixels("1i"))
        except Exception:
            dpi = 96.0
        try:
            self.root.tk.call("tk", "scaling", max(1.0, dpi / 72.0))
        except tk.TclError:
            pass

    def _apply_dark_titlebar(self) -> None:
        """Windows 11 深色标题栏，跟界面配色保持一致。"""
        apply_dark_titlebar(self.root)

    def px(self, value: int) -> int:
        try:
            scale = float(self.root.winfo_fpixels("1i")) / 96.0
        except Exception:
            scale = 1.0
        return int(round(value * max(1.0, min(2.0, scale))))

    def _build_styles(self) -> None:
        px = self.px
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "App.TCombobox",
            fieldbackground=PANEL_ALT,
            background=PANEL_ALT,
            foreground=TEXT,
            arrowcolor=MUTED,
            bordercolor=BORDER,
            lightcolor=PANEL_ALT,
            darkcolor=PANEL_ALT,
            padding=px(6),
            font=_point(10),
        )
        style.map(
            "App.TCombobox",
            fieldbackground=[("readonly", PANEL_ALT), ("disabled", PANEL_SOFT)],
            foreground=[("readonly", TEXT), ("disabled", FAINT)],
            selectbackground=[("readonly", PANEL_ALT)],
            selectforeground=[("readonly", TEXT)],
            bordercolor=[("focus", ACCENT), ("hover", ACCENT_DIM)],
            arrowcolor=[("disabled", FAINT)],
        )
        style.configure(
            "TScale",
            background=ACCENT,
            troughcolor="#0a0d14",
            bordercolor=PANEL,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            gripcount=0,
        )
        style.map("TScale", background=[("active", ACCENT_HOVER)])
        style.configure(
            "Vertical.TScrollbar",
            background=PANEL_ALT,
            troughcolor=BG,
            bordercolor=BG,
            arrowcolor=MUTED,
            lightcolor=PANEL_ALT,
            darkcolor=PANEL_ALT,
            width=px(10),
        )
        style.map(
            "Vertical.TScrollbar",
            background=[("active", BORDER)],
            arrowcolor=[("active", TEXT)],
        )
        style.configure(
            "Voices.Treeview",
            background=PANEL_ALT,
            fieldbackground=PANEL_ALT,
            foreground=TEXT,
            rowheight=px(29),
            borderwidth=0,
            font=_point(9),
        )
        style.configure(
            "Voices.Treeview.Heading",
            background=PANEL,
            foreground=MUTED,
            relief="flat",
            borderwidth=0,
            padding=(px(7), px(9)),
            font=_point(9, "bold"),
        )
        style.map(
            "Voices.Treeview",
            background=[("selected", ACCENT)],
            foreground=[("selected", "#0b0d12")],
        )
        self.root.option_add("*TCombobox*Listbox.background", PANEL_ALT)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#0b0d12")
        self.root.option_add("*TCombobox*Listbox.font", _point(10))

    # -- 布局 -------------------------------------------------------------

    def _build(self) -> None:
        px = self.px
        self._build_header(px)
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=px(24), pady=(0, px(8)))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, minsize=px(382))
        body.rowconfigure(0, weight=1)

        left = tk.Frame(
            body,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(14)))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        self._build_editor(left, px)

        right = tk.Frame(
            body,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        right.grid(row=0, column=1, sticky="nsew")
        self._build_settings(right, px)

        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=px(26), pady=(0, px(12)))
        self.state_label = tk.Label(
            footer, text="就绪", bg=BG, fg=MUTED, font=_point(9)
        )
        self.state_label.pack(side="left")
        self.latency_label = tk.Label(
            footer, text="首音 —", bg=BG, fg=ACCENT, font=_point(9, "bold")
        )
        self.latency_label.pack(side="left", padx=(px(18), 0))
        tk.Label(
            footer,
            text=f"v{__version__}",
            bg=BG,
            fg=FAINT,
            font=_point(9),
        ).pack(side="right")
        self.output_label = tk.Label(
            footer, text="输出：—", bg=BG, fg=FAINT, font=_point(9)
        )
        self.output_label.pack(side="right", padx=(0, px(18)))

    def _build_header(self, px: Callable[[int], int]) -> None:
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=px(26), pady=(px(18), px(12)))

        icon = tk.Canvas(
            header,
            width=px(44),
            height=px(44),
            bg=BG,
            highlightthickness=0,
        )
        icon.pack(side="left")
        icon.create_rectangle(
            px(1), px(1), px(43), px(43), fill=ACCENT, outline=""
        )
        bars = [(px(12), px(18)), (px(20), px(28)), (px(28), px(22)), (px(36), px(14))]
        for x, height in bars:
            top = (px(44) - height) / 2
            icon.create_rectangle(
                x,
                top,
                x + px(4),
                top + height,
                fill="#0d1017",
                outline="",
            )

        title_box = tk.Frame(header, bg=BG)
        title_box.pack(side="left", padx=(px(12), 0))
        tk.Label(
            title_box,
            text=__app_name__,
            bg=BG,
            fg=TEXT,
            font=_point(17, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="文字转语音 · 低延迟 · 指定声卡",
            bg=BG,
            fg=MUTED,
            font=_point(9),
        ).pack(anchor="w", pady=(px(2), 0))

        self.chip = StatusChip(header, px)
        self.chip.pack(side="right", pady=(px(5), 0))

    def _build_editor(self, parent: tk.Frame, px: Callable[[int], int]) -> None:
        head = tk.Frame(parent, bg=PANEL)
        head.grid(row=0, column=0, sticky="ew", padx=px(20), pady=(px(16), 0))
        tk.Label(
            head, text="要说的话", bg=PANEL, fg=TEXT, font=_point(11, "bold")
        ).pack(side="left")
        self.char_label = tk.Label(
            head, text="0 字", bg=PANEL, fg=FAINT, font=_point(9)
        )
        self.char_label.pack(side="right")

        text_box = tk.Frame(
            parent,
            bg=PANEL_ALT,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        text_box.grid(
            row=1, column=0, sticky="nsew", padx=px(20), pady=(px(10), px(8))
        )
        text_box.columnconfigure(0, weight=1)
        text_box.rowconfigure(0, weight=1)
        self.text = PlaceholderText(
            text_box,
            "在这里输入想说的话……\n\nCtrl+Enter 朗读，Esc 立即停止。\n配合 VB-CABLE，可以把声音送进游戏或语音软件。",
            px,
            bg=PANEL_ALT,
            fg=TEXT,
            insertbackground=ACCENT,
            selectbackground="#39466f",
            selectforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            wrap="word",
            font=_point(12),
            width=20,
            height=6,
            padx=px(14),
            pady=px(12),
            spacing1=px(2),
            spacing3=px(4),
            undo=True,
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(
            text_box,
            orient="vertical",
            command=self.text.yview,
            style="Vertical.TScrollbar",
        )
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll.set)
        self.text.bind("<<Modified>>", self._on_text_modified)

        self.notice_label = tk.Label(
            parent,
            text="",
            bg=PANEL,
            fg=MUTED,
            font=_point(9),
            anchor="w",
            justify="left",
            wraplength=px(520),
        )
        self.notice_label.grid(
            row=2, column=0, sticky="ew", padx=px(22), pady=(0, px(2))
        )

        actions = tk.Frame(parent, bg=PANEL)
        actions.grid(
            row=3, column=0, sticky="ew", padx=px(20), pady=(px(4), px(16))
        )
        self.speak_btn = app_button(
            actions, "▶  朗读", self._speak, px, kind="primary"
        )
        self.speak_btn.pack(side="left")
        self.stop_btn = app_button(
            actions, "■  停止", self._stop, px, kind="danger", compact=True
        )
        self.stop_btn.pack(side="left", padx=(px(9), 0))
        self.stop_btn.config(state="disabled")
        app_button(
            actions, "清空", self._clear_text, px, kind="ghost", compact=True
        ).pack(side="left", padx=(px(2), 0))
        tk.Label(
            actions,
            text="Ctrl+Enter 朗读   Esc 停止",
            bg=PANEL,
            fg=FAINT,
            font=_point(9),
        ).pack(side="right", pady=(px(6), 0))

    def _section_title(
        self, parent: tk.Frame, title: str, px: Callable[[int], int]
    ) -> tk.Frame:
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x")
        tk.Label(
            row, text=title, bg=PANEL, fg=TEXT, font=_point(10, "bold")
        ).pack(side="left")
        return row

    def _build_settings(self, parent: tk.Frame, px: Callable[[int], int]) -> None:
        inner = tk.Frame(parent, bg=PANEL)
        inner.pack(fill="both", expand=True, padx=px(20), pady=px(16))

        self._section_title(inner, "输出设备", px)
        tk.Label(
            inner,
            text="选择声音要送进的声卡（例如 CABLE Input）",
            bg=PANEL,
            fg=FAINT,
            font=_point(9),
        ).pack(anchor="w", pady=(px(2), px(7)))
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            inner,
            textvariable=self.device_var,
            state="readonly",
            style="App.TCombobox",
            font=_point(9),
        )
        self.device_combo.pack(fill="x")
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_changed)

        device_row = tk.Frame(inner, bg=PANEL)
        device_row.pack(fill="x", pady=(px(7), 0))
        self.refresh_device_btn = app_button(
            device_row, "刷新设备", self._refresh_devices_clicked, px,
            kind="secondary", compact=True,
        )
        self.refresh_device_btn.pack(side="left")
        self.test_btn = app_button(
            device_row, "测试声音", self._test_device, px,
            kind="secondary", compact=True,
        )
        self.test_btn.pack(side="left", padx=(px(4), 0))
        self.device_hint = tk.Label(
            device_row, text="", bg=PANEL, fg=FAINT, font=_point(8)
        )
        self.device_hint.pack(side="right")

        self._divider(inner, px)

        voice_head = self._section_title(inner, "音色", px)
        more = tk.Label(
            voice_head,
            text="更多音色…",
            bg=PANEL,
            fg=ACCENT,
            font=_point(9),
            cursor="hand2",
        )
        more.pack(side="right")
        more.bind("<Button-1>", lambda _e: self._open_voice_dialog())

        self.voice_var = tk.StringVar()
        self.voice_combo = ttk.Combobox(
            inner,
            textvariable=self.voice_var,
            state="readonly",
            style="App.TCombobox",
            font=_point(9),
        )
        self.voice_combo.pack(fill="x", pady=(px(6), 0))
        self.voice_combo.bind("<<ComboboxSelected>>", self._on_voice_changed)

        preset_head = tk.Frame(inner, bg=PANEL)
        preset_head.pack(fill="x", pady=(px(10), 0))
        tk.Label(
            preset_head, text="音色预设", bg=PANEL, fg=MUTED, font=_point(9)
        ).pack(side="left")
        save_link = tk.Label(
            preset_head,
            text="保存当前",
            bg=PANEL,
            fg=ACCENT,
            font=_point(9),
            cursor="hand2",
        )
        save_link.pack(side="right")
        save_link.bind("<Button-1>", lambda _e: self._save_preset())
        delete_link = tk.Label(
            preset_head,
            text="删除",
            bg=PANEL,
            fg=FAINT,
            font=_point(9),
            cursor="hand2",
        )
        delete_link.pack(side="right", padx=(0, px(10)))
        delete_link.bind("<Button-1>", lambda _e: self._delete_preset())

        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(
            inner,
            textvariable=self.preset_var,
            state="readonly",
            style="App.TCombobox",
            font=_point(9),
        )
        self.preset_combo.pack(fill="x", pady=(px(5), 0))
        self.preset_combo.bind(
            "<<ComboboxSelected>>", self._on_preset_changed
        )

        tune_head = tk.Frame(inner, bg=PANEL)
        tune_head.pack(fill="x", pady=(px(12), 0))
        tk.Label(
            tune_head, text="音色微调", bg=PANEL, fg=MUTED, font=_point(9)
        ).pack(side="left")
        reset_link = tk.Label(
            tune_head,
            text="重置",
            bg=PANEL,
            fg=ACCENT,
            font=_point(9),
            cursor="hand2",
        )
        reset_link.pack(side="right")
        reset_link.bind("<Button-1>", lambda _e: self._reset_tuning())

        self.rate_var = tk.DoubleVar(value=0)
        self.pitch_var = tk.DoubleVar(value=0)
        self.volume_var = tk.DoubleVar(value=0)
        self.rate_value = self._build_slider(
            inner, "语速", self.rate_var, -50, 100, px
        )
        self.pitch_value = self._build_slider(
            inner, "音调", self.pitch_var, -50, 50, px
        )
        self.volume_value = self._build_slider(
            inner, "音量", self.volume_var, -50, 50, px
        )

        self._divider(inner, px)
        tip_box = tk.Frame(inner, bg=PANEL_SOFT)
        tip_box.pack(fill="x")
        tk.Label(
            tip_box,
            text="用法提示",
            bg=PANEL_SOFT,
            fg=MUTED,
            font=_point(8, "bold"),
            anchor="w",
        ).pack(fill="x", padx=px(10), pady=(px(8), px(1)))
        tk.Label(
            tip_box,
            text="把输出设备设为 CABLE Input，再把游戏 / 语音软件的\n麦克风设为 CABLE Output，就能用这个声音说话。",
            bg=PANEL_SOFT,
            fg=FAINT,
            font=_point(8),
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=px(10), pady=(0, px(8)))

    def _divider(self, parent: tk.Frame, px: Callable[[int], int]) -> None:
        tk.Frame(parent, bg=BORDER_SOFT, height=1).pack(
            fill="x", pady=px(12)
        )

    def _build_slider(
        self,
        parent: tk.Frame,
        label: str,
        variable: tk.DoubleVar,
        low: int,
        high: int,
        px: Callable[[int], int],
    ) -> tk.Label:
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=(px(6), 0))
        tk.Label(
            row, text=label, bg=PANEL, fg=MUTED, font=_point(9), width=3,
            anchor="w",
        ).pack(side="left")
        scale = ttk.Scale(
            row,
            from_=low,
            to=high,
            variable=variable,
            orient="horizontal",
            command=lambda _v: self._on_slider(),
        )
        scale.pack(side="left", fill="x", expand=True, padx=(px(6), px(8)))
        value = tk.Label(
            row,
            text="+0",
            bg=PANEL,
            fg=TEXT,
            font=(MONO, 9),
            width=6,
            anchor="e",
        )
        value.pack(side="right")
        scale.bind("<ButtonRelease-1>", lambda _e: self._save_soon())
        scale.bind("<KeyRelease>", lambda _e: self._save_soon())
        return value

    # -- 配置应用 ---------------------------------------------------------

    def _apply_config(self) -> None:
        self._suspend = True
        self.text.set_text(self.cfg.get("text") or "")
        self._update_char_count()
        self._ensure_voice_option(self.cfg["voice"])
        self.voice_var.set(self._voice_label(self.cfg["voice"]))
        self._voice_short = self.cfg["voice"]
        self.rate_var.set(self.cfg["rate"])
        self.pitch_var.set(self.cfg["pitch"])
        self.volume_var.set(self.cfg["volume"])
        self._update_slider_labels()
        self._refresh_device_values(self.cfg.get("device"))
        self._refresh_presets()
        self._suspend = False

        matched = self._match_preset(
            self._voice_short,
            self.cfg["rate"],
            self.cfg["pitch"],
            self.cfg["volume"],
        )
        self.preset_var.set(matched["name"] if matched else CUSTOM_PRESET)
        self._update_output_label()
        self.state_label.config(text="就绪")

    def _restore_geometry(self) -> None:
        geometry = self.cfg.get("geometry") or "1120x780"
        try:
            self.root.geometry(geometry)
        except tk.TclError:
            self.root.geometry("1120x780")
        # 换了分辨率/缩放比例后，旧窗口可能比屏幕还大，做一次收敛
        try:
            self.root.update_idletasks()
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            width = self.root.winfo_width()
            height = self.root.winfo_height()
            max_w = max(self.px(720), screen_w - self.px(40))
            max_h = max(self.px(520), screen_h - self.px(80))
            if width > max_w or height > max_h:
                self.root.geometry(f"{min(width, max_w)}x{min(height, max_h)}")
        except Exception:
            pass

    def _bind_keys(self) -> None:
        self.root.bind("<Control-Return>", self._on_hotkey_speak)
        self.root.bind("<Control-KP_Enter>", self._on_hotkey_speak)
        self.root.bind("<Escape>", self._on_hotkey_stop)
        self.text.bind("<Control-Return>", self._on_hotkey_speak)
        self.text.bind("<Escape>", self._on_hotkey_stop)

    def _on_hotkey_speak(self, _event=None):
        self._speak()
        return "break"

    def _on_hotkey_stop(self, _event=None):
        self._stop()
        return "break"

    # -- 设备 -------------------------------------------------------------

    def _refresh_device_values(self, saved: dict[str, Any] | None = None) -> None:
        devices = audio.list_output_devices()
        self._device_options = [None] + devices
        values = [DEFAULT_DEVICE_LABEL] + [d.label for d in devices]
        self.device_combo["values"] = values
        index = 0
        if isinstance(saved, dict) and saved.get("name"):
            key = f"{saved.get('hostapi', '')}|{saved['name']}"
            for i, device in enumerate(devices, start=1):
                if device.key == key:
                    index = i
                    break
        if index == 0 and devices:
            # 没有保存记录时选系统默认设备；但把 CABLE 之类排在前面，
            # 用户第一次点开下拉就能看到。
            self.device_hint.config(text="使用系统默认")
        self.device_combo.current(index)
        self._on_device_changed(save=False)

    def _on_device_changed(self, _event=None, save: bool = True) -> None:
        device = self._current_device()
        if device is None:
            self.device_hint.config(text="未发现设备")
        else:
            self.device_hint.config(text=f"{device.samplerate} Hz")
        self._update_output_label()
        if save and not self._suspend:
            self._save_soon()

    def _current_device(self) -> audio.OutputDevice | None:
        try:
            index = self.device_combo.current()
        except tk.TclError:
            return None
        if index < 0 or index >= len(self._device_options):
            return audio.default_or_first()
        return self._device_options[index] or audio.default_or_first()

    def _refresh_devices_clicked(self) -> None:
        current = self._current_device()
        saved = current.to_config() if current else None
        self._refresh_device_values(saved)
        count = len(audio.list_output_devices())
        self._notice(f"已刷新，发现 {count} 个可播放设备")

    def _update_output_label(self) -> None:
        device = self._current_device()
        if device is None:
            self.output_label.config(text="输出：未发现设备", fg=RED)
        else:
            name = device.name
            if len(name) > 34:
                name = name[:32] + "…"
            self.output_label.config(
                text=f"输出：{name} · {device.api_short}", fg=FAINT
            )

    def _test_device(self) -> None:
        device = self._current_device()
        if device is None:
            self._notice("没有可用输出设备，请点击“刷新设备”", RED)
            return
        self.test_btn.config(state="disabled", text="测试中…")
        self.run_bg(
            lambda: audio.play_test_tone(device),
            lambda _r: self._test_done(True, device),
            lambda exc: self._test_done(False, device, exc),
        )

    def _test_done(
        self,
        ok: bool,
        device: audio.OutputDevice,
        exc: BaseException | None = None,
    ) -> None:
        if self._closing:
            return
        self.test_btn.config(state="normal", text="测试声音")
        if ok:
            self._notice(f"已向「{device.name}」发送测试音", GREEN)
        else:
            self._notice(f"测试失败：{exc}", RED, ms=7000)

    # -- 音色 / 预设 ------------------------------------------------------

    def _voice_label(self, short: str) -> str:
        label = voices.curated_display(short)
        if label != short:
            return label
        for voice in self.voices:
            if str(voice.get("ShortName")) == short:
                name = voices.display_name(voice)
                locale = voices.locale_label(str(voice.get("Locale", "")))
                return f"{name} · {locale}"
        return short

    def _ensure_voice_option(self, short: str) -> None:
        label = self._voice_label(short)
        existing = self._voice_map.get(label)
        if existing and existing != short:
            label = f"{label} · {short}"
        if short not in self._voice_map.values():
            self._voice_options.append((label, short))
            self._voice_map[label] = short
            self.voice_combo["values"] = [item[0] for item in self._voice_options]

    def _on_voice_changed(self, _event=None) -> None:
        if self._suspend:
            return
        label = self.voice_var.get()
        short = self._voice_map.get(label)
        if not short:
            return
        self._voice_short = short
        self.preset_var.set(CUSTOM_PRESET)
        self._save_soon()

    def _open_voice_dialog(self) -> None:
        VoiceDialog(self, self._voice_short, self._on_voice_dialog_select)

    def _on_voice_dialog_select(self, short: str) -> None:
        self._ensure_voice_option(short)
        self.voice_var.set(self._voice_label(short))
        self._voice_short = short
        self.preset_var.set(CUSTOM_PRESET)
        self._save_soon()

    def _refresh_presets(self, select: str | None = None) -> None:
        names = voices.preset_names(self.cfg["presets"])
        self.preset_combo["values"] = [CUSTOM_PRESET] + names
        if select:
            self.preset_var.set(select)

    def _match_preset(
        self, voice: str, rate: int, pitch: int, volume: int
    ) -> dict[str, Any] | None:
        for preset in voices.BUILTIN_PRESETS + self.cfg["presets"]:
            if (
                preset.get("voice") == voice
                and int(preset.get("rate", 0)) == int(rate)
                and int(preset.get("pitch", 0)) == int(pitch)
                and int(preset.get("volume", 0)) == int(volume)
            ):
                return preset
        return None

    def _on_preset_changed(self, _event=None) -> None:
        if self._suspend:
            return
        name = self.preset_var.get()
        if name == CUSTOM_PRESET:
            return
        preset = voices.find_preset(name, self.cfg["presets"])
        if not preset:
            return
        self._suspend = True
        self._ensure_voice_option(str(preset["voice"]))
        self.voice_var.set(self._voice_label(str(preset["voice"])))
        self._voice_short = str(preset["voice"])
        self.rate_var.set(preset.get("rate", 0))
        self.pitch_var.set(preset.get("pitch", 0))
        self.volume_var.set(preset.get("volume", 0))
        self._update_slider_labels()
        self._suspend = False
        self.preset_var.set(name)
        self._save_soon()

    def _save_preset(self) -> None:
        name = simpledialog.askstring(
            "保存音色",
            "给这个音色起个名字：",
            parent=self.root,
            initialvalue=f"我的音色 {len(self.cfg['presets']) + 1}",
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            return
        if name in (CUSTOM_PRESET,):
            self._notice("这个名字不能用", RED)
            return
        if voices.is_builtin(name):
            self._notice("这是内置预设，请换一个名字", AMBER)
            return
        preset = {
            "name": name[:40],
            "voice": self._voice_short,
            "rate": int(round(self.rate_var.get())),
            "pitch": int(round(self.pitch_var.get())),
            "volume": int(round(self.volume_var.get())),
        }
        existing = next(
            (p for p in self.cfg["presets"] if p["name"] == preset["name"]), None
        )
        if existing and not messagebox.askyesno(
            "覆盖预设", f"“{preset['name']}”已存在，要覆盖吗？", parent=self.root
        ):
            return
        if existing:
            existing.update(preset)
        else:
            self.cfg["presets"].append(preset)
        config.save(self.cfg)
        self._refresh_presets(select=preset["name"])
        self._notice(f"已保存音色预设「{preset['name']}」", GREEN)

    def _delete_preset(self) -> None:
        name = self.preset_var.get()
        if name in (CUSTOM_PRESET, ""):
            self._notice("当前是自定义参数，没有可删除的预设")
            return
        if voices.is_builtin(name):
            self._notice("内置预设不能删除", AMBER)
            return
        if not messagebox.askyesno(
            "删除预设", f"确定删除「{name}」吗？", parent=self.root
        ):
            return
        self.cfg["presets"] = [
            p for p in self.cfg["presets"] if p.get("name") != name
        ]
        config.save(self.cfg)
        self._refresh_presets(select=CUSTOM_PRESET)
        self._notice(f"已删除「{name}」")

    def _on_slider(self) -> None:
        self._update_slider_labels()
        if self._suspend:
            return
        self.preset_var.set(CUSTOM_PRESET)
        self._save_soon()

    def _update_slider_labels(self) -> None:
        self.rate_value.config(text=f"{int(round(self.rate_var.get())):+d}%")
        self.pitch_value.config(text=f"{int(round(self.pitch_var.get())):+d}Hz")
        self.volume_value.config(text=f"{int(round(self.volume_var.get())):+d}%")

    def _reset_tuning(self) -> None:
        self._suspend = True
        self.rate_var.set(0)
        self.pitch_var.set(0)
        self.volume_var.set(0)
        self._update_slider_labels()
        self._suspend = False
        self.preset_var.set(CUSTOM_PRESET)
        self._save_soon()
        self._notice("音调、语速、音量已复位")

    # -- 文本 -------------------------------------------------------------

    def _on_text_modified(self, _event=None) -> None:
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self._update_char_count()
            self._save_soon()

    def _update_char_count(self) -> None:
        count = len("".join(self.text.get_text().split()))
        self.char_label.config(text=f"{count} 字")
        if count > 20000:
            self.char_label.config(text=f"{count} 字（超长，建议分段）", fg=AMBER)
        else:
            self.char_label.config(fg=FAINT)

    def _clear_text(self) -> None:
        self.text.set_text("")
        self._update_char_count()
        self.text.focus_set()
        self._save_soon()

    # -- 朗读 -------------------------------------------------------------

    def _speak(self) -> None:
        if self._status in (CONNECTING, SYNTHESIZING, SPEAKING):
            return
        text = self.text.get_text().strip()
        if not text:
            self._notice("先输入要朗读的文字", AMBER)
            self.text.focus_set()
            return
        if len(text) > 20000:
            self._notice("文字太长了，请截到 2 万字以内或分段朗读", AMBER)
            return
        device = self._current_device()
        if device is None:
            self._notice("没有可用输出设备，请检查声卡或点击“刷新设备”", RED)
            return
        config_text = self.text.get_text()
        self.cfg.update(
            {
                "text": config_text,
                "voice": self._voice_short,
                "rate": int(round(self.rate_var.get())),
                "pitch": int(round(self.pitch_var.get())),
                "volume": int(round(self.volume_var.get())),
                "device": device.to_config(),
            }
        )
        config.save(self.cfg)
        self._notice("")
        self.latency_label.config(text="首音 …", fg=ACCENT)
        self.pipeline.speak(
            text,
            self._voice_short,
            int(round(self.rate_var.get())),
            int(round(self.pitch_var.get())),
            int(round(self.volume_var.get())),
            device,
            prebuffer_ms=int(self.cfg.get("prebuffer_ms", 60)),
        )

    def _stop(self) -> None:
        if self._status in (IDLE, STOPPED, ERROR):
            return
        self.pipeline.stop(wait=False)
        self._status = STOPPED
        self._set_controls(STOPPED)
        self.state_label.config(text="已停止", fg=MUTED)
        self._notice("已停止")

    # -- 状态 / 事件 ------------------------------------------------------

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.put((kind, dict(payload)))

    def _poll(self) -> None:
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                try:
                    self._handle_event(kind, payload)
                except Exception:
                    log.exception("处理界面事件失败: %s", kind)
        except queue.Empty:
            pass
        try:
            while True:
                callback, value = self.ui_results.get_nowait()
                if callback is None:
                    continue
                try:
                    callback(value)
                except Exception:
                    log.exception("后台任务回调失败")
        except queue.Empty:
            pass
        self.root.after(40, self._poll)

    def _handle_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "engine_status":
            status = payload.get("status")
            message = payload.get("message") or ""
            if status == "ready":
                self.chip.set("在线就绪", GREEN)
            elif status == "connecting":
                self.chip.set("连接中…", AMBER)
            else:
                self.chip.set("离线", RED)
                if message:
                    log.info("语音服务不可用：%s", message)
            return

        if kind == "state":
            state = payload.get("state", IDLE)
            self._status = state
            self._set_controls(state)
            labels = {
                CONNECTING: "连接语音服务…",
                SYNTHESIZING: "合成中…",
                SPEAKING: "播放中…",
                IDLE: "就绪",
                STOPPED: "已停止",
                ERROR: "出错",
            }
            colors = {
                CONNECTING: AMBER,
                SYNTHESIZING: AMBER,
                SPEAKING: GREEN,
                IDLE: MUTED,
                STOPPED: MUTED,
                ERROR: RED,
            }
            self.state_label.config(
                text=labels.get(state, state), fg=colors.get(state, MUTED)
            )
            return

        if kind == "first_audio":
            elapsed = float(payload.get("elapsed", 0.0))
            self.latency_label.config(
                text=f"首音 {elapsed * 1000:.0f} ms", fg=GREEN
            )
            self.state_label.config(text="播放中…", fg=GREEN)
            self._status = SPEAKING
            self._set_controls(SPEAKING)
            return

        if kind == "playback_done":
            self._status = IDLE
            self._set_controls(IDLE)
            self.state_label.config(text="就绪", fg=MUTED)
            return

        if kind in ("error", "engine_error"):
            state = payload.get("state", ERROR)
            self._status = state if state == ERROR else ERROR
            message = payload.get("message") or "未知错误"
            self._set_controls(ERROR)
            self.state_label.config(text="出错", fg=RED)
            self._notice(message, RED, ms=8000)
            log.warning("朗读错误：%s", message)
            return

        if kind == "first_chunk":
            log.debug("首个音频分片：%.0f ms", payload.get("elapsed", 0) * 1000)
            return

    def _set_controls(self, state: str) -> None:
        active = state in (CONNECTING, SYNTHESIZING, SPEAKING)
        self.speak_btn.config(state="disabled" if active else "normal")
        self.stop_btn.config(state="normal" if active else "disabled")

    # -- 提示 / 保存 ------------------------------------------------------

    def _notice(self, text: str, color: str = MUTED, ms: int = 5000) -> None:
        if self._notice_job:
            try:
                self.root.after_cancel(self._notice_job)
            except Exception:
                pass
            self._notice_job = None
        self.notice_label.config(text=text, fg=color)
        if text:
            self._notice_job = self.root.after(
                ms, lambda: self.notice_label.config(text="")
            )

    def _save_soon(self) -> None:
        if self._closing:
            return
        if self._save_job:
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.root.after(600, self._flush_save)

    def _flush_save(self) -> None:
        self._save_job = None
        if self._closing:
            return
        device = self._current_device()
        self.cfg.update(
            {
                "text": self.text.get_text(),
                "voice": self._voice_short,
                "rate": int(round(self.rate_var.get())),
                "pitch": int(round(self.pitch_var.get())),
                "volume": int(round(self.volume_var.get())),
                "device": device.to_config() if device else None,
            }
        )
        try:
            config.save(self.cfg)
        except Exception:
            log.warning("保存配置失败", exc_info=True)

    def _remember_geometry(self) -> None:
        try:
            self.cfg["geometry"] = self.root.geometry()
        except tk.TclError:
            pass

    def run_bg(
        self,
        work: Callable[[], Any],
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                self.ui_results.put((on_error, exc))
            else:
                self.ui_results.put((on_success, result))

        threading.Thread(target=runner, daemon=True).start()

    def _maybe_refresh_voice_cache(self) -> None:
        try:
            age = time.time() - config.VOICE_CACHE_PATH.stat().st_mtime
        except OSError:
            age = 10**9
        if age < 3 * 86400:
            return

        def fetched(result: Any) -> None:
            fetched_voices = [v for v in result if isinstance(v, dict)]
            if not fetched_voices:
                return
            self.voices = voices.merge_curated(fetched_voices)
            config.save_voice_cache(self.voices)
            self.cfg["voice_cache_ts"] = time.time()
            try:
                config.save(self.cfg)
            except Exception:
                log.debug("保存配置失败", exc_info=True)

        def ignore(_exc: BaseException) -> None:
            return

        self.run_bg(voices.fetch_voices, fetched, ignore)

    # -- 关闭 -------------------------------------------------------------

    def _on_close(self) -> None:
        if self._closing:
            return
        if self._save_job:
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
        self._remember_geometry()
        self._flush_save()
        self._closing = True
        try:
            self.pipeline.stop(wait=False)
        except Exception:
            pass
        try:
            self.pipeline.engine.shutdown(timeout=1.5)
        except Exception:
            log.debug("关闭语音引擎失败", exc_info=True)
        self.root.destroy()


# ---------------------------------------------------------------------------
# 通用按钮
# ---------------------------------------------------------------------------


def app_button(
    parent: tk.Misc,
    text: str,
    command: Callable[[], None],
    px: Callable[[int], int],
    kind: str = "secondary",
    compact: bool = False,
) -> tk.Button:
    colors = {
        "primary": (ACCENT, "#0b0d12", ACCENT_HOVER, "#0b0d12"),
        "secondary": (PANEL_ALT, TEXT, BORDER, TEXT),
        "ghost": (PANEL, MUTED, PANEL_ALT, TEXT),
        "danger": (PANEL_ALT, RED, "#2a1a22", RED),
    }
    bg, fg, hover_bg, hover_fg = colors.get(kind, colors["secondary"])
    font = _point(10, "bold") if kind == "primary" else _point(9)
    pad_y = px(7) if compact else px(10)
    pad_x = px(13) if compact else px(22)
    button = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=hover_bg,
        activeforeground=hover_fg,
        disabledforeground=FAINT,
        relief="flat",
        bd=0,
        highlightthickness=0,
        cursor="hand2",
        font=font,
        padx=pad_x,
        pady=pad_y,
        takefocus=False,
    )

    if kind == "secondary":
        button.config(highlightthickness=1, highlightbackground=BORDER)
        button.bind(
            "<Enter>",
            lambda _e: button.config(
                bg=hover_bg,
                highlightbackground=ACCENT_DIM,
            )
            if button["state"] == "normal"
            else None,
        )
        button.bind(
            "<Leave>",
            lambda _e: button.config(
                bg=bg if button["state"] == "normal" else bg,
                highlightbackground=BORDER,
            ),
        )
    else:
        button.bind(
            "<Enter>",
            lambda _e: button.config(bg=hover_bg, fg=hover_fg)
            if button["state"] == "normal"
            else None,
        )
        button.bind(
            "<Leave>",
            lambda _e: button.config(bg=bg, fg=fg),
        )
    return button


def run() -> None:
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", max(1.0, float(root.winfo_fpixels("1i")) / 72.0))
    except Exception:
        pass
    try:
        from .icon import ensure_icon

        icon = ensure_icon(config.DATA_DIR / "icon.ico")
        if icon is not None:
            root.iconbitmap(str(icon))
    except Exception:
        pass
    MouthApp(root)
    root.mainloop()
