from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from skill_debugger.skill_registry import slugify


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkspaceStore:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def ensure_default_workspace(self) -> dict:
        workspaces = self.list_workspaces()
        if workspaces:
            return workspaces[0]
        return self.create_workspace("product-skill-lab")

    def list_workspaces(self) -> list[dict]:
        items: list[dict] = []
        for path in sorted(self.base_dir.glob("*/workspace.json")):
            items.append(json.loads(path.read_text(encoding="utf-8")))
        return items

    def create_workspace(self, name: str | None = None) -> dict:
        with self._lock:
            requested = slugify(name or f"workspace-{uuid4().hex[:8]}")
            workspace_id = requested
            suffix = 2
            while self.workspace_dir(workspace_id).exists():
                workspace_id = f"{requested}-{suffix}"
                suffix += 1
            now = utcnow_iso()
            workspace = {
                "workspace_id": workspace_id,
                "name": (name or workspace_id).strip(),
                "created_at": now,
                "updated_at": now,
            }
            self.workspace_dir(workspace_id).mkdir(parents=True, exist_ok=True)
            self.claude_dir(workspace_id).mkdir(parents=True, exist_ok=True)
            self.skills_dir(workspace_id).mkdir(parents=True, exist_ok=True)
            self.runtime_projects_dir(workspace_id).mkdir(parents=True, exist_ok=True)
            self._write_json(self.workspace_dir(workspace_id) / "workspace.json", workspace)
            self._write_json(self.workspace_dir(workspace_id) / "session.json", self._empty_session())
            self._write_json(self.tools_path(workspace_id), self._empty_tools())
            return workspace

    def delete_workspace(self, workspace_id: str) -> None:
        with self._lock:
            target = self.workspace_dir(workspace_id)
            if not target.exists():
                raise KeyError(f"Workspace not found: {workspace_id}")
            shutil.rmtree(target)

    def workspace_dir(self, workspace_id: str) -> Path:
        return self.base_dir / workspace_id

    def claude_dir(self, workspace_id: str) -> Path:
        return self.workspace_dir(workspace_id) / ".claude"

    def skills_dir(self, workspace_id: str) -> Path:
        return self.claude_dir(workspace_id) / "skills"

    def legacy_skills_dir(self, workspace_id: str) -> Path:
        return self.workspace_dir(workspace_id) / "skills"

    def runtime_projects_dir(self, workspace_id: str) -> Path:
        return self.workspace_dir(workspace_id) / ".runtime_projects"

    def runtime_project_dir(self, workspace_id: str, runtime_key: str) -> Path:
        return self.runtime_projects_dir(workspace_id) / runtime_key

    def tools_path(self, workspace_id: str) -> Path:
        return self.workspace_dir(workspace_id) / "tools.json"

    def get_workspace(self, workspace_id: str) -> dict:
        path = self.workspace_dir(workspace_id) / "workspace.json"
        if not path.exists():
            raise KeyError(f"Workspace not found: {workspace_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def touch_workspace(self, workspace_id: str) -> dict:
        with self._lock:
            workspace = self.get_workspace(workspace_id)
            workspace["updated_at"] = utcnow_iso()
            self._write_json(self.workspace_dir(workspace_id) / "workspace.json", workspace)
            return workspace

    def get_session(self, workspace_id: str) -> dict:
        self.get_workspace(workspace_id)
        self.ensure_native_skill_layout(workspace_id)
        path = self.workspace_dir(workspace_id) / "session.json"
        if not path.exists():
            session = self._empty_session()
            self._write_json(path, session)
            return session
        return json.loads(path.read_text(encoding="utf-8"))

    def save_session(self, workspace_id: str, session: dict) -> dict:
        with self._lock:
            self.get_workspace(workspace_id)
            session["updated_at"] = utcnow_iso()
            self._write_json(self.workspace_dir(workspace_id) / "session.json", session)
            self.touch_workspace(workspace_id)
            return session

    def clear_session(self, workspace_id: str) -> dict:
        session = self._empty_session()
        return self.save_session(workspace_id, session)

    def invalidate_runtime_session(self, workspace_id: str) -> dict:
        with self._lock:
            session = self.get_session(workspace_id)
            session["claude_session_id"] = None
            session["last_mode"] = "agent"
            session["last_forced_skill_id"] = None
            session["last_model"] = None
            return self.save_session(workspace_id, session)

    def append_turn(
        self,
        workspace_id: str,
        *,
        user_message: str,
        assistant_message: str,
        trace: list[dict],
        mode: str,
        forced_skill_id: str | None,
        model: str | None,
        claude_session_id: str | None,
    ) -> dict:
        session = self.get_session(workspace_id)
        session["claude_session_id"] = claude_session_id
        session["last_mode"] = mode
        session["last_forced_skill_id"] = forced_skill_id
        session["last_model"] = model
        session["turns"].append(
            {
                "turn_id": uuid4().hex,
                "created_at": utcnow_iso(),
                "mode": mode,
                "forced_skill_id": forced_skill_id,
                "model": model,
                "user_message": user_message,
                "assistant_message": assistant_message,
                "trace": trace,
            }
        )
        return self.save_session(workspace_id, session)

    def write_skill(self, workspace_id: str, skill_dir_name: str, content: str) -> Path:
        return self.write_skill_package(workspace_id, skill_dir_name, {"SKILL.md": content.encode("utf-8")}) / "SKILL.md"

    def write_skill_package(self, workspace_id: str, skill_dir_name: str, files: dict[str, bytes]) -> Path:
        self.get_workspace(workspace_id)
        self.ensure_native_skill_layout(workspace_id)
        target_dir = self.skills_dir(workspace_id) / skill_dir_name
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        for rel_path, raw in files.items():
            normalized = str(rel_path).replace("\\", "/").strip("/")
            parts = [part for part in normalized.split("/") if part and part != "."]
            if not parts or any(part == ".." for part in parts):
                raise ValueError(f"Invalid skill package path: {rel_path}")
            target_path = target_dir.joinpath(*parts)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(raw)
        self.touch_workspace(workspace_id)
        return target_dir

    def delete_skill(self, workspace_id: str, skill_dir_name: str) -> None:
        self.get_workspace(workspace_id)
        self.ensure_native_skill_layout(workspace_id)
        target_dir = self.skills_dir(workspace_id) / skill_dir_name
        if not target_dir.exists():
            raise KeyError(f"Skill not found: {skill_dir_name}")
        shutil.rmtree(target_dir)
        legacy_dir = self.legacy_skills_dir(workspace_id) / skill_dir_name
        if legacy_dir.exists():
            shutil.rmtree(legacy_dir)
        self.touch_workspace(workspace_id)

    def ensure_native_skill_layout(self, workspace_id: str) -> None:
        self.get_workspace(workspace_id)
        canonical_dir = self.skills_dir(workspace_id)
        canonical_dir.mkdir(parents=True, exist_ok=True)

        legacy_dir = self.legacy_skills_dir(workspace_id)
        if not legacy_dir.exists():
            return

        for child in sorted(legacy_dir.iterdir()):
            if not child.is_dir():
                continue
            source_skill = child / "SKILL.md"
            if not source_skill.exists():
                continue
            target_dir = canonical_dir / child.name
            if target_dir.exists():
                continue
            shutil.copytree(child, target_dir)

    def get_tool_registry(self, workspace_id: str) -> dict:
        self.get_workspace(workspace_id)
        path = self.tools_path(workspace_id)
        if not path.exists():
            payload = self._empty_tools()
            self._write_json(path, payload)
            return payload
        return json.loads(path.read_text(encoding="utf-8"))

    def save_tool_registry(self, workspace_id: str, payload: dict) -> dict:
        with self._lock:
            self.get_workspace(workspace_id)
            self._write_json(self.tools_path(workspace_id), payload)
            self.touch_workspace(workspace_id)
            return payload

    @staticmethod
    def _empty_session() -> dict:
        now = utcnow_iso()
        return {
            "created_at": now,
            "updated_at": now,
            "claude_session_id": None,
            "last_mode": "agent",
            "last_forced_skill_id": None,
            "last_model": None,
            "turns": [],
        }

    @staticmethod
    def _empty_tools() -> dict:
        return {"tools": []}

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
