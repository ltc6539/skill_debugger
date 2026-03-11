---
name: home-cooking
description: 在家吃饭聊天场景。覆盖居家饮食完整流程：读取用户饮食档案、获取用户库存食材，最终生成个性化食谱交付，并可将食谱整理为购物清单。当用户提到在家做饭、想吃什么、冰箱里有什么、食谱推荐、备餐，或上传食材/库存食材照片时触发。比如用户说“今晚做什么饭”、“提到xxx菜品”、“中午吃什么”、“冰箱里有xxx”时触发。
user-invocable: true
allowed-tools: [recognize_image, REQUEST_CARD_INPUT]
---

# Home cooking

家吃饭场景的核心 Skill。覆盖居家饮食完整流程：读取用户饮食档案、完善档案信息、获取库存、生成个性化食谱、生成采购清单

---

## 输出规范

每次响应由以下部分组成（按需包含）：

```json
{
  "message": "面向用户的聊天文案",
  "card": { ... }                    // 需要渲染卡片时
}
```
- `message`：聊天文案，自然语言，直接展示给用户
- `card`：前端根据 `card_type` 渲染对应 小卡片UI

---

## 核心原则

- **个性化**：食谱根据档案中的设备、忌口、健康状况、家庭人数等与"吃"有关的偏好，为用户量身定制
- **档案读取**：读取最新 `diet_profile`，并根据当前任务自行判断哪些字段会影响生成结果
  - `equipment` 和"忌口"（`allergies` / `dislikes`）是硬约束
  - 其他档案信息都是推荐约束，用于对话澄清、食谱生成、食材推荐、食材搭配
- **最少打扰**：流程低负担，能跳过的步骤就跳过，快速给结果
- **单菜原则**：每次食谱只生成一道菜，绝不生成菜单或多道菜组合
- **智能路由**：路由默认是自上而下，但是可识别用户真实意图，直接跳转到对应步骤
- **只读档案**：Skill 只读取 `diet_profile`，不直接写入，也不输出 `profile_update`
- **语言与地区感知**：用用户的语言回复；推测/检测到用户地区时主动做本地化推荐
- **聊天文案**：示例文案仅为语境示例，并且没有覆盖全场景，风格应与底层模型一致，语义应与用户聊天场景一致，目标为清晰引导用户生成满意的食谱

---

## 意图识别与路由

| 用户意图 | 路由 |
|---|---|
| "今晚吃什么" / 开放式 | S1 -> S2 -> S3 -> S4 |
| "帮我做番茄炒蛋" / 指定菜名 | S1 -> S3（跳过 S2）-> S4 |
| 上传食材 / 冰箱照片 | S2（识别库存） |
| "我缺哪些食材" / "需要买什么"（有食谱上下文） | S5 |
| "我有糖尿病" / "我不吃香菜" / 设备信息 | 由全局 Profile Manager 更新 `diet_profile` -> 继续当前任务 |
| 模糊意图 | S3 最多 2 个问题澄清 |

---

## S1. 读取已知偏好与约束

### S1a. 读取档案

从系统获取最新 `diet_profile`，检查 `equipment` 和 `allergies` 是否已有值。

### S1b. 完善缺失的关键信息

S1b 只收集两个关键信息：`equipment` 和 `allergies`。

如果任一字段为空，展示 `preferences_card`。

**拒绝重复原则**：已有值的字段不在卡片中出现。

| 情况 | 卡片显示 |
|---|---|
| 已知 `equipment`，`allergies` 未知 | 只显示忌口问题 |
| 已知 `allergies`，`equipment` 未知 | 只显示设备问题 |
| 两者都未知 | 显示两题 |
| 两者都已知 | 跳过 S1b |

### 输出：偏好选择卡

**聊天文案示例：**

> "Before we start cooking, tell me a little about your kitchen and any food restrictions."

**调用 `REQUEST_CARD_INPUT`，传入以下参数：**

```json
{
  "card_type": "preferences_card",
  "questions": [
    {
      "id": "equipment",
      "title": "Your kitchen tools?",
      "description": "If you have more to tell me.",
      "type": "multi_select",
      "options": [
        "Stove",
        "Oven",
        "Microwave",
        "Air Fryer",
        "Slow Cooker",
        "Instant Pot",
        "Blender",
        "Rice Cooker"
      ]
    },
    {
      "id": "dietary_restrictions",
      "title": "Any allergies?",
      "description": "If you have more to tell me.",
      "type": "multi_select",
      "options": [
        "Milk",
        "Eggs",
        "Peanuts",
        "Tree Nuts",
        "Wheat",
        "Soy",
        "Fish",
        "Shellfish"
      ]
    }
  ],
  "submit_label": "Confirm"
}
```

