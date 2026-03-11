from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import yaml


REVIEW_RUBRIC = """
Review rubric:
- Frontmatter description must state both what the skill does and when to use it, with likely trigger phrases.
- First decide whether this query should have triggered the skill based on frontmatter.
- Then check whether the instructions body covers the key steps required by this query.
- Then check whether the trace shows those steps actually happened.
- Always distinguish should_trigger_but_did_not from triggered_but_served_poorly.
- Attribute problems to frontmatter, instructions, tools, runtime, or user_query.
- Suggestions must point to a concrete edit target and generate should trigger / should not trigger tests.
""".strip()

FRONTMATTER_PATTERN = re.compile(
    r"\A---[ \t]*\r?\n(?P<frontmatter>.*?)(?:\r?\n)---[ \t]*(?:\r?\n|$)",
    re.DOTALL,
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SignalPattern:
    category: str
    label: str
    pattern: re.Pattern[str]


SIGNAL_PATTERNS: tuple[SignalPattern, ...] = (
    SignalPattern("recommendation", "找餐厅 / 推荐", re.compile(r"推荐|找(?:一[家个])?餐厅|restaurant|where to eat|吃什么|去哪吃", re.IGNORECASE)),
    SignalPattern("booking", "预约 / 订位", re.compile(r"预约|预订|订位|订座|reservation|reserve|book\b|打电话|外呼|online booking|订餐厅", re.IGNORECASE)),
    SignalPattern("navigation", "导航 / 路线", re.compile(r"导航|怎么去|路线|route|directions|带我去|前往", re.IGNORECASE)),
    SignalPattern("allergy", "过敏 / 忌口", re.compile(r"过敏|allerg|花生|忌口|vegan|vegetarian|gluten|乳糖|dietary restriction|饮食限制", re.IGNORECASE)),
    SignalPattern("location", "位置 / 城市", re.compile(r"附近|downtown|location|地址|旧金山|在哪|哪里|city|商圈", re.IGNORECASE)),
    SignalPattern("budget", "预算 / 价位", re.compile(r"预算|高端|便宜|价位|budget|price|\${1,4}|人均", re.IGNORECASE)),
    SignalPattern("time", "时间 / 餐期", re.compile(r"今天|明天|今晚|午餐|晚餐|下午|晚上|几点|tomorrow|tonight|lunch|dinner|date|time", re.IGNORECASE)),
    SignalPattern("party_size", "人数", re.compile(r"\d+\s*人|for\s+\d+|party size|人数", re.IGNORECASE)),
    SignalPattern("phone", "电话 / 拨号", re.compile(r"电话|拨号|call\b|phone|回拨|Twilio", re.IGNORECASE)),
    SignalPattern("calendar", "日历 / 日程", re.compile(r"日历|日程|calendar", re.IGNORECASE)),
    SignalPattern("image", "图片", re.compile(r"图片|照片|image|upload", re.IGNORECASE)),
)

PRIMARY_INTENT_ORDER: tuple[str, ...] = ("recommendation", "booking", "navigation")
EXPECTED_SUPPORT_BY_INTENT: dict[str, set[str]] = {
    "recommendation": {"allergy", "location", "budget", "time", "party_size"},
    "booking": {"allergy", "time", "party_size", "phone"},
    "navigation": {"location", "time"},
}
TRACE_TOOL_CATEGORY_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"YELP_|places_|restaurant|REQUEST_CARD_INPUT|canvas_card", re.IGNORECASE), "recommendation"),
    (re.compile(r"twilio|opentable|reservation|book|phone|YELP_GET_BUSINESS_DETAILS", re.IGNORECASE), "booking"),
    (re.compile(r"navigation|route|maps", re.IGNORECASE), "navigation"),
    (re.compile(r"calendar", re.IGNORECASE), "calendar"),
)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    match = FRONTMATTER_PATTERN.match(text)
    if match is None:
        return {}, text
    raw_frontmatter = match.group("frontmatter")
    body = text[match.end() :].lstrip("\n")
    try:
        payload = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError:
        return {}, body
    return payload if isinstance(payload, dict) else {}, body


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _skill_activation_ids(trace: list[dict[str, Any]]) -> list[str]:
    skills: list[str] = []
    for entry in trace:
        if entry.get("category") != "skill_activation":
            continue
        for raw in entry.get("skills") or []:
            value = str(raw or "").strip()
            if value and value not in skills:
                skills.append(value)
        input_payload = entry.get("input") or {}
        explicit = str(input_payload.get("skill") or input_payload.get("skill_id") or "").strip()
        if explicit and explicit not in skills:
            skills.append(explicit)
    return skills


