from typing import List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, joinedload

from app.database import Base, engine, get_db
from app.models import Distributor, Ingredient, IngredientDistributorMatch, MenuSource, Recipe, RecipeIngredient, RFPEmail
from app.schemas import (
    IngredientDistributorMatchOut,
    IngredientPricingTrendOut,
    MenuSourceOut,
    ParseMenuRequest,
    RFPEmailDetailOut,
    RFPEmailOut,
    SendRFPResponse,
    Step3FindDistributorsResponse,
)
from services.distributor_service import find_and_store_distributors_for_menu, list_distributors_for_menu
from services.email_service import send_rfp_emails_for_menu, list_rfp_emails_for_menu, get_rfp_email_detail
from services.menu_parser import (
    fetch_menu_text_from_url,
    parse_menu_image_with_gemini,
    parse_menu_with_gemini,
    save_parsed_menu,
)
from services.nutrition_service import get_ingredient_nutrition
from services.pricing_service import (
    build_trend_summary,
    fetch_and_store_pricing_for_ingredient,
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Pathway RFP Pipeline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "message": "Pathway RFP backend is running",
        "step": "1, 2, 3 and 4",
        "health": "/health",
        "docs": "/docs",
    }


@app.get("/health")
def health_check():
    return {"status": "ok", "step": "1, 2, 3 and 4"}


# ── Step 1: Parse Menu ─────────────────────────────────────────

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