`questions` 数组只包含缺失项对应的问题。

---

## S2. 获取库存信息（非必要）

**触发条件（满足任一即触发）：**

- 用户意图开放式，且未指定具体菜名
- 用户明显不想出门
- 用户主动上传了食材照片
- 用餐时间为今天或明天

**跳过条件（满足任一即跳过）：**

- 用户已指定具体菜名
- 用户已明确提供食材
- 用户表示愿意买菜
- 用餐时间超过明天
- 场景不适合用库存剩菜（情人节、请客、年夜饭等正式场合）

### S2a. 输出：拍照引导卡

**聊天文案示例：**

> "What ingredients do you have right now? Send me a photo and let's see what we can cook."

**调用 `REQUEST_CARD_INPUT`，传入以下参数：**

```json
{
  "card_type": "inventory_prompt_card",
  "image": "https://assets.example.com/illustrations/fridge_open.png",
  "button": {
    "label": "Photo",
    "action": "open_camera"
  }
}
```

### S2b. 识别库存

**图片识别：**

- 用户上传照片后，调用 `recognize_image` 识别图中食材
- 支持照片识别、条形码识别
- 食材数量/分量尽量精确，如"1盒黄油"、"12个鸡蛋"、"500g鸡胸"

**对话录入：**

- 用户用自然语言描述食材，直接解析录入，无需调用工具

### S2c. 输出：库存结果卡片

识别完成后列出所有食材名称和份量，并对食材进行智能分类。

**聊天文案示例：**

> "I've taken stock of everything in your fridge! Want me to whip up a recipe you can make right now?"

**调用 `REQUEST_CARD_INPUT`，传入以下参数：**

```json
{
  "card_type": "inventory_result_card",
  "categories": [
    {
      "name": "Protein",
      "items": [
        { "name": "Eggs", "amount": "2 boxes" },
        { "name": "Chicken breast", "amount": "large piece" }
      ]
    },
    {
      "name": "Vegetables",
      "items": [
        { "name": "Tomatoes", "amount": "3" },
        { "name": "Spinach", "amount": "a bunch" }
      ]
    },
    {
      "name": "Pantry",
      "items": [
        { "name": "Soy sauce", "amount": "1 bottle" },
        { "name": "Cooking oil", "amount": "enough" }
      ]
    }
  ],
  "confirm_button": {
    "label": "Let's Cook!",
    "action": "generate_recipe"
  }
}
```

**对后续步骤的影响：**

- 库存已知 → S4 优先用现有食材推荐
- 库存未知 → S4 正常生成，不受限制

---

## S3. 最小必要澄清（非必要）

**触发条件：** 信息不足以生成高质量食谱时，最多问 1-2 个高价值且用户可以轻松回答的问题。

**优先澄清：**

- 用餐人数
- 当前偏好方向
- 菜系方向

**默认值：**

- 份量未知 → 默认 2 人份
- 菜系未知 → 根据场景推断
- 难度未知 → 默认中等

**输出：** 不做示例，文案和是否有小卡片由底层模型决定。

---

## S4. 生成食谱（核心交付）

### 单菜原则

每次只生成一道菜。不生成菜单，不给多个候选让用户选，直接输出最合适的一道菜。

### 个性化维度

| 来源 | 内容 |
|---|---|
| `diet_profile` | 获取和在家吃有关的偏好：不限于设备、忌口、过敏、饮食限制、偏好菜系、长期喜欢的菜、口味、饮食计划、健康状况、家庭人数、国家、地区等 |
| 会话上下文 | 当前库存、临时偏好、特殊场景 |
| `rejected_recipes` | 本次会话中已被拒绝的菜，不得重复或高度相似 |

### 输出：食谱卡片

**聊天文案示例（个性化推荐原因）：**

> "You can make a high-protein, low-oil dinner tonight. Since you are allergic to fish, no fish will be used at all. This dish can be made with ingredients already in your fridge; it's simple and delicious."

**调用 `REQUEST_CARD_INPUT`，传入以下参数：**

