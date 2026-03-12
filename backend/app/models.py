from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class MenuSource(Base):
    __tablename__ = "menu_sources"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_name = Column(String(255), nullable=True)
    source_type = Column(String(50), nullable=False)  # text, url
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