from pathlib import Path
from datetime import datetime
import logging
import subprocess
import time
import sys
import os
import socket
import shutil
import yaml
import argparse
from dataclasses import dataclass

from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.yaml"
LOCK_FILE = BASE_DIR / ".runner.lock"


@dataclass
class TaskSpec:
    name: str
    content: str
    source_path: Path | None = None
    archive_on_success: bool = False
    move_to_failed_on_error: bool = False


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_FILE}")

    with CONFIG_FILE.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    if not isinstance(config, dict):
        raise ValueError("config.yaml must contain an object")

    return config


CONFIG = load_config()

LOG_DIR = BASE_DIR / CONFIG.get("logs_dir", "logs")
SCREENSHOT_DIR = BASE_DIR / CONFIG.get("screenshots_dir", "screenshots")
OUTPUTS_DIR = BASE_DIR / CONFIG.get("outputs_dir", "outputs")
TASKS_DIR = BASE_DIR / CONFIG.get("tasks_dir", "tasks")
ARCHIVE_DIR = BASE_DIR / CONFIG.get("archive_dir", "archive")
FAILED_DIR = BASE_DIR / CONFIG.get("failed_dir", "failed")
SCHEDULED_TEMPLATE_DIR = BASE_DIR / "scheduled_templates"

for directory in [
    LOG_DIR,
    SCREENSHOT_DIR,
    OUTPUTS_DIR,
    TASKS_DIR,
    ARCHIVE_DIR,
    FAILED_DIR,
    SCHEDULED_TEMPLATE_DIR,
]:
    directory.mkdir(exist_ok=True)


