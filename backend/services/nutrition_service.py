import requests
from typing import Dict, Optional, Tuple

from app.config import settings

USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

GRAMS_PER_UNIT_DEFAULT = {
    "g": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "kg": 1000.0,
    "oz": 28.3495,
    "lb": 453.592,
    "ml": 1.0,
    "l": 1000.0,
    "tsp": 5.0,
    "tbsp": 15.0,
    "cup": 240.0,
    "cups": 240.0,
    "piece": 50.0,
    "pieces": 50.0,
    "clove": 5.0,
    "cloves": 5.0,
}

INGREDIENT_UNIT_OVERRIDES = {
    "olive oil": {"tbsp": 13.5, "tsp": 4.5, "cup": 216.0},
    "butter": {"tbsp": 14.2, "tsp": 4.7, "cup": 227.0},
    "garlic butter": {"tbsp": 14.0, "tsp": 4.7},
    "salt": {"tsp": 6.0, "tbsp": 18.0},
    "black pepper": {"tsp": 2.3, "tbsp": 6.9},
    "parmesan cheese": {"cup": 100.0, "tbsp": 5.0},
    "fresh basil": {"cup": 21.0, "tbsp": 1.3},
    "tomato sauce": {"cup": 245.0, "tbsp": 15.0},
    "caesar dressing": {"tbsp": 15.0, "oz": 28.3495},
    "romaine lettuce": {"cup": 47.0, "oz": 28.3495},
    "croutons": {"cup": 30.0},
    "asparagus": {"cup": 134.0, "oz": 28.3495},
    "fresh mozzarella": {"oz": 28.3495, "cup": 132.0},
    "pizza dough": {"oz": 28.3495},
    "salmon fillet": {"oz": 28.3495},
    "lemon juice": {"tbsp": 15.0, "tsp": 5.0},
}


def search_food(food_name: str) -> Optional[Dict]:
    params = {
        "query": food_name,
        "pageSize": 1,
        "api_key": settings.USDA_API_KEY,
    }

    response = requests.get(USDA_SEARCH_URL, params=params, timeout=20)

    if response.status_code != 200:
        return None

    data = response.json()
    foods = data.get("foods", [])

    if not foods:
        return None

    return foods[0]


def extract_nutrition_per_100g(food_item: Dict) -> Dict:
    
    nutrients = food_item.get("foodNutrients", [])

    nutrition = {
        "calories_per_100g": 0.0,
        "protein_per_100g": 0.0,
        "fat_per_100g": 0.0,
        "carbs_per_100g": 0.0,
    }

    for nutrient in nutrients:
        name = nutrient.get("nutrientName")
        value = float(nutrient.get("value", 0) or 0)

        if name == "Energy":
            nutrition["calories_per_100g"] = value
        elif name == "Protein":
            nutrition["protein_per_100g"] = value
        elif name == "Total lipid (fat)":
            nutrition["fat_per_100g"] = value
        elif name == "Carbohydrate, by difference":
            nutrition["carbs_per_100g"] = value

    return nutrition


def normalize_unit(unit: Optional[str]) -> Optional[str]:
    if not unit:
        return None
    return unit.strip().lower()


def quantity_to_grams(
    ingredient_name: str,
    quantity: Optional[float],
    unit: Optional[str],
) -> Optional[float]:
    if quantity is None:
        return None

    unit_norm = normalize_unit(unit)

    if not unit_norm:
        return None

    ingredient_key = ingredient_name.strip().lower()

    if ingredient_key in INGREDIENT_UNIT_OVERRIDES:
        override_map = INGREDIENT_UNIT_OVERRIDES[ingredient_key]
        if unit_norm in override_map:
            return quantity * override_map[unit_norm]

    if unit_norm in GRAMS_PER_UNIT_DEFAULT:
        return quantity * GRAMS_PER_UNIT_DEFAULT[unit_norm]

    return None


def scale_nutrition(per_100g: Dict, grams: float) -> Dict:
    factor = grams / 100.0

    return {
        "calories": round(per_100g["calories_per_100g"] * factor, 2),
        "protein": round(per_100g["protein_per_100g"] * factor, 2),
        "fat": round(per_100g["fat_per_100g"] * factor, 2),
        "carbs": round(per_100g["carbs_per_100g"] * factor, 2),
    }


def get_ingredient_nutrition(
    ingredient_name: str,
    quantity: Optional[float] = None,
    unit: Optional[str] = None,
) -> Optional[Dict]:
    food = search_food(ingredient_name)

    if not food:
        return None

    per_100g = extract_nutrition_per_100g(food)
    grams = quantity_to_grams(ingredient_name, quantity, unit)

    if grams is None:
        return {
            "matched_food": food.get("description"),
            "reference_basis": "per_100g",
            "grams_used": None,
            "calories": round(per_100g["calories_per_100g"], 2),
            "protein": round(per_100g["protein_per_100g"], 2),
            "fat": round(per_100g["fat_per_100g"], 2),
            "carbs": round(per_100g["carbs_per_100g"], 2),
            "scaled": False,
        }

    scaled = scale_nutrition(per_100g, grams)

    return {
        "matched_food": food.get("description"),
        "reference_basis": "per_100g",
        "grams_used": round(grams, 2),
        "calories": scaled["calories"],
        "protein": scaled["protein"],
        "fat": scaled["fat"],
        "carbs": scaled["carbs"],
        "scaled": True,
    }