from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent
OPENCODE_DIR = PROJECT_ROOT / ".opencode"
AGENTS_DIR = OPENCODE_DIR / "agents"
SKILLS_DIR = OPENCODE_DIR / "skills"
ADOS_MD = OPENCODE_DIR / "ADOS.md"
AGENTS_MD = PROJECT_ROOT / "AGENTS.md"


ADOS_ROLE_NAMES = [
    "ados-planner",
    "ados-explorer",
    "ados-builder",
    "ados-reviewer",
    "ados-verifier",
]

ADOS_STAGE_ORDER = [
    "planner",
    "explorer",
    "builder",
    "verifier",
    "reviewer",
]

ADOS_ROLE_TO_STAGE = {
    "ados-planner": "planner",
    "ados-explorer": "explorer",
    "ados-builder": "builder",
    "ados-verifier": "verifier",
    "ados-reviewer": "reviewer",
}

ADOS_ROLE_TO_WORKFLOW_MODE = {
    "ados-planner": "single_agent_planner",
    "ados-explorer": "single_agent_explorer",
    "ados-builder": "single_agent_builder",
    "ados-verifier": "single_agent_verifier",
    "ados-reviewer": "single_agent_reviewer",
}


def _safe_rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def discover_ados_agents() -> List[Dict[str, Any]]:
    agents: List[Dict[str, Any]] = []

    if not AGENTS_DIR.exists():
        return agents

    for path in sorted(AGENTS_DIR.glob("*.md")):
        agents.append(
            {
                "name": path.stem,
                "path": _safe_rel(path),
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else 0,
            }
        )

    return agents


def discover_skills() -> List[Dict[str, Any]]:
    skills: List[Dict[str, Any]] = []

    if not SKILLS_DIR.exists():
        return skills

    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        skill_dir = path.parent
        skills.append(
            {
                "name": skill_dir.name,
                "path": _safe_rel(path),
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else 0,
            }
        )

    return skills


def infer_ados_template_from_messages(
    messages: Optional[Iterable[Dict[str, Any]]],
    selected_agent_override: Optional[str] = None,
) -> Dict[str, Any]:
    override = str(selected_agent_override or "").strip()
    if override in ADOS_ROLE_NAMES:
        return {
            "selected": override,
            "source": "selected_agent_override",
            "matched": override,
        }

    joined = ""

    try:
        for msg in messages or []:
            content = msg.get("content", "")
            if isinstance(content, str):
                joined += "\n" + content
            elif isinstance(content, list):
                joined += "\n" + " ".join(str(x) for x in content)
            else:
                joined += "\n" + str(content)
    except Exception:
        joined = ""

    lowered = joined.lower()

    for role in ADOS_ROLE_NAMES:
        if role in lowered:
            return {
                "selected": role,
                "source": "message_explicit",
                "matched": role,
            }

    if any(x in lowered for x in ["review", "檢查 diff", "審查", "reviewer"]):
        return {
            "selected": "ados-reviewer",
            "source": "heuristic",
            "matched": "review",
        }

    if any(x in lowered for x in ["test", "lint", "verify", "驗證", "測試"]):
        return {
            "selected": "ados-verifier",
            "source": "heuristic",
            "matched": "verify",
        }

    if any(x in lowered for x in ["read-only", "explore", "inspect", "探索", "查看", "搜尋"]):
        return {
            "selected": "ados-explorer",
            "source": "heuristic",
            "matched": "explore",
        }

    if any(x in lowered for x in ["plan", "規劃", "方案", "步驟"]):
        return {
            "selected": "ados-planner",
            "source": "heuristic",
            "matched": "plan",
        }

    return {
        "selected": "ados-builder",
        "source": "default",
        "matched": "default_builder",
    }


