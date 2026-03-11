---
name: restaurant-recommendation
description: 基于用户的位置、时间、人数、预算、健康状态和口味偏好，从 Yelp 和 Google Maps 中筛选并推荐最适合的餐厅，以动态卡片形式呈现。
user-invocable: true
allowed-tools: [get_user_profile, YELP_SEARCH_BUSINESSES, YELP_SEARCH_AND_CHAT, YELP_GET_BUSINESS_DETAILS, YELP_GET_BUSINESS_REVIEWS, YELP_GET_REVIEW_HIGHLIGHTS, places_text_search, places_details, log_preference_event, twilio_call_restaurant, REQUEST_CARD_INPUT, test]
legacy_id: SKILL_02
license: MIT
---

# Restaurant Recommendation Skill

本 Skill 在 context-collection 完成后执行，`merged_context` 必须已注入会话。

## CRITICAL: Preference Fetch Before Recommendation

在执行任何餐厅推荐任务前，必须先调用 `get_user_profile` 获取用户偏好，再开始搜索与排序。

强制顺序：
1) 调用 `get_user_profile`
2) 合并 `user_profile` 与 `merged_context`（用户本轮明确输入优先）
3) 再调用 `YELP_SEARCH_BUSINESSES` / `places_text_search` 等搜索工具

NEVER 跳过第 1 步直接推荐；若 `get_user_profile` 失败，使用 `merged_context` 继续，并明确提示“已按当前对话信息推荐，历史偏好读取失败”。

## Search Strategy

**构建搜索参数**（先 `get_user_profile`，再从 `merged_context + user_profile` 提取）：

```
search_params = {
  location:       current_location,          # 或 latitude + longitude
  term:           cuisine_prefs[0] 或用户本次指定菜系,
  price:          budget_to_yelp_price(budget_per_meal),  # 1~4档，逗号分隔
  open_at:        next_meal_window 开始时间戳,
  sort_by:        "best_match"（默认）/ "rating"（用户要求高分时）,
  radius:         步行场景 500m / 驾车场景 3km
}
```

优先调用 `YELP_SEARCH_BUSINESSES` 取前 20 条，再调用 `places_text_search` 补充 Google Maps 数据源。

NOTE: Yelp 在部分地区不支持（如中国、印度等），若出现 LOCATION_NOT_FOUND 或无结果：
1) 优先改用经纬度（latitude/longitude）重试；
2) 仍无结果则直接用 Google Maps 数据源。

## Filtering Rules

**硬过滤（NEVER 保留违反以下规则的候选）：**
- 排除菜系标签明确含有 `allergies` 食材的餐厅
- 排除不符合 `diet_tags` 的餐厅（如素食者过滤无素食选项的餐厅）
- 评分低于 `min_rating` 的餐厅直接过滤

**软过滤（降权而非排除）：**
- `disliked_foods` 为主打的餐厅降权
- 过敏原无法从 Yelp 精确过滤时，**保留候选并在卡片上显示警告**，由用户判断

## Health-Aware Ranking

基于 `remaining_calories` 和 `health_labels` 对候选软排序：

- 今日热量剩余 < 400kcal → 优先轻食、沙拉、粤菜、日料
- 高血压标签 → 重口/高钠菜系（川菜、烧烤）降权
- 商务场合 → 优先安静环境、评分 ≥ 4.0、有包厢
- 约会场合 → 优先氛围感、评分 ≥ 4.2

## Nutrition Estimation

对 Top 5 候选调用 `YELP_GET_BUSINESS_DETAILS` 获取营业时间/电话/照片等；如需菜单再调用 `places_details`。
然后**用 LLM 内部知识**对菜单做营养粗估：

IMPORTANT: 餐厅菜品热量估算误差约 **±25%**，仅用于辅助筛选，不得作为精确医疗依据。在卡片上必须注明误差范围。

## Card Generation

调用 `REQUEST_CARD_INPUT` 生成 Top 3 推荐卡片，每张包含：

- 餐厅名、评分、价位、菜系、距离
- `highlight`：一句个性化推荐理由（结合用户上下文生成，NEVER 用模板套话）
- `health_note`：营养适配说明或热量参考
- `allergy_warning`：若存在过敏风险必须显示
- 操作按钮：查看详情 / 预约位置（→ restaurant-booking）/ 一键电话预订（Twilio）/ 开始导航（→ navigation）

## Flow Handoff (Chain Continuation)

场景一不是“只推荐就结束”，而是链式流程：

`restaurant-recommendation -> restaurant-booking -> navigation`

执行规则：

1. 推荐结果给出后，必须给出明确下一步引导（而不是泛化收尾）：
   > "你选 1/2/3 哪家？我可以直接继续帮你订位，然后给你导航。"
2. 用户一旦选定餐厅（即使未说“订位”），应立即进入 `restaurant-booking`，补齐订位必填字段。
3. 用户明确说“直接去/怎么去/带我去”时，进入 `navigation`（可跳过订位）。
4. 若用户只说“先看看”，保留候选并询问一个最小决策问题（例如人数或大概时间），不要结束链路。

## Direct Phone Booking (Twilio)

当用户在推荐列表中明确说“就这家，直接打电话帮我订位”时，可直接调用 `twilio_call_restaurant`。

CRITICAL：`twilio_call_restaurant` 是不可逆外呼操作，必须先用一句话确认：
> "我可以现在给这家餐厅自动拨号并播报订位信息，确认现在拨打吗？"

仅在用户明确同意后调用，并设置 `confirmed=true`。若缺少订位时间、人数、姓名、回拨电话，先补齐再外呼。

## Review Signals (Optional)

需要更细致口碑时，可对 Top 3 调用 `YELP_GET_BUSINESS_REVIEWS`，提炼 1 句摘要加入 `highlight`。
若可用 Premium 计划，可用 `YELP_GET_REVIEW_HIGHLIGHTS` 获取主题高亮；失败时静默跳过。

## Preference Feedback Loop

- 用户点击"预约"或"导航" → 调用 `log_preference_event(preference_type="restaurant", value=restaurant_name, polarity="select")`
- 用户点击"换一批" → 调用 `log_preference_event(preference_type="restaurant", value=restaurant_name, polarity="skip")`，排除后重新搜索

## Graceful Degradation

| 情况 | 处理 |
|---|---|
| 搜索结果为空 | 扩大半径或放宽价位重试一次；仍为空则建议外卖或调整条件 |
| 所有结果不符合健康约束 | 放宽健康筛选，在卡片注明"不完全符合今日饮食目标" |
| 菜单数据缺失 | 跳过营养粗估，不显示 `health_note` |
| 用户否定全部推荐 | 询问不满意原因，更新偏好事件，调整参数重推 |
