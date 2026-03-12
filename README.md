# Pathway RFP Pipeline

Automated end-to-end system that parses a restaurant menu, finds ingredient pricing, discovers local distributors, sends RFP emails, and compares quotes — all programmatically.

## Restaurant Menu Source

Coastal Bistro — sample menu included in the UI, or upload any menu image.

## Tech Stack

Python, FastAPI, SQLAlchemy, SQLite, Google Gemini 2.0 Flash, USDA MARS API, OpenStreetMap (Nominatim + Overpass), Gmail SMTP, React 18.

## Setup

Prerequisites: Python 3.11+, Gemini API key, USDA API key, Gmail App Password (optional).

Install dependencies:

    cd pathway-rfp
    pip install -r requirements.txt

Set environment variables:

    export GEMINI_API_KEY="your-gemini-key"
    export USDA_API_KEY="your-usda-key"
    export EMAIL_MOCK_MODE=false
    export SMTP_USER="your@gmail.com"
    export SMTP_PASSWORD="your-app-password"
    export SENDER_EMAIL="your@gmail.com"
    export RESTAURANT_CITY="Portland"
    export RESTAURANT_STATE="Oregon"

Run the server:

    cd backend
    uvicorn app.main:app --reload --port 8000

API docs at http://127.0.0.1:8000/docs. Frontend UI by opening frontend/index.html in a browser.

## Step 1 — Menu to Recipes & Ingredients

Parses menu text or image with Gemini into structured recipes with ingredients and estimated quantities. Stores everything in SQLite across four tables: menu_sources, recipes, ingredients, and recipe_ingredients. Ingredients are deduplicated globally. Handles vague dish names by inferring reasonable classic recipes.

Test: POST /step1/parse-menu with restaurant_name and menu_text. Or upload an image via POST /step1/parse-menu-image. Verify with GET /step1/menus.

## Step 2 — Ingredient Pricing Trends (USDA API)

Queries the USDA MARS API for each extracted ingredient's recent pricing data. Stores price snapshots in the ingredient_price_snapshots table and computes trends (min, max, average, direction). Many specialty items like dressings and sauces aren't in USDA's database — the system handles this gracefully and logs zero snapshots without failing.

Test: POST /step2/fetch-pricing/{menu_source_id}. Verify with GET /step2/pricing-trends/{ingredient_id}.

## Step 3 — Find Local Distributors

Geocodes the restaurant's city and state via Nominatim, then searches the Overpass API for nearby food businesses including wholesalers, butchers, seafood shops, greengrocers, bakeries, and farms. Uses Gemini to intelligently match each ingredient to the most relevant distributors with confidence scores and rationale. Stores distributors and matches in the distributors and ingredient_distributor_matches tables.

If OSM returns no results, the system generates fallback mock distributors so the pipeline never stalls. When OSM returns too many results (e.g., 454 in Portland), it prioritizes specialty shops and caps at 20 for Gemini matching while storing all of them in the database.

Test: POST /step3/find-distributors/{menu_source_id}. Verify with GET /step3/distributors/{menu_source_id}.

## Step 4 — Send RFP Emails to Distributors

Composes professional HTML emails to each matched distributor with the full ingredient list, estimated quantities, and a 7-day quote deadline. Groups ingredients by distributor so each email only requests what that distributor is likely to carry. Sends via Gmail SMTP when configured, or runs in mock mode (emails saved to DB but not sent) when SMTP credentials are not set.

Emails are stored in the rfp_emails table with status tracking (sent, sent_mock, or failed) and full HTML body for preview.

Test: POST /step4/send-rfp-emails/{menu_source_id}. Verify with GET /step4/rfp-emails/{menu_source_id}. View full email with GET /step4/rfp-email-detail/{email_id}. Check Gmail Sent folder for actual delivery.

## Step 5 — Collect & Compare Quotes (Nice-to-have)

Simulates distributor quote replies using Gemini (each distributor responds in character based on their category), then parses the replies into structured price data with unit prices, minimum orders, and delivery terms. Checks each quote for completeness against the original RFP and generates follow-up emails for missing ingredients. Compiles a comparison table across all distributors and produces an AI-powered recommendation based on total cost, coverage, delivery lead time, and payment terms.

Stores quotes in distributor_quotes and line items in distributor_quote_items. Also supports manual quote submission for real email replies.

Test: POST /step5/simulate-replies/{menu_source_id} to generate mock replies. GET /step5/quotes/{menu_source_id} to see parsed quotes. GET /step5/compare/{menu_source_id} for comparison table and recommendation. POST /step5/followup/{quote_id} to generate follow-up for incomplete quotes.

## Edge Cases Handled

Vague dish names are resolved by Gemini inferring classic recipes. Missing distributor data triggers fallback mock distributors. Gemini rate limits and JSON parsing failures fall back to rule-based matching. USDA API gaps for specialty items are logged without crashing. Large OSM result sets are capped and prioritized by specialty. Ingredients are deduplicated across dishes. Email sending works in both live SMTP and mock mode.