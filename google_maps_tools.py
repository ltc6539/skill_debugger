"""Direct Google Maps Platform tools used by the standalone skill debugger."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import requests
from agents import function_tool


ROUTES_ENDPOINT = "https://routes.googleapis.com/directions/v2:computeRoutes"
DIRECTIONS_ENDPOINT = "https://maps.googleapis.com/maps/api/directions/json"
PLACES_BASE = "https://places.googleapis.com/v1"

DEFAULT_ROUTES_FIELD_MASK = (
    "routes.duration,"
    "routes.distanceMeters,"
    "routes.polyline.encodedPolyline,"
    "routes.legs"
)
DEFAULT_PLACES_TEXT_FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.location,"
    "places.rating,"
    "places.priceLevel,"
    "places.types"
)
DEFAULT_PLACES_NEARBY_FIELD_MASK = DEFAULT_PLACES_TEXT_FIELD_MASK
DEFAULT_PLACES_DETAILS_FIELD_MASK = (
    "id,displayName,formattedAddress,location,rating,priceLevel,types,"
    "internationalPhoneNumber,websiteUri,regularOpeningHours"
)
DEFAULT_PLACES_AUTOCOMPLETE_FIELD_MASK = (
    "suggestions.placePrediction.text.text,"
    "suggestions.placePrediction.placeId,"
    "suggestions.queryPrediction.text.text"
)

_GOOGLE_MAPS_API_KEY: str | None = None


def configure_google_maps(api_key: str | None) -> None:
    global _GOOGLE_MAPS_API_KEY
    _GOOGLE_MAPS_API_KEY = (api_key or "").strip() or None


def _api_key() -> Optional[str]:
    return _GOOGLE_MAPS_API_KEY or (os.getenv("GOOGLE_MAPS_API_KEY") or "").strip() or None


def _headers(field_mask: Optional[str] = None) -> Dict[str, str]:
    api_key = _api_key() or ""
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
    }
    if field_mask:
        headers["X-Goog-FieldMask"] = field_mask
    return headers


def _request_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        resp = requests.request(
            method,
            url,
            headers=headers,
            params=params,
            json=body,
            timeout=10,
        )
    except Exception as exc:
        return {"error": f"request_failed: {exc}"}

    if resp.status_code >= 400:
        return {
            "error": "http_error",
            "status": resp.status_code,
            "body": resp.text[:500],
        }

    try:
        return resp.json()
    except Exception:
        return {"error": "invalid_json", "body": resp.text[:500]}


def _parse_lat_lng(value: str) -> Optional[Tuple[float, float]]:
    if not value:
        return None
    parts = value.split(",")
    if len(parts) != 2:
        return None
    try:
        lat = float(parts[0].strip())
        lng = float(parts[1].strip())
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def _build_waypoint(value: str) -> Dict[str, Any]:
    latlng = _parse_lat_lng(value)
    if latlng:
        return {"location": {"latLng": {"latitude": latlng[0], "longitude": latlng[1]}}}
    if value.startswith("place_id:"):
        return {"placeId": value.replace("place_id:", "", 1)}
    return {"address": value}


def _build_circle(lat: float, lng: float, radius_m: float) -> Dict[str, Any]:
    return {
        "circle": {
            "center": {"latitude": lat, "longitude": lng},
            "radius": radius_m,
        }
    }


def _sanitize_field_mask(mask: Optional[str], default: str) -> str:
    if mask:
        return mask
    return default


def _require_api_key() -> Optional[Dict[str, Any]]:
    if not _api_key():
        return {"error": "GOOGLE_MAPS_API_KEY is not set"}
    return None


def _duration_to_seconds(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    if value.endswith("s"):
        try:
            return int(float(value[:-1]))
        except ValueError:
            return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _build_maps_search_url(location: Dict[str, Any]) -> Optional[str]:
    lat = location.get("latitude") if isinstance(location, dict) else None
    lng = location.get("longitude") if isinstance(location, dict) else None
    if lat is None or lng is None:
        return None
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"


def _build_navigation_url(origin: str, destination: str, travel_mode: str) -> str:
    params = ["api=1", f"destination={destination}", f"travelmode={travel_mode.lower()}"]
    if origin:
        params.append(f"origin={origin}")
    params.append("dir_action=navigate")
    return "https://www.google.com/maps/dir/?" + "&".join(params)


def _normalize_routes_response(
    response: Dict[str, Any], *, origin: str, destination: str, travel_mode: str
) -> Dict[str, Any]:
    if response.get("error"):
        return {
            "kind": "route",
            "origin": origin,
            "destination": destination,
            "travel_mode": travel_mode,
            "error": response.get("error"),
            "status": response.get("status"),
            "details": response.get("body"),
        }

    routes = []
    for route in response.get("routes", [])[:3]:
        duration_seconds = _duration_to_seconds(route.get("duration"))
        legs = route.get("legs") or []
        leg = legs[0] if legs else {}
        routes.append(
            {
                "distance_meters": route.get("distanceMeters"),
                "duration_seconds": duration_seconds,
                "start_location": leg.get("startLocation", {}).get("latLng"),
                "end_location": leg.get("endLocation", {}).get("latLng"),
            }
        )

    return {
        "kind": "route",
        "origin": origin,
        "destination": destination,
        "travel_mode": travel_mode,
        "routes": routes,
        "navigation_url": _build_navigation_url(origin, destination, travel_mode),
    }


def _normalize_directions_response(
    response: Dict[str, Any], *, origin: str, destination: str, travel_mode: str
) -> Dict[str, Any]:
    if response.get("error"):
        return {
            "kind": "route",
            "origin": origin,
            "destination": destination,
            "travel_mode": travel_mode,
            "error": response.get("error"),
            "status": response.get("status"),
            "details": response.get("body"),
        }

    if response.get("status") not in (None, "OK"):
        return {
            "kind": "route",
            "origin": origin,
            "destination": destination,
            "travel_mode": travel_mode,
            "error": response.get("status"),
            "details": response.get("error_message"),
        }

    routes = []
    for route in response.get("routes", [])[:3]:
        leg = (route.get("legs") or [{}])[0]
        routes.append(
            {
                "distance_meters": (leg.get("distance") or {}).get("value"),
                "duration_seconds": (leg.get("duration") or {}).get("value"),
                "start_address": leg.get("start_address"),
                "end_address": leg.get("end_address"),
            }
        )

    return {
        "kind": "route",
        "origin": origin,
        "destination": destination,
        "travel_mode": travel_mode,
        "routes": routes,
        "navigation_url": _build_navigation_url(origin, destination, travel_mode),
    }


def _normalize_place(place: Dict[str, Any]) -> Dict[str, Any]:
    display = place.get("displayName") or {}
    if isinstance(display, dict):
        name = display.get("text")
    else:
        name = display
    location = place.get("location") or {}
    return {
        "id": place.get("id") or place.get("placeId"),
        "name": name,
        "address": place.get("formattedAddress"),
        "location": location,
        "rating": place.get("rating"),
        "price_level": place.get("priceLevel"),
        "types": place.get("types"),
        "maps_url": _build_maps_search_url(location),
    }


def _normalize_places_list(
    response: Dict[str, Any], *, query: Optional[str] = None
) -> Dict[str, Any]:
    if response.get("error"):
        return {
            "kind": "place_list",
            "query": query,
            "error": response.get("error"),
            "details": response.get("body"),
        }
    places = [_normalize_place(place) for place in response.get("places", [])]
    return {"kind": "place_list", "query": query, "places": places}


def _normalize_place_details(response: Dict[str, Any]) -> Dict[str, Any]:
    if response.get("error"):
        return {"kind": "place_details", "error": response.get("error"), "details": response.get("body")}
    return {"kind": "place_details", "place": _normalize_place(response)}


def _normalize_autocomplete(response: Dict[str, Any]) -> Dict[str, Any]:
    if response.get("error"):
        return {"kind": "autocomplete", "error": response.get("error"), "details": response.get("body")}
    suggestions = []
    for item in response.get("suggestions", [])[:10]:
        place_pred = item.get("placePrediction") or {}
        query_pred = item.get("queryPrediction") or {}
        text = None
        place_id = None
        if place_pred:
            text = (place_pred.get("text") or {}).get("text")
            place_id = place_pred.get("placeId")
        if not text and query_pred:
            text = (query_pred.get("text") or {}).get("text")
        suggestions.append({"text": text, "place_id": place_id})
    return {"kind": "autocomplete", "suggestions": suggestions}


@function_tool
def gmaps_compute_routes(
    origin: str,
    destination: str,
    travel_mode: str = "DRIVE",
    routing_preference: Optional[str] = "TRAFFIC_AWARE",
    alternatives: bool = False,
    language: Optional[str] = None,
    units: Optional[str] = None,
    field_mask: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute routes using Google Routes API.

    Args:
        origin: Address or "lat,lng" or "place_id:...".
        destination: Address or "lat,lng" or "place_id:...".
        travel_mode: DRIVE, WALK, BICYCLE, TWO_WHEELER, TRANSIT.
        routing_preference: TRAFFIC_AWARE or TRAFFIC_AWARE_OPTIMAL for driving.
        alternatives: Whether to compute alternative routes.
        language: BCP-47 language code, e.g. en-US.
        units: METRIC or IMPERIAL.
        field_mask: Response field mask, defaults to duration/distance/polyline/legs.
    """
    err = _require_api_key()
    if err:
        return err
    body: Dict[str, Any] = {
        "origin": _build_waypoint(origin),
        "destination": _build_waypoint(destination),
        "travelMode": travel_mode,
        "computeAlternativeRoutes": alternatives,
    }
    if routing_preference:
        body["routingPreference"] = routing_preference
    if language:
        body["languageCode"] = language
    if units:
        body["units"] = units

    headers = _headers(_sanitize_field_mask(field_mask, DEFAULT_ROUTES_FIELD_MASK))
    response = _request_json("POST", ROUTES_ENDPOINT, headers=headers, body=body)
    return _normalize_routes_response(
        response,
        origin=origin,
        destination=destination,
        travel_mode=travel_mode,
    )


