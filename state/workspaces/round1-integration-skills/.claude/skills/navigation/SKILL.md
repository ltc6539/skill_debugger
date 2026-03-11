---
name: navigation
description: 为用户计算从当前位置到目的地的路线，生成嵌入聊天的地图卡片，并提供 Google Maps / Apple Maps 深链直接跳转导航。
user-invocable: true
allowed-tools: [places_text_search, places_details, gmaps_compute_routes, gmaps_directions_legacy, navigation_link]
legacy_id: SKILL_04
license: MIT
---

# Navigation Skill

## Endpoint Resolution

```
origin:      current_location（从上下文读取，若无则询问）
destination: 从推荐/预约上下文读取；若用户手动输入则解析地点名称
```

若目的地是模糊描述（如"楼上那家日料"），调用 `places_text_search` 获取候选地点，再用 `places_details(place_id)` 解析为精确坐标，**解析失败时询问用户而非猜测**。

## Travel Mode Selection

根据距离和场景自动推断出行方式：

| 距离 | 默认推荐 |
|---|---|
| < 800m | 步行 |
| 800m ~ 3km | 骑行 或 驾车 |
| > 3km | 驾车 或 公交 |

**以下情况强制优先驾车：**
- 日历事件标注"商务"
- 距预约时间 < 30 分钟

## Route Calculation

调用 `gmaps_compute_routes(origin, destination, travel_mode)`：
如需出发/到达时间，使用 `gmaps_directions_legacy` 作为降级方案。

```
route_result = {
  routes: [
    { distance_meters, duration_seconds, start_location, end_location }
  ],
  navigation_url
}
```

## Map Card & Deep Links

路线或地点结果返回后，系统会自动附加地图卡片（无需额外工具），包含：

- 路线概览、时长、距离
- 深链按钮（调用 `navigation_link(destination, origin, travel_mode, provider)` 生成）

IMPORTANT: 深链生成后**交由系统导航 App 接管**，agent 不再介入导航过程。

## Departure Reminder

若存在预约时间，在路线展示后提示：
> "预约7点，步行约18分钟，建议 **6:38 出发**。需要提醒你吗？"

用户确认后输出结构化提醒信息（由前端定时器执行）：
```json
{ "remind_at": "18:38", "message": "该出发去 {restaurant} 了" }
```

## Graceful Degradation

| 情况 | 处理 |
|---|---|
| 位置权限未开启 | 询问当前地址或附近地标 |
| 目的地无法解析 | 返回搜索链接，由用户自行打开地图 App |
| Routes API 超时 | 提供目的地地址文本，引导手动导航 |
| 多条路线时长差异 > 15分钟 | 展示最快和最省力两条，由用户选择 |
