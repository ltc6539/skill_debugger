from __future__ import annotations

import base64
import binascii
import copy
import json
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse
from uuid import uuid4

from openai import OpenAI

from skill_debugger.settings import SkillDebuggerSettings
from skill_debugger.tool_registry import GENERIC_OBJECT_SCHEMA


Handler = Callable[[dict[str, Any]], Any | Awaitable[Any]]

DEFAULT_VLM_MODEL = "openai/gpt-4o-mini"

_openrouter_api_key: str | None = None
_openrouter_base_url: str = "https://openrouter.ai/api"
_vlm_model: str = DEFAULT_VLM_MODEL
_calendar_store: dict[str, list[dict[str, Any]]] = {}


def configure_aligned_project_tools(settings: SkillDebuggerSettings) -> None:
    global _openrouter_api_key, _openrouter_base_url, _vlm_model, _calendar_store
    _openrouter_api_key = settings.openrouter_api_key
    _openrouter_base_url = settings.openrouter_base_url
    _vlm_model = settings.vlm_model or settings.default_model or DEFAULT_VLM_MODEL
    _calendar_store = {}


@dataclass
class LocalProjectTool:
    name: str
    description: str
    params_json_schema: dict[str, Any]
    handler: Handler

    async def on_invoke_tool(self, _ctx: Any, payload: str) -> Any:
        try:
            parsed = json.loads(payload or "{}")
        except json.JSONDecodeError:
            return {
                "error": "invalid_payload",
                "message": "Tool payload must be valid JSON.",
            }
        if not isinstance(parsed, dict):
            return {
                "error": "invalid_payload",
                "message": "Tool payload must be a JSON object.",
            }
        result = self.handler(parsed)
        if hasattr(result, "__await__"):
            return await result
        return result


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _tool_error(message: str, *, code: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"error": code, "message": message}
    if extra:
        payload.update(extra)
    return payload


def _calendar_events(calendar_id: str) -> list[dict[str, Any]]:
    return _calendar_store.setdefault(calendar_id, [])


def _handle_get_calendar_events(args: dict[str, Any]) -> dict[str, Any]:
    calendar_id = str(args.get("calendar_id") or "primary").strip() or "primary"
    max_results = int(args.get("max_results") or 10)
    max_results = max(1, min(max_results, 100))

    time_min = _parse_iso8601(args.get("time_min"))
    if time_min is None:
        time_min = datetime.now(timezone.utc)
    time_max = _parse_iso8601(args.get("time_max"))
    if time_max is None:
        time_max = time_min + timedelta(days=7)
    if time_max < time_min:
        return _tool_error(
            "`time_max` must be greater than or equal to `time_min`.",
            code="invalid_time_range",
        )

    filtered: list[dict[str, Any]] = []
    for item in _calendar_events(calendar_id):
        start_at = _parse_iso8601(item.get("start"))
        end_at = _parse_iso8601(item.get("end")) or start_at
        if start_at is None:
            continue
        if end_at and end_at < time_min:
            continue
        if start_at > time_max:
            continue
        filtered.append(_json_clone(item))

    filtered.sort(key=lambda item: item.get("start") or "")
    return {
        "events": filtered[:max_results],
        "count": min(len(filtered), max_results),
        "calendar_id": calendar_id,
        "time_min": time_min.isoformat(),
        "time_max": time_max.isoformat(),
        "backend": "debug_memory",
    }


def _handle_create_calendar_event(args: dict[str, Any]) -> dict[str, Any]:
    summary = str(args.get("summary") or "").strip()
    if not summary:
        return _tool_error("`summary` is required.", code="missing_summary")

    start_time = _parse_iso8601(args.get("start_time"))
    end_time = _parse_iso8601(args.get("end_time"))
    if start_time is None or end_time is None:
        return _tool_error(
            "`start_time` and `end_time` must be valid ISO 8601 timestamps.",
            code="invalid_datetime",
        )
    if end_time <= start_time:
        return _tool_error(
            "`end_time` must be later than `start_time`.",
            code="invalid_datetime_range",
        )

    calendar_id = str(args.get("calendar_id") or "primary").strip() or "primary"
    attendees_raw = args.get("attendees")
    attendees: list[dict[str, str]] = []
    if isinstance(attendees_raw, str):
        for item in attendees_raw.split(","):
            email = item.strip()
            if email:
                attendees.append({"email": email})
    elif isinstance(attendees_raw, list):
        for item in attendees_raw:
            email = str(item).strip()
            if email:
                attendees.append({"email": email})

    event_id = f"dbg_evt_{uuid4().hex[:12]}"
    event = {
        "id": event_id,
        "summary": summary,
        "start": start_time.isoformat(),
        "end": end_time.isoformat(),
        "location": str(args.get("location") or "").strip() or None,
        "description": str(args.get("description") or "").strip() or None,
        "attendees": attendees,
    }
    _calendar_events(calendar_id).append(event)
    _calendar_events(calendar_id).sort(key=lambda item: item.get("start") or "")

    return {
        "success": True,
        "event_id": event_id,
        "summary": summary,
        "start": event["start"],
        "end": event["end"],
        "calendar_id": calendar_id,
        "html_link": f"debug://calendar/{calendar_id}/{event_id}",
        "backend": "debug_memory",
    }


