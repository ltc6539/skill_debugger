from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml


LintSeverity = Literal["error", "warning"]
SkillSourceKind = Literal["folder", "single_file"]

FRONTMATTER_PATTERN = re.compile(
    r"\A---[ \t]*\r?\n(?P<frontmatter>.*?)(?:\r?\n)---[ \t]*(?:\r?\n|$)",
    re.DOTALL,
)
KEBAB_CASE_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DESCRIPTION_TRIGGER_PATTERN = re.compile(
    "|".join(
        [
            r"\buse when\b",
            r"\bwhen user\b",
            r"\bwhen the user\b",
            r"\bif the user\b",
            r"\buser asks\b",
            r"\basks? for\b",
            r"\basks? to\b",
            r"\bmentions?\b",
            r"\buploads?\b",
            r"\bsays?\b",
            r"\btrigger(?:ed)? by\b",
            r"当用户",
            r"用户说",
            r"用户提到",
            r"用户上传",
            r"适用于",
            r"用于",
            r"在.+时使用",
        ]
    ),
    re.IGNORECASE,
)
VAGUE_DESCRIPTION_PATTERNS = (
    re.compile(r"^\s*helps? with\b", re.IGNORECASE),
    re.compile(r"^\s*does things\b", re.IGNORECASE),
    re.compile(r"^\s*processes documents\b", re.IGNORECASE),
    re.compile(r"^\s*处理文档\b"),
    re.compile(r"^\s*帮助处理\b"),
)


def is_kebab_case(value: str) -> bool:
    return bool(KEBAB_CASE_PATTERN.fullmatch(str(value or "").strip()))


@dataclass(frozen=True)
class SkillLintFinding:
    severity: LintSeverity
    code: str
    message: str
    path: str | None = None
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.path:
            payload["path"] = self.path
        if self.field:
            payload["field"] = self.field
        return payload


@dataclass
class SkillLintReport:
    package_name: str
    source_kind: SkillSourceKind
    skill_name: str | None = None
    findings: list[SkillLintFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[SkillLintFinding]:
        return [item for item in self.findings if item.severity == "error"]

    @property
    def warnings(self) -> list[SkillLintFinding]:
        return [item for item in self.findings if item.severity == "warning"]

    @property
    def valid(self) -> bool:
        return not self.errors

    def add(
        self,
        severity: LintSeverity,
        code: str,
        message: str,
        *,
        path: str | None = None,
        field: str | None = None,
    ) -> None:
        finding = SkillLintFinding(
            severity=severity,
            code=code,
            message=message,
            path=path,
            field=field,
        )
        if finding not in self.findings:
            self.findings.append(finding)

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_name": self.package_name,
            "source_kind": self.source_kind,
            "skill_name": self.skill_name,
            "valid": self.valid,
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
        }


def lint_skill_directory(skill_dir: Path) -> SkillLintReport:
    files: dict[str, bytes] = {}
    for path in sorted(Path(skill_dir).rglob("*")):
        if not path.is_file():
            continue
        files[path.relative_to(skill_dir).as_posix()] = path.read_bytes()
    return lint_skill_package(Path(skill_dir).name, files, source_kind="folder")


