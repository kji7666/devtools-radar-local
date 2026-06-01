from pathlib import Path
from datetime import datetime
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import re


BASE_DIR = Path(__file__).resolve().parent
PYTHON_EXE = BASE_DIR / ".venv" / "Scripts" / "python.exe"
MAIN_PY = BASE_DIR / "main.py"

TASKS_DIR = BASE_DIR / "tasks"
OUTPUTS_DIR = BASE_DIR / "outputs"
TEMPLATES_DIR = BASE_DIR / "scheduled_templates"
LOG_DIR = BASE_DIR / "logs"

OUTPUT_FILE = BASE_DIR / "output.txt"
RUN_BAT = BASE_DIR / "run.bat"

for d in [TASKS_DIR, OUTPUTS_DIR, TEMPLATES_DIR, LOG_DIR]:
    d.mkdir(exist_ok=True)


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_name(text: str) -> str:
    text = text.strip() or "task"
    text = re.sub(r'[\\/:*?"<>| ]+', "_", text)
    return text[:60]


def run_command_async(command: list[str], on_done):
    def worker():
        try:
            result = subprocess.run(
                command,
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            on_done(result.returncode, result.stdout, result.stderr)
        except Exception as e:
            on_done(1, "", f"{type(e).__name__}: {e}")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()


class AutoGPTUI(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Auto GPT UI")
        self.geometry("980x720")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.chat_frame = ttk.Frame(self.notebook)
        self.schedule_frame = ttk.Frame(self.notebook)
        self.tasks_frame = ttk.Frame(self.notebook)
        self.logs_frame = ttk.Frame(self.notebook)

        self.notebook.add(self.chat_frame, text="對話")
        self.notebook.add(self.schedule_frame, text="排程")
        self.notebook.add(self.tasks_frame, text="任務")
        self.notebook.add(self.logs_frame, text="Log")

        self.build_chat_tab()
        self.build_schedule_tab()
        self.build_tasks_tab()
        self.build_logs_tab()

    def build_chat_tab(self):
        top = ttk.Frame(self.chat_frame)
        top.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.chat_output = scrolledtext.ScrolledText(top, wrap=tk.WORD, height=24)
        self.chat_output.pack(fill=tk.BOTH, expand=True)

        input_frame = ttk.Frame(self.chat_frame)
        input_frame.pack(fill=tk.X, padx=10, pady=8)

        self.chat_input = scrolledtext.ScrolledText(input_frame, wrap=tk.WORD, height=6)
        self.chat_input.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        button_frame = ttk.Frame(input_frame)
        button_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))

        self.send_button = ttk.Button(button_frame, text="送出", command=self.send_chat)
        self.send_button.pack(fill=tk.X, pady=4)

        self.run_batch_button = ttk.Button(button_frame, text="執行 tasks/", command=self.run_batch)
        self.run_batch_button.pack(fill=tk.X, pady=4)

        self.status_var = tk.StringVar(value="就緒")
        status = ttk.Label(self.chat_frame, textvariable=self.status_var)
        status.pack(anchor=tk.W, padx=10, pady=(0, 8))

    def build_schedule_tab(self):
        container = ttk.Frame(self.schedule_frame)
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        row1 = ttk.Frame(container)
        row1.pack(fill=tk.X, pady=4)

        ttk.Label(row1, text="任務名稱").pack(side=tk.LEFT)
        self.schedule_name_var = tk.StringVar(value=f"scheduled_{now_stamp()}")
        ttk.Entry(row1, textvariable=self.schedule_name_var, width=40).pack(side=tk.LEFT, padx=8)

        row2 = ttk.Frame(container)
        row2.pack(fill=tk.X, pady=4)

        ttk.Label(row2, text="類型").pack(side=tk.LEFT)
        self.schedule_type_var = tk.StringVar(value="once")
        ttk.Combobox(
            row2,
            textvariable=self.schedule_type_var,
            values=["once", "daily", "weekly"],
            width=12,
            state="readonly",
        ).pack(side=tk.LEFT, padx=8)

        ttk.Label(row2, text="日期 YYYY-MM-DD").pack(side=tk.LEFT, padx=(20, 0))
        self.schedule_date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        ttk.Entry(row2, textvariable=self.schedule_date_var, width=14).pack(side=tk.LEFT, padx=8)

        ttk.Label(row2, text="時間 HH:MM").pack(side=tk.LEFT, padx=(20, 0))
        self.schedule_time_var = tk.StringVar(value="09:00")
        ttk.Entry(row2, textvariable=self.schedule_time_var, width=8).pack(side=tk.LEFT, padx=8)

        row3 = ttk.Frame(container)
        row3.pack(fill=tk.BOTH, expand=True, pady=8)

        ttk.Label(row3, text="排程 Prompt").pack(anchor=tk.W)
        self.schedule_prompt = scrolledtext.ScrolledText(row3, wrap=tk.WORD, height=18)
        self.schedule_prompt.pack(fill=tk.BOTH, expand=True)

        row4 = ttk.Frame(container)
        row4.pack(fill=tk.X, pady=8)

        ttk.Button(row4, text="只存成 tasks/ 待處理", command=self.save_pending_task).pack(side=tk.LEFT)
        ttk.Button(row4, text="建立 Windows 排程", command=self.create_windows_schedule).pack(side=tk.LEFT, padx=8)

        self.schedule_status_var = tk.StringVar(value="尚未建立排程")
        ttk.Label(container, textvariable=self.schedule_status_var).pack(anchor=tk.W)

    def build_tasks_tab(self):
        container = ttk.Frame(self.tasks_frame)
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        button_row = ttk.Frame(container)
        button_row.pack(fill=tk.X)

        ttk.Button(button_row, text="重新整理", command=self.refresh_tasks).pack(side=tk.LEFT)
        ttk.Button(button_row, text="執行 tasks/", command=self.run_batch).pack(side=tk.LEFT, padx=8)

        self.tasks_list = tk.Listbox(container)
        self.tasks_list.pack(fill=tk.BOTH, expand=True, pady=10)

        self.refresh_tasks()

    def build_logs_tab(self):
        container = ttk.Frame(self.logs_frame)
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        ttk.Button(container, text="重新載入 runner.log", command=self.refresh_log).pack(anchor=tk.W)

        self.log_text = scrolledtext.ScrolledText(container, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=8)

        self.refresh_log()

    def append_chat(self, speaker: str, text: str):
        self.chat_output.insert(tk.END, f"\n[{speaker}]\n{text}\n")
        self.chat_output.see(tk.END)

    def set_busy(self, busy: bool):
        if busy:
            self.send_button.config(state=tk.DISABLED)
            self.run_batch_button.config(state=tk.DISABLED)
            self.status_var.set("執行中...")
        else:
            self.send_button.config(state=tk.NORMAL)
            self.run_batch_button.config(state=tk.NORMAL)
            self.status_var.set("就緒")

    def send_chat(self):
        prompt = self.chat_input.get("1.0", tk.END).strip()

        if not prompt:
            messagebox.showwarning("提示", "請先輸入內容")
            return

        self.chat_input.delete("1.0", tk.END)
        self.append_chat("你", prompt)
        self.set_busy(True)

        command = [str(PYTHON_EXE), str(MAIN_PY), "--prompt-text", prompt]

        def done(code, stdout, stderr):
            self.after(0, lambda: self.on_chat_done(code, stdout, stderr))

        run_command_async(command, done)

    def on_chat_done(self, code: int, stdout: str, stderr: str):
        self.set_busy(False)

        if OUTPUT_FILE.exists():
            answer = OUTPUT_FILE.read_text(encoding="utf-8", errors="replace")
        else:
            answer = ""

        if code == 0:
            self.append_chat("ChatGPT", answer or "(沒有讀到 output.txt)")
        else:
            self.append_chat("錯誤", stderr or stdout or answer or "未知錯誤")

        self.refresh_log()

    def run_batch(self):
        self.set_busy(True)
        command = [str(PYTHON_EXE), str(MAIN_PY), "--batch"]

        def done(code, stdout, stderr):
            self.after(0, lambda: self.on_batch_done(code, stdout, stderr))

        run_command_async(command, done)

    def on_batch_done(self, code: int, stdout: str, stderr: str):
        self.set_busy(False)
        msg = stdout if code == 0 else (stderr or stdout)
        self.append_chat("系統", msg)
        self.refresh_tasks()
        self.refresh_log()

    def save_pending_task(self):
        name = safe_name(self.schedule_name_var.get())
        prompt = self.schedule_prompt.get("1.0", tk.END).strip()

        if not prompt:
            messagebox.showwarning("提示", "請輸入排程 Prompt")
            return

        path = TASKS_DIR / f"{name}_{now_stamp()}.txt"
        path.write_text(prompt, encoding="utf-8")

        self.schedule_status_var.set(f"已存成待處理任務：{path}")
        self.refresh_tasks()

    def create_windows_schedule(self):
        name = safe_name(self.schedule_name_var.get())
        prompt = self.schedule_prompt.get("1.0", tk.END).strip()
        schedule_type = self.schedule_type_var.get()
        date_text = self.schedule_date_var.get().strip()
        time_text = self.schedule_time_var.get().strip()

        if not prompt:
            messagebox.showwarning("提示", "請輸入排程 Prompt")
            return

        template_path = TEMPLATES_DIR / f"{name}.txt"
        template_path.write_text(prompt, encoding="utf-8")

        task_name = f"AutoGPT_{name}"

        command = (
            f'"{PYTHON_EXE}" "{MAIN_PY}" '
            f'--template "{template_path}"'
        )

        if schedule_type == "once":
            schtasks_cmd = [
                "schtasks",
                "/create",
                "/tn",
                task_name,
                "/sc",
                "once",
                "/sd",
                date_text,
                "/st",
                time_text,
                "/tr",
                command,
                "/f",
            ]
        elif schedule_type == "daily":
            schtasks_cmd = [
                "schtasks",
                "/create",
                "/tn",
                task_name,
                "/sc",
                "daily",
                "/st",
                time_text,
                "/tr",
                command,
                "/f",
            ]
        elif schedule_type == "weekly":
            schtasks_cmd = [
                "schtasks",
                "/create",
                "/tn",
                task_name,
                "/sc",
                "weekly",
                "/st",
                time_text,
                "/tr",
                command,
                "/f",
            ]
        else:
            messagebox.showerror("錯誤", "不支援的排程類型")
            return

        try:
            result = subprocess.run(
                schtasks_cmd,
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            if result.returncode == 0:
                self.schedule_status_var.set(f"已建立排程：{task_name}")
                messagebox.showinfo("成功", f"已建立 Windows 排程：{task_name}")
            else:
                self.schedule_status_var.set(result.stderr or result.stdout)
                messagebox.showerror("建立失敗", result.stderr or result.stdout)

        except Exception as e:
            messagebox.showerror("錯誤", f"{type(e).__name__}: {e}")

    def refresh_tasks(self):
        self.tasks_list.delete(0, tk.END)

        files = sorted(TASKS_DIR.glob("*.txt"))
        if not files:
            self.tasks_list.insert(tk.END, "(tasks/ 目前沒有任務)")
            return

        for path in files:
            self.tasks_list.insert(tk.END, path.name)

    def refresh_log(self):
        log_path = LOG_DIR / "runner.log"

        self.log_text.delete("1.0", tk.END)

        if not log_path.exists():
            self.log_text.insert(tk.END, "尚無 runner.log")
            return

        text = log_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()[-300:]
        self.log_text.insert(tk.END, "\n".join(lines))
        self.log_text.see(tk.END)


if __name__ == "__main__":
    app = AutoGPTUI()
    app.mainloop()