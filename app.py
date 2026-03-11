from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from skill_debugger.service import SkillDebuggerService
from skill_debugger.settings import load_skill_debugger_settings
from skill_debugger.store import WorkspaceStore

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = Path(os.getenv("SKILL_DEBUGGER_STATE_DIR", BASE_DIR / "state"))

store = WorkspaceStore(STATE_DIR / "workspaces")
settings = load_skill_debugger_settings(BASE_DIR)
service = SkillDebuggerService(store=store, settings=settings)

app = FastAPI(title="Skill Debugger", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


class CreateWorkspaceRequest(BaseModel):
    name: str | None = Field(default=None)


class ChatRequest(BaseModel):
    message: str
    mode: Literal["agent", "forced"] = "agent"
    forced_skill_id: str | None = None
    model: str | None = None


class CreateToolRequest(BaseModel):
    name: str
    description: str | None = None


class UpdateSkillDocumentRequest(BaseModel):
    content: str


class SyncProjectToolsRequest(BaseModel):
    presets: list[str] | None = None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/tools")
async def tools_page() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "tools.html")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/bootstrap")
async def bootstrap() -> dict:
    return service.bootstrap()


@app.get("/api/workspaces")
async def list_workspaces() -> dict:
    return {"workspaces": store.list_workspaces(), "runtime": service.runtime_status()}


@app.post("/api/workspaces")
async def create_workspace(payload: CreateWorkspaceRequest) -> dict:
    return service.create_workspace(payload.name)


@app.delete("/api/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str) -> dict:
    try:
        return service.delete_workspace(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/workspaces/{workspace_id}")
async def get_workspace(workspace_id: str) -> dict:
    try:
        return service.get_workspace_state(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/workspaces/{workspace_id}/skills/upload")
async def upload_skills(
    workspace_id: str,
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(default=[]),
) -> dict:
    try:
        payloads: list[tuple[str, bytes]] = []
        for index, upload in enumerate(files):
            if not upload.filename:
                continue
            relative_path = paths[index] if index < len(paths) and paths[index] else upload.filename
            payloads.append((relative_path, await upload.read()))
        return service.upload_skills(workspace_id, payloads)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Skill file must be UTF-8 text: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/workspaces/{workspace_id}/skills/{skill_id}")
async def delete_skill(workspace_id: str, skill_id: str) -> dict:
    try:
        return service.delete_skill(workspace_id, skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/workspaces/{workspace_id}/skills/{skill_id}/document")
async def get_skill_document(workspace_id: str, skill_id: str) -> dict:
    try:
        return service.get_skill_document(workspace_id, skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/workspaces/{workspace_id}/skills/{skill_id}/document")
async def update_skill_document(
    workspace_id: str,
    skill_id: str,
    payload: UpdateSkillDocumentRequest,
) -> dict:
    try:
        return service.update_skill_document(workspace_id, skill_id, payload.content)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/workspaces/{workspace_id}/tools")
async def create_tool(workspace_id: str, payload: CreateToolRequest) -> dict:
    try:
        return service.add_tool(workspace_id, payload.name, payload.description)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/workspaces/{workspace_id}/tools/{tool_name}")
async def delete_tool(workspace_id: str, tool_name: str) -> dict:
    try:
        return service.delete_tool(workspace_id, tool_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/workspaces/{workspace_id}/tools/project-sync")
async def sync_project_tools(workspace_id: str, payload: SyncProjectToolsRequest) -> dict:
    try:
        return service.sync_project_tool_presets(workspace_id, payload.presets)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/workspaces/{workspace_id}/context/clear")
async def clear_context(workspace_id: str) -> dict:
    try:
        return service.clear_context(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/workspaces/{workspace_id}/chat/stream")
async def chat_stream(workspace_id: str, payload: ChatRequest) -> StreamingResponse:
    async def event_stream():
        try:
            async for event in service.run_chat(
                workspace_id=workspace_id,
                message=payload.message,
                mode=payload.mode,
                forced_skill_id=payload.forced_skill_id,
                model=payload.model,
            ):
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        except KeyError as exc:
            yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"
        except ValueError as exc:
            yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # pragma: no cover - runtime-specific failures
            yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