def lint_skill_package(
    package_name: str,
    files: dict[str, bytes],
    *,
    source_kind: SkillSourceKind,
) -> SkillLintReport:
    report = SkillLintReport(package_name=package_name, source_kind=source_kind)
    normalized_files = {
        _normalize_rel_path(path): raw for path, raw in files.items() if _normalize_rel_path(path)
    }

    if source_kind == "folder" and not is_kebab_case(package_name):
        report.add(
            "error",
            "invalid_folder_name",
            "Skill folder name must use kebab-case (for example: restaurant-debug).",
        )

    if _has_root_readme(normalized_files):
        report.add(
            "warning",
            "readme_in_skill_folder",
            "README.md inside the skill folder is discouraged. Put skill docs in SKILL.md or references/ instead.",
            path="README.md",
        )

    exact_skill = normalized_files.get("SKILL.md")
    root_skill_candidates = sorted(
        path
        for path in normalized_files
        if PurePosixPath(path).parent == PurePosixPath(".") and PurePosixPath(path).name.lower() == "skill.md"
    )
    nested_skill_files = sorted(
        path
        for path in normalized_files
        if PurePosixPath(path).name == "SKILL.md" and path != "SKILL.md"
    )

    if nested_skill_files:
        report.add(
            "warning",
            "nested_skill_md",
            "Found additional nested SKILL.md files. Only the root-level SKILL.md is used as the main skill file.",
            path=nested_skill_files[0],
        )

    if exact_skill is None:
        if root_skill_candidates:
            report.add(
                "error",
                "skill_md_wrong_case",
                "Main skill file must be named exactly SKILL.md (case-sensitive).",
                path=root_skill_candidates[0],
            )
        elif any(PurePosixPath(path).name == "SKILL.md" for path in normalized_files):
            report.add(
                "error",
                "skill_md_not_at_root",
                "SKILL.md must be at the root of the uploaded skill folder.",
            )
        else:
            report.add(
                "error",
                "missing_skill_md",
                "Could not find SKILL.md in the uploaded skill package.",
            )
        return report

    try:
        text = exact_skill.decode("utf-8")
    except UnicodeDecodeError as exc:
        report.add(
            "error",
            "skill_md_not_utf8",
            f"SKILL.md must be UTF-8 text: {exc}",
            path="SKILL.md",
        )
        return report

    frontmatter, body = _parse_frontmatter(text, report)
    if frontmatter is None:
        return report

    if "<" in text or ">" in text:
        report.add(
            "warning",
            "angle_brackets_present",
            "Angle brackets (< or >) were found in SKILL.md. Anthropic's guidance recommends avoiding XML-like tags in skills.",
            path="SKILL.md",
        )

    _validate_name(frontmatter.get("name"), package_name, source_kind, report)
    _validate_description(frontmatter.get("description"), report)
    _validate_optional_string(frontmatter, "license", report, max_length=128)
    _validate_optional_string(frontmatter, "compatibility", report, max_length=500)
    _validate_allowed_tools(frontmatter.get("allowed-tools") or frontmatter.get("allowed_tools"), report)
    _validate_tool_definitions(
        frontmatter.get("tools")
        or frontmatter.get("tool-definitions")
        or frontmatter.get("tool_definitions"),
        report,
    )
    _validate_metadata(frontmatter.get("metadata"), report)

    if not body.strip():
        report.add(
            "warning",
            "missing_instructions_body",
            "SKILL.md should include instruction content after the YAML frontmatter.",
            path="SKILL.md",
        )
    if len(body.split()) > 5000:
        report.add(
            "warning",
            "skill_body_too_large",
            "SKILL.md body is quite large. Anthropic recommends keeping the main file lean and moving details into references/.",
            path="SKILL.md",
        )

    return report


def _normalize_rel_path(path: str) -> str:
    raw = str(path or "").replace("\\", "/").strip()
    if not raw:
        return ""
    parts = [part for part in raw.split("/") if part and part != "."]
    return "/".join(parts)


def _has_root_readme(files: dict[str, bytes]) -> bool:
    return any(
        PurePosixPath(path).parent == PurePosixPath(".") and PurePosixPath(path).name.lower() == "readme.md"
        for path in files
    )


def _parse_frontmatter(text: str, report: SkillLintReport) -> tuple[dict[str, Any] | None, str]:
    if not text.startswith("---"):
        report.add(
            "error",
            "missing_frontmatter",
            "SKILL.md must start with YAML frontmatter delimited by --- lines.",
            path="SKILL.md",
        )
        return None, text

    match = FRONTMATTER_PATTERN.match(text)
    if match is None:
        report.add(
            "error",
            "unterminated_frontmatter",
            "YAML frontmatter is missing a closing --- delimiter.",
            path="SKILL.md",
        )
        return None, text

    raw_frontmatter = match.group("frontmatter")
    body = text[match.end() :].lstrip("\n")
    try:
        payload = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as exc:
        message = str(exc).split("\n", 1)[0]
        report.add(
            "error",
            "invalid_frontmatter_yaml",
            f"Frontmatter is not valid YAML: {message}",
            path="SKILL.md",
        )
        return None, body

    if not isinstance(payload, dict):
        report.add(
            "error",
            "frontmatter_not_mapping",
            "Frontmatter must parse to a YAML mapping/object.",
            path="SKILL.md",
        )
        return None, body

    return payload, body


def _validate_name(
    raw_name: Any,
    package_name: str,
    source_kind: SkillSourceKind,
    report: SkillLintReport,
) -> None:
    if not isinstance(raw_name, str) or not raw_name.strip():
        report.add(
            "error",
            "missing_name",
            "Frontmatter must include a non-empty `name` field.",
            field="name",
        )
        return

    name = raw_name.strip()
    report.skill_name = name
    lowered = name.lower()
    if not is_kebab_case(name):
        report.add(
            "error",
            "invalid_name",
            "Frontmatter `name` must use kebab-case only.",
            field="name",
        )
    if lowered.startswith("claude") or lowered.startswith("anthropic"):
        report.add(
            "error",
            "reserved_name_prefix",
            "Skill names starting with `claude` or `anthropic` are reserved.",
            field="name",
        )
    if "<" in name or ">" in name:
        report.add(
            "error",
            "invalid_name_characters",
            "Frontmatter `name` cannot contain angle brackets (< or >).",
            field="name",
        )
    if source_kind == "folder" and package_name != name:
        report.add(
            "warning",
            "name_folder_mismatch",
            "Frontmatter `name` should match the skill folder name.",
            field="name",
        )