def build_ados_asset_snapshot(
    messages: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    agents = discover_ados_agents()
    skills = discover_skills()
    selected = infer_ados_template_from_messages(messages)

    return {
        "ados": {
            "enabled": ADOS_MD.exists(),
            "ados_md": {
                "path": _safe_rel(ADOS_MD),
                "exists": ADOS_MD.exists(),
                "size_bytes": ADOS_MD.stat().st_size if ADOS_MD.exists() else 0,
            },
            "selected_template": selected,
            "agents": agents,
            "agent_count": len(agents),
        },
        "agents_md": {
            "path": _safe_rel(AGENTS_MD),
            "exists": AGENTS_MD.exists(),
            "size_bytes": AGENTS_MD.stat().st_size if AGENTS_MD.exists() else 0,
        },
        "skills": {
            "items": skills,
            "skill_count": len(skills),
        },
    }


def _append_event_compat(
    append_event: Callable[..., Any],
    *,
    event_type: str,
    title: str,
    preview: str,
    payload: Dict[str, Any],
    source_name: str = "opencode-ados",
    level: str = "info",
    status: str = "success",
) -> None:
    """
    api_server.py 傳進來的是 safe_append_event。

    safe_append_event 的格式是 keyword-only：

        safe_append_event(
            source="...",
            event_type="...",
            title="...",
            preview="...",
            payload={...},
        )

    所以這裡不能使用 append_event("event_type", payload) 這種 positional 格式。
    """

    append_event(
        source=source_name,
        event_type=event_type,
        title=title,
        preview=preview,
        payload=payload,
        level=level,
        status=status,
    )


def build_ados_workflow_info(selected_agent: Optional[str]) -> Dict[str, Any]:
    selected = str(selected_agent or "").strip()
    workflow_mode = ADOS_ROLE_TO_WORKFLOW_MODE.get(selected, "single_agent_unknown")
    active_stage = ADOS_ROLE_TO_STAGE.get(selected, "")
    workflow_id = f"ados-workflow-{active_stage or 'unknown'}"

    return {
        "workflow_id": workflow_id,
        "workflow_mode": workflow_mode,
        "selected_agent": selected,
        "active_stage": active_stage,
        "stages": list(ADOS_STAGE_ORDER),
    }


def emit_ados_workflow_started_events(
    append_event: Callable[..., Any],
    workflow_info: Dict[str, Any],
) -> None:
    workflow_id = workflow_info.get("workflow_id", "")
    workflow_mode = workflow_info.get("workflow_mode", "single_agent_unknown")
    selected_agent = workflow_info.get("selected_agent", "")
    active_stage = workflow_info.get("active_stage", "")
    stages = list(workflow_info.get("stages") or ADOS_STAGE_ORDER)

    _append_event_compat(
        append_event,
        event_type="opencode_ados_workflow_started",
        title="ADOS workflow started",
        preview=f"workflow={workflow_mode} agent={selected_agent or 'unknown'} stage={active_stage or 'unknown'}",
        payload={
            "workflow_id": workflow_id,
            "workflow_mode": workflow_mode,
            "selected_agent": selected_agent,
            "active_stage": active_stage,
            "stages": stages,
        },
    )

    for stage in stages:
        if stage == active_stage:
            continue

        _append_event_compat(
            append_event,
            event_type="opencode_ados_stage_skipped",
            title="ADOS stage skipped",
            preview=f"stage={stage} reason=single_agent_workflow",
            payload={
                "workflow_id": workflow_id,
                "workflow_mode": workflow_mode,
                "selected_agent": selected_agent,
                "stage": stage,
                "reason": "single_agent_workflow",
            },
        )

    _append_event_compat(
        append_event,
        event_type="opencode_ados_stage_started",
        title="ADOS stage started",
        preview=f"stage={active_stage or 'unknown'} agent={selected_agent or 'unknown'}",
        payload={
            "workflow_id": workflow_id,
            "workflow_mode": workflow_mode,
            "selected_agent": selected_agent,
            "stage": active_stage,
        },
    )


def emit_ados_workflow_completed_events(
    append_event: Callable[..., Any],
    workflow_info: Dict[str, Any],
    *,
    status: str,
    duration_ms: Optional[int] = None,
    completed_stages_override: Optional[Iterable[str]] = None,
    failed_stages_override: Optional[Iterable[str]] = None,
    skipped_stages_override: Optional[Iterable[str]] = None,
) -> None:
    workflow_id = workflow_info.get("workflow_id", "")
    workflow_mode = workflow_info.get("workflow_mode", "single_agent_unknown")
    selected_agent = workflow_info.get("selected_agent", "")
    active_stage = workflow_info.get("active_stage", "")
    stages = list(workflow_info.get("stages") or ADOS_STAGE_ORDER)
    skipped_stages = list(skipped_stages_override) if skipped_stages_override is not None else [stage for stage in stages if stage != active_stage]
    completed_stages = list(completed_stages_override) if completed_stages_override is not None else ([active_stage] if active_stage and status == "completed" else [])
    failed_stages = list(failed_stages_override) if failed_stages_override is not None else ([active_stage] if active_stage and status == "error" else [])

    _append_event_compat(
        append_event,
        event_type="opencode_ados_stage_finished",
        title="ADOS stage finished",
        preview=f"stage={active_stage or 'unknown'} status={status}",
        payload={
            "workflow_id": workflow_id,
            "workflow_mode": workflow_mode,
            "selected_agent": selected_agent,
            "stage": active_stage,
            "status": status,
        },
        status="error" if status == "error" else "success",
    )

    _append_event_compat(
        append_event,
        event_type="opencode_ados_workflow_completed",
        title="ADOS workflow completed",
        preview=(
            f"workflow={workflow_mode} status={status} "
            f"completed={','.join(completed_stages) or 'none'} skipped={len(skipped_stages)}"
        ),
        payload={
            "workflow_id": workflow_id,
            "workflow_mode": workflow_mode,
            "selected_agent": selected_agent,
            "active_stage": active_stage,
            "completed_stages": completed_stages,
            "skipped_stages": skipped_stages,
            "failed_stages": failed_stages,
            "status": status,
            "duration_ms": duration_ms,
        },
        status="error" if status == "error" else "success",
    )

def emit_ados_trace_events(
    append_event: Callable[..., Any],
    *,
    messages: Optional[Iterable[Dict[str, Any]]] = None,
    source: str = "opencode_ados_trace",
) -> Dict[str, Any]:
    snapshot = build_ados_asset_snapshot(messages)
    selected_template = snapshot["ados"]["selected_template"]

    ados_enabled = snapshot["ados"]["enabled"]
    agent_count = snapshot["ados"]["agent_count"]
    skill_count = snapshot["skills"]["skill_count"]

    _append_event_compat(
        append_event,
        event_type="opencode_ados_assets_detected",
        title="偵測 ADOS assets",
        preview=(
            f"ados_enabled={ados_enabled} "
            f"agent_count={agent_count} "
            f"skill_count={skill_count}"
        ),
        payload={
            "source": source,
            "ados_enabled": ados_enabled,
            "ados_md": snapshot["ados"]["ados_md"],
            "agent_count": agent_count,
            "skill_count": skill_count,
        },
    )

    selected = selected_template.get("selected")
    selection_source = selected_template.get("source")
    matched = selected_template.get("matched")

    _append_event_compat(
        append_event,
        event_type="opencode_ados_template_selected",
        title="選擇 ADOS template",
        preview=(
            f"selected={selected} "
            f"source={selection_source} "
            f"matched={matched}"
        ),
        payload={
            "source": source,
            "selected": selected,
            "selection_source": selection_source,
            "matched": matched,
        },
    )

    _append_event_compat(
        append_event,
        event_type="opencode_agents_md_detected",
        title="偵測 AGENTS.md / ADOS.md",
        preview=(
            f"agents_md_exists={snapshot['agents_md']['exists']} "
            f"ados_md_exists={snapshot['ados']['ados_md']['exists']} "
            f"agents={agent_count}"
        ),
        payload={
            "source": source,
            "agents_md": snapshot["agents_md"],
            "ados_md": snapshot["ados"]["ados_md"],
            "agents": snapshot["ados"]["agents"],
        },
    )

    for skill in snapshot["skills"]["items"]:
        skill_name = skill.get("name")
        skill_path = skill.get("path")

        _append_event_compat(
            append_event,
            event_type="opencode_skill_discovered",
            title="發現 OpenCode skill",
            preview=f"skill={skill_name} path={skill_path}",
            payload={
                "source": source,
                "skill_name": skill_name,
                "skill_path": skill_path,
                "skill_size_bytes": skill.get("size_bytes"),
            },
        )

    _append_event_compat(
        append_event,
        event_type="opencode_trace_ready",
        title="OpenCode ADOS trace ready",
        preview=(
            f"selected_template={selected} "
            f"agent_count={agent_count} "
            f"skill_count={skill_count}"
        ),
        payload={
            "source": source,
            "message": "OpenCode ADOS trace bootstrap completed.",
            "selected_template": selected,
            "agent_count": agent_count,
            "skill_count": skill_count,
        },
    )

    return snapshot

def load_selected_ados_template(
    messages: Optional[Iterable[Dict[str, Any]]] = None,
    selected_agent_override: Optional[str] = None,
) -> Dict[str, Any]:
    selected = infer_ados_template_from_messages(
        messages,
        selected_agent_override=selected_agent_override,
    )
    role_name = selected.get("selected") or "ados-builder"

    if role_name not in ADOS_ROLE_NAMES:
        role_name = "ados-builder"

    template_path = AGENTS_DIR / f"{role_name}.md"
    exists = template_path.exists()

    content = ""
    error = ""

    if exists:
        try:
            content = template_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            content = ""

    return {
        "selected": role_name,
        "selection_source": selected.get("source"),
        "matched": selected.get("matched"),
        "template_path": _safe_rel(template_path),
        "exists": exists,
        "size_bytes": template_path.stat().st_size if exists else 0,
        "content": content,
        "content_length": len(content),
        "error": error,
    }


def build_ados_template_instruction(
    messages: Optional[Iterable[Dict[str, Any]]] = None,
    selected_agent_override: Optional[str] = None,
) -> Dict[str, Any]:
    loaded = load_selected_ados_template(
        messages,
        selected_agent_override=selected_agent_override,
    )

    role_name = loaded["selected"]
    template_path = loaded["template_path"]
    content = loaded["content"]

    if not content:
        instruction = f"""
[ADOS]
ADOS template selected: {role_name}
Template path: {template_path}
Template content could not be loaded.

Fallback behavior:
- Keep changes small and focused.
- Do not create random markdown files.
- Prefer .opencode/plans/ for plans.
- Report files changed and validation status.
""".strip()
    else:
        instruction = f"""
[ADOS ROLE TEMPLATE ACTIVE]

Selected ADOS role: {role_name}
Template path: {template_path}

The following ADOS role template is active for this OpenCode run.
Follow it as higher-priority project behavior for this request.

--- BEGIN ADOS TEMPLATE: {role_name} ---

{content}

--- END ADOS TEMPLATE: {role_name} ---
""".strip()

    return {
        "selected": role_name,
        "template_path": template_path,
        "exists": loaded["exists"],
        "size_bytes": loaded["size_bytes"],
        "content_length": loaded["content_length"],
        "selection_source": loaded["selection_source"],
        "matched": loaded["matched"],
        "instruction": instruction,
        "instruction_length": len(instruction),
        "error": loaded["error"],
    }


def emit_ados_template_loaded_event(
    append_event: Callable[..., Any],
    loaded: Dict[str, Any],
) -> None:
    append_event(
        source="opencode-ados",
        event_type="opencode_ados_template_loaded",
        title="載入 ADOS template",
        preview=(
            f"selected={loaded.get('selected')} "
            f"path={loaded.get('template_path')} "
            f"exists={loaded.get('exists')} "
            f"content_length={loaded.get('content_length')}"
        ),
        payload={
            "selected": loaded.get("selected"),
            "template_path": loaded.get("template_path"),
            "exists": loaded.get("exists"),
            "size_bytes": loaded.get("size_bytes"),
            "content_length": loaded.get("content_length"),
            "selection_source": loaded.get("selection_source"),
            "matched": loaded.get("matched"),
            "error": loaded.get("error"),
        },
        level="info",
        status="success" if loaded.get("exists") else "warning",
    )


def emit_ados_template_injected_event(
    append_event: Callable[..., Any],
    injected: Dict[str, Any],
) -> None:
    append_event(
        source="opencode-ados",
        event_type="opencode_ados_template_injected",
        title="注入 ADOS template 到 prompt",
        preview=(
            f"selected={injected.get('selected')} "
            f"instruction_length={injected.get('instruction_length')}"
        ),
        payload={
            "selected": injected.get("selected"),
            "template_path": injected.get("template_path"),
            "exists": injected.get("exists"),
            "instruction_length": injected.get("instruction_length"),
            "content_length": injected.get("content_length"),
            "selection_source": injected.get("selection_source"),
            "matched": injected.get("matched"),
        },
        level="info",
        status="success",
    )

def infer_skill_names_from_messages(
    messages: Optional[Iterable[Dict[str, Any]]],
    available_skill_names: list[str],
    max_skills: int = 2,
) -> list[str]:
    joined = ""

    try:
        for msg in messages or []:
            content = msg.get("content", "")
            if isinstance(content, str):
                joined += "\n" + content
            elif isinstance(content, list):
                joined += "\n" + " ".join(str(x) for x in content)
            else:
                joined += "\n" + str(content)
    except Exception:
        joined = ""

    lowered = joined.lower()
    available = set(available_skill_names)
    selected: list[str] = []

    def add(name: str) -> None:
        if name in available and name not in selected:
            selected.append(name)

    # 1. Explicit skill name mention.
    for name in available_skill_names:
        if name.lower() in lowered:
            add(name)

    # 2. Conservative heuristics.
    if any(
        x in lowered
        for x in [
            "api_server",
            "fastapi",
            "/v1/",
            "endpoint",
            "runtime_event",
            "mcp loop",
            "debug api",
            "api debug",
            "後端",
            "事件",
            "runtime",
            "log",
        ]
    ):
        add("api-debug")

    if any(
        x in lowered
        for x in [
            "ui",
            "frontend",
            "app-agent-console",
            "timeline",
            "inspector",
            "vite",
            "css",
            "畫面",
            "前端",
            "介面",
        ]
    ):
        add("ui-change")

    if any(
        x in lowered
        for x in [
            "test",
            "lint",
            "typecheck",
            "type check",
            "verify",
            "validation",
            "測試",
            "驗證",
            "檢查",
        ]
    ):
        add("testing")

    if any(
        x in lowered
        for x in [
            "git",
            "diff",
            "commit",
            "status",
            "branch",
            "merge",
            "rebase",
            "版本",
            "提交",
        ]
    ):
        add("git-workflow")

    return selected[:max_skills]


def _read_skill_text(path: Path) -> tuple[str, str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace"), ""
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"


def build_selected_skill_instructions(
    messages: Optional[Iterable[Dict[str, Any]]] = None,
    max_skills: int = 2,
) -> Dict[str, Any]:
    discovered = discover_skills()
    available_names = [str(x.get("name")) for x in discovered if x.get("name")]
    selected_names = infer_skill_names_from_messages(
        messages,
        available_names,
        max_skills=max_skills,
    )

    skills_by_name = {str(x.get("name")): x for x in discovered if x.get("name")}

    loaded_skills: list[dict[str, Any]] = []
    instruction_parts: list[str] = []

    for skill_name in selected_names:
        skill = skills_by_name.get(skill_name)
        if not skill:
            continue

        raw_path = skill.get("path")
        skill_path = PROJECT_ROOT / str(raw_path).replace("/", "\\")

        content, error = _read_skill_text(skill_path)

        loaded = {
            "name": skill_name,
            "path": skill.get("path"),
            "exists": bool(skill_path.exists()),
            "size_bytes": skill_path.stat().st_size if skill_path.exists() else 0,
            "content_length": len(content),
            "error": error,
        }
        loaded_skills.append(loaded)

        if content:
            instruction_parts.append(
                f"""
[OPENCODE SKILL ACTIVE]

Skill name: {skill_name}
Skill path: {skill.get("path")}

The following SKILL.md is active for this OpenCode run.
Use it only for the relevant task area.

--- BEGIN SKILL: {skill_name} ---

{content}

--- END SKILL: {skill_name} ---
""".strip()
            )

    instruction = "\n\n---\n\n".join(instruction_parts).strip()

    return {
        "available_skill_count": len(available_names),
        "available_skill_names": available_names,
        "selected_skill_count": len(selected_names),
        "selected_skill_names": selected_names,
        "loaded_skill_count": len(loaded_skills),
        "loaded_skills": loaded_skills,
        "instruction": instruction,
        "instruction_length": len(instruction),
        "max_skills": max_skills,
    }


def emit_opencode_skill_trace_events(
    append_event: Callable[..., Any],
    skill_info: Dict[str, Any],
) -> None:
    selected_names = skill_info.get("selected_skill_names") or []
    loaded_skills = skill_info.get("loaded_skills") or []
    instruction_length = int(skill_info.get("instruction_length") or 0)

    _append_event_compat(
        append_event,
        event_type="opencode_skill_selection_completed",
        title="OpenCode skill selection completed",
        preview=(
            f"selected_skills={len(selected_names)} "
            f"available_skills={skill_info.get('available_skill_count')}"
        ),
        payload={
            "available_skill_count": skill_info.get("available_skill_count"),
            "available_skill_names": skill_info.get("available_skill_names"),
            "selected_skill_count": len(selected_names),
            "selected_skill_names": selected_names,
            "max_skills": skill_info.get("max_skills"),
        },
    )

    for skill in loaded_skills:
        _append_event_compat(
            append_event,
            event_type="opencode_skill_loaded",
            title="載入 OpenCode skill",
            preview=(
                f"skill={skill.get('name')} "
                f"path={skill.get('path')} "
                f"exists={skill.get('exists')} "
                f"content_length={skill.get('content_length')}"
            ),
            payload=skill,
            status="success" if skill.get("exists") and not skill.get("error") else "warning",
        )

    if instruction_length > 0:
        _append_event_compat(
            append_event,
            event_type="opencode_skill_injected",
            title="注入 OpenCode skill 到 prompt",
            preview=(
                f"skills={','.join(selected_names)} "
                f"instruction_length={instruction_length}"
            ),
            payload={
                "selected_skill_names": selected_names,
                "loaded_skill_count": len(loaded_skills),
                "instruction_length": instruction_length,
            },
        )
    else:
        _append_event_compat(
            append_event,
            event_type="opencode_skill_injection_skipped",
            title="略過 OpenCode skill 注入",
            preview="no selected skill",
            payload={
                "reason": "no selected skill",
                "available_skill_names": skill_info.get("available_skill_names"),
            },
            status="success",
        )
        