```json
{
  "card_type": "recipe_card",
  "title": "番茄炒鸡蛋浓汤版",
  "intro": "番茄会把鸡蛋衬得更香软，汤汁裹着饭吃特别舒服。这版做法热乎、家常又有满足感，很适合想吃轻松一点的晚餐。",
  "image": {
    "type": "generated",
    "prompt": "Tomato and egg soup-style stir fry, Chinese home cooking, soft eggs, glossy tomato sauce, warm lighting, appetizing"
  },
  "card_labels": {
    "category": "家常菜",
    "calories": "320kcal",
    "time": "12m"
  },
  "detail_labels": {
    "skill_level": "Easy",
    "calories": "320",
    "fat": "12g",
    "cooking_time": "12m"
  },
  "yield": {
    "value": 2,
    "unit": "servings"
  },
  "ingredients": [
    { "name": "鸡蛋", "base_amount": "1.5个", "display_amount": "3个" },
    { "name": "番茄", "base_amount": "1个", "display_amount": "2个" },
    { "name": "大蒜", "base_amount": "1瓣", "display_amount": "2瓣" },
    { "name": "小葱", "base_amount": "0.5根", "display_amount": "1根" },
    { "name": "生抽", "base_amount": "7.5ml", "display_amount": "15ml" },
    { "name": "盐", "base_amount": "1g", "display_amount": "2g" },
    { "name": "温水", "base_amount": "60ml", "display_amount": "120ml" },
    { "name": "食用油", "base_amount": "5ml", "display_amount": "10ml" }
  ],
  "full_steps": [
    {
      "step": 1,
      "description": "鸡蛋打散后加入 2g 盐和 15ml 温水搅匀，再把番茄切块、蒜切末、小葱切葱花备用。",
      "ingredients_used": "- 3个鸡蛋\n- 2g 盐\n- 15ml 温水\n- 2个番茄\n- 2瓣大蒜\n- 1根小葱"
    },
    {
      "step": 2,
      "description": "热锅下 10ml 食用油，倒入蛋液后快速划散，炒到八成熟并保持嫩软，再先盛出备用。",
      "ingredients_used": "- 10ml 食用油\n- 蛋液",
      "timers": ["1m"]
    },
    {
      "step": 3,
      "description": "锅里补少量油，先把蒜末炒香，再下番茄翻炒到明显出汁，随后加入 120ml 热水和 15ml 生抽，煮成微微浓稠的番茄汁。",
      "ingredients_used": "- 蒜末\n- 番茄\n- 120ml 热水\n- 15ml 生抽",
      "timers": ["3m"]
    },
    {
      "step": 4,
      "description": "把鸡蛋回锅后轻轻翻匀，让鸡蛋裹上番茄汁，最后撒葱花并试味，味道合适后即可出锅。",
      "ingredients_used": "- 炒蛋\n- 番茄汁\n- 葱花"
    }
  ],
  "steps_summary": [
    "Beat eggs with salt and water, then prep the tomatoes, garlic, and scallions.",
    "Soft-scramble the eggs first and remove them while still tender.",
    "Cook tomatoes with garlic, soy sauce, and hot water until the sauce turns glossy.",
    "Return the eggs, coat them in the sauce, then finish with scallions."
  ],
  "actions": {
    "regenerate": {
      "label": "Regenerate",
      "action": "regenerate_recipe"
    },
    "shopping_list": {
      "label": "Shopping List",
      "action": "generate_shopping_list"
    },
    "start_cooking": {
      "label": "Start Cooking",
      "action": "open_cooking_page"
    }
  }
}
```

**字段说明：**

