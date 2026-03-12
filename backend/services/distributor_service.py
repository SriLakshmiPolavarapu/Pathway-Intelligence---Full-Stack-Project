"""
Step 3 — Find Local Distributors
Uses OpenStreetMap (Nominatim + Overpass) to find food distributors/wholesalers
near the restaurant, then uses Gemini to intelligently match ingredients to distributors.
"""

import json
import os
import re
import time
from typing import Optional

import google.generativeai as genai
import requests
from sqlalchemy.orm import Session, joinedload

from app.models import (
    Distributor,
    Ingredient,
    IngredientDistributorMatch,
    MenuSource,
    Recipe,
    RecipeIngredient,
)

# ── Config ──────────────────────────────────────────────────────

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Default restaurant location — override via env vars
DEFAULT_CITY = os.getenv("RESTAURANT_CITY", "Portland")
DEFAULT_STATE = os.getenv("RESTAURANT_STATE", "Oregon")
SEARCH_RADIUS_KM = int(os.getenv("DISTRIBUTOR_SEARCH_RADIUS_KM", "50"))

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Overpass tags that indicate food distributors / wholesalers
OVERPASS_QUERY_TEMPLATE = """
[out:json][timeout:30];
(
  node["shop"="wholesale"](around:{radius},{lat},{lon});
  node["shop"="supermarket"](around:{radius},{lat},{lon});
  node["shop"="farm"](around:{radius},{lat},{lon});
  node["shop"="butcher"](around:{radius},{lat},{lon});
  node["shop"="seafood"](around:{radius},{lat},{lon});
  node["shop"="greengrocer"](around:{radius},{lat},{lon});
  node["shop"="bakery"](around:{radius},{lat},{lon});
  node["shop"="beverages"](around:{radius},{lat},{lon});
  node["shop"="spices"](around:{radius},{lat},{lon});
  node["industrial"="food"](around:{radius},{lat},{lon});
  node["trade"="food"](around:{radius},{lat},{lon});
  way["shop"="wholesale"](around:{radius},{lat},{lon});
  way["shop"="supermarket"](around:{radius},{lat},{lon});
  way["industrial"="food"](around:{radius},{lat},{lon});
  way["trade"="food"](around:{radius},{lat},{lon});
);
out center body;
"""

HEADERS = {"User-Agent": "PathwayRFP/1.0 (restaurant-rfp-pipeline)"}


# ── Geocoding ───────────────────────────────────────────────────

def geocode_location(city: str, state: str) -> Optional[dict]:
    """Use Nominatim to geocode a city/state into lat/lon."""
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": f"{city}, {state}, USA", "format": "json", "limit": 1},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            return {
                "lat": float(results[0]["lat"]),
                "lon": float(results[0]["lon"]),
                "display_name": results[0].get("display_name", ""),
            }
    except Exception as e:
        print(f"[Geocode] Error geocoding {city}, {state}: {e}")
    return None


# ── Overpass (find distributors on OSM) ─────────────────────────

def search_distributors_osm(lat: float, lon: float, radius_km: int = 50) -> list[dict]:
    """Query Overpass API for food-related businesses near the given coordinates."""
    radius_m = radius_km * 1000
    query = OVERPASS_QUERY_TEMPLATE.format(radius=radius_m, lat=lat, lon=lon)

    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers=HEADERS,
            timeout=35,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[Overpass] Error: {e}")
        return []

    distributors = []
    seen_ids = set()

    for element in data.get("elements", []):
        osm_id = f"{element.get('type', 'node')}_{element['id']}"
        if osm_id in seen_ids:
            continue
        seen_ids.add(osm_id)

        tags = element.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        # Determine category from tags
        category = (
            tags.get("shop")
            or tags.get("trade")
            or tags.get("industrial")
            or tags.get("amenity")
            or "food_supplier"
        )

        # Get coordinates (nodes have lat/lon directly, ways have center)
        e_lat = element.get("lat") or element.get("center", {}).get("lat")
        e_lon = element.get("lon") or element.get("center", {}).get("lon")

        addr_parts = [
            tags.get("addr:housenumber", ""),
            tags.get("addr:street", ""),
        ]
        address = " ".join(p for p in addr_parts if p).strip() or None

        distributors.append({
            "name": name,
            "category": category,
            "city": tags.get("addr:city"),
            "state": tags.get("addr:state"),
            "address": address,
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "website": tags.get("website") or tags.get("contact:website"),
            "email": tags.get("email") or tags.get("contact:email"),
            "latitude": e_lat,
            "longitude": e_lon,
            "osm_type": element.get("type", "node"),
            "osm_id": osm_id,
        })

    return distributors


