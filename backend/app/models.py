from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class MenuSource(Base):
    __tablename__ = "menu_sources"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_name = Column(String(255), nullable=True)
    source_type = Column(String(50), nullable=False)
    source_value = Column(Text, nullable=False)
    raw_menu_text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    recipes = relationship("Recipe", back_populates="menu_source", cascade="all, delete-orphan")


class Recipe(Base):
    __tablename__ = "recipes"

    id = Column(Integer, primary_key=True, index=True)
    menu_source_id = Column(Integer, ForeignKey("menu_sources.id"), nullable=False)
    dish_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    estimated_serving_size = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    menu_source = relationship("MenuSource", back_populates="recipes")
    ingredients = relationship("RecipeIngredient", back_populates="recipe", cascade="all, delete-orphan")


class Ingredient(Base):
    __tablename__ = "ingredients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    recipe_links = relationship("RecipeIngredient", back_populates="ingredient")
    price_snapshots = relationship("IngredientPriceSnapshot", back_populates="ingredient", cascade="all, delete-orphan")
    distributor_matches = relationship("IngredientDistributorMatch", back_populates="ingredient", cascade="all, delete-orphan")


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"

    id = Column(Integer, primary_key=True, index=True)
    recipe_id = Column(Integer, ForeignKey("recipes.id"), nullable=False)
    ingredient_id = Column(Integer, ForeignKey("ingredients.id"), nullable=False)
    quantity = Column(Float, nullable=True)
    unit = Column(String(50), nullable=True)
    preparation_notes = Column(String(255), nullable=True)
    confidence_note = Column(String(255), nullable=True)

    recipe = relationship("Recipe", back_populates="ingredients")
    ingredient = relationship("Ingredient", back_populates="recipe_links")


class IngredientPriceSnapshot(Base):
    __tablename__ = "ingredient_price_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    ingredient_id = Column(Integer, ForeignKey("ingredients.id"), nullable=False)
    commodity_name = Column(String(255), nullable=False)
    report_id = Column(String(50), nullable=False)
    report_title = Column(String(255), nullable=True)
    market_name = Column(String(255), nullable=True)
    office_name = Column(String(255), nullable=True)
    price_low = Column(Float, nullable=True)
    price_high = Column(Float, nullable=True)
    price_avg = Column(Float, nullable=True)
    unit = Column(String(100), nullable=True)
    report_date = Column(String(50), nullable=True)
    source = Column(String(100), nullable=False, default="USDA_MMN")
    raw_payload = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    ingredient = relationship("Ingredient", back_populates="price_snapshots")


class Distributor(Base):
    __tablename__ = "distributors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    category = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(50), nullable=True)
    address = Column(Text, nullable=True)
    phone = Column(String(100), nullable=True)
    website = Column(Text, nullable=True)
    email = Column(String(255), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    osm_type = Column(String(50), nullable=True)
    osm_id = Column(String(100), nullable=True, unique=True)
    source = Column(String(100), nullable=False, default="OSM")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    ingredient_matches = relationship("IngredientDistributorMatch", back_populates="distributor", cascade="all, delete-orphan")


class IngredientDistributorMatch(Base):
    __tablename__ = "ingredient_distributor_matches"

    id = Column(Integer, primary_key=True, index=True)
    ingredient_id = Column(Integer, ForeignKey("ingredients.id"), nullable=False)
    distributor_id = Column(Integer, ForeignKey("distributors.id"), nullable=False)
    menu_source_id = Column(Integer, ForeignKey("menu_sources.id"), nullable=False)
    matched_category = Column(String(100), nullable=True)
    confidence_score = Column(Float, nullable=True)
    rationale = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    ingredient = relationship("Ingredient", back_populates="distributor_matches")
    distributor = relationship("Distributor", back_populates="ingredient_matches")


class RFPEmail(Base):
    __tablename__ = "rfp_emails"

    id = Column(Integer, primary_key=True, index=True)
    menu_source_id = Column(Integer, ForeignKey("menu_sources.id"), nullable=False)
    distributor_id = Column(Integer, ForeignKey("distributors.id"), nullable=False)
    to_email = Column(String(255), nullable=False)
    subject = Column(String(500), nullable=False)
    body_text = Column(Text, nullable=False)
    body_html = Column(Text, nullable=True)
    ingredient_count = Column(Integer, default=0)
    quote_deadline = Column(String(100), nullable=True)
    status = Column(String(50), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    distributor = relationship("Distributor")