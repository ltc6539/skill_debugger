from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from skill_debugger.reviewer import SkillReviewer
from skill_debugger.skill_linter import lint_skill_package
from skill_debugger.skill_registry import UploadedSkillRegistry
from skill_debugger.store import WorkspaceStore


RECOMMENDATION_SKILL = """---
name: restaurant-recommendation
description: 基于用户的位置、时间、人数、预算、健康状态和口味偏好，从 Yelp 和 Google Maps 中筛选并推荐最适合的餐厅，以动态卡片形式呈现。
allowed-tools: [YELP_SEARCH_BUSINESSES, REQUEST_CARD_INPUT]
---

# Restaurant Recommendation

1. 先确认位置、时间、人数、预算。
2. 如用户提到过敏或忌口，必须提示风险。
3. 然后推荐餐厅并给出下一步引导。
"""

BOOKING_SKILL = """---
name: restaurant-booking
description: 当用户要预约餐厅、订位、打电话订餐或确认 reservation 时使用，收集预约信息并生成电话话术。
allowed-tools: [twilio_call_restaurant]
---

# Restaurant Booking

1. 收集 restaurant / date / time / party_size。
2. 如果用户提到过敏，必须在话术里包含过敏说明。
3. 用户确认后才能调用 twilio_call_restaurant。
"""


def build_skill_payload(skill_id: str, content: str) -> dict:
    meta = UploadedSkillRegistry.parse_skill_text(content, fallback_name=skill_id)
    payload = meta.to_dict()
    payload["lint"] = lint_skill_package(skill_id, {"SKILL.md": content.encode("utf-8")}, source_kind="folder").to_dict()
    return payload


class ReviewerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reviewer = SkillReviewer()
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = WorkspaceStore(Path(self.tempdir.name) / "workspaces")
        self.workspace = self.store.create_workspace("review-mvp")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_review_marks_triggered_turn_with_stubbed_tool_as_partial(self) -> None:
        review = self.reviewer.review(
            turn={
                "turn_id": "turn_1",
                "mode": "agent",
                "forced_skill_id": None,
                "user_message": "帮我预约 Gary Danko，明晚 7 点 3 个人，我花生过敏。",
                "assistant_message": "我已准备好为你拨打电话并播报预约信息。",
                "trace": [
                    {
                        "trace_id": 1,
                        "type": "tool_call",
                        "tool": "Skill",
                        "status": "ok",
                        "input": {"skill": "restaurant-booking"},
                        "output": {"status": "completed_without_tool_result", "inferred": True},
                        "category": "skill_activation",
                        "skills": ["restaurant-booking"],
                    },
                    {
                        "trace_id": 2,
                        "type": "tool_call",
                        "tool": "mcp__skill_debugger__twilio_call_restaurant",
                        "status": "ok",
                        "input": {"confirmed": True},
                        "output": {
                            "status": "stubbed",
                            "message": "Debug stub executed. No production backend call was made.",
                        },
                    },
                ],
            },
            recent_turns=[],
            skill=build_skill_payload("restaurant-booking", BOOKING_SKILL),
            skill_document=BOOKING_SKILL,
            tools=[
                {
                    "name": "twilio_call_restaurant",
                    "execution_mode": "stub",
                    "declared_by_skills": ["restaurant-booking"],
                }
            ],
            unregistered_declared_tools=[],
        )

        self.store.save_review(self.workspace["workspace_id"], review)

        self.assertEqual(review["skill_id"], "restaurant-booking")
        self.assertTrue(review["should_trigger"])
        self.assertTrue(review["did_trigger"])
        self.assertEqual(review["verdict"], "partial")
        self.assertTrue(any(item["type"] == "tools" for item in review["findings"]))
        saved = self.store.list_reviews(self.workspace["workspace_id"])
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["review_id"], review["review_id"])

    def test_review_marks_should_trigger_but_not_triggered_as_missed_trigger(self) -> None:
        review = self.reviewer.review(
            turn={
                "turn_id": "turn_2",
                "mode": "agent",
                "forced_skill_id": None,
                "user_message": "帮我推荐一家旧金山的晚餐餐厅。",
                "assistant_message": "你想吃什么菜系？",
                "trace": [],
            },
            recent_turns=[],
            skill=build_skill_payload("restaurant-recommendation", RECOMMENDATION_SKILL),
            skill_document=RECOMMENDATION_SKILL,
            tools=[],
            unregistered_declared_tools=[],
        )

        self.assertEqual(review["skill_id"], "restaurant-recommendation")
        self.assertTrue(review["should_trigger"])
        self.assertFalse(review["did_trigger"])
        self.assertEqual(review["verdict"], "missed_trigger")
        self.assertTrue(any(item["type"] in {"frontmatter", "runtime"} for item in review["findings"]))

    def test_review_marks_out_of_scope_query(self) -> None:
        review = self.reviewer.review(
            turn={
                "turn_id": "turn_3",
                "mode": "agent",
                "forced_skill_id": None,
                "user_message": "帮我写一首关于春天的短诗。",
                "assistant_message": "当然，我来写一首诗。",
                "trace": [],
            },
            recent_turns=[],
            skill=build_skill_payload("restaurant-booking", BOOKING_SKILL),
            skill_document=BOOKING_SKILL,
            tools=[],
            unregistered_declared_tools=[],
        )

        self.assertEqual(review["skill_id"], "restaurant-booking")
        self.assertFalse(review["should_trigger"])
        self.assertFalse(review["did_trigger"])
        self.assertEqual(review["verdict"], "out_of_scope")
        self.assertTrue(any(item["type"] == "user_query" for item in review["findings"]))
        self.assertTrue(any(item["expected"] == "should_not_trigger" for item in review["suggested_tests"]))


if __name__ == "__main__":
    unittest.main()
