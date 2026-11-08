"""ProChat - desktop chat client for the OpenAI API."""
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import uuid
from tkinter import filedialog, messagebox, ttk

import api_client as api

try:
    import keyring
except Exception:  # keyring is optional: without it the key lives only in memory
    keyring = None

APP_NAME = "ProChat"
KEY_SERVICE = "ProChat"
KEY_USER = "openai_api_key"
DEFAULT_MODELS = ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"]
SYSTEM_PROMPT = "You are a helpful assistant. Reply in the same language the user writes in."
MAX_CONTEXT_MESSAGES = 40
NEW_TITLE = "New chat"


# ----------------------------------------------------------------- storage
def data_dir():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


class Store:
    def __init__(self):
        self.path = os.path.join(data_dir(), "chats.json")
        self.data = {"chats": [], "model": DEFAULT_MODELS[0]}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("chats"), list):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass

    def save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def get(self, chat_id):
        for chat in self.data["chats"]:
            if chat["id"] == chat_id:
                return chat
        return None

    def sorted_chats(self):
        return sorted(self.data["chats"], key=lambda c: c.get("updated", 0), reverse=True)


def load_key():
    if keyring is not None:
        try:
            value = keyring.get_password(KEY_SERVICE, KEY_USER)
            if value:
                return value
        except Exception:
            pass
    return os.environ.get("OPENAI_API_KEY", "")


def save_key(value):
    """Returns True if stored persistently."""
    if keyring is None:
        return False
    try:
        keyring.set_password(KEY_SERVICE, KEY_USER, value)
        return True
    except Exception:
        return False


def delete_key():
    if keyring is not None:
        try:
            keyring.delete_password(KEY_SERVICE, KEY_USER)
        except Exception:
            pass