@function_tool
def gmaps_directions_legacy(
    origin: str,
    destination: str,
    mode: str = "driving",
    departure_time: Optional[str] = None,
    arrival_time: Optional[str] = None,
    waypoints: Optional[List[str]] = None,
    language: Optional[str] = None,
    region: Optional[str] = None,
    alternatives: bool = False,
    units: Optional[str] = None,
) -> Dict[str, Any]:
    """Get directions using Directions API (Legacy)."""
    err = _require_api_key()
    if err:
        return err
    params: Dict[str, Any] = {
        "origin": origin,
        "destination": destination,
        "mode": mode,
        "key": _api_key(),
    }
    if departure_time:
        params["departure_time"] = departure_time
    if arrival_time:
        params["arrival_time"] = arrival_time
    if waypoints:
        params["waypoints"] = "|".join(waypoints)
    if language:
        params["language"] = language
    if region:
        params["region"] = region
    if alternatives:
        params["alternatives"] = "true"
    if units:
        params["units"] = units

    response = _request_json("GET", DIRECTIONS_ENDPOINT, params=params)
    return _normalize_directions_response(
        response,
        origin=origin,
        destination=destination,
        travel_mode=mode,
    )


@function_tool
def places_text_search(
    text_query: str,
    location_bias_lat: Optional[float] = None,
    location_bias_lng: Optional[float] = None,
    location_bias_radius_m: Optional[float] = None,
    included_types: Optional[List[str]] = None,
    included_type: Optional[str] = None,
    max_result_count: int = 10,
    language_code: Optional[str] = None,
    region_code: Optional[str] = None,
    field_mask: Optional[str] = None,
) -> Dict[str, Any]:
    """Search places by text using Places API (New)."""
    err = _require_api_key()
    if err:
        return err
    body: Dict[str, Any] = {"textQuery": text_query, "maxResultCount": max_result_count}
    if location_bias_lat is not None and location_bias_lng is not None and location_bias_radius_m:
        body["locationBias"] = _build_circle(location_bias_lat, location_bias_lng, location_bias_radius_m)
    effective_included_type = (included_type or "").strip()
    if not effective_included_type and included_types:
        for value in included_types:
            candidate = str(value).strip()
            if candidate:
                effective_included_type = candidate
                break
    if effective_included_type:
        body["includedType"] = effective_included_type
    if language_code:
        body["languageCode"] = language_code
    if region_code:
        body["regionCode"] = region_code

    headers = _headers(_sanitize_field_mask(field_mask, DEFAULT_PLACES_TEXT_FIELD_MASK))
    response = _request_json("POST", f"{PLACES_BASE}/places:searchText", headers=headers, body=body)
    return _normalize_places_list(response, query=text_query)


