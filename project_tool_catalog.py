from __future__ import annotations

from skill_debugger.tool_registry import WorkspaceToolMeta


PROJECT_TOOL_PRESETS: dict[str, list[WorkspaceToolMeta]] = {
    "google_maps": [
        WorkspaceToolMeta(
            name="places_text_search",
            description="Google Maps Places 文本搜索。",
            source="project_catalog:google_maps",
        ),
        WorkspaceToolMeta(
            name="places_nearby_search",
            description="Google Maps Places 附近搜索。",
            source="project_catalog:google_maps",
        ),
        WorkspaceToolMeta(
            name="places_details",
            description="Google Maps 地点详情查询。",
            source="project_catalog:google_maps",
        ),
        WorkspaceToolMeta(
            name="places_autocomplete",
            description="Google Maps Places 自动补全。",
            source="project_catalog:google_maps",
        ),
        WorkspaceToolMeta(
            name="gmaps_compute_routes",
            description="Google Maps Routes 路线计算。",
            source="project_catalog:google_maps",
        ),
        WorkspaceToolMeta(
            name="gmaps_directions_legacy",
            description="Google Maps Directions 路线计算，支持出发/到达时间场景。",
            source="project_catalog:google_maps",
        ),
        WorkspaceToolMeta(
            name="navigation_link",
            description="生成 Google Maps / Apple Maps 导航深链。",
            source="project_catalog:google_maps",
        ),
    ],
    "yelp": [
        WorkspaceToolMeta(
            name="YELP_SEARCH_BUSINESSES",
            description="Yelp 搜索餐厅。",
            source="project_catalog:yelp",
        ),
        WorkspaceToolMeta(
            name="YELP_SEARCH_AND_CHAT",
            description="Yelp 对话式搜索。",
            source="project_catalog:yelp",
        ),
        WorkspaceToolMeta(
            name="YELP_GET_BUSINESS_DETAILS",
            description="Yelp 餐厅详情查询，包含电话、营业时间、照片等。",
            source="project_catalog:yelp",
        ),
        WorkspaceToolMeta(
            name="YELP_GET_BUSINESS_REVIEWS",
            description="Yelp 评价摘要查询。",
            source="project_catalog:yelp",
        ),
        WorkspaceToolMeta(
            name="YELP_GET_REVIEW_HIGHLIGHTS",
            description="Yelp 评价高亮查询。",
            source="project_catalog:yelp",
        ),
        WorkspaceToolMeta(
            name="YELP_SEARCH_BY_PHONE",
            description="Yelp 电话反查 business。",
            source="project_catalog:yelp",
        ),
    ],
}


def get_project_tool_preset_names() -> list[str]:
    return sorted(PROJECT_TOOL_PRESETS.keys())


def get_project_tool_metas(preset_names: list[str] | None = None) -> list[WorkspaceToolMeta]:
    selected = preset_names or get_project_tool_preset_names()
    items: list[WorkspaceToolMeta] = []
    seen: set[str] = set()
    for preset_name in selected:
        tools = PROJECT_TOOL_PRESETS.get(preset_name)
        if not tools:
            raise ValueError(f"Unknown project tool preset: {preset_name}")
        for meta in tools:
            if meta.name in seen:
                continue
            seen.add(meta.name)
            items.append(meta)
    return items