def _extract_signal_matches(text: str) -> dict[str, list[str]]:
    matches: dict[str, list[str]] = {}
    source = str(text or "")
    if not source:
        return matches
    for spec in SIGNAL_PATTERNS:
        found = spec.pattern.findall(source)
        flattened: list[str] = []
        for item in found:
            if isinstance(item, tuple):
                flattened.extend(part for part in item if part)
            else:
                flattened.append(str(item))
        if flattened:
            matches[spec.category] = _unique(flattened)
    return matches


def _extract_categories(text: str) -> set[str]:
    categories = set(_extract_signal_matches(text).keys())
    return categories


def _skill_primary_intent(skill_id: str, name: str, description: str, body: str) -> str | None:
    signal_text = "\n".join([skill_id, name, description, body])
    categories = _extract_categories(signal_text)
    for intent in PRIMARY_INTENT_ORDER:
        if intent in categories:
            return intent
    lowered = signal_text.lower()
    if "recommend" in lowered:
        return "recommendation"
    if "book" in lowered or "reservation" in lowered:
        return "booking"
    if "nav" in lowered or "route" in lowered:
        return "navigation"
    return None


def _trace_categories(trace: list[dict[str, Any]]) -> set[str]:
    categories: set[str] = set()
    for entry in trace:
        categories.update(_extract_categories(json.dumps(entry, ensure_ascii=False)))
        tool_name = str(entry.get("tool") or "")
        for pattern, category in TRACE_TOOL_CATEGORY_HINTS:
            if pattern.search(tool_name):
                categories.add(category)
    return categories


def _is_stubbed(entry: dict[str, Any]) -> bool:
    output = entry.get("output")
    if not isinstance(output, dict):
        return False
    status = str(output.get("status") or "").strip().lower()
    message = str(output.get("message") or "").strip().lower()
    return status == "stubbed" or "debug stub executed" in message