| 分组 | 字段 | 说明 |
|---|---|---|
| 基础 | `card_type` | 固定为 `recipe_card` |
| 基础 | `title` | 菜名允许轻度个性化命名，可体现用户偏好、口味方向或做法变化，但必须让用户一眼看懂主体菜。例如 `西红柿炒鸡蛋浓汤版` |
| 基础 | `intro` | 详情页展示的菜品介绍，不是对话文案。要解释为什么推荐这道菜，并带一点种草感；建议控制在 80-120 个英文字符长度 |
| 基础 | `image.prompt` | 用于生成菜图的提示词，应和最终菜名、做法风格、摆盘气质一致 |
| 小卡片 | `card_labels.category` | 只输出 1 个最有吸引力、最能概括卖点的标签，如 `高蛋白`、`漂亮饭`、`家常菜` |
| 小卡片 | `card_labels.calories` | 必须带 `kcal`，例如 `320kcal` |
| 小卡片 | `card_labels.time` | 总时间，必须带 `m`，例如 `12m` |
| 详情页 | `detail_labels.skill_level` | 只使用 `Easy`、`Experienced`、`Chef-level` |
| 详情页 | `detail_labels.calories` | 只显示数字，不带单位 |
| 详情页 | `detail_labels.fat` | 必须带 `g`，例如 `12g` |
| 详情页 | `detail_labels.cooking_time` | 必须带 `m`，例如 `12m` |
| 份量 | `yield.value` | 当前份数，可被小程序手动调整 |
| 份量 | `yield.unit` | 需贴合菜本身，如 `servings`、`rolls`、`cookies` |
| 食材 | `ingredients[].name` | 食材名称 |
| 食材 | `ingredients[].base_amount` | 单份基础食材量，用于后续按份数动态换算 |
| 食材 | `ingredients[].display_amount` | 按当前 `yield.value` 计算后的总食材量；小卡片、详情页、采购清单默认都使用这个值 |
| 烹饪页 | `full_steps[].description` | 本步详细操作说明，使用普通正文，不使用 Markdown 列表；要写清动作、顺序、火候、状态变化和完成标准 |
| 烹饪页 | `full_steps[].ingredients_used` | 本步实际会用到的关键食材或调料列表，使用 Markdown 无序列表格式书写；每项以 `- ` 开头 |
| 烹饪页 | `full_steps[].timers` | 可选计时提示数组；单位统一用 `m` 或 `s`，例如 `25m`、`1m`；超过 1 分钟的等待步骤必须包含 |
| 小卡片 | `steps_summary[]` | 必须逐条对应 `full_steps[]` 单独压缩，不可重新生成另一套流程；每条建议 45-70 个英文字符，硬上限 80 个字符 |
| 动作 | `actions.regenerate` | 重新生成另一道菜 |
| 动作 | `actions.shopping_list` | 基于当前食谱生成采购清单 |
| 动作 | `actions.start_cooking` | 打开烹饪页，由系统处理 |

### 重新生成逻辑

1. **用户点击 Regenerate**：直接重新生成
2. **用户口头拒绝，但没给原因**：先问 1 个高价值问题
3. **用户口头拒绝，并给出原因**：直接按原因重新生成
4. **排除规则**：新菜不得与已拒绝的菜重复，也不得高度相似
5. **兜底**：若当前约束下可推荐的菜已全部被拒绝，或拒绝 5 次及以上，输出示例：

   > "You have very picky taste! Why don't you tell me what you want to eat, and I'll customize it for you?"

---

## S5. 生成采购清单

**触发方式：**

- 用户点击食谱卡片的 `shopping_list`
- 用户主动询问缺什么、要买什么

### 生成逻辑

1. **输入来源**：优先基于对应食谱的 `ingredients[].display_amount` 生成采购清单
2. **只保留主要食材**：采购清单的目标不是复述整张食谱配料表，而是只列出真正值得购买的食材
   - 默认保留：主蛋白、主蔬菜、主结构食材、主风味食材
   - 默认忽略：用量极小、且对成菜主体不构成影响的辅料
3. **合并食材**：多个同物食材进行合并
4. **分量标准化**：`amount` 不应照抄食谱单位，而应改成真实可购买的份量和单位
5. **便于购买分类**：`categories` 应按用户购买时更顺手的分类来组织
6. **库存勾选**：
   - 库存中已有 → `checked: true`
   - 库存中没有 → `checked: false`
   - 无库存数据 → 所有食材 `checked: false`

### 输出：采购清单卡片

**聊天文案示例：**

> "这是这道菜真正需要买的主要食材。我已经按更方便购买的分类整理好了，库存里已有的也先帮你勾上了。"

**调用 `REQUEST_CARD_INPUT`，传入以下参数：**

```json
{
  "card_type": "shopping_list_card",
  "recipe": "番茄炒鸡蛋",
  "categories": [
    {
      "name": "Produce",
      "items": [
        {
          "name": "西红柿",
          "canonical_name": "tomato",
          "amount": "2个",
          "checked": true,
          "source": "inventory",
          "importance": "primary"
        }
      ]
    },
    {
      "name": "Dairy & Eggs",
      "items": [
        {
          "name": "鸡蛋",
          "canonical_name": "egg",
          "amount": "1盒",
          "checked": false,
          "source": "recipe",
          "importance": "primary"
        }
      ]
    }
  ],
  "actions": {
    "add_to_cart": {
      "label": "Add to my cart",
      "action": "trigger_online_purchase"
    }
  }
}
```

### 点击 Add to my cart

仅把 `checked: false` 的项目传给线上采购 Skill，并优先传标准化后的采购信息：

```json
{
  "purchase_requests": [
    {
      "name": "egg",
      "amount": "1 carton",
      "purpose": "番茄炒鸡蛋"
    }
  ]
}
```
