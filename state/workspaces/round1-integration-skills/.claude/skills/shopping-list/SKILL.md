---
name: shopping-list
description: 汇总菜谱缺口食材，按超市分区排序生成结构化购物清单；在 demo 中可调用 MealMe mock 工具完成“搜索-选品-下单-查单”。
user-invocable: true
allowed-tools: [get_user_profile, REQUEST_CARD_INPUT, mealme_search_store_v3, mealme_search_product_v4, mealme_order_create_v4, mealme_order_finalize, mealme_get_orders]
legacy_id: SKILL_08
license: MIT
---

本 Skill 的核心价值是**把“还差什么”一次性回答清楚，并可直接推进到下单**。在 demo 环境中，使用 MealMe mock 工具模拟真实买菜链路，便于训练模型的工具调用能力。

## Shopping List Aggregation

从以下来源合并缺货食材，按优先级读取：

1. `selected_plan.missing_ingredients`（meal-planning 传入）
2. 用户本次消息中直接提到的食材
3. ingredient-recognition 识别时标注的"即将用完"项

去重后生成 `shopping_list`，基于 LLM 知识补充每项的建议采购量（根据菜谱份量计算）和简短采购备注（如"买去皮无骨的更方便"）。

## Category Sorting

按超市分区顺序排列，减少用户来回走动：

**蔬果区 → 肉蛋区 → 豆制品 → 主食粮油 → 调料区 → 冷冻区**

## Allergy Check

调用 `get_user_profile` 检查 `allergies`，若某食材有过敏风险，在清单中标注 ⚠️。

**CRITICAL**：过敏原标注必须显著可见，不能混在普通备注里。

## Plain Text Export

生成可直接粘贴到任意买菜 App 的纯文本格式：

```
【今日购物清单】
蔬果区：番茄×3个、生菜×1颗
肉蛋区：鸡胸肉约300g、鸡蛋×4个
调料区：生抽、老抽各一小瓶（若无）
```

## Card Generation

调用 `REQUEST_CARD_INPUT`，包含按分区分组的食材列表（各项可勾选标记已购）和操作按钮：复制清单 / 📦 一键下单（TODO）/ 清单已买好（→ cooking-guide）。

## Grocery Order Interface (MealMe Mock)

当用户明确要求“直接下单”时，按以下顺序调用：

1. `mealme_search_store_v3`（筛可配送门店）
2. `mealme_search_product_v4`（匹配商品）
3. `mealme_order_create_v4(place_order=false)`（先给最终报价）
4. 用户确认后 `mealme_order_finalize`（提交订单）
5. `mealme_get_orders`（回查订单状态）

**IMPORTANT**：`mealme_order_finalize` 是不可逆动作，必须先展示价格并确认，**NEVER** 自动触发。

## Fallback

- 用户未选定菜谱 → 询问想做什么，引导回 meal-planning
- 食材全部齐全 → 直接触发 cooking-guide，不展示空清单
- 用户补充额外食材 → 追加到对应分区，重新生成卡片
