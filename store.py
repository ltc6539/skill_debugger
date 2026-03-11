from __future__ import annotations

import json
import logging
import mimetypes
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from skill_debugger.skill_registry import slugify

logger = logging.getLogger(__name__)


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
            try:
                items.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                logger.warning("Skipping unreadable workspace metadata: %s", path)
        return items

    def create_workspace(self, name: str | None = None) -> dict:
        with self._lock:
            normalized_name = str(name or "").strip()
            requested = slugify(normalized_name or f"workspace-{uuid4().hex[:8]}")
            workspace_id = requested
            suffix = 2
            while self.workspace_dir(workspace_id).exists():
                workspace_id = f"{requested}-{suffix}"
                suffix += 1
            now = utcnow_iso()
            workspace = {
                "workspace_id": workspace_id,
                "name": normalized_name or workspace_id,
                "created_at": now,
                "updated_at": now,
            }
            self.workspace_dir(workspace_id).mkdir(parents=True, exist_ok=True)
            self.claude_dir(workspace_id).mkdir(parents=True, exist_ok=True)
            self.skills_dir(workspace_id).mkdir(parents=True, exist_ok=True)
            self.runtime_projects_dir(workspace_id).mkdir(parents=True, exist_ok=True)
            self.reviews_dir(workspace_id).mkdir(parents=True, exist_ok=True)
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
            runtime_root = self.runtime_projects_dir(workspace_id)
            if runtime_root.exists():
                shutil.rmtree(runtime_root)

    def workspace_dir(self, workspace_id: str) -> Path:
        return self.base_dir / workspace_id

    def claude_dir(self, workspace_id: str) -> Path:
        return self.workspace_dir(workspace_id) / ".claude"

    def skills_dir(self, workspace_id: str) -> Path:
        return self.claude_dir(workspace_id) / "skills"

    def legacy_skills_dir(self, workspace_id: str) -> Path:
        return self.workspace_dir(workspace_id) / "skills"

    def runtime_projects_dir(self, workspace_id: str) -> Path:
        return self.base_dir / ".runtime_projects" / workspace_id

    def runtime_project_dir(self, workspace_id: str, runtime_key: str) -> Path:
        return self.runtime_projects_dir(workspace_id) / runtime_key

    def tools_path(self, workspace_id: str) -> Path:
        return self.workspace_dir(workspace_id) / "tools.json"

    def reviews_dir(self, workspace_id: str) -> Path:
        return self.workspace_dir(workspace_id) / "reviews"

    def images_dir(self, workspace_id: str) -> Path:
        return self.workspace_dir(workspace_id) / "images"

    def skill_dir(self, workspace_id: str, skill_dir_name: str) -> Path:
        return self.skills_dir(workspace_id) / skill_dir_name

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

    def clear_reviews(self, workspace_id: str) -> None:
        with self._lock:
            self.get_workspace(workspace_id)
            reviews_dir = self.reviews_dir(workspace_id)
            if reviews_dir.exists():
                shutil.rmtree(reviews_dir)
            reviews_dir.mkdir(parents=True, exist_ok=True)
            self.touch_workspace(workspace_id)

    def invalidate_runtime_session(self, workspace_id: str) -> dict:
        with self._lock:
            session = self.get_session(workspace_id)
            session["claude_session_id"] = None
            session["last_mode"] = "agent"
            session["last_forced_skill_id"] = None
            session["last_model"] = None
            session["last_runtime_cwd"] = None
            return self.save_session(workspace_id, session)

    def append_turn(
        self,
        workspace_id: str,
        *,
        user_message: str,
        assistant_message: str,
        trace: list[dict],
        attached_images: list[dict] | None = None,
        mode: str,
        forced_skill_id: str | None,
        model: str | None,
        claude_session_id: str | None,
        runtime_cwd: str | None,
    ) -> dict:
        session = self.get_session(workspace_id)
        session["claude_session_id"] = claude_session_id
        session["last_mode"] = mode
        session["last_forced_skill_id"] = forced_skill_id
        session["last_model"] = model
        session["last_runtime_cwd"] = runtime_cwd
        session["turns"].append(
            {
                "turn_id": uuid4().hex,
                "created_at": utcnow_iso(),
                "mode": mode,
                "forced_skill_id": forced_skill_id,
                "model": model,
                "user_message": user_message,
                "assistant_message": assistant_message,
                "attached_images": list(attached_images or []),
                "trace": trace,
            }
        )
        return self.save_session(workspace_id, session)

    def save_uploaded_image(
        self,
        workspace_id: str,
        *,
        filename: str,
        content: bytes,
        mime_type: str | None,
    ) -> dict:
        with self._lock:
            self.get_workspace(workspace_id)
            images_dir = self.images_dir(workspace_id)
            images_dir.mkdir(parents=True, exist_ok=True)

            image_id = f"img_{uuid4().hex[:12]}"
            safe_name = self._sanitize_filename(filename)
            suffix = Path(safe_name).suffix
            if not suffix:
                suffix = mimetypes.guess_extension(mime_type or "") or ".bin"
                safe_name = f"{safe_name}{suffix}"
            stored_name = f"{image_id}{suffix.lower()}"
            blob_path = images_dir / stored_name
            blob_path.write_bytes(content)

            payload = {
                "image_id": image_id,
                "filename": safe_name,
                "mime_type": mime_type or "application/octet-stream",
                "size_bytes": len(content),
                "stored_name": stored_name,
                "created_at": utcnow_iso(),
                "url": f"/api/workspaces/{workspace_id}/images/{image_id}",
            }
            self._write_json(self._image_meta_path(workspace_id, image_id), payload)
            self.touch_workspace(workspace_id)
            return payload

    def get_uploaded_image(self, workspace_id: str, image_id: str) -> dict:
        self.get_workspace(workspace_id)
        meta_path = self._image_meta_path(workspace_id, image_id)
        if not meta_path.exists():
            raise KeyError(f"Image not found: {image_id}")
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        payload["url"] = f"/api/workspaces/{workspace_id}/images/{image_id}"
        return payload

    def get_uploaded_image_path(self, workspace_id: str, image_id: str) -> Path:
        payload = self.get_uploaded_image(workspace_id, image_id)
        path = self.images_dir(workspace_id) / str(payload.get("stored_name") or "")
        if not path.exists():
            raise KeyError(f"Image blob not found: {image_id}")
        return path

    def write_skill(self, workspace_id: str, skill_dir_name: str, content: str) -> Path:
        return self.write_skill_package(workspace_id, skill_dir_name, {"SKILL.md": content.encode("utf-8")}) / "SKILL.md"

    def read_skill_text(self, workspace_id: str, skill_dir_name: str) -> str:
        self.get_workspace(workspace_id)
        self.ensure_native_skill_layout(workspace_id)
        path = self.skill_dir(workspace_id, skill_dir_name) / "SKILL.md"
        if not path.exists():
            raise KeyError(f"Skill not found: {skill_dir_name}")
        return path.read_text(encoding="utf-8")

    def read_skill_package(self, workspace_id: str, skill_dir_name: str) -> dict[str, bytes]:
        self.get_workspace(workspace_id)
        self.ensure_native_skill_layout(workspace_id)
        target_dir = self.skill_dir(workspace_id, skill_dir_name)
        if not target_dir.exists():
            raise KeyError(f"Skill not found: {skill_dir_name}")
        files: dict[str, bytes] = {}
        for path in sorted(target_dir.rglob("*")):
            if not path.is_file():
                continue
            files[path.relative_to(target_dir).as_posix()] = path.read_bytes()
        return files

    def write_skill_package(self, workspace_id: str, skill_dir_name: str, files: dict[str, bytes]) -> Path:
        with self._lock:
            self.get_workspace(workspace_id)
            self.ensure_native_skill_layout(workspace_id)
            target_dir = self.skill_dir(workspace_id, skill_dir_name)
            parent_dir = target_dir.parent
            staging_dir = parent_dir / f".{skill_dir_name}.tmp-{uuid4().hex}"
            backup_dir = parent_dir / f".{skill_dir_name}.bak-{uuid4().hex}"

            self._cleanup_path(staging_dir)
            self._cleanup_path(backup_dir)

            try:
                staging_dir.mkdir(parents=True, exist_ok=True)
                self._write_skill_tree(staging_dir, files)
                had_existing = target_dir.exists()
                if had_existing:
                    target_dir.rename(backup_dir)
                staging_dir.rename(target_dir)
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                self.touch_workspace(workspace_id)
                return target_dir
            except Exception:
                if backup_dir.exists() and not target_dir.exists():
                    backup_dir.rename(target_dir)
                raise
            finally:
                self._cleanup_path(staging_dir)
                self._cleanup_path(backup_dir)

    def delete_skill(self, workspace_id: str, skill_dir_name: str) -> None:
        with self._lock:
            self.get_workspace(workspace_id)
            self.ensure_native_skill_layout(workspace_id)
            target_dir = self.skill_dir(workspace_id, skill_dir_name)
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

    def list_reviews(self, workspace_id: str) -> list[dict]:
        self.get_workspace(workspace_id)
        reviews_dir = self.reviews_dir(workspace_id)
        reviews_dir.mkdir(parents=True, exist_ok=True)
        items: list[dict] = []
        for path in sorted(reviews_dir.glob("*.json")):
            try:
                items.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return items

    def save_review(self, workspace_id: str, payload: dict) -> dict:
        with self._lock:
            self.get_workspace(workspace_id)
            review_id = str(payload.get("review_id") or "").strip()
            if not review_id:
                raise ValueError("Review payload must include review_id.")
            reviews_dir = self.reviews_dir(workspace_id)
            reviews_dir.mkdir(parents=True, exist_ok=True)
            self._write_json(reviews_dir / f"{review_id}.json", payload)
            self.touch_workspace(workspace_id)
            return payload

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
            "last_runtime_cwd": None,
            "turns": [],
        }

    @staticmethod
    def _empty_tools() -> dict:
        return {"tools": []}

    def _image_meta_path(self, workspace_id: str, image_id: str) -> Path:
        return self.images_dir(workspace_id) / f"{image_id}.json"

    @staticmethod
    def _sanitize_filename(filename: str | None) -> str:
        raw = Path(str(filename or "image")).name
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(raw).stem).strip(".-") or "image"
        suffix = re.sub(r"[^A-Za-z0-9.]+", "", Path(raw).suffix)[:12]
        return f"{stem}{suffix}"

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
        try:
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @staticmethod
    def _write_skill_tree(target_dir: Path, files: dict[str, bytes]) -> None:
        for rel_path, raw in files.items():
            normalized = str(rel_path).replace("\\", "/").strip("/")
            parts = [part for part in normalized.split("/") if part and part != "."]
            if not parts or any(part == ".." for part in parts):
                raise ValueError(f"Invalid skill package path: {rel_path}")
            target_path = target_dir.joinpath(*parts)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(raw)

    @staticmethod
    def _cleanup_path(path: Path) -> None:
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            return
        path.unlink(missing_ok=True)