def _validate_description(raw_description: Any, report: SkillLintReport) -> None:
    if not isinstance(raw_description, str) or not raw_description.strip():
        report.add(
            "error",
            "missing_description",
            "Frontmatter must include a non-empty `description` field.",
            field="description",
        )
        return

    description = raw_description.strip()
    if len(description) > 1024:
        report.add(
            "error",
            "description_too_long",
            "Frontmatter `description` must be 1024 characters or fewer.",
            field="description",
        )
    if "<" in description or ">" in description:
        report.add(
            "error",
            "description_contains_angle_brackets",
            "Frontmatter `description` cannot contain angle brackets (< or >).",
            field="description",
        )
    if len(description.split()) < 6 and len(description) < 32:
        report.add(
            "warning",
            "description_too_short",
            "Description is very short. Consider adding clearer capability and trigger details.",
            field="description",
        )
    if not DESCRIPTION_TRIGGER_PATTERN.search(description):
        report.add(
            "warning",
            "description_missing_trigger_language",
            "Description should explain when to use the skill and include likely trigger phrases.",
            field="description",
        )
    if any(pattern.search(description) for pattern in VAGUE_DESCRIPTION_PATTERNS):
        report.add(
            "warning",
            "description_too_vague",
            "Description looks vague. Make the task and trigger conditions more specific.",
            field="description",
        )


def _validate_optional_string(
    frontmatter: dict[str, Any],
    field_name: str,
    report: SkillLintReport,
    *,
    max_length: int,
) -> None:
    value = frontmatter.get(field_name)
    if value is None:
        return
    if not isinstance(value, str):
        report.add(
            "error",
            f"{field_name}_not_string",
            f"Frontmatter `{field_name}` must be a string when provided.",
            field=field_name,
        )
        return
    stripped = value.strip()
    if not stripped:
        report.add(
            "warning",
            f"{field_name}_empty",
            f"Frontmatter `{field_name}` is empty.",
            field=field_name,
        )
    if len(stripped) > max_length:
        report.add(
            "error",
            f"{field_name}_too_long",
            f"Frontmatter `{field_name}` must be {max_length} characters or fewer.",
            field=field_name,
        )


def _validate_allowed_tools(value: Any, report: SkillLintReport) -> None:
    if value is None:
        return
    if isinstance(value, str):
        if not value.strip():
            report.add(
                "warning",
                "allowed_tools_empty",
                "Frontmatter `allowed-tools` is present but empty.",
                field="allowed-tools",
            )
        return
    if not isinstance(value, list):
        report.add(
            "error",
            "allowed_tools_invalid_type",
            "Frontmatter `allowed-tools` must be a string or a list of strings.",
            field="allowed-tools",
        )
        return
    for item in value:
        if not isinstance(item, str) or not item.strip():
            report.add(
                "error",
                "allowed_tools_invalid_item",
                "Each `allowed-tools` item must be a non-empty string.",
                field="allowed-tools",
            )
            return


def _validate_tool_definitions(value: Any, report: SkillLintReport) -> None:
    if value is None:
        return

    entries: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        entries = [(str(name), payload) for name, payload in value.items()]
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                report.add(
                    "error",
                    "tool_definitions_invalid_item",
                    "Frontmatter `tools` list items must be YAML objects/mappings.",
                    field="tools",
                )
                return
            entries.append((str(item.get("name") or ""), item))
    else:
        report.add(
            "error",
            "tool_definitions_invalid_type",
            "Frontmatter `tools` must be a YAML object or a list of YAML objects.",
            field="tools",
        )
        return

    for raw_name, payload in entries:
        tool_name = str(raw_name).strip()
        if not tool_name:
            report.add(
                "error",
                "tool_definition_missing_name",
                "Each tool definition must include a non-empty `name`.",
                field="tools",
            )
            return
        if not isinstance(payload, dict):
            report.add(
                "error",
                "tool_definition_not_mapping",
                f"Tool definition `{tool_name}` must be a YAML object/mapping.",
                field="tools",
            )
            return

        description = payload.get("description")
        if not isinstance(description, str) or not description.strip():
            report.add(
                "error",
                "tool_definition_missing_description",
                f"Tool definition `{tool_name}` must include a non-empty `description`.",
                field="tools",
            )

        input_schema = (
            payload.get("input_schema")
            or payload.get("input-schema")
            or payload.get("schema")
        )
        if not isinstance(input_schema, dict):
            report.add(
                "error",
                "tool_definition_missing_input_schema",
                f"Tool definition `{tool_name}` must include an object `input_schema`.",
                field="tools",
            )
            continue
        if input_schema.get("type") not in {None, "object"}:
            report.add(
                "error",
                "tool_definition_invalid_input_schema_type",
                f"Tool definition `{tool_name}` must use an object JSON schema.",
                field="tools",
            )


def _validate_metadata(value: Any, report: SkillLintReport) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        report.add(
            "error",
            "metadata_not_mapping",
            "Frontmatter `metadata` must be a YAML object/mapping when provided.",
            field="metadata",
        )
