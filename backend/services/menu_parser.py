import io
import json
from typing import Any, Dict, List, Optional, Tuple

import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
from PIL import Image
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Ingredient, MenuSource, Recipe, RecipeIngredient

genai.configure(api_key=settings.GEMINI_API_KEY)


def fetch_menu_text_from_url(url: str) -> str:
    response = requests.get(url, timeout=20)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    cleaned_lines = [line for line in lines if line]

    return "\n".join(cleaned_lines[:400])


def clean_model_text(raw_text: str) -> str:
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
    return raw_text


def build_image_extraction_prompt(restaurant_name: Optional[str] = None) -> str:
    restaurant_context = f"Restaurant name: {restaurant_name}\n" if restaurant_name else ""

    return f"""
You are reading a restaurant menu image.

{restaurant_context}
Extract the menu into clean plain text.
Rules:
- Return ONLY plain text.
- Preserve dish names and descriptions.
- Group dishes by section if visible.
- Do not invent dishes that are not visible.
- Ignore decorative elements and page styling.
- Fix obvious OCR issues when confidence is high.
- Keep prices if visible.

Format example:
SALADS
Dish Name - description - price

SANDWICHES
Dish Name - description - price
""".strip()


def extract_menu_text_from_image(
    image_bytes: bytes,
    restaurant_name: Optional[str] = None,
) -> str:
    image = Image.open(io.BytesIO(image_bytes))
    model = genai.GenerativeModel(settings.GEMINI_MODEL)

    prompt = build_image_extraction_prompt(restaurant_name=restaurant_name)
    response = model.generate_content([prompt, image])

    extracted_text = clean_model_text(response.text)

    if not extracted_text:
        raise ValueError("Failed to extract text from menu image.")

    return extracted_text


def build_prompt(menu_text: str, restaurant_name: Optional[str] = None) -> str:
    restaurant_context = f"Restaurant name: {restaurant_name}\n" if restaurant_name else ""

    return f"""
You are a culinary operations assistant.

Your task is to parse the restaurant menu below into structured recipes.
For each dish, infer a likely ingredient list and estimated quantities.
Be realistic but concise. If exact quantities are unknown, estimate reasonable values.
Do not skip dishes that seem ambiguous; make your best estimate and include a confidence note when needed.
Ignore section headers like SALADS, SANDWICHES, SMALL PLATES unless useful for context.
Treat each actual dish as a recipe entry.

{restaurant_context}
Return ONLY valid JSON in this exact format:
{{
  "restaurant_name": "string or null",
  "recipes": [
    {{
      "dish_name": "string",
      "description": "string or null",
      "estimated_serving_size": "string or null",
      "ingredients": [
        {{
          "name": "string",
          "quantity": 1.0,
          "unit": "oz",
          "preparation_notes": "diced / grilled / fresh / etc or null",
          "confidence_note": "high confidence / estimated from dish name / etc or null"
        }}
      ]
    }}
  ]
}}

Menu text:
\"\"\"
{menu_text}
\"\"\"
""".strip()


def parse_menu_with_gemini(menu_text: str, restaurant_name: Optional[str] = None) -> Dict[str, Any]:
    model = genai.GenerativeModel(settings.GEMINI_MODEL)

    prompt = build_prompt(menu_text=menu_text, restaurant_name=restaurant_name)
    response = model.generate_content(prompt)

    raw_text = clean_model_text(response.text)

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini did not return valid JSON. Raw response: {raw_text}") from exc

    if "recipes" not in parsed or not isinstance(parsed["recipes"], list):
        raise ValueError("Gemini response missing 'recipes' list.")

    return parsed


def parse_menu_image_with_gemini(
    image_bytes: bytes,
    restaurant_name: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    extracted_menu_text = extract_menu_text_from_image(
        image_bytes=image_bytes,
        restaurant_name=restaurant_name,
    )
    parsed_menu = parse_menu_with_gemini(
        menu_text=extracted_menu_text,
        restaurant_name=restaurant_name,
    )
    return extracted_menu_text, parsed_menu


def get_or_create_ingredient(db: Session, ingredient_name: str) -> Ingredient:
    normalized_name = ingredient_name.strip().lower()

    ingredient = db.query(Ingredient).filter(Ingredient.name == normalized_name).first()
    if ingredient:
        return ingredient

    ingredient = Ingredient(name=normalized_name)
    db.add(ingredient)
    db.flush()
    return ingredient


def save_parsed_menu(
    db: Session,
    parsed_menu: Dict[str, Any],
    source_type: str,
    source_value: str,
    raw_menu_text: str,
    restaurant_name: Optional[str] = None,
) -> MenuSource:
    menu_source = MenuSource(
        restaurant_name=restaurant_name or parsed_menu.get("restaurant_name"),
        source_type=source_type,
        source_value=source_value,
        raw_menu_text=raw_menu_text,
    )
    db.add(menu_source)
    db.flush()

    recipes: List[Dict[str, Any]] = parsed_menu.get("recipes", [])

    for recipe_data in recipes:
        recipe = Recipe(
            menu_source_id=menu_source.id,
            dish_name=recipe_data.get("dish_name", "").strip(),
            description=recipe_data.get("description"),
            estimated_serving_size=recipe_data.get("estimated_serving_size"),
        )
        db.add(recipe)
        db.flush()

        for ing_data in recipe_data.get("ingredients", []):
            ing_name = (ing_data.get("name") or "").strip()
            if not ing_name:
                continue

            ingredient = get_or_create_ingredient(db, ing_name)

            recipe_ingredient = RecipeIngredient(
                recipe_id=recipe.id,
                ingredient_id=ingredient.id,
                quantity=ing_data.get("quantity"),
                unit=ing_data.get("unit"),
                preparation_notes=ing_data.get("preparation_notes"),
                confidence_note=ing_data.get("confidence_note"),
            )
            db.add(recipe_ingredient)

    db.commit()
    db.refresh(menu_source)
    return menu_source