def _tool_names(trace: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for entry in trace:
        tool_name = str(entry.get("tool") or "").strip()
        if tool_name and tool_name != "Skill" and tool_name not in names:
            names.append(tool_name)
    return names


def _format_category_list(categories: list[str]) -> str:
    labels = []
    for category in categories:
        for spec in SIGNAL_PATTERNS:
            if spec.category == category:
                labels.append(spec.label)
                break
        else:
            labels.append(category)
    return " / ".join(labels)


class SkillReviewer:
    def review(
        self,
        *,
        turn: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        skill: dict[str, Any],
        skill_document: str,
        tools: list[dict[str, Any]],
        unregistered_declared_tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        frontmatter, body = _parse_frontmatter(skill_document)
        description = str(frontmatter.get("description") or skill.get("description") or "").strip()
        body_text = body.strip()
        skill_id = str(skill.get("skill_id") or "").strip()
        skill_name = str(skill.get("name") or skill_id).strip()
        trace = list(turn.get("trace") or [])
        assistant_message = str(turn.get("assistant_message") or "").strip()
        current_query = str(turn.get("user_message") or "").strip()
        recent_user_messages = [str(item.get("user_message") or "").strip() for item in recent_turns if item.get("user_message")]
        recent_assistant_messages = [
            str(item.get("assistant_message") or "").strip()
            for item in recent_turns
            if item.get("assistant_message")
        ]
        query_context = "\n".join([*recent_user_messages, current_query]).strip()
        assistant_context = "\n".join([*recent_assistant_messages, assistant_message]).strip()

        query_signal_matches = _extract_signal_matches(query_context)
        description_signal_matches = _extract_signal_matches("\n".join([skill_name, skill_id, description]))
        body_signal_matches = _extract_signal_matches(body_text)
        assistant_signal_matches = _extract_signal_matches(assistant_context)
        trace_signal_matches = _extract_signal_matches(json.dumps(trace, ensure_ascii=False))

        query_categories = set(query_signal_matches)
        description_categories = set(description_signal_matches)
        body_categories = set(body_signal_matches)
        trace_categories = _trace_categories(trace)
        assistant_categories = set(assistant_signal_matches)
        skill_primary_intent = _skill_primary_intent(skill_id, skill_name, description, body_text)
        query_primary = next((intent for intent in PRIMARY_INTENT_ORDER if intent in query_categories), None)
        target_primary_intent = query_primary or skill_primary_intent
        relevant_support = EXPECTED_SUPPORT_BY_INTENT.get(skill_primary_intent or "", set())
        relevant_query_categories = {category for category in query_categories if category in relevant_support}
        if query_primary:
            relevant_query_categories.add(query_primary)
        if not relevant_query_categories and query_primary:
            relevant_query_categories = {query_primary}
        if not relevant_query_categories:
            relevant_query_categories = set(query_categories)

        activated_skills = _skill_activation_ids(trace)
        did_trigger = skill_id in activated_skills or (
            str(turn.get("mode") or "").strip() == "forced"
            and str(turn.get("forced_skill_id") or "").strip() == skill_id
        )
        description_has_trigger_language = not any(
            finding.get("code") == "description_missing_trigger_language"
            for finding in skill.get("lint", {}).get("warnings", [])
        )
        primary_in_description = bool(query_primary and query_primary in description_categories)
        primary_in_instructions = bool(query_primary and query_primary in body_categories)
        should_trigger = False
        if str(turn.get("forced_skill_id") or "").strip() == skill_id:
            should_trigger = True
        elif query_primary and (primary_in_description or primary_in_instructions):
            should_trigger = True
        elif query_primary and skill_primary_intent == query_primary:
            should_trigger = True
        elif not query_primary and did_trigger:
            should_trigger = True

        instruction_missing = sorted(
            category
            for category in relevant_query_categories
            if category not in body_categories
        )
        execution_categories = assistant_categories | trace_categories
        execution_missing = sorted(
            category
            for category in relevant_query_categories
            if category not in execution_categories
        )

        skill_tools = [
            tool
            for tool in tools
            if skill_id in (tool.get("declared_by_skills") or [])
        ]
        skill_tool_names = _unique([str(tool.get("name") or "").strip() for tool in skill_tools])
        missing_declared_tools = [
            item
            for item in unregistered_declared_tools
            if skill_id in (item.get("declared_by_skills") or [])
        ]
        missing_declared_tool_names = _unique(
            [str(item.get("name") or "").strip() for item in missing_declared_tools]
        )
        stubbed_tools = [
            str(entry.get("tool") or "").strip()
            for entry in trace
            if _is_stubbed(entry)
        ]
        trace_errors = [
            str(entry.get("tool") or entry.get("type") or "trace").strip()
            for entry in trace
            if str(entry.get("status") or "").strip() == "error"
        ]
        trace_tool_names = _tool_names(trace)
        tool_count = len(trace_tool_names)
        covered_ratio = (
            1.0
            if not relevant_query_categories
            else (len(relevant_query_categories - set(execution_missing)) / len(relevant_query_categories))
        )

        trigger_fit = 1
        if description:
            trigger_fit += 1
        if description_has_trigger_language:
            trigger_fit += 1
        if primary_in_description:
            trigger_fit += 2
        elif query_primary and skill_primary_intent == query_primary:
            trigger_fit += 1
        trigger_fit = max(1, min(5, trigger_fit))

        instruction_fit = 1
        if body_text:
            instruction_fit += 1
        if query_primary and (primary_in_instructions or primary_in_description):
            instruction_fit += 1
        if relevant_query_categories:
            instruction_fit += round(
                2 * (len(relevant_query_categories) - len(instruction_missing)) / len(relevant_query_categories)
            )
        instruction_fit = max(1, min(5, instruction_fit))

        if not should_trigger:
            execution_fit = 3
        else:
            execution_fit = 1 if not did_trigger else 2
            if did_trigger and tool_count:
                execution_fit += 1
            if covered_ratio >= 0.5:
                execution_fit += 1
            if covered_ratio >= 0.9:
                execution_fit += 1
            if trace_errors:
                execution_fit -= 1
            if stubbed_tools:
                execution_fit -= 1
            execution_fit = max(1, min(5, execution_fit))

        user_fit = 4 if should_trigger else 1
        if not current_query and not recent_user_messages:
            user_fit = 1
        elif len(current_query) < 6 and not recent_user_messages:
            user_fit = min(user_fit, 2)
        elif len(current_query) < 12 and recent_user_messages:
            user_fit = max(user_fit, 4)
        elif len(query_categories) >= 4 and query_primary is None:
            user_fit = min(user_fit, 2)

        findings: list[dict[str, str]] = []
        suggested_edits: list[dict[str, str]] = []

        lint_warnings = skill.get("lint", {}).get("warnings", [])
        frontmatter_warning_messages = [
            str(item.get("message") or "").strip()
            for item in lint_warnings
            if str(item.get("field") or "") == "description"
        ]
        if should_trigger and not did_trigger:
            if trigger_fit <= 3 or frontmatter_warning_messages:
                findings.append(
                    {
                        "type": "frontmatter",
                        "severity": "high",
                        "message": "这条 query 按能力范围看应该触发，但 frontmatter 的触发说明不够清晰，容易 under-trigger。",
                        "evidence": "；".join(frontmatter_warning_messages) or (description or "description 为空"),
                    }
                )
                suggested_edits.append(
                    {
                        "location": "frontmatter.description",
                        "proposal": self._description_edit_proposal(query_primary, query_signal_matches),
                        "reason": "把“什么时候用”写进 description，降低 should trigger 但没触发的风险。",
                    }
                )
            else:
                findings.append(
                    {
                        "type": "runtime",
                        "severity": "high",
                        "message": "frontmatter 已经覆盖这条 query，但本轮 trace 没有看到这个 skill 被真正激活。",
                        "evidence": f"query primary={query_primary or 'unknown'}；activated_skills={activated_skills or ['none']}",
                    }
                )
                suggested_edits.append(
                    {
                        "location": "runtime / trace routing",
                        "proposal": "补一条路由回归测试，确保符合 frontmatter 的 query 一定会产生对应的 skill_activation 记录。",
                        "reason": "先区分路由没命中还是 trace 没写出来。",
                    }
                )

        if did_trigger and instruction_missing:
            findings.append(
                {
                    "type": "instructions",
                    "severity": "high",
                    "message": f"SKILL.md 正文没有显式覆盖这轮 query 的关键点：{_format_category_list(instruction_missing)}。",
                    "evidence": f"query_categories={sorted(relevant_query_categories)}；instructions_categories={sorted(body_categories)}",
                }
            )
            suggested_edits.append(
                {
                    "location": "SKILL.md instructions",
                    "proposal": self._instruction_edit_proposal(instruction_missing),
                    "reason": "把关键约束变成显式步骤，避免 skill 触发了但服务不完整。",
                }
            )

        if did_trigger and execution_missing:
            findings.append(
                {
                    "type": "runtime" if trace_errors else "instructions",
                    "severity": "medium" if not trace_errors else "high",
                    "message": f"Skill 被触发了，但本轮产出里没有充分覆盖：{_format_category_list(execution_missing)}。",
                    "evidence": f"assistant_categories={sorted(assistant_categories)}；trace_categories={sorted(trace_categories)}",
                }
            )

        if missing_declared_tool_names or stubbed_tools:
            evidence_parts: list[str] = []
            if missing_declared_tool_names:
                evidence_parts.append(f"未注册工具: {', '.join(missing_declared_tool_names)}")
            if stubbed_tools:
                evidence_parts.append(f"stub 工具: {', '.join(_unique(stubbed_tools))}")
            findings.append(
                {
                    "type": "tools",
                    "severity": "medium",
                    "message": "工具层存在缺口，review 只能把本轮结论视为部分验证。",
                    "evidence": "；".join(evidence_parts),
                }
            )
            suggested_edits.append(
                {
                    "location": "frontmatter.allowed-tools / workspace tools",
                    "proposal": self._tool_edit_proposal(missing_declared_tool_names, stubbed_tools),
                    "reason": "让 skill 声明、workspace 注册和真实执行能力保持一致。",
                }
            )

        if trace_errors:
            findings.append(
                {
                    "type": "runtime",
                    "severity": "high",
                    "message": "trace 里出现了运行失败，execution gap 不能只归咎于 SKILL.md。",
                    "evidence": ", ".join(trace_errors),
                }
            )

        if not should_trigger:
            findings.append(
                {
                    "type": "user_query",
                    "severity": "high",
                    "message": "这条 query 的主诉求更像是 skill 范围外的问题，不该强行归到这个 skill。",
                    "evidence": f"query_categories={sorted(query_categories)}；skill_primary_intent={skill_primary_intent or 'unknown'}",
                }
            )
            if trigger_fit <= 3:
                suggested_edits.append(
                    {
                        "location": "frontmatter.description",
                        "proposal": "收紧 description 的边界，除了能力描述，也明确不适用的场景。",
                        "reason": "避免 reviewer 和路由都把边界太宽的 query 误判成 should trigger。",
                    }
                )

        findings = self._dedupe_finding_list(findings)
        suggested_edits = self._dedupe_edit_list(suggested_edits)

        if not should_trigger:
            verdict = "out_of_scope"
        elif should_trigger and not did_trigger:
            verdict = "missed_trigger"
        elif findings:
            verdict = "partial"
        else:
            verdict = "good"

        query_signals = self._build_query_signals(
            current_query=current_query,
            recent_user_messages=recent_user_messages,
            signal_matches=query_signal_matches,
        )
        skill_signals = self._build_skill_signals(
            description=description,
            description_signal_matches=description_signal_matches,
            body_signal_matches=body_signal_matches,
            lint_warnings=frontmatter_warning_messages,
        )
        trace_signals = self._build_trace_signals(
            did_trigger=did_trigger,
            skill_id=skill_id,
            activated_skills=activated_skills,
            trace_tool_names=trace_tool_names,
            trace_errors=trace_errors,
            stubbed_tools=stubbed_tools,
        )
        suggested_tests = self._suggested_tests(
            skill_id=skill_id,
            skill_primary_intent=skill_primary_intent,
            query_categories=query_categories,
            should_trigger=should_trigger,
        )
        summary = self._build_summary(
            verdict=verdict,
            skill_id=skill_id,
            did_trigger=did_trigger,
            should_trigger=should_trigger,
            execution_missing=execution_missing,
            instruction_missing=instruction_missing,
            trace_tool_names=trace_tool_names,
        )

        return {
            "review_id": f"rvw_{uuid4().hex[:12]}",
            "created_at": utcnow_iso(),
            "turn_id": str(turn.get("turn_id") or "").strip(),
            "skill_id": skill_id,
            "skill_name": skill_name,
            "verdict": verdict,
            "should_trigger": should_trigger,
            "did_trigger": did_trigger,
            "scores": {
                "trigger_fit": trigger_fit,
                "instruction_fit": instruction_fit,
                "execution_fit": execution_fit,
                "user_fit": user_fit,
            },
            "summary": summary,
            "evidence": {
                "query_signals": query_signals,
                "skill_signals": skill_signals,
                "trace_signals": trace_signals,
            },
            "findings": findings,
            "suggested_edits": suggested_edits,
            "suggested_tests": suggested_tests,
            "rubric_version": "reviewer-mvp-v1",
            "context": {
                "include_recent_turns": bool(recent_turns),
                "recent_turn_ids": [str(item.get("turn_id") or "").strip() for item in recent_turns],
                "workspace_tool_names": skill_tool_names,
                "missing_declared_tool_names": missing_declared_tool_names,
            },
        }

    @staticmethod
    def _build_query_signals(
        *,
        current_query: str,
        recent_user_messages: list[str],
        signal_matches: dict[str, list[str]],
    ) -> list[str]:
        lines: list[str] = []
        for category, matches in signal_matches.items():
            snippet = matches[0]
            if snippet and snippet in current_query:
                lines.append(f"当前 query 提到“{snippet}” -> {category}")
            elif snippet:
                lines.append(f"前文提到“{snippet}” -> {category}")
        if recent_user_messages:
            lines.append(f"带上前 2 轮上下文后，一共参考了 {len(recent_user_messages)} 条历史用户消息。")
        return lines[:6]

    @staticmethod
    def _build_skill_signals(
        *,
        description: str,
        description_signal_matches: dict[str, list[str]],
        body_signal_matches: dict[str, list[str]],
        lint_warnings: list[str],
    ) -> list[str]:
        lines: list[str] = []
        if description:
            lines.append(f"description: {description[:140]}")
        for category, matches in description_signal_matches.items():
            lines.append(f"description 命中 {category}: “{matches[0]}”")
        for category, matches in body_signal_matches.items():
            lines.append(f"instructions 命中 {category}: “{matches[0]}”")
        for message in lint_warnings:
            lines.append(f"lint: {message}")
        return lines[:8]

    @staticmethod
    def _build_trace_signals(
        *,
        did_trigger: bool,
        skill_id: str,
        activated_skills: list[str],
        trace_tool_names: list[str],
        trace_errors: list[str],
        stubbed_tools: list[str],
    ) -> list[str]:
        lines: list[str] = []
        if did_trigger:
            lines.append(f"trace 中看到了 {skill_id} 的触发。")
        elif activated_skills:
            lines.append(f"trace 激活了其他 skill: {', '.join(activated_skills)}")
        else:
            lines.append("trace 中没有 skill_activation。")
        if trace_tool_names:
            lines.append(f"实际执行的工具: {', '.join(trace_tool_names)}")
        if trace_errors:
            lines.append(f"trace error: {', '.join(trace_errors)}")
        if stubbed_tools:
            lines.append(f"stub tools: {', '.join(_unique(stubbed_tools))}")
        return lines[:6]

    @staticmethod
    def _build_summary(
        *,
        verdict: str,
        skill_id: str,
        did_trigger: bool,
        should_trigger: bool,
        execution_missing: list[str],
        instruction_missing: list[str],
        trace_tool_names: list[str],
    ) -> str:
        if verdict == "out_of_scope":
            return f"按当前 query 和最近上下文看，这轮不属于 `{skill_id}` 的主要触发范围。"
        if verdict == "missed_trigger":
            return f"`{skill_id}` 按 SKILL.md 应该被触发，但这次运行里没有真正触发。"
        if verdict == "good":
            if trace_tool_names:
                return f"`{skill_id}` 触发和执行基本匹配这轮 query，trace 里也看到了关键工具调用。"
            return f"`{skill_id}` 触发判断基本正确，这轮主要完成了应做的事情。"
        missing = execution_missing or instruction_missing
        if did_trigger and missing:
            return f"`{skill_id}` 应该被触发，而且确实触发了，但对 `{_format_category_list(missing)}` 的覆盖仍然不完整。"
        if should_trigger:
            return f"`{skill_id}` 触发正确，但执行质量只有部分达标。"
        return f"`{skill_id}` 这轮表现为部分匹配。"

    @staticmethod
    def _description_edit_proposal(
        primary_intent: str | None,
        query_signal_matches: dict[str, list[str]],
    ) -> str:
        trigger_bits: list[str] = []
        if primary_intent == "recommendation":
            trigger_bits.append("找餐厅 / 推荐 / where to eat")
        elif primary_intent == "booking":
            trigger_bits.append("预约 / 订位 / reservation")
        elif primary_intent == "navigation":
            trigger_bits.append("导航 / 路线 / directions")
        for category in ("allergy", "location", "time", "budget", "party_size"):
            if category in query_signal_matches:
                trigger_bits.extend(query_signal_matches[category][:1])
        trigger_bits = _unique(trigger_bits)
        suffix = f"例如：{', '.join(trigger_bits)}" if trigger_bits else "补充具体 trigger phrases。"
        return f"把“做什么 + 什么时候用”写成一句明确的触发描述，{suffix}"

    @staticmethod
    def _instruction_edit_proposal(missing_categories: list[str]) -> str:
        if not missing_categories:
            return "把关键约束拆成显式步骤，并写明缺信息时该如何降级。"
        labels = _format_category_list(missing_categories)
        return f"加入显式流程：先识别 {labels}，再执行对应检查/提示；信息不足时必须明确声明。"

    @staticmethod
    def _tool_edit_proposal(
        missing_declared_tool_names: list[str],
        stubbed_tools: list[str],
    ) -> str:
        parts: list[str] = []
        if missing_declared_tool_names:
            parts.append(f"在 workspace 注册这些工具：{', '.join(missing_declared_tool_names)}")
        if stubbed_tools:
            parts.append(f"把这些只会 stub 的工具换成可验证实现，或在 SKILL.md 里明确它们只是模拟：{', '.join(_unique(stubbed_tools))}")
        return "；".join(parts) or "清理 skill 声明与 workspace 实际工具之间的偏差。"

    @staticmethod
    def _dedupe_finding_list(findings: list[dict[str, str]]) -> list[dict[str, str]]:
        seen: set[tuple[str, str, str]] = set()
        items: list[dict[str, str]] = []
        for finding in findings:
            key = (
                str(finding.get("type") or ""),
                str(finding.get("severity") or ""),
                str(finding.get("message") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            items.append(finding)
        return items[:6]

    @staticmethod
    def _dedupe_edit_list(edits: list[dict[str, str]]) -> list[dict[str, str]]:
        seen: set[tuple[str, str]] = set()
        items: list[dict[str, str]] = []
        for edit in edits:
            key = (str(edit.get("location") or ""), str(edit.get("proposal") or ""))
            if key in seen:
                continue
            seen.add(key)
            items.append(edit)
        return items[:4]

    @staticmethod
    def _suggested_tests(
        *,
        skill_id: str,
        skill_primary_intent: str | None,
        query_categories: set[str],
        should_trigger: bool,
    ) -> list[dict[str, str]]:
        primary = skill_primary_intent or "generic"
        if primary == "recommendation":
            should_query = "我花生过敏，帮我在旧金山找一家适合今晚的餐厅，并标出风险。"
            edge_query = "帮我推荐 3 个人、预算高端、适合晚餐的餐厅。"
            should_not_query = "帮我把明天下午的会议记到日历里。"
        elif primary == "booking":
            should_query = "帮我预约 Gary Danko，明晚 7 点 3 个人，我花生过敏。"
            edge_query = "给这家餐厅打电话订位，人数 2 人，先提醒我营业时间。"
            should_not_query = "帮我推荐一家旧金山的高端美式餐厅。"
        elif primary == "navigation":
            should_query = "带我导航去 Gary Danko。"
            edge_query = "从 downtown 去 Gary Danko，给我 Google Maps 路线。"
            should_not_query = "帮我直接订位，明晚 7 点 2 人。"
        else:
            should_query = f"请执行和 `{skill_id}` 明确匹配的典型用户请求。"
            edge_query = f"请执行和 `{skill_id}` 部分匹配、需要补信息的请求。"
            should_not_query = "帮我写一首和工作无关的短诗。"

        if "allergy" in query_categories and primary in {"recommendation", "booking"}:
            edge_query = edge_query.replace("。", "，并明确处理过敏约束。")

        expected_positive = "should_trigger" if should_trigger else "should_trigger_candidate"
        return [
            {"query": should_query, "expected": expected_positive},
            {"query": edge_query, "expected": "should_trigger_and_collect_missing_fields"},
            {"query": should_not_query, "expected": "should_not_trigger"},
        ]