# ── Persist distributors to DB ──────────────────────────────────

def upsert_distributors(db: Session, raw_distributors: list[dict], city: str, state: str) -> list[Distributor]:
    """Insert new distributors or return existing ones (deduplicated by osm_id)."""
    result = []
    for d in raw_distributors:
        existing = db.query(Distributor).filter(Distributor.osm_id == d["osm_id"]).first()
        if existing:
            result.append(existing)
            continue

        dist = Distributor(
            name=d["name"],
            category=d.get("category"),
            city=d.get("city") or city,
            state=d.get("state") or state,
            address=d.get("address"),
            phone=d.get("phone"),
            website=d.get("website"),
            email=d.get("email"),
            latitude=d.get("latitude"),
            longitude=d.get("longitude"),
            osm_type=d.get("osm_type"),
            osm_id=d["osm_id"],
            source="OSM",
        )
        db.add(dist)
        db.flush()
        result.append(dist)

    return result


# ── Gemini-powered ingredient <-> distributor matching ──────────

MATCH_PROMPT = """You are a restaurant supply chain expert. Given a list of ingredients a restaurant needs and a list of local distributors, determine which distributors are most likely to supply each ingredient.

Return ONLY valid JSON (no markdown, no code fences) in this format:
{
  "matches": [
    {
      "ingredient_id": 1,
      "distributor_id": 5,
      "matched_category": "Produce",
      "confidence_score": 0.85,
      "rationale": "Greengrocer specializing in fresh vegetables"
    }
  ]
}

Rules:
- Match EVERY ingredient to at least one distributor.
- A single distributor can supply multiple ingredients.
- confidence_score is 0.0-1.0 (how likely they carry this item).
- Wholesale/bulk suppliers get higher scores for restaurant quantities.
- If no great match exists, pick the best available option with a lower score.
- Supermarkets and wholesale stores can supply most general ingredients.
- Specialty shops (butcher, seafood, bakery) should score higher for their specialties.

INGREDIENTS:
{ingredients_json}

DISTRIBUTORS:
{distributors_json}
"""


def _build_fallback_matches(ingredients: list[dict], distributors: list[dict], reason: str) -> list[dict]:
    """Build fallback matches when Gemini fails."""
    fallback = []
    for ing in ingredients:
        for dist in distributors[:3]:
            fallback.append({
                "ingredient_id": ing["id"],
                "distributor_id": dist["id"],
                "matched_category": dist.get("category", "general"),
                "confidence_score": 0.3,
                "rationale": f"Fallback match ({reason})",
            })
    return fallback


def match_ingredients_to_distributors(
    ingredients: list[dict],
    distributors: list[dict],
) -> list[dict]:
    """Use Gemini to intelligently match ingredients to distributors."""
    if not ingredients or not distributors:
        return []

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.0-flash")

        prompt = MATCH_PROMPT.format(
            ingredients_json=json.dumps(ingredients, indent=2),
            distributors_json=json.dumps(distributors, indent=2),
        )

        # Force Gemini to return pure JSON (no markdown wrapping)
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.1,
                max_output_tokens=8192,
                response_mime_type="application/json",
            ),
        )
    except Exception as e:
        print(f"[Gemini Match] API call failed: {e}", flush=True)
        return _build_fallback_matches(ingredients, distributors, "Gemini API error")

    # Extract text from response safely
    try:
        raw = response.text.strip()
        print(f"[Gemini Match] Raw response (first 500 chars): {raw[:500]}", flush=True)
    except Exception as e:
        print(f"[Gemini Match] Could not read response.text: {e}", flush=True)
        return _build_fallback_matches(ingredients, distributors, "Response unreadable")

    # Parse JSON
    try:
        # Even with response_mime_type, strip fences just in case
        raw = re.sub(r'^```(?:json)?\s*\n?', '', raw)
        raw = re.sub(r'\n?```\s*$', '', raw)
        raw = raw.strip()

        # Find the JSON object
        first_brace = raw.find('{')
        last_brace = raw.rfind('}')
        if first_brace != -1 and last_brace != -1:
            raw = raw[first_brace:last_brace + 1]

        print(f"[Gemini Match] Cleaned JSON (first 300 chars): {raw[:300]}", flush=True)

        parsed = json.loads(raw)
        matches = parsed.get("matches", [])
        print(f"[Gemini Match] Successfully parsed {len(matches)} matches", flush=True)
        return matches

    except (json.JSONDecodeError, TypeError, AttributeError) as e:
        print(f"[Gemini Match] JSON parse error: {e}", flush=True)
        print(f"[Gemini Match] Raw text was: {raw[:1000]}", flush=True)
        return _build_fallback_matches(ingredients, distributors, "JSON parse failed")