# --------------------------------------------------------------------- app
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1040x700")
        self.minsize(780, 480)

        self.store = Store()
        self.key = load_key()
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.current = None
        self.list_ids = []
        self.settings_win = None
        self.settings_status = None

        self._style()
        self._build()
        self._refresh_list()
        chats = self.store.sorted_chats()
        if chats:
            self.open_chat(chats[0]["id"])
        else:
            self.new_chat()

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(40, self._poll)
        if self.key:
            self._fetch_models()
        else:
            self.after(300, self.open_settings)

    # ------------------------------------------------------------- layout
    def _style(self):
        style = ttk.Style(self)
        for theme in ("vista", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        self.option_add("*Font", ("Segoe UI", 10))
        style.configure("Send.TButton", padding=(18, 8))
        style.configure("Side.TButton", padding=(8, 5))

    def _build(self):
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        # sidebar
        side = ttk.Frame(root, padding=8)
        side.grid(row=0, column=0, sticky="ns")
        ttk.Button(side, text="+ New chat", style="Side.TButton", command=self.new_chat).pack(fill="x")
        self.listbox = tk.Listbox(side, width=28, activestyle="none", exportselection=False,
                                  borderwidth=1, relief="solid", highlightthickness=0)
        self.listbox.pack(fill="both", expand=True, pady=8)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        ttk.Button(side, text="Delete chat", style="Side.TButton", command=self.delete_chat).pack(fill="x")
        ttk.Button(side, text="Settings", style="Side.TButton", command=self.open_settings).pack(fill="x", pady=(6, 0))

        # main area
        main = ttk.Frame(root, padding=(0, 8, 8, 8))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        bar = ttk.Frame(main)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(bar, text="Model:").pack(side="left")
        self.model_var = tk.StringVar(value=self.store.data.get("model", DEFAULT_MODELS[0]))
        self.model_box = ttk.Combobox(bar, textvariable=self.model_var, values=DEFAULT_MODELS, width=26)
        self.model_box.pack(side="left", padx=6)
        ttk.Button(bar, text="Export", command=self.export_chat).pack(side="right")
        ttk.Button(bar, text="Copy last answer", command=self.copy_last).pack(side="right", padx=6)

        frame = ttk.Frame(main)
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.chat = tk.Text(frame, wrap="word", state="disabled", padx=12, pady=10,
                            borderwidth=1, relief="solid", font=("Segoe UI", 11),
                            spacing1=2, spacing3=2, highlightthickness=0)
        scroll = ttk.Scrollbar(frame, command=self.chat.yview)
        self.chat.configure(yscrollcommand=scroll.set)
        self.chat.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.chat.bind("<Button-1>", lambda e: self.chat.focus_set())
        self.chat.tag_configure("who_user", foreground="#1f5eff", font=("Segoe UI", 10, "bold"), spacing1=12)
        self.chat.tag_configure("who_bot", foreground="#0f7b4a", font=("Segoe UI", 10, "bold"), spacing1=12)
        self.chat.tag_configure("body", lmargin1=0, lmargin2=0)
        self.chat.tag_configure("error", foreground="#b3261e", spacing1=10)
        self.chat.tag_configure("note", foreground="#6b7280", spacing1=10)

        entry = ttk.Frame(main)
        entry.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        entry.columnconfigure(0, weight=1)
        self.input = tk.Text(entry, height=3, wrap="word", font=("Segoe UI", 11), padx=8, pady=6,
                             borderwidth=1, relief="solid", highlightthickness=0, undo=True)
        self.input.grid(row=0, column=0, sticky="ew")
        self.input.bind("<Return>", self.send)
        self.input.bind("<Shift-Return>", lambda e: None)
        self.send_btn = ttk.Button(entry, text="Send", style="Send.TButton", command=self.send)
        self.send_btn.grid(row=0, column=1, padx=(8, 0), sticky="ns")

        self.status = tk.StringVar(value="Enter to send, Shift+Enter for a new line.")
        ttk.Label(main, textvariable=self.status, foreground="#6b7280").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.input.focus_set()

    # -------------------------------------------------------------- chats
    def _refresh_list(self):
        self.listbox.delete(0, "end")
        self.list_ids = []
        for chat in self.store.sorted_chats():
            self.listbox.insert("end", chat["title"])
            self.list_ids.append(chat["id"])
        if self.current and self.current["id"] in self.list_ids:
            idx = self.list_ids.index(self.current["id"])
            self.listbox.selection_set(idx)
            self.listbox.see(idx)

    def new_chat(self):
        if self.busy:
            return
        for chat in self.store.data["chats"]:
            if chat["title"] == NEW_TITLE and not chat["messages"]:
                self.open_chat(chat["id"])
                return
        chat = {"id": uuid.uuid4().hex, "title": NEW_TITLE, "messages": [], "updated": time.time()}
        self.store.data["chats"].append(chat)
        self.store.save()
        self.open_chat(chat["id"])

    def open_chat(self, chat_id):
        chat = self.store.get(chat_id)
        if not chat:
            return
        self.current = chat
        self._refresh_list()
        self._render()
        self.input.focus_set()

    def _on_select(self, _event):
        sel = self.listbox.curselection()
        if not sel:
            return
        chat_id = self.list_ids[sel[0]]
        if self.busy:
            self._refresh_list()
            return
        if not self.current or chat_id != self.current["id"]:
            self.open_chat(chat_id)

    def delete_chat(self):
        if self.busy or not self.current:
            return
        if not messagebox.askyesno(APP_NAME, "Delete this chat permanently?"):
            return
        self.store.data["chats"] = [c for c in self.store.data["chats"] if c["id"] != self.current["id"]]
        self.store.save()
        self.current = None
        chats = self.store.sorted_chats()
        if chats:
            self.open_chat(chats[0]["id"])
        else:
            self.new_chat()

    # ------------------------------------------------------------ display
    def _write(self, text, *tags):
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tags)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _render(self):
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        if not self.current["messages"]:
            self._write("Start a conversation below.\n", "note")
        for msg in self.current["messages"]:
            self._write_message(msg["role"], msg["content"])

    def _write_message(self, role, content):
        if role == "user":
            self._write("You\n", "who_user")
        else:
            self._write("Assistant\n", "who_bot")
        self._write(content + "\n", "body")

    # ------------------------------------------------------------ sending
    def send(self, _event=None):
        if self.busy:
            return "break"
        text = self.input.get("1.0", "end").strip()
        if not text:
            return "break"
        if not self.key:
            self.open_settings()
            return "break"
        model = self.model_var.get().strip()
        if not model:
            self.status.set("Choose a model first.")
            return "break"

        chat = self.current
        if not chat["messages"]:
            self._render_clear()
        self.input.delete("1.0", "end")
        chat["messages"].append({"role": "user", "content": text})
        if chat["title"] == NEW_TITLE:
            chat["title"] = (text.replace("\n", " ")[:38] + ("..." if len(text) > 38 else ""))
        chat["updated"] = time.time()
        self.store.data["model"] = model
        self.store.save()
        self._refresh_list()
        self._write_message("user", text)
        self._write("Assistant\n", "who_bot")

        history = chat["messages"][-MAX_CONTEXT_MESSAGES:]
        payload = [{"role": "system", "content": SYSTEM_PROMPT}] + history
        self.stop_event.clear()
        self._set_busy(True)
        threading.Thread(target=self._worker, args=(chat["id"], model, payload), daemon=True).start()
        return "break"

    def _render_clear(self):
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")

    def _worker(self, chat_id, model, payload):
        parts = []
        try:
            for chunk in api.stream_chat(self.key, model, payload, stop=self.stop_event):
                parts.append(chunk)
                self.events.put(("chunk", chat_id, chunk))
            self.events.put(("done", chat_id, "".join(parts)))
        except api.ApiError as exc:
            self.events.put(("error", chat_id, str(exc), "".join(parts)))
        except Exception as exc:  # never let the thread die silently
            self.events.put(("error", chat_id, f"Unexpected error: {exc}", "".join(parts)))

    def _set_busy(self, busy):
        self.busy = busy
        if busy:
            self.send_btn.configure(text="Stop", command=self.stop)
            self.status.set("Generating...")
        else:
            self.send_btn.configure(text="Send", command=self.send)
            self.status.set("Enter to send, Shift+Enter for a new line.")

    def stop(self):
        self.stop_event.set()

    def _poll(self):
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(40, self._poll)

    def _handle(self, ev):
        kind = ev[0]
        if kind == "chunk":
            _, chat_id, chunk = ev
            if self.current and self.current["id"] == chat_id:
                self._write(chunk, "body")
        elif kind == "done":
            _, chat_id, text = ev
            chat = self.store.get(chat_id)
            if chat is not None and text:
                chat["messages"].append({"role": "assistant", "content": text})
                chat["updated"] = time.time()
                self.store.save()
            if self.current and self.current["id"] == chat_id:
                self._write("\n")
            self._set_busy(False)
        elif kind == "error":
            _, chat_id, message, partial = ev
            chat = self.store.get(chat_id)
            if chat is not None:
                if partial:
                    chat["messages"].append({"role": "assistant", "content": partial})
                elif chat["messages"] and chat["messages"][-1]["role"] == "user":
                    # nothing was answered: give the question back so it can be re-sent
                    last = chat["messages"].pop()
                    self.input.delete("1.0", "end")
                    self.input.insert("1.0", last["content"])
                self.store.save()
            if self.current and self.current["id"] == chat_id:
                self._write("\n" + message + "\n", "error")
            self._set_busy(False)
            if "API key" in message:
                self.after(200, self.open_settings)
        elif kind == "models":
            models = ev[1]
            if models:
                self.model_box.configure(values=models)
        elif kind == "keytest":
            _, ok, message = ev
            if self.settings_status is not None and self.settings_win is not None \
                    and self.settings_win.winfo_exists():
                self.settings_status.set(message)
            if ok:
                self._fetch_models()

    def _fetch_models(self):
        key = self.key

        def run():
            try:
                self.events.put(("models", api.list_models(key)))
            except Exception:
                pass

        threading.Thread(target=run, daemon=True).start()

    # --------------------------------------------------------- utilities
    def copy_last(self):
        if not self.current:
            return
        for msg in reversed(self.current["messages"]):
            if msg["role"] == "assistant":
                self.clipboard_clear()
                self.clipboard_append(msg["content"])
                self.status.set("Last answer copied.")
                return
        self.status.set("No answer to copy yet.")

    def export_chat(self):
        if not self.current or not self.current["messages"]:
            self.status.set("Nothing to export.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".md", initialfile="chat.md",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt")])
        if not path:
            return
        lines = [f"# {self.current['title']}", ""]
        for msg in self.current["messages"]:
            who = "You" if msg["role"] == "user" else "Assistant"
            lines += [f"**{who}**", "", msg["content"], ""]
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self.status.set("Exported.")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not save the file:\n{exc}")

    # ----------------------------------------------------------- settings
    def open_settings(self):
        if self.settings_win is not None and self.settings_win.winfo_exists():
            self.settings_win.lift()
            return
        win = tk.Toplevel(self)
        self.settings_win = win
        win.title("Settings")
        win.transient(self)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=16)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="OpenAI API key").grid(row=0, column=0, sticky="w")
        key_var = tk.StringVar(value=self.key)
        entry = ttk.Entry(body, textvariable=key_var, show="\u2022", width=56)
        entry.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 8))
        ttk.Label(body, foreground="#6b7280", wraplength=420, justify="left",
                  text="Create a key at platform.openai.com. API usage is billed separately "
                       "from a ChatGPT subscription. The key is stored in Windows Credential "
                       "Manager, never in the chat file.").grid(row=2, column=0, columnspan=3, sticky="w")

        self.settings_status = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.settings_status, wraplength=420,
                  justify="left").grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))

        def save():
            value = key_var.get().strip()
            if not value:
                self.settings_status.set("Paste a key first.")
                return
            self.key = value
            if save_key(value):
                self.settings_status.set("Saved. Testing the key...")
            else:
                self.settings_status.set("Saved for this session only (secure storage unavailable). Testing...")
            self._test_key(value)

        def test():
            value = key_var.get().strip()
            if not value:
                self.settings_status.set("Paste a key first.")
                return
            self.settings_status.set("Testing...")
            self._test_key(value)

        def remove():
            delete_key()
            self.key = ""
            key_var.set("")
            self.settings_status.set("Key removed.")

        row = ttk.Frame(body)
        row.grid(row=4, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(row, text="Remove key", command=remove).pack(side="left", padx=4)
        ttk.Button(row, text="Test", command=test).pack(side="left", padx=4)
        ttk.Button(row, text="Save", command=save).pack(side="left", padx=4)
        ttk.Button(row, text="Close", command=win.destroy).pack(side="left", padx=4)
        entry.focus_set()

    def _test_key(self, value):
        def run():
            try:
                models = api.list_models(value)
                self.events.put(("keytest", True, f"Key works. {len(models)} chat models available."))
            except api.ApiError as exc:
                self.events.put(("keytest", False, str(exc)))
            except Exception as exc:
                self.events.put(("keytest", False, f"Unexpected error: {exc}"))

        threading.Thread(target=run, daemon=True).start()

    def _close(self):
        self.store.data["model"] = self.model_var.get().strip() or DEFAULT_MODELS[0]
        self.store.save()
        self.destroy()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
