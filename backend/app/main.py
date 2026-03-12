from typing import List

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, joinedload

from app.database import Base, engine, get_db
from app.models import MenuSource, Recipe, RecipeIngredient
from app.schemas import MenuSourceOut, ParseMenuRequest
from services.menu_parser import (
    fetch_menu_text_from_url,
    parse_menu_with_gemini,
    save_parsed_menu,
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Pathway RFP Pipeline - Step 1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "ok", "step": 1}


@app.post("/step1/parse-menu", response_model=MenuSourceOut)
def step1_parse_menu(payload: ParseMenuRequest, db: Session = Depends(get_db)):
    try:
        payload.validate_input()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        if payload.menu_url:
            raw_menu_text = fetch_menu_text_from_url(str(payload.menu_url))
            source_type = "url"
            source_value = str(payload.menu_url)
        else:
            raw_menu_text = payload.menu_text.strip()
            source_type = "text"
            source_value = raw_menu_text

        parsed_menu = parse_menu_with_gemini(
            menu_text=raw_menu_text,
            restaurant_name=payload.restaurant_name,
        )

        menu_source = save_parsed_menu(
            db=db,
            parsed_menu=parsed_menu,
            source_type=source_type,
            source_value=source_value,
            raw_menu_text=raw_menu_text,
            restaurant_name=payload.restaurant_name,
        )

        saved = (
            db.query(MenuSource)
            .options(
                joinedload(MenuSource.recipes)
                .joinedload(Recipe.ingredients)
                .joinedload(RecipeIngredient.ingredient)
            )
            .filter(MenuSource.id == menu_source.id)
            .first()
        )

        return transform_menu_source(saved)

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/step1/menus", response_model=List[MenuSourceOut])
def list_parsed_menus(db: Session = Depends(get_db)):
    menu_sources = (
        db.query(MenuSource)
        .options(
            joinedload(MenuSource.recipes)
            .joinedload(Recipe.ingredients)
            .joinedload(RecipeIngredient.ingredient)
        )
        .order_by(MenuSource.id.desc())
        .all()
    )

    return [transform_menu_source(item) for item in menu_sources]


def transform_menu_source(menu_source: MenuSource):
    return {
        "id": menu_source.id,
        "restaurant_name": menu_source.restaurant_name,
        "source_type": menu_source.source_type,
        "source_value": menu_source.source_value,
        "raw_menu_text": menu_source.raw_menu_text,
        "recipes": [
            {
                "id": recipe.id,
                "dish_name": recipe.dish_name,
                "description": recipe.description,
                "estimated_serving_size": recipe.estimated_serving_size,
                "ingredients": [
                    {
                        "ingredient_name": recipe_ingredient.ingredient.name,
                        "quantity": recipe_ingredient.quantity,
                        "unit": recipe_ingredient.unit,
                        "preparation_notes": recipe_ingredient.preparation_notes,
                        "confidence_note": recipe_ingredient.confidence_note,
                    }
                    for recipe_ingredient in recipe.ingredients
                ],
            }
            for recipe in menu_source.recipes
        ],
    }