# ── Main orchestrator ───────────────────────────────────────────

def find_and_store_distributors_for_menu(db: Session, menu_source_id: int) -> dict:
    """
    Full Step 3 pipeline:
    1. Load menu + ingredients from DB
    2. Geocode restaurant location
    3. Search OSM for nearby distributors
    4. Use Gemini to match ingredients <-> distributors
    5. Persist matches to DB
    """
    # 1. Load menu source with all ingredients
    menu_source = (
        db.query(MenuSource)
        .options(
            joinedload(MenuSource.recipes)
            .joinedload(Recipe.ingredients)
            .joinedload(RecipeIngredient.ingredient)
        )
        .filter(MenuSource.id == menu_source_id)
        .first()
    )

    if not menu_source:
        raise ValueError(f"Menu source {menu_source_id} not found")

    # Collect unique ingredients
    seen_ids = set()
    ingredients = []
    for recipe in menu_source.recipes:
        for ri in recipe.ingredients:
            if ri.ingredient.id not in seen_ids:
                seen_ids.add(ri.ingredient.id)
                ingredients.append(ri.ingredient)

    if not ingredients:
        raise ValueError("No ingredients found for this menu")

    # 2. Geocode
    city = DEFAULT_CITY
    state = DEFAULT_STATE
    geo = geocode_location(city, state)

    if not geo:
        raise ValueError(f"Could not geocode location: {city}, {state}")

    print(f"[Step 3] Geocoded {city}, {state} -> ({geo['lat']}, {geo['lon']})")

    # 3. Search OSM for distributors
    time.sleep(1)  # Be polite to Nominatim rate limits
    raw_distributors = search_distributors_osm(geo["lat"], geo["lon"], SEARCH_RADIUS_KM)
    print(f"[Step 3] Found {len(raw_distributors)} distributors on OSM")

    if not raw_distributors:
        # Fallback: create some mock distributors so the pipeline doesn't stall
        raw_distributors = _generate_fallback_distributors(city, state)
        print(f"[Step 3] Using {len(raw_distributors)} fallback distributors")

    # 4. Upsert distributors into DB
    db_distributors = upsert_distributors(db, raw_distributors, city, state)
    db.flush()

    # 5. Gemini matching — limit to 20 distributors to avoid token overflow
    # Prefer specialty shops (butcher, seafood, greengrocer, farm) over generic supermarkets
    SPECIALTY_CATEGORIES = {"wholesale", "butcher", "seafood", "greengrocer", "farm", "bakery", "beverages", "spices"}
    specialty = [d for d in db_distributors if d.category in SPECIALTY_CATEGORIES]
    general = [d for d in db_distributors if d.category not in SPECIALTY_CATEGORIES]
    prioritized = (specialty + general)[:20]

    print(f"[Step 3] Sending {len(prioritized)} distributors (of {len(db_distributors)}) to Gemini for matching")

    ing_payload = [{"id": i.id, "name": i.name} for i in ingredients]
    dist_payload = [
        {"id": d.id, "name": d.name, "category": d.category}
        for d in prioritized
    ]

    matches_raw = match_ingredients_to_distributors(ing_payload, dist_payload)

    # 6. Persist matches
    # Clear old matches for this menu_source_id to allow re-runs
    db.query(IngredientDistributorMatch).filter(
        IngredientDistributorMatch.menu_source_id == menu_source_id
    ).delete()
    db.flush()

    valid_ing_ids = {i.id for i in ingredients}
    valid_dist_ids = {d.id for d in db_distributors}

    db_matches = []
    for m in matches_raw:
        ing_id = m.get("ingredient_id")
        dist_id = m.get("distributor_id")

        if ing_id not in valid_ing_ids or dist_id not in valid_dist_ids:
            continue

        match = IngredientDistributorMatch(
            ingredient_id=ing_id,
            distributor_id=dist_id,
            menu_source_id=menu_source_id,
            matched_category=m.get("matched_category"),
            confidence_score=m.get("confidence_score"),
            rationale=m.get("rationale"),
        )
        db.add(match)
        db_matches.append(match)

    db.commit()

    # Reload matches with relationships
    loaded_matches = (
        db.query(IngredientDistributorMatch)
        .options(
            joinedload(IngredientDistributorMatch.ingredient),
            joinedload(IngredientDistributorMatch.distributor),
        )
        .filter(IngredientDistributorMatch.menu_source_id == menu_source_id)
        .all()
    )

    return {
        "menu_source_id": menu_source_id,
        "restaurant_name": menu_source.restaurant_name,
        "city": city,
        "state": state,
        "distributor_count": len(db_distributors),
        "match_count": len(loaded_matches),
        "matches": loaded_matches,
    }


