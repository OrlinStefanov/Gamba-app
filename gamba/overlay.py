"""Always-on-top heads-up display.

Tkinter is not thread-safe, so every method here that can be called from a
worker thread just puts a command on a queue; the Tk main loop drains it.
"""

from __future__ import annotations

import queue
import tkinter as tk
from typing import Callable, Optional


class Overlay:
    def __init__(self, config, on_question: Callable[[str], None], on_quit: Callable[[], None]) -> None:
        self.config = config
        self._on_question = on_question
        self._on_quit = on_quit
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._visible = True
        self._drag_origin = (0, 0)

        self.root = tk.Tk()
        self.root.title("Gamba")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", config.opacity)
        except tk.TclError:
            pass  # some window managers do not support per-window alpha
        self.root.configure(bg=config.background)

        pad = 12
        frame = tk.Frame(self.root, bg=config.background, padx=pad, pady=pad)
        frame.pack(fill="both", expand=True)

        header = tk.Frame(frame, bg=config.background)
        header.pack(fill="x")
        self.status_label = tk.Label(
            header,
            text="ready",
            bg=config.background,
            fg=config.muted,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.status_label.pack(side="left")
        self.meta_label = tk.Label(
            header,
            text="",
            bg=config.background,
            fg=config.muted,
            font=("Segoe UI", 9),
            anchor="e",
        )
        self.meta_label.pack(side="right")

        wrap = config.width - 2 * pad
        self.answer_label = tk.Label(
            frame,
            text="Press the ask hotkey to begin.",
            bg=config.background,
            fg=config.foreground,
            font=("Segoe UI", config.answer_font_size, "bold"),
            wraplength=wrap,
            justify="left",
            anchor="w",
        )
        self.answer_label.pack(fill="x", pady=(8, 0))

        self.detail_label = tk.Label(
            frame,
            text="",
            bg=config.background,
            fg=config.muted,
            font=("Segoe UI", config.detail_font_size),
            wraplength=wrap,
            justify="left",
            anchor="w",
        )
        self.detail_label.pack(fill="x", pady=(6, 0))

        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(
            frame,
            textvariable=self.entry_var,
            bg="#1c1f27",
            fg=config.foreground,
            insertbackground=config.foreground,
            relief="flat",
            font=("Segoe UI", 11),
        )
        self.entry.bind("<Return>", self._submit_question)
        self.entry.bind("<Escape>", lambda _event: self.hide_entry())

        for widget in (frame, header, self.status_label, self.meta_label,
                       self.answer_label, self.detail_label):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)

        self.root.geometry(f"{config.width}x1")
        self.root.update_idletasks()
        self._place()
        self.root.after(30, self._pump)

    # -- geometry -----------------------------------------------------------

    def _place(self) -> None:
        self.root.update_idletasks()
        width = self.config.width
        height = self.root.winfo_reqheight()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = self.config.x if self.config.x >= 0 else screen_w - width + self.config.x
        y = self.config.y if self.config.y >= 0 else screen_h - height + self.config.y
        self.root.geometry(f"{width}x{height}+{int(x)}+{int(y)}")

    def _start_drag(self, event) -> None:
        self._drag_origin = (event.x_root, event.y_root)
        self._drag_window = (self.root.winfo_x(), self.root.winfo_y())

    def _drag(self, event) -> None:
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        x, y = self._drag_window
        self.root.geometry(f"+{x + dx}+{y + dy}")

    # -- thread-safe entry points -------------------------------------------

    def post(self, name: str, *args) -> None:
        self._queue.put((name, args))

    def start_request(self, question: str) -> None:
        self.post("_ui_start_request", question)

    def append_text(self, text: str) -> None:
        self.post("_ui_append_text", text)

    def finish(self, answer, meta: str) -> None:
        self.post("_ui_finish", answer, meta)

    def set_status(self, text: str, color: Optional[str] = None) -> None:
        self.post("_ui_set_status", text, color)

    def set_meta(self, text: str) -> None:
        self.post("_ui_set_meta", text)

    def ask_question(self, prefill: str = "") -> None:
        self.post("_ui_ask_question", prefill)

    def toggle_visible(self) -> None:
        self.post("_ui_toggle_visible")

    def show(self) -> None:
        self.post("_ui_show")

    def shutdown(self) -> None:
        self.post("_ui_shutdown")

    # -- main loop ----------------------------------------------------------

    def _pump(self) -> None:
        try:
            while True:
                name, args = self._queue.get_nowait()
                getattr(self, name)(*args)
        except queue.Empty:
            pass
        except Exception as exc:  # never let a UI error kill the loop
            self._ui_set_status(f"ui error: {exc}", "#ff7b72")
        self.root.after(30, self._pump)

    def mainloop(self) -> None:
        self.root.mainloop()

    # -- UI-thread handlers -------------------------------------------------

    def _ui_start_request(self, question: str) -> None:
        self._buffer = ""
        self._ui_show()
        self.status_label.config(text=_shorten(question, 48), fg=self.config.accent)
        self.answer_label.config(text="...", fg=self.config.foreground)
        self.detail_label.config(text="")
        self.meta_label.config(text="")
        self._place()

    def _ui_append_text(self, text: str) -> None:
        self._buffer = getattr(self, "_buffer", "") + text
        head, _, rest = self._buffer.partition("\n")
        self.answer_label.config(text=head or "...")
        self.detail_label.config(text=rest.strip())
        self._place()

    def _ui_finish(self, answer, meta: str) -> None:
        if answer.error:
            self.answer_label.config(text=answer.error, fg="#ff7b72")
            self.detail_label.config(text="")
        else:
            self.answer_label.config(text=answer.headline or "(no answer)",
                                     fg=self.config.foreground)
            self.detail_label.config(text=answer.detail)
        self.status_label.config(
            text="truncated at deadline" if answer.truncated else "done",
            fg="#e3b341" if answer.truncated else self.config.muted,
        )
        self.meta_label.config(text=meta)
        self._place()
        if self.config.autohide_seconds > 0:
            self.root.after(int(self.config.autohide_seconds * 1000), self._ui_hide)

    def _ui_set_status(self, text: str, color: Optional[str]) -> None:
        self.status_label.config(text=text, fg=color or self.config.muted)

    def _ui_set_meta(self, text: str) -> None:
        self.meta_label.config(text=text)

    def _ui_ask_question(self, prefill: str) -> None:
        self._ui_show()
        self.entry_var.set(prefill)
        self.entry.pack(fill="x", pady=(10, 0))
        self._place()
        self.entry.focus_force()
        self.entry.icursor("end")
        self.status_label.config(text="type a question, Enter to send",
                                 fg=self.config.accent)

    def hide_entry(self) -> None:
        self.entry.pack_forget()
        self._place()

    def _submit_question(self, _event=None) -> None:
        question = self.entry_var.get().strip()
        self.hide_entry()
        if question:
            self._on_question(question)

    def _ui_toggle_visible(self) -> None:
        if self._visible:
            self._ui_hide()
        else:
            self._ui_show()

    def _ui_show(self) -> None:
        if not self._visible:
            self.root.deiconify()
            self._visible = True
        self.root.attributes("-topmost", True)
        self.root.lift()

    def _ui_hide(self) -> None:
        self.root.withdraw()
        self._visible = False

    def _ui_shutdown(self) -> None:
        self._on_quit()
        self.root.quit()
        self.root.destroy()


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
