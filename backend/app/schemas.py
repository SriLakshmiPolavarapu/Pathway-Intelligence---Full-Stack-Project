from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl


class ParseMenuRequest(BaseModel):
    restaurant_name: Optional[str] = None
    menu_text: Optional[str] = None
    menu_url: Optional[HttpUrl] = None

    def validate_input(self):
        if not self.menu_text and not self.menu_url:
            raise ValueError("Either menu_text or menu_url must be provided.")


class ParsedIngredient(BaseModel):
    name: str
    quantity: Optional[float] = None
    unit: Optional[str] = None
    preparation_notes: Optional[str] = None
    confidence_note: Optional[str] = None


class ParsedRecipe(BaseModel):
    dish_name: str
    description: Optional[str] = None
    estimated_serving_size: Optional[str] = None
    ingredients: List[ParsedIngredient] = Field(default_factory=list)


class ParsedMenuResponse(BaseModel):
    restaurant_name: Optional[str] = None
    recipes: List[ParsedRecipe]


class RecipeIngredientOut(BaseModel):
    ingredient_name: str
    quantity: Optional[float] = None
    unit: Optional[str] = None
    preparation_notes: Optional[str] = None
    confidence_note: Optional[str] = None

    class Config:
        from_attributes = True


class RecipeOut(BaseModel):
    id: int
    dish_name: str
    description: Optional[str] = None
    estimated_serving_size: Optional[str] = None
    ingredients: List[RecipeIngredientOut]

    class Config:
        from_attributes = True


class MenuSourceOut(BaseModel):
    id: int
    restaurant_name: Optional[str] = None
    source_type: str
    source_value: str
    raw_menu_text: str
    recipes: List[RecipeOut]

    class Config:
        from_attributes = True