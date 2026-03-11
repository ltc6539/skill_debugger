---
name: context-collection
description: 所有需要个人化信息的场景的前置 Skill。从 Calendar、HealthKit、用户画像中聚合上下文，注入后续 Skill 的执行环境。
user-invocable: false
allowed-tools: [get_user_profile]
legacy_id: SKILL_01
license: MIT
---

# Context Collection Skill

本 Skill 负责在业务执行前收集并合并所有必要的用户上下文。**输出结果注入当前会话，供所有后续 Skill 直接读取，不得重复调用工具。**

## Data Collection

**从用户画像读取静态偏好：**（调用 `get_user_profile`）

```
profile = {
  location, cuisine_prefs, disliked_foods,
  allergies, budget_per_meal, min_rating,
  diet_tags, health_labels
}
```

**从 HealthKit 读取动态健康数据：**（`get_health_data` 暂未接入，直接跳过，走降级策略）

**从 Calendar 读取日程信息：**（`get_calendar_events` 暂未接入，直接跳过，走降级策略）

## Merge & Inject

将三个来源合并为统一的 `merged_context` 对象，附加到本次会话上下文尾部：

```
merged_context = {
  ...profile, ...health_context, ...calendar_context,
  current_location: 从用户消息或画像解析,
  session_intent:   由当前用户消息推断
}
```

CRITICAL: 若用户已在消息中明确提供位置、人数或时间，**以用户输入为准，不覆盖**。

## Missing Data Protocol

若关键字段无法从任何来源获取，用**一句话**向用户补充确认：
> "今晚几个人、大概在哪个区域？"

**一次最多询问 2 个字段，合并在一句话里。** NEVER 逐项单独询问。

## Graceful Degradation

| 数据缺失 | 降级策略 |
|---|---|
| HealthKit 未授权 | 用 `diet_tags` 替代数值约束，不报错不中断 |
| Calendar 未授权 | 跳过场合推断，默认"日常用餐"场景 |
| 用户画像为空（新用户）| 跳过画像注入，流程完成后引导用户完善偏好 |
| 位置未知 | 先询问，或使用"上次已知位置"兜底 |

IMPORTANT: 任何单个数据源失败都**不能**中断整个 Skill 的执行。
