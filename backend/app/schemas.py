from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl


# -------------------------
# Step 1 - Menu Parsing
# -------------------------

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


# -------------------------
# Step 1 DB Response
# -------------------------

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


# -------------------------
# Step 2 - Pricing
# -------------------------

class PriceSnapshotOut(BaseModel):
    id: int
    commodity_name: str
    report_id: str
    report_title: Optional[str] = None
    market_name: Optional[str] = None
    office_name: Optional[str] = None
    price_low: Optional[float] = None
    price_high: Optional[float] = None
    price_avg: Optional[float] = None
    unit: Optional[str] = None
    report_date: Optional[str] = None
    source: str

    class Config:
        from_attributes = True


class IngredientPricingTrendOut(BaseModel):
    ingredient_id: int
    ingredient_name: str
    commodity_name: Optional[str] = None
    snapshot_count: int
    latest_price_avg: Optional[float] = None
    min_price_avg: Optional[float] = None
    max_price_avg: Optional[float] = None
    avg_price_avg: Optional[float] = None
    trend: str
    latest_unit: Optional[str] = None
    snapshots: List[PriceSnapshotOut]


# -------------------------
# Step 3 - Distributors
# -------------------------

class DistributorOut(BaseModel):
    id: int
    name: str
    category: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    source: str

    class Config:
        from_attributes = True


class IngredientDistributorMatchOut(BaseModel):
    ingredient_id: int
    ingredient_name: str
    distributor_id: int
    distributor_name: str
    matched_category: Optional[str] = None
    confidence_score: Optional[float] = None
    rationale: Optional[str] = None

    class Config:
        from_attributes = True


class Step3FindDistributorsResponse(BaseModel):
    menu_source_id: int
    restaurant_name: Optional[str] = None
    city: str
    state: str
    distributor_count: int
    match_count: int
    matches: List[IngredientDistributorMatchOut]


# -------------------------
# Step 4 - RFP Emails
# -------------------------

class RFPEmailOut(BaseModel):
    id: int
    distributor_id: int
    distributor_name: str
    to_email: str
    subject: str
    ingredient_count: int
    quote_deadline: Optional[str] = None
    status: str
    error_message: Optional[str] = None

    class Config:
        from_attributes = True


class RFPEmailDetailOut(RFPEmailOut):
    body_text: str
    body_html: Optional[str] = None


class SendRFPResponse(BaseModel):
    menu_source_id: int
    restaurant_name: str
    emails_sent: int
    mock_mode: bool
    quote_deadline: str
    results: list