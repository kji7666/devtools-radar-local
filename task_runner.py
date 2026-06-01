from pathlib import Path
from datetime import datetime
import argparse
import json
import re
import subprocess
import sys
import uuid


BASE_DIR = Path(__file__).resolve().parent
PYTHON_EXE = BASE_DIR / ".venv" / "Scripts" / "python.exe"
MAIN_PY = BASE_DIR / "main.py"

OUTPUT_TXT = BASE_DIR / "output.txt"
RUNS_DIR = BASE_DIR / "runs"
TASKS_META_DIR = BASE_DIR / "tasks_meta"
OUTPUTS_DIR = BASE_DIR / "outputs"

RUNS_DIR.mkdir(exist_ok=True)
TASKS_META_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_filename_part(text: str) -> str:
    text = text.strip() or "task"
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = text.strip("._ ")

    if not text:
        text = "task"

    return text[:80]


def render_prompt(prompt: str) -> str:
    now = datetime.now()
    return (
        prompt
        .replace("{{date}}", now.strftime("%Y-%m-%d"))
        .replace("{{time}}", now.strftime("%H:%M:%S"))
        .replace("{{datetime}}", now.strftime("%Y-%m-%d %H:%M:%S"))
    )


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_output() -> str:
    if not OUTPUT_TXT.exists():
        return ""
    return OUTPUT_TXT.read_text(encoding="utf-8", errors="replace")


def run_main_with_prompt(prompt: str) -> dict:
    if not PYTHON_EXE.exists():
        return {
            "code": 1,
            "stdout": "",
            "stderr": f"Python executable not found: {PYTHON_EXE}",
            "output": "",
        }

    if not MAIN_PY.exists():
        return {
            "code": 1,
            "stdout": "",
            "stderr": f"main.py not found: {MAIN_PY}",
            "output": "",
        }

    process = subprocess.run(
        [str(PYTHON_EXE), str(MAIN_PY), "--prompt-text", prompt],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    return {
        "code": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
        "output": read_output(),
    }


def write_markdown_output(task: dict, markdown: str) -> Path:
    task_name = safe_filename_part(
        task.get("title")
        or task.get("id")
        or "task"
    )

    filename = f"{now_stamp()}_{task_name}.md"
    output_path = OUTPUTS_DIR / filename

    output_path.write_text(markdown, encoding="utf-8")

    return output_path


def write_run_record(
    task: dict,
    task_path: Path,
    result: dict,
    started_at: str,
    finished_at: str,
    markdown_output_path: Path | None,
) -> Path:
    run_id = f"run_{now_stamp()}_{uuid.uuid4().hex[:8]}"

    run_record = {
        "id": run_id,
        "taskId": task.get("id", ""),
        "taskTitle": task.get("title", ""),
        "taskPath": str(task_path),
        "status": "success" if result["code"] == 0 else "failed",
        "startedAt": started_at,
        "finishedAt": finished_at,
        "exitCode": result["code"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "output": result["output"],
        "outputFile": str(markdown_output_path) if markdown_output_path else "",
        "outputFormat": "markdown",
    }

    run_path = RUNS_DIR / f"{run_id}.json"
    save_json(run_path, run_record)

    return run_path


def run_task_json(task_json_path: Path) -> int:
    task_json_path = task_json_path.resolve()

    if not task_json_path.exists():
        print(f"Task JSON not found: {task_json_path}")
        return 1

    task = load_json(task_json_path)

    prompt = str(task.get("prompt", "")).strip()
    if not prompt:
        print("Task prompt is empty")
        return 1

    rendered_prompt = render_prompt(prompt)

    started_at = now_iso()
    result = run_main_with_prompt(rendered_prompt)
    finished_at = now_iso()

    markdown_output_path = None

    if result["code"] == 0 and result["output"].strip():
        markdown_output_path = write_markdown_output(task, result["output"])

    run_path = write_run_record(
        task=task,
        task_path=task_json_path,
        result=result,
        started_at=started_at,
        finished_at=finished_at,
        markdown_output_path=markdown_output_path,
    )

    task["lastRunAt"] = finished_at
    task["lastStatus"] = "success" if result["code"] == 0 else "failed"
    task["lastRunFile"] = str(run_path)
    task["updatedAt"] = finished_at

    if markdown_output_path:
        task["lastOutputFile"] = str(markdown_output_path)
        task["lastOutputPreview"] = result["output"][:500]

    if result["code"] != 0:
        task["lastError"] = result["stderr"] or result["stdout"] or result["output"]

    save_json(task_json_path, task)

    print(f"Task: {task.get('title', task_json_path.name)}")
    print(f"Status: {task['lastStatus']}")
    print(f"Run file: {run_path}")

    if markdown_output_path:
        print(f"Markdown output: {markdown_output_path}")

    if result["stdout"]:
        print(result["stdout"])

    if result["stderr"]:
        print(result["stderr"], file=sys.stderr)

    return result["code"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-json", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_task_json(Path(args.task_json))


if __name__ == "__main__":
    raise SystemExit(main())