@function_tool
def places_nearby_search(
    latitude: float,
    longitude: float,
    radius_m: float,
    included_types: Optional[List[str]] = None,
    excluded_types: Optional[List[str]] = None,
    max_result_count: int = 10,
    rank_preference: Optional[str] = None,
    language_code: Optional[str] = None,
    region_code: Optional[str] = None,
    field_mask: Optional[str] = None,
) -> Dict[str, Any]:
    """Search nearby places using Places API (New)."""
    err = _require_api_key()
    if err:
        return err
    body: Dict[str, Any] = {
        "locationRestriction": _build_circle(latitude, longitude, radius_m),
        "maxResultCount": max_result_count,
    }
    if included_types:
        body["includedTypes"] = included_types
    if excluded_types:
        body["excludedTypes"] = excluded_types
    if rank_preference:
        body["rankPreference"] = rank_preference
    if language_code:
        body["languageCode"] = language_code
    if region_code:
        body["regionCode"] = region_code

    headers = _headers(_sanitize_field_mask(field_mask, DEFAULT_PLACES_NEARBY_FIELD_MASK))
    response = _request_json("POST", f"{PLACES_BASE}/places:searchNearby", headers=headers, body=body)
    return _normalize_places_list(response, query=f"{latitude},{longitude}")


@function_tool
def places_details(
    place_id: str,
    field_mask: Optional[str] = None,
    language_code: Optional[str] = None,
    region_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Get place details using Places API (New)."""
    err = _require_api_key()
    if err:
        return err
    params: Dict[str, Any] = {}
    if language_code:
        params["languageCode"] = language_code
    if region_code:
        params["regionCode"] = region_code

    headers = _headers(_sanitize_field_mask(field_mask, DEFAULT_PLACES_DETAILS_FIELD_MASK))
    response = _request_json(
        "GET",
        f"{PLACES_BASE}/places/{place_id}",
        headers=headers,
        params=params,
    )
    return _normalize_place_details(response)


@function_tool
def places_autocomplete(
    input_text: str,
    location_bias_lat: Optional[float] = None,
    location_bias_lng: Optional[float] = None,
    location_bias_radius_m: Optional[float] = None,
    included_primary_types: Optional[List[str]] = None,
    include_query_predictions: bool = False,
    language_code: Optional[str] = None,
    region_code: Optional[str] = None,
    field_mask: Optional[str] = None,
) -> Dict[str, Any]:
    """Autocomplete places using Places API (New)."""
    err = _require_api_key()
    if err:
        return err
    body: Dict[str, Any] = {"input": input_text}
    if location_bias_lat is not None and location_bias_lng is not None and location_bias_radius_m:
        body["locationBias"] = _build_circle(location_bias_lat, location_bias_lng, location_bias_radius_m)
    if included_primary_types:
        body["includedPrimaryTypes"] = included_primary_types
    if include_query_predictions:
        body["includeQueryPredictions"] = True
    if language_code:
        body["languageCode"] = language_code
    if region_code:
        body["regionCode"] = region_code

    headers = _headers(_sanitize_field_mask(field_mask, DEFAULT_PLACES_AUTOCOMPLETE_FIELD_MASK))
    response = _request_json("POST", f"{PLACES_BASE}/places:autocomplete", headers=headers, body=body)
    return _normalize_autocomplete(response)


@function_tool
def navigation_link(
    destination: str,
    origin: Optional[str] = None,
    travel_mode: str = "driving",
    provider: str = "google",
) -> Dict[str, Any]:
    """Create a navigation deep link for Google Maps or Apple Maps."""
    if provider.lower() == "apple":
        params = []
        if origin:
            params.append(f"saddr={origin}")
        params.append(f"daddr={destination}")
        if travel_mode.startswith("w"):
            params.append("dirflg=w")
        elif travel_mode.startswith("t"):
            params.append("dirflg=r")
        elif travel_mode.startswith("b"):
            params.append("dirflg=w")
        else:
            params.append("dirflg=d")
        url = "https://maps.apple.com/?" + "&".join(params)
        return {"kind": "navigation", "provider": "apple", "url": url}

    params = ["api=1", f"destination={destination}", f"travelmode={travel_mode}"]
    if origin:
        params.append(f"origin={origin}")
    params.append("dir_action=navigate")
    url = "https://www.google.com/maps/dir/?" + "&".join(params)
    return {"kind": "navigation", "provider": "google", "url": url}


GOOGLE_MAPS_DIRECT_TOOLS = [
    gmaps_compute_routes,
    gmaps_directions_legacy,
    places_text_search,
    places_nearby_search,
    places_details,
    places_autocomplete,
    navigation_link,
]
