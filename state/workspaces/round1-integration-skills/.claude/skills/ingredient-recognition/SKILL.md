---
name: ingredient-recognition
description: 通过 VLM 识别用户上传的冰箱/食材照片，输出结构化食材清单，为菜谱规划和购物清单提供输入。与食物记录（food-logging）共用图片入口，但处理逻辑和输出完全不同。
user-invocable: true
allowed-tools: [get_user_profile, REQUEST_CARD_INPUT]
legacy_id: SKILL_05
license: MIT
---

# Ingredient Recognition Skill

## Intent Disambiguation

**首先判断图片用途，再执行识别：**

- 用户说"帮我看看有什么食材/能做什么" → 执行本 Skill
- 用户说"刚吃了这个/记录一下" → 转交 **food-logging**
- 无法判断 → 用一句话确认：
  > "这是要记录饮食，还是看看有什么食材可以用？"

NEVER 在未确认意图的情况下同时执行两个 Skill。

## Recognition Strategy

**图片已由系统在消息进入前完成 VLM 识别，识别结果以 `[图片内容识别：...]` 格式注入消息上下文。**
直接从消息上下文中读取识别描述，无需再调用 `recognize_image`。

**按置信度分级处理：**

```
recognized = [
  { name: "鸡蛋",    quantity: "6个",   confidence: "high"   },
  { name: "番茄",    quantity: "约3个", confidence: "high"   },
  { name: "豆腐",    quantity: "1块",   confidence: "medium" },
  { name: "不明蔬菜", quantity: "少量", confidence: "low"    }
]
```

- `high`：直接采用
- `medium`：保留但标注待确认
- `low`：向用户询问，**每次最多询问 1 个模糊项**：
  > "还有一样不确定，看起来像青椒或西葫芦，是哪个？"

**多张照片**：合并识别结果，同一食材叠加数量。

## Allergy Check

调用 `get_user_profile` 获取 `allergies`。

CRITICAL: 若识别出过敏原食材，**必须立即显示警告**，不得等到后续步骤：
> "⚠️ 识别到花生，你标记过对花生过敏，请注意。"

## Context Output

将确认后的食材清单写入会话上下文，供后续 Skill 直接读取：

```
ingredient_inventory = {
  available:         [ { name, quantity }, ... ],
  flagged_allergens: [ ... ],
  scan_timestamp:    now()
}
```

IMPORTANT: meal-planning 和 shopping-list **必须从此对象读取**，不得重新调用 `recognize_image`。

## Card Generation

调用 `REQUEST_CARD_INPUT` 输出食材库存卡片：

```
card = {
  sections: [
    { label: "已确认食材", items: high/medium 列表 },
    { label: "需确认",     items: low 列表（可点击修正）}
  ],
  actions: [
    { label: "用这些食材做饭", skill: "meal-planning" },
    { label: "看看还缺什么",   skill: "shopping-list" }
  ]
}
```

## Graceful Degradation

| 情况 | 处理 |
|---|---|
| 图片模糊无法识别 | 告知并给出重新拍摄建议（光线/正面角度）|
| 全部为低置信度 | 展示初步结果，提示逐项确认或手动输入 |
| 识别出非食物内容 | 静默忽略，不显示、不报错 |