def _build_image_data_url(image_bytes: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _read_image_reference(args: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    image_url = str(args.get("image_url") or "").strip()
    if image_url:
        parsed = urlparse(image_url)
        if parsed.scheme in {"http", "https", "data"}:
            return image_url, {
                "input_kind": "image_url",
                "image_url": image_url,
            }
        return None

    image_base64 = str(args.get("image_base64") or "").strip()
    if image_base64:
        try:
            raw = base64.b64decode(image_base64, validate=True)
        except (ValueError, binascii.Error):
            return None
        mime_type = str(args.get("mime_type") or "image/jpeg").strip() or "image/jpeg"
        return _build_image_data_url(raw, mime_type), {
            "input_kind": "image_base64",
            "mime_type": mime_type,
            "image_bytes": len(raw),
        }

    image_path = str(args.get("image_path") or "").strip()
    if image_path:
        path = Path(image_path)
        if not path.exists() or not path.is_file():
            return None
        raw = path.read_bytes()
        mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        return _build_image_data_url(raw, mime_type), {
            "input_kind": "image_path",
            "image_path": str(path),
            "mime_type": mime_type,
            "image_bytes": len(raw),
        }

    return None


def _recognize_image_prompt(purpose: str, prompt_hint: str | None) -> str:
    base = {
        "food": "Describe the food in the image. List the likely dishes, ingredients, portion size, and cuisine in concise plain text.",
        "ingredient": "Describe the ingredients visible in the image. Focus on raw ingredients, produce, pantry items, and likely quantities in concise plain text.",
    }.get(purpose, "Describe the image contents relevant to the user's request in concise plain text.")
    if prompt_hint:
        return f"{base}\nAdditional instruction: {prompt_hint}"
    return base


def _openrouter_vlm_client() -> OpenAI | None:
    if not _openrouter_api_key:
        return None
    base_url = _openrouter_base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    return OpenAI(api_key=_openrouter_api_key, base_url=base_url)


async def _handle_recognize_image(args: dict[str, Any]) -> dict[str, Any]:
    image_reference = _read_image_reference(args)
    if image_reference is None:
        return _tool_error(
            "Provide one of `image_url`, `image_base64`, or `image_path`, or use a valid uploaded `image_id` through the chat workflow.",
            code="missing_image_source",
            extra={"image_id": str(args.get("image_id") or "").strip() or None},
        )

    client = _openrouter_vlm_client()
    if client is None:
        return _tool_error(
            "OPENROUTER_API_KEY is not configured for skill_debugger.",
            code="openrouter_not_configured",
        )

    image_url, metadata = image_reference
    purpose = str(args.get("purpose") or "food").strip() or "food"
    prompt_hint = str(args.get("prompt_hint") or "").strip() or None
    prompt = _recognize_image_prompt(purpose, prompt_hint)

    try:
        response = client.responses.create(
            model=_vlm_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": image_url},
                    ],
                }
            ],
            max_output_tokens=300,
            temperature=0.2,
        )
        description = (getattr(response, "output_text", None) or "").strip()
    except Exception as exc:
        return _tool_error(
            f"OpenRouter VLM request failed: {exc}",
            code="vlm_request_failed",
            extra={"model": _vlm_model},
        )

    if not description:
        return _tool_error(
            "OpenRouter VLM returned an empty description.",
            code="empty_vlm_response",
            extra={"model": _vlm_model},
        )

    return {
        "image_id": str(args.get("image_id") or "").strip() or None,
        "purpose": purpose,
        "description": description,
        "status": "recognized",
        "model": _vlm_model,
        "backend": "openrouter_vlm",
        **metadata,
    }


def _handle_canvas_card(args: dict[str, Any]) -> dict[str, Any]:
    if not args:
        return _tool_error(
            "canvas_card expects a JSON object describing the card payload.",
            code="empty_card_payload",
        )

    if isinstance(args.get("card"), dict):
        payload = copy.deepcopy(args["card"])
    else:
        payload = copy.deepcopy(args)

    payload.setdefault("kind", "canvas_card")
    payload.setdefault("generated_at", _iso_now())
    return payload


RECOGNIZE_IMAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "image_id": {"type": "string"},
        "purpose": {"type": "string", "enum": ["food", "ingredient", "general"]},
        "image_url": {"type": "string"},
        "image_base64": {"type": "string"},
        "image_path": {"type": "string"},
        "mime_type": {"type": "string"},
        "prompt_hint": {"type": "string"},
    },
    "additionalProperties": False,
}

GET_CALENDAR_EVENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "time_min": {"type": "string"},
        "time_max": {"type": "string"},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
        "calendar_id": {"type": "string"},
    },
    "additionalProperties": False,
}

CREATE_CALENDAR_EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "start_time": {"type": "string"},
        "end_time": {"type": "string"},
        "location": {"type": "string"},
        "description": {"type": "string"},
        "attendees": {
            "oneOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
            ]
        },
        "calendar_id": {"type": "string"},
    },
    "required": ["summary", "start_time", "end_time"],
    "additionalProperties": False,
}


ALIGNED_PROJECT_TOOLS = [
    LocalProjectTool(
        name="recognize_image",
        description="Recognize food or ingredient images with an OpenRouter VLM model.",
        params_json_schema=RECOGNIZE_IMAGE_SCHEMA,
        handler=_handle_recognize_image,
    ),
    LocalProjectTool(
        name="get_calendar_events",
        description="Read upcoming calendar events from the debugger's local calendar store.",
        params_json_schema=GET_CALENDAR_EVENTS_SCHEMA,
        handler=_handle_get_calendar_events,
    ),
    LocalProjectTool(
        name="create_calendar_event",
        description="Create a calendar event inside the debugger's local calendar store.",
        params_json_schema=CREATE_CALENDAR_EVENT_SCHEMA,
        handler=_handle_create_calendar_event,
    ),
    LocalProjectTool(
        name="canvas_card",
        description="Return the provided JSON payload as a ready-to-render canvas card.",
        params_json_schema=copy.deepcopy(GENERIC_OBJECT_SCHEMA),
        handler=_handle_canvas_card,
    ),
]