logging.basicConfig(
    filename=LOG_DIR / "runner.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_name_text(text: str) -> str:
    cleaned = (
        text.replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("*", "_")
        .replace("?", "_")
        .replace('"', "_")
        .replace("<", "_")
        .replace(">", "_")
        .replace("|", "_")
    )
    return cleaned[:80] or "task"


def safe_name(path: Path) -> str:
    return safe_name_text(path.stem)


def acquire_lock() -> None:
    if LOCK_FILE.exists():
        raise RuntimeError(
            f"Runner lock already exists: {LOCK_FILE}. Another run may still be active. "
            "If the previous run has already finished, delete .runner.lock manually."
        )

    LOCK_FILE.write_text(
        f"pid={os.getpid()}\nstarted_at={datetime.now().isoformat()}",
        encoding="utf-8",
    )


def release_lock() -> None:
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except Exception:
        logging.exception("Failed to delete lock file")


def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def get_cdp_port(config: dict) -> int:
    cdp_url = str(config.get("cdp_url", "http://127.0.0.1:9222"))

    try:
        return int(cdp_url.rsplit(":", 1)[1].rstrip("/"))
    except Exception:
        return 9222


def find_pids_listening_on_port(port: int) -> set[int]:
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception:
        logging.exception("Failed to inspect CDP port listeners")
        return set()

    pids: set[int] = set()

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue

        proto, local_address, _, state, pid_text = parts[:5]
        if proto.upper() != "TCP" or state.upper() != "LISTENING":
            continue

        if not local_address.endswith(f":{port}"):
            continue

        try:
            pids.add(int(pid_text))
        except Exception:
            continue

    return pids


def find_edge_pids_for_profile(profile_dir: str) -> set[int]:
    escaped_profile = str(profile_dir).replace("'", "''")
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'msedge.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{escaped_profile}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception:
        logging.exception("Failed to inspect Edge profile PIDs")
        return set()

    if result.returncode != 0:
        logging.warning("Failed to inspect Edge profile PIDs: %s", (result.stderr or result.stdout or "").strip())
        return set()

    pids: set[int] = set()

    for line in result.stdout.splitlines():
        text = line.strip()
        if not text:
            continue

        try:
            pids.add(int(text))
        except Exception:
            continue

    return pids


def stop_process_tree(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception:
        logging.exception("Failed to stop PID %s", pid)
        return False

    if result.returncode == 0:
        logging.info("Stopped stale Edge debug PID=%s", pid)
        return True

    logging.warning(
        "Failed to stop PID %s: %s",
        pid,
        (result.stderr or result.stdout or f"code={result.returncode}").strip(),
    )
    return False


def stop_stale_edge_debug_session(config: dict) -> None:
    cdp_port = get_cdp_port(config)
    profile_dir = str(config.get("edge_profile_dir", str(BASE_DIR / "edge_debug_profile")))

    logging.info("restart_edge_debug_session enabled")
    logging.info("checking existing CDP port %s", cdp_port)

    port_pids = find_pids_listening_on_port(cdp_port)
    profile_pids = find_edge_pids_for_profile(profile_dir)
    candidate_pids = port_pids | profile_pids

    if not candidate_pids:
        logging.info("No stale Edge debug session found")
        return

    logging.info(
        "stopping stale Edge debug session: port_pids=%s profile_pids=%s",
        sorted(port_pids),
        sorted(profile_pids),
    )

    for pid in sorted(candidate_pids):
        stop_process_tree(pid)

    for _ in range(20):
        if not is_port_open("127.0.0.1", cdp_port):
            logging.info("CDP port %s is now closed", cdp_port)
            return
        time.sleep(0.5)

    logging.warning("CDP port %s is still open after stale session cleanup", cdp_port)


def start_edge_if_needed(config: dict) -> None:
    if not bool(config.get("auto_start_edge", True)):
        logging.info("auto_start_edge=false, skipping automatic Edge launch")
        return

    cdp_port = get_cdp_port(config)
    restart_debug_session = bool(config.get("restart_edge_debug_session", True))

    if restart_debug_session:
        stop_stale_edge_debug_session(config)
    elif is_port_open("127.0.0.1", cdp_port):
        logging.info("CDP port %s already has an active Edge debug session", cdp_port)
        return

    edge_path = config.get(
        "edge_path",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    )
    profile_dir = config.get("edge_profile_dir", str(BASE_DIR / "edge_debug_profile"))
    chatgpt_url = config.get("chatgpt_url", "https://chatgpt.com/")

    if not Path(edge_path).exists():
        raise FileNotFoundError(f"Edge executable not found: {edge_path}")

    args = [
        edge_path,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        f"--window-size={config.get('browser_width', 1280)},{config.get('browser_height', 900)}",
        chatgpt_url,
    ]

    logging.info("starting fresh Edge debug session: %s", args)
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(30):
        if is_port_open("127.0.0.1", cdp_port):
            logging.info("Edge debug port %s is ready", cdp_port)
            time.sleep(3)
            return
        time.sleep(1)

    raise RuntimeError(f"Failed to start Edge: CDP port {cdp_port} did not open")


def save_screenshot(page, prefix: str = "debug") -> None:
    screenshot_path = SCREENSHOT_DIR / f"{prefix}_{now_stamp()}.png"

    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
        logging.info("Saved screenshot: %s", screenshot_path)
    except Exception:
        logging.exception("Failed to save screenshot")


def write_latest_output(config: dict, content: str) -> None:
    output_path = BASE_DIR / config.get("output_file", "output.txt")
    output_path.write_text(content, encoding="utf-8")


def write_task_output(task_name: str, content: str) -> Path:
    output_name = f"{safe_name_text(task_name)}_{now_stamp()}.txt"
    output_path = OUTPUTS_DIR / output_name
    output_path.write_text(content, encoding="utf-8")
    return output_path


def write_error_output(message: str) -> None:
    output_path = BASE_DIR / "output.txt"
    output_path.write_text(message, encoding="utf-8")

    error_path = OUTPUTS_DIR / f"error_{now_stamp()}.txt"
    error_path.write_text(message, encoding="utf-8")


def move_with_timestamp(src: Path, dst_dir: Path, suffix: str = "") -> Path:
    dst_dir.mkdir(exist_ok=True)
    dst_name = f"{src.stem}_{now_stamp()}{suffix}{src.suffix}"
    dst_path = dst_dir / dst_name
    shutil.move(str(src), str(dst_path))
    return dst_path


def read_file_content(path: Path) -> str:
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"{path.name} ?批捆?箇征")
    return content


def build_tasks_from_args(config: dict, args: argparse.Namespace) -> list[TaskSpec]:
    if args.prompt_text:
        return [
            TaskSpec(
                name=f"ui_prompt_{now_stamp()}",
                content=args.prompt_text.strip(),
            )
        ]

    if args.prompt_file:
        path = Path(args.prompt_file)
        if not path.is_absolute():
            path = BASE_DIR / path
        return [
            TaskSpec(
                name=path.stem,
                content=read_file_content(path),
                source_path=path,
                archive_on_success=False,
                move_to_failed_on_error=False,
            )
        ]

    if args.template:
        path = Path(args.template)
        if not path.is_absolute():
            path = BASE_DIR / path
        return [
            TaskSpec(
                name=path.stem,
                content=read_file_content(path),
                source_path=path,
                archive_on_success=False,
                move_to_failed_on_error=False,
            )
        ]

    batch_mode = bool(config.get("batch_mode", True))
    if batch_mode or args.batch:
        pattern = config.get("task_file_pattern", "*.txt")
        files = sorted(TASKS_DIR.glob(pattern))
        max_tasks = int(config.get("max_tasks_per_run", 10))

        tasks: list[TaskSpec] = []
        for path in files[:max_tasks]:
            tasks.append(
                TaskSpec(
                    name=path.stem,
                    content=read_file_content(path),
                    source_path=path,
                    archive_on_success=bool(config.get("archive_processed_tasks", True)),
                    move_to_failed_on_error=True,
                )
            )
        return tasks

    input_path = BASE_DIR / config.get("input_file", "input.txt")
    if input_path.exists():
        return [
            TaskSpec(
                name=input_path.stem,
                content=read_file_content(input_path),
                source_path=input_path,
            )
        ]

    return []


def get_chatgpt_page(browser, config: dict):
    chatgpt_url = config.get("chatgpt_url", "https://chatgpt.com/")

    for context in browser.contexts:
        for page in context.pages:
            try:
                if "chatgpt.com" in page.url:
                    logging.info("Found existing ChatGPT page: %s", page.url)
                    return page
            except Exception:
                pass

    context = browser.contexts[0]
    page = context.new_page()

    try:
        page.goto(chatgpt_url, wait_until="commit", timeout=30000)
        logging.info("Opened a new ChatGPT page")
    except Exception as error:
        logging.warning("Failed to open ChatGPT directly, using current page: %s", error)

    return page


def find_composer_once(page, config: dict, timeout_ms: int = 5000):
    try:
        textbox = page.get_by_role("textbox").last
        textbox.wait_for(state="visible", timeout=timeout_ms)
        return textbox
    except Exception:
        pass

    selectors = config.get("selectors", {}).get("composer_candidates", [])

    for selector in selectors:
        try:
            locator = page.locator(selector).last
            locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except Exception:
            pass

    return None


def wait_for_page_ready(page, config: dict):
    timeout_seconds = int(config.get("page_ready_timeout_seconds", 90))
    auto_refresh = bool(config.get("auto_refresh_if_not_ready", True))
    max_refresh_attempts = int(config.get("max_refresh_attempts", 2))

    logging.info("Waiting for ChatGPT page to become ready, timeout=%s seconds", timeout_seconds)

    start = time.time()
    refresh_count = 0

    while time.time() - start < timeout_seconds:
        composer = find_composer_once(page, config, timeout_ms=3000)

        if composer:
            logging.info("ChatGPT page is ready")
            return composer

        elapsed = time.time() - start
        logging.info("Page still not ready, elapsed=%.1f", elapsed)

        if auto_refresh and refresh_count < max_refresh_attempts:
            if elapsed > 15 * (refresh_count + 1):
                refresh_count += 1
                logging.info("Refreshing page, attempt %s", refresh_count)
                try:
                    page.reload(wait_until="commit", timeout=30000)
                except Exception as error:
                    logging.warning("Reload failed: %s", error)

        time.sleep(3)

    save_screenshot(page, prefix="page_not_ready")
    raise RuntimeError("Timed out waiting for ChatGPT page to become ready")


def click_new_chat_if_needed(page, config: dict) -> None:
    if not bool(config.get("new_chat_per_task", False)):
        logging.info("new_chat_per_task=false, reusing the current conversation")
        return

    logging.info("Preparing a new chat")
    chatgpt_url = config.get("chatgpt_url", "https://chatgpt.com/")

    try:
        page.goto(chatgpt_url, wait_until="commit", timeout=30000)
        time.sleep(3)
        logging.info("Opened a new chat by navigating to ChatGPT")
        return
    except Exception as error:
        logging.warning("Direct navigation to a new chat failed: %s", error)

    selectors = config.get("selectors", {}).get("new_chat_candidates", [])

    for selector in selectors:
        try:
            button = page.locator(selector).first
            button.wait_for(state="visible", timeout=5000)
            button.click()
            time.sleep(3)
            logging.info("Clicked new chat selector: %s", selector)
            return
        except Exception as error:
            logging.info("New chat selector failed %s: %s", selector, error)

    logging.warning("No working new chat entry point found; continuing with the current chat")


def clear_and_fill_composer(page, composer, content: str) -> None:
    logging.info("皞?憛怠 prompt")

    composer.click()
    time.sleep(0.5)

    page.keyboard.press("Control+A")
    time.sleep(0.2)
    page.keyboard.press("Backspace")
    time.sleep(0.2)

    try:
        page.keyboard.insert_text(content)
        logging.info("Filled prompt using keyboard.insert_text()")
    except Exception as error:
        logging.warning("keyboard.insert_text() failed, falling back to clipboard paste: %s", error)

        page.evaluate(
            """async text => {
                await navigator.clipboard.writeText(text);
            }""",
            content,
        )
        page.keyboard.press("Control+V")
        logging.info("Filled prompt using clipboard paste fallback")

    time.sleep(1)


def click_send_button_if_available(page, config: dict) -> bool:
    selectors = config.get("selectors", {}).get("send_button_candidates", [])

    for selector in selectors:
        try:
            button = page.locator(selector).last
            button.wait_for(state="visible", timeout=5000)

            if button.is_enabled():
                button.click()
                logging.info("Clicked send button selector: %s", selector)
                return True

        except Exception as error:
            logging.info("Send button selector failed %s: %s", selector, error)

    return False


def submit_prompt(page, config: dict, composer, content: str, task_name: str) -> None:
    clear_and_fill_composer(page, composer, content)

    save_screenshot(page, prefix=f"after_input_{safe_name_text(task_name)}")

    clicked = click_send_button_if_available(page, config)

    if not clicked:
        logging.info("No send button worked; pressing Enter instead")
        page.keyboard.press("Enter")

    logging.info("Submitted prompt")
    time.sleep(3)
    save_screenshot(page, prefix=f"after_submit_{safe_name_text(task_name)}")


def is_generating(page, config: dict) -> bool:
    selectors = config.get("selectors", {}).get("stop_button_candidates", [])

    for selector in selectors:
        try:
            button = page.locator(selector).last
            if button.is_visible(timeout=1000):
                return True
        except Exception:
            pass

    return False

def get_last_assistant_message(page, config: dict) -> str:
    selector = config.get("selectors", {}).get(
        "assistant_message",
        '[data-message-author-role="assistant"]',
    )

    js = """
    (element) => {
      function clean(text) {
        return (text || '').replace(/\\n{3,}/g, '\\n\\n').trim();
      }

      function escapeMarkdown(text) {
        return text || '';
      }

      function nodeToMarkdown(node, depth = 0) {
        if (!node) return '';

        if (node.nodeType === Node.TEXT_NODE) {
          return node.textContent || '';
        }

        if (node.nodeType !== Node.ELEMENT_NODE) {
          return '';
        }

        const tag = node.tagName.toLowerCase();

        if (tag === 'button' || tag === 'svg' || tag === 'path') {
          return '';
        }

        if (tag === 'pre') {
          const code = node.innerText || '';
          const codeElement = node.querySelector('code');
          let lang = '';

          if (codeElement) {
            const cls = codeElement.getAttribute('class') || '';
            const match = cls.match(/language-([a-zA-Z0-9_-]+)/);
            if (match) lang = match[1];
          }

          return `\\n\\n\\`\\`\\`${lang}\\n${code.replace(/\\n+$/, '')}\\n\\`\\`\\`\\n\\n`;
        }

        if (tag === 'code') {
          const parent = node.parentElement;
          if (parent && parent.tagName.toLowerCase() === 'pre') {
            return node.innerText || '';
          }
          return '`' + (node.innerText || '') + '`';
        }

        if (tag === 'br') {
          return '\\n';
        }

        if (tag === 'p') {
          return '\\n\\n' + childrenToMarkdown(node, depth).trim() + '\\n\\n';
        }

        if (tag === 'strong' || tag === 'b') {
          return '**' + childrenToMarkdown(node, depth).trim() + '**';
        }

        if (tag === 'em' || tag === 'i') {
          return '*' + childrenToMarkdown(node, depth).trim() + '*';
        }

        if (tag === 'a') {
          const text = childrenToMarkdown(node, depth).trim() || node.innerText || '';
          const href = node.getAttribute('href');
          if (href) return `[${text}](${href})`;
          return text;
        }

        if (/^h[1-6]$/.test(tag)) {
          const level = Number(tag.slice(1));
          return '\\n\\n' + '#'.repeat(level) + ' ' + childrenToMarkdown(node, depth).trim() + '\\n\\n';
        }

        if (tag === 'ul') {
          let out = '\\n';
          Array.from(node.children).forEach((child) => {
            if (child.tagName && child.tagName.toLowerCase() === 'li') {
              out += `${'  '.repeat(depth)}- ${childrenToMarkdown(child, depth + 1).trim()}\\n`;
            }
          });
          return out + '\\n';
        }

        if (tag === 'ol') {
          let out = '\\n';
          let index = 1;
          Array.from(node.children).forEach((child) => {
            if (child.tagName && child.tagName.toLowerCase() === 'li') {
              out += `${'  '.repeat(depth)}${index}. ${childrenToMarkdown(child, depth + 1).trim()}\\n`;
              index += 1;
            }
          });
          return out + '\\n';
        }

        if (tag === 'li') {
          return childrenToMarkdown(node, depth).trim();
        }

        if (tag === 'blockquote') {
          const text = childrenToMarkdown(node, depth).trim();
          return '\\n\\n' + text.split('\\n').map(line => `> ${line}`).join('\\n') + '\\n\\n';
        }

        if (tag === 'table') {
          const rows = Array.from(node.querySelectorAll('tr')).map((tr) => {
            return Array.from(tr.children).map((cell) => clean(cell.innerText).replace(/\\|/g, '\\\\|'));
          });

          if (!rows.length) return '';

          let out = '\\n\\n';
          out += '| ' + rows[0].join(' | ') + ' |\\n';
          out += '| ' + rows[0].map(() => '---').join(' | ') + ' |\\n';

          rows.slice(1).forEach((row) => {
            out += '| ' + row.join(' | ') + ' |\\n';
          });

          return out + '\\n';
        }

        if (tag === 'hr') {
          return '\\n\\n---\\n\\n';
        }

        return childrenToMarkdown(node, depth);
      }

      function childrenToMarkdown(element, depth = 0) {
        return Array.from(element.childNodes).map(child => nodeToMarkdown(child, depth)).join('');
      }

      return clean(nodeToMarkdown(element));
    }
    """

    try:
        messages = page.locator(selector)
        count = messages.count()

        if count == 0:
            return ""

        last_message = messages.nth(count - 1)
        markdown = last_message.evaluate(js)

        if markdown:
            return markdown.strip()

        return last_message.inner_text(timeout=5000).strip()

    except Exception as error:
        logging.warning("Failed to extract assistant markdown: %s", error)
        return ""
    
def wait_for_answer(page, config: dict) -> str:
    timeout_seconds = int(config.get("timeout_seconds", 300))
    interval = int(config.get("stable_check_interval_seconds", 3))
    required_stable_rounds = int(config.get("stable_rounds_required", 4))

    start_time = time.time()
    last_text = ""
    stable_rounds = 0

    logging.info("Waiting for the answer to stabilize, timeout=%s seconds", timeout_seconds)

    while time.time() - start_time < timeout_seconds:
        current_text = get_last_assistant_message(page, config)
        generating = is_generating(page, config)

        if current_text:
            if current_text == last_text:
                stable_rounds += 1
            else:
                stable_rounds = 0
                last_text = current_text

            logging.info(
                "answer_length=%s stable_rounds=%s/%s generating=%s",
                len(current_text),
                stable_rounds,
                required_stable_rounds,
                generating,
            )

            if stable_rounds >= required_stable_rounds and not generating:
                logging.info("Answer is stable and generation appears complete")
                return current_text

        time.sleep(interval)

    logging.warning("Timed out waiting for a stable answer; returning the latest content")
    return last_text


def process_one_task(page, config: dict, task: TaskSpec) -> dict:
    logging.info("Processing task: %s", task.name)

    click_new_chat_if_needed(page, config)

    composer = wait_for_page_ready(page, config)

    submit_prompt(page, config, composer, task.content, task.name)

    answer = wait_for_answer(page, config)

    if not answer:
        save_screenshot(page, prefix=f"no_answer_{safe_name_text(task.name)}")
        raise RuntimeError(f"{task.name} did not produce any answer")

    output_path = write_task_output(task.name, answer)
    write_latest_output(config, answer)

    archive_path = None
    if task.archive_on_success and task.source_path and task.source_path.exists():
        archive_path = move_with_timestamp(task.source_path, ARCHIVE_DIR)

    logging.info("Task completed: %s -> %s", task.name, output_path.name)

    return {
        "task": task.name,
        "status": "success",
        "output": str(output_path),
        "archive": str(archive_path) if archive_path else "",
        "error": "",
    }


def process_task_with_retry(page, config: dict, task: TaskSpec) -> dict:
    retries = int(config.get("retry_per_task", 1))
    delay = int(config.get("retry_delay_seconds", 5))

    last_error = None

    for attempt in range(retries + 1):
        try:
            logging.info("Task %s attempt %s", task.name, attempt + 1)
            return process_one_task(page, config, task)
        except Exception as error:
            last_error = error
            logging.exception("Task failed: %s attempt %s", task.name, attempt + 1)
            save_screenshot(page, prefix=f"failed_{safe_name_text(task.name)}_attempt_{attempt + 1}")

            if attempt < retries:
                time.sleep(delay)

    failed_path = None
    if task.move_to_failed_on_error and task.source_path and task.source_path.exists():
        failed_path = move_with_timestamp(task.source_path, FAILED_DIR, suffix="_failed")

    return {
        "task": task.name,
        "status": "failed",
        "output": "",
        "archive": "",
        "failed_path": str(failed_path) if failed_path else "",
        "error": f"{type(last_error).__name__}: {last_error}",
    }


def write_summary(results: list[dict]) -> Path:
    stamp = now_stamp()
    summary_path = OUTPUTS_DIR / f"summary_{stamp}.txt"

    success_count = sum(1 for item in results if item["status"] == "success")
    failed_count = sum(1 for item in results if item["status"] == "failed")

    lines = [
        f"Run summary: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Success: {success_count}",
        f"Failed: {failed_count}",
        "",
    ]

    for item in results:
        lines.append(f"[{item['status'].upper()}] {item['task']}")
        if item.get("output"):
            lines.append(f"  output: {item['output']}")
        if item.get("archive"):
            lines.append(f"  archive: {item['archive']}")
        if item.get("failed_path"):
            lines.append(f"  failed_path: {item['failed_path']}")
        if item.get("error"):
            lines.append(f"  error: {item['error']}")
        lines.append("")

    summary_path.write_text("\n".join(lines), encoding="utf-8")
    return summary_path


def run(args: argparse.Namespace) -> int:
    config = load_config()
    tasks = build_tasks_from_args(config, args)

    if not tasks:
        message = "No runnable tasks found. Provide prompt text, prompt_file, or place .txt tasks in tasks/."
        logging.warning(message)
        write_error_output(message)
        print(message)
        return 0

    start_edge_if_needed(config)

    results = []

    with sync_playwright() as p:
        cdp_url = config.get("cdp_url", "http://127.0.0.1:9222")
        logging.info("connecting over CDP: %s", cdp_url)

        browser = p.chromium.connect_over_cdp(cdp_url)

        try:
            page = get_chatgpt_page(browser, config)

            for task in tasks:
                result = process_task_with_retry(page, config, task)
                results.append(result)

            summary_path = write_summary(results)

            success_count = sum(1 for item in results if item["status"] == "success")
            failed_count = sum(1 for item in results if item["status"] == "failed")

            print(f"Completed: success {success_count}, failed {failed_count}")
            print(f"summary: {summary_path}")

            logging.info("Completed: success %s, failed %s", success_count, failed_count)

            return 1 if failed_count else 0

        finally:
            try:
                browser.close()
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-text", type=str, default="")
    parser.add_argument("--prompt-file", type=str, default="")
    parser.add_argument("--template", type=str, default="")
    parser.add_argument("--batch", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        acquire_lock()
        return run(args)

    except Exception as error:
        logging.exception("Run failed")
        message = f"ERROR: {type(error).__name__}: {error}"
        write_error_output(message)
        print(message)
        return 1

    finally:
        release_lock()


from pathlib import Path

def get_prompt_from_args(args) -> str:
    if getattr(args, "prompt_file", ""):
        return Path(args.prompt_file).read_text(encoding="utf-8", errors="replace").strip()

    if getattr(args, "prompt_text", ""):
        return args.prompt_text.strip()

    input_path = Path("input.txt")
    if input_path.exists():
        return input_path.read_text(encoding="utf-8", errors="replace").strip()

    return ""

if __name__ == "__main__":
    sys.exit(main())