def list_distributors_for_menu(db: Session, menu_source_id: int) -> list:
    """Return all ingredient-distributor matches for a given menu source."""
    return (
        db.query(IngredientDistributorMatch)
        .options(
            joinedload(IngredientDistributorMatch.ingredient),
            joinedload(IngredientDistributorMatch.distributor),
        )
        .filter(IngredientDistributorMatch.menu_source_id == menu_source_id)
        .all()
    )


# ── Fallback distributors (when OSM returns nothing) ────────────

def _generate_fallback_distributors(city: str, state: str) -> list[dict]:
    """Generate realistic mock distributors when OSM has no results."""
    return [
        {
            "name": f"{city} Restaurant Supply Co.",
            "category": "wholesale",
            "city": city,
            "state": state,
            "address": "100 Market St",
            "phone": "(503) 555-0101",
            "website": None,
            "email": f"orders@{city.lower().replace(' ', '')}supply.com",
            "latitude": None,
            "longitude": None,
            "osm_type": "mock",
            "osm_id": f"mock_wholesale_{city.lower().replace(' ', '_')}",
        },
        {
            "name": f"Pacific Coast Seafood - {city}",
            "category": "seafood",
            "city": city,
            "state": state,
            "address": "250 Harbor Blvd",
            "phone": "(503) 555-0202",
            "website": None,
            "email": f"sales@pacificseafood-{city.lower().replace(' ', '')}.com",
            "latitude": None,
            "longitude": None,
            "osm_type": "mock",
            "osm_id": f"mock_seafood_{city.lower().replace(' ', '_')}",
        },
        {
            "name": f"{city} Fresh Produce Market",
            "category": "greengrocer",
            "city": city,
            "state": state,
            "address": "75 Farm Road",
            "phone": "(503) 555-0303",
            "website": None,
            "email": f"info@{city.lower().replace(' ', '')}produce.com",
            "latitude": None,
            "longitude": None,
            "osm_type": "mock",
            "osm_id": f"mock_produce_{city.lower().replace(' ', '_')}",
        },
        {
            "name": "Valley Meats & Provisions",
            "category": "butcher",
            "city": city,
            "state": state,
            "address": "400 Industrial Way",
            "phone": "(503) 555-0404",
            "website": None,
            "email": "orders@valleymeats.com",
            "latitude": None,
            "longitude": None,
            "osm_type": "mock",
            "osm_id": f"mock_butcher_{city.lower().replace(' ', '_')}",
        },
        {
            "name": f"{city} Dairy & Specialty Foods",
            "category": "farm",
            "city": city,
            "state": state,
            "address": "800 Creamery Lane",
            "phone": "(503) 555-0505",
            "website": None,
            "email": f"dairy@{city.lower().replace(' ', '')}specialty.com",
            "latitude": None,
            "longitude": None,
            "osm_type": "mock",
            "osm_id": f"mock_dairy_{city.lower().replace(' ', '_')}",
        },
    ]