@app.post("/step1/parse-menu-image", response_model=MenuSourceOut)
async def step1_parse_menu_image(
    restaurant_name: Optional[str] = Form(None),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    try:
        if not image.content_type or not image.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="Please upload a valid image file.")

        image_bytes = await image.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="Uploaded image is empty.")

        extracted_menu_text, parsed_menu = parse_menu_image_with_gemini(
            image_bytes=image_bytes,
            restaurant_name=restaurant_name,
        )

        menu_source = save_parsed_menu(
            db=db,
            parsed_menu=parsed_menu,
            source_type="image",
            source_value=image.filename or "uploaded_menu_image",
            raw_menu_text=extracted_menu_text,
            restaurant_name=restaurant_name,
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

    except HTTPException:
        raise
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


# ── Step 2: Pricing ────────────────────────────────────────────

@app.get("/step2/recipe-nutrition/{recipe_id}")
def recipe_nutrition(recipe_id: int, db: Session = Depends(get_db)):
    recipe = (
        db.query(Recipe)
        .options(
            joinedload(Recipe.ingredients).joinedload(RecipeIngredient.ingredient)
        )
        .filter(Recipe.id == recipe_id)
        .first()
    )

    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    nutrition_totals = {
        "calories": 0.0,
        "protein": 0.0,
        "fat": 0.0,
        "carbs": 0.0,
    }

    ingredient_results = []

    for item in recipe.ingredients:
        ingredient_name = item.ingredient.name

        nutrition = get_ingredient_nutrition(
            ingredient_name=ingredient_name,
            quantity=item.quantity,
            unit=item.unit,
        )

        if nutrition and nutrition.get("scaled"):
            nutrition_totals["calories"] += nutrition.get("calories", 0.0)
            nutrition_totals["protein"] += nutrition.get("protein", 0.0)
            nutrition_totals["fat"] += nutrition.get("fat", 0.0)
            nutrition_totals["carbs"] += nutrition.get("carbs", 0.0)

        ingredient_results.append(
            {
                "ingredient": ingredient_name,
                "quantity": item.quantity,
                "unit": item.unit,
                "nutrition": nutrition,
            }
        )

    nutrition_totals = {
        "calories": round(nutrition_totals["calories"], 2),
        "protein": round(nutrition_totals["protein"], 2),
        "fat": round(nutrition_totals["fat"], 2),
        "carbs": round(nutrition_totals["carbs"], 2),
    }

    return {
        "recipe_id": recipe.id,
        "recipe": recipe.dish_name,
        "ingredients": ingredient_results,
        "total_nutrition": nutrition_totals,
    }


@app.post("/step2/fetch-pricing/{menu_source_id}")
def fetch_pricing_for_menu(menu_source_id: int, db: Session = Depends(get_db)):
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
        raise HTTPException(status_code=404, detail="Menu source not found")

    seen = set()
    ingredients = []

    for recipe in menu_source.recipes:
        for recipe_ingredient in recipe.ingredients:
            ingredient = recipe_ingredient.ingredient
            if ingredient.id not in seen:
                seen.add(ingredient.id)
                ingredients.append(ingredient)

    results = []
    total_stored = 0

    for ingredient in ingredients:
        result = fetch_and_store_pricing_for_ingredient(
            db=db,
            ingredient=ingredient,
        )
        total_stored += result["snapshots_stored"]
        results.append(result)

    return {
        "menu_source_id": menu_source.id,
        "restaurant_name": menu_source.restaurant_name,
        "ingredient_count": len(ingredients),
        "total_snapshots_stored": total_stored,
        "results": results,
    }


@app.get("/step2/pricing-trends/{ingredient_id}", response_model=IngredientPricingTrendOut)
def pricing_trends(ingredient_id: int, db: Session = Depends(get_db)):
    try:
        return build_trend_summary(db=db, ingredient_id=ingredient_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── Step 3: Find Distributors ──────────────────────────────────

@app.post("/step3/find-distributors/{menu_source_id}", response_model=Step3FindDistributorsResponse)
def find_distributors(menu_source_id: int, db: Session = Depends(get_db)):
    try:
        result = find_and_store_distributors_for_menu(db=db, menu_source_id=menu_source_id)

        return {
            "menu_source_id": result["menu_source_id"],
            "restaurant_name": result["restaurant_name"],
            "city": result["city"],
            "state": result["state"],
            "distributor_count": result["distributor_count"],
            "match_count": result["match_count"],
            "matches": [
                {
                    "ingredient_id": match.ingredient_id,
                    "ingredient_name": match.ingredient.name,
                    "distributor_id": match.distributor_id,
                    "distributor_name": match.distributor.name,
                    "matched_category": match.matched_category,
                    "confidence_score": match.confidence_score,
                    "rationale": match.rationale,
                }
                for match in result["matches"]
            ],
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/step3/distributors/{menu_source_id}", response_model=List[IngredientDistributorMatchOut])
def list_distributors(menu_source_id: int, db: Session = Depends(get_db)):
    matches = list_distributors_for_menu(db=db, menu_source_id=menu_source_id)

    return [
        {
            "ingredient_id": match.ingredient_id,
            "ingredient_name": match.ingredient.name,
            "distributor_id": match.distributor_id,
            "distributor_name": match.distributor.name,
            "matched_category": match.matched_category,
            "confidence_score": match.confidence_score,
            "rationale": match.rationale,
        }
        for match in matches
    ]


# ── Step 4: Send RFP Emails ───────────────────────────────────

@app.post("/step4/send-rfp-emails/{menu_source_id}", response_model=SendRFPResponse)
def send_rfp_emails(menu_source_id: int, db: Session = Depends(get_db)):
    try:
        result = send_rfp_emails_for_menu(db=db, menu_source_id=menu_source_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/step4/rfp-emails/{menu_source_id}", response_model=List[RFPEmailOut])
def list_rfp_emails(menu_source_id: int, db: Session = Depends(get_db)):
    emails = list_rfp_emails_for_menu(db=db, menu_source_id=menu_source_id)
    return [
        {
            "id": e.id,
            "distributor_id": e.distributor_id,
            "distributor_name": e.distributor.name,
            "to_email": e.to_email,
            "subject": e.subject,
            "ingredient_count": e.ingredient_count,
            "quote_deadline": e.quote_deadline,
            "status": e.status,
            "error_message": e.error_message,
        }
        for e in emails
    ]


@app.get("/step4/rfp-email-detail/{email_id}", response_model=RFPEmailDetailOut)
def rfp_email_detail(email_id: int, db: Session = Depends(get_db)):
    email = get_rfp_email_detail(db=db, email_id=email_id)
    if not email:
        raise HTTPException(status_code=404, detail="RFP email not found")
    return {
        "id": email.id,
        "distributor_id": email.distributor_id,
        "distributor_name": email.distributor.name,
        "to_email": email.to_email,
        "subject": email.subject,
        "body_text": email.body_text,
        "body_html": email.body_html,
        "ingredient_count": email.ingredient_count,
        "quote_deadline": email.quote_deadline,
        "status": email.status,
        "error_message": email.error_message,
    }


# ── Helper ─────────────────────────────────────────────────────

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