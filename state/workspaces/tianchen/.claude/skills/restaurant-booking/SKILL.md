---
name: restaurant-booking
description: 餐厅预约辅助流程。收集预约要素、生成电话话术，并在用户确认后可通过 Twilio 自动外呼餐厅播报订位信息；不伪装成“已百分百预订成功”。
user-invocable: true
allowed-tools: [YELP_GET_BUSINESS_DETAILS, YELP_SEARCH_BY_PHONE, places_details, twilio_call_restaurant, update_user_preferences, REQUEST_CARD_INPUT, opentable_directory_search, opentable_availability_search_v2, opentable_slot_lock_create_v2, opentable_reservation_create_v2, opentable_reservation_get_v2, opentable_reservation_cancel_v2]
legacy_id: SKILL_03
license: MIT
---

# Restaurant Booking Assistant Skill

CRITICAL: 生产环境若无 OpenTable key，则使用电话链路；在本 demo 中已提供 OpenTable mock 工具，可完整演练“查位-锁位-下单-改签-取消”流程用于 tool-use 训练。无论走线上 API 还是 mock，都不能在用户未确认时触发不可逆动作（下单/外呼）。

## Booking Info Collection

从上下文和用户消息中提取预约要素，缺失项**合并在一句话里询问**：

```
booking_info = {
  restaurant,    # 已选餐厅
  date,          # 日期（从日历或用户输入）
  time,          # 时间（从日历空闲时段建议，用户确认）
  party_size,    # 人数（从日历参与人数或用户输入）
  user_name,     # 从画像读取
  user_phone,    # 从画像读取，若无则询问
  special_req    # 过敏原、包厢、儿童椅等（主动询问一次）
}
```

NEVER 逐字段单独询问。一次最多确认 2 个缺失字段：
> "几点钟、几个人？有没有特殊需求（过敏或需要包厢）？"

## Contact Retrieval

优先调用 `YELP_GET_BUSINESS_DETAILS` 获取：
- 电话号码
- 官方预约链接（若有）
- 营业时间（验证所选时间段是否在营业内）

若只有电话信息，可先用 `YELP_SEARCH_BY_PHONE` 反查 business_id，再取详情。
若 Yelp 详情不可用，再调用 `places_details` 作为补充。

若所选时间餐厅未营业，立即提示并推荐最近可用时段，**不继续生成话术**。

## OpenTable Mock Booking Flow

当用户明确希望“在线预约”或“直接帮我订位”时，优先走 OpenTable 工具链：

1. `opentable_directory_search`：确认 `rid`
2. `opentable_availability_search_v2`：查询可订时段
3. `opentable_slot_lock_create_v2`：创建 `reservation_token`
4. `opentable_reservation_create_v2`：创建预约
5. 后续根据用户指令调用：
   - `opentable_reservation_get_v2`
   - `opentable_reservation_cancel_v2`

## Script Generation

基于 `booking_info` 生成完整电话话术，供用户直接读或复制：

**话术要求：**
- 包含：时间、人数、姓名、特殊需求
- 语气自然，像真人打电话的口吻
- 若用户有过敏原，**必须在话术中包含过敏说明**

## Card Output

调用 `REQUEST_CARD_INPUT` 生成预约操作卡片：

```
card = {
  title:   "预约 · {restaurant_name}",
  summary: 时间 / 人数 / 姓名（一眼确认用）,
  script:  话术文本（可整段复制）,
  actions: [
    { label: "🤖 Twilio 自动拨号", type: "tool", value: "twilio_call_restaurant" },
    { label: "📞 拨打电话",  type: "tel",   value: phone },
    { label: "🔗 在线预约",  type: "url",   value: booking_url },  # 若有
    { label: "🗺️ 同时导航",  skill: "navigation" }
  ]
}
```

## Twilio Auto Call

用户明确确认“现在拨打”后，调用：

```python
twilio_call_restaurant(
  restaurant_phone=phone,
  restaurant_name=restaurant_name,
  booking_datetime=f"{date} {time}",
  party_size=party_size,
  customer_name=user_name,
  customer_phone=user_phone,
  special_requests=special_req,
  confirmed=True
)
```

外呼成功（queued）后，明确告知“已发起电话”，并提醒“需等餐厅人工确认才算订位成功”。

## Confirmation Loop

卡片展示后，询问用户是否预约成功：
> "预约好了吗？确认后我帮你记到日程里。"

用户确认后：调用 `update_user_preferences` 记录本次餐厅选择（如更新偏好菜系），可选触发 navigation。

## Graceful Degradation

| 情况 | 处理 |
|---|---|
| 无电话且无在线链接 | 提供 Yelp 主页链接，说明需用户自行联系 |
| 用户不提供电话号码 | 从话术中移除回拨号码，附加提示"对方可能要求回拨" |
| 餐厅未营业 | 提示营业时间，推荐最近可用时段，不生成话术 |
