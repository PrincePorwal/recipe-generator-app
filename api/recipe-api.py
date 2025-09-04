"""
Flask Recipe API for Netlify Serverless Function
Location: netlify/functions/recipe-api.py
"""

import json
import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
import requests
from typing import List, Dict, Any
import serverless_wsgi # <-- ADDED IMPORT

app = Flask(__name__)
CORS(app)

# ... (All your functions from init_firebase to create_fallback_recipe stay exactly the same) ...
# (No changes needed in the middle of the file)
def init_firebase():
    """Initialize Firebase connection using environment variables"""
    try:
        firebase_admin.get_app()
    except ValueError:
        cred_dict = {
            "type": "service_account",
            "project_id": os.environ.get('FIREBASE_PROJECT_ID'),
            "private_key_id": os.environ.get('FIREBASE_PRIVATE_KEY_ID'),
            "private_key": os.environ.get('FIREBASE_PRIVATE_KEY', '').replace('\\n', '\n'),
            "client_email": os.environ.get('FIREBASE_CLIENT_EMAIL'),
            "client_id": os.environ.get('FIREBASE_CLIENT_ID'),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": os.environ.get('FIREBASE_CERT_URL')
        }
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
    return firestore.client()

TOGETHER_API_KEY = os.environ.get('TOGETHER_API_KEY')
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
MODEL_NAME = "meta-llama/Llama-3-8B-Instruct-Turbo"

def search_recipes_in_db(ingredients: List[str], db) -> List[Dict[str, Any]]:
    try:
        recipes_ref = db.collection('recipes')
        matching_recipes = []
        all_recipes = recipes_ref.stream()
        for doc in all_recipes:
            recipe_data = doc.to_dict()
            recipe_ingredients = [ing.lower() for ing in recipe_data.get('ingredients', [])]
            if all(ingredient in recipe_ingredients for ingredient in ingredients):
                matching_recipes.append({
                    'id': doc.id,
                    'title': recipe_data.get('title', 'Untitled Recipe'),
                    'description': recipe_data.get('description', ''),
                    'ingredients': recipe_data.get('ingredients', []),
                    'instructions': recipe_data.get('instructions', [])
                })
        return matching_recipes
    except Exception as e:
        print(f"Database search error: {str(e)}")
        return []

def generate_recipe_with_ai(ingredients: List[str]) -> Dict[str, Any]:
    ingredients_str = ", ".join(ingredients)
    prompt = f"""You are a helpful culinary assistant named 'Chef Gemini'. Your task is to create a simple, easy-to-follow recipe using only a specific list of ingredients.
**RULES:**
1. Use only the ingredients provided. You may assume common pantry staples like salt, pepper, oil, and water are available.
2. The tone should be encouraging and simple.
3. The output must be in a clean, readable format.
**INGREDIENTS:**
{ingredients_str}
**RECIPE:**
**Title:** [Create a catchy and descriptive title for the recipe]
**Description:** [Write a one or two-sentence description of the dish]
**Ingredients List:**
- [List each ingredient with an estimated measurement (e.g., 1 cup, 200g)]
**Instructions:**
1. [Write the first clear, step-by-step instruction]
2. [Write the next instruction]
3. [Continue with all necessary steps]"""
    headers = {
        "Authorization": f"Bearer {TOGETHER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You are a helpful culinary assistant."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 800, "temperature": 0.7, "top_p": 0.9,
        "top_k": 50, "repetition_penalty": 1.1, "stop": ["<|eot_id|>"]
    }
    try:
        response = requests.post(TOGETHER_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        recipe_text = result['choices'][0]['message']['content']
        return parse_ai_recipe(recipe_text, ingredients)
    except Exception as e:
        print(f"AI generation error: {str(e)}")
        return create_fallback_recipe(ingredients)

def parse_ai_recipe(recipe_text: str, original_ingredients: List[str]) -> Dict[str, Any]:
    lines = recipe_text.split('\n')
    title, description, ingredients, instructions = "AI-Generated Recipe", "A delicious recipe created just for you!", [], []
    current_section = None
    for line in lines:
        line = line.strip()
        if not line: continue
        if '**Title:**' in line: title = line.replace('**Title:**', '').replace('**', '').strip()
        elif '**Description:**' in line: description = line.replace('**Description:**', '').replace('**', '').strip()
        elif '**Ingredients List:**' in line: current_section = 'ingredients'
        elif '**Instructions:**' in line: current_section = 'instructions'
        elif current_section == 'ingredients' and line.startswith('-'): ingredients.append(line[1:].strip())
        elif current_section == 'instructions' and line[0].isdigit():
            instruction = line.split('.', 1)[1].strip() if '.' in line else line
            instructions.append(instruction)
    if not ingredients: ingredients = [f"{ing} - as needed" for ing in original_ingredients]
    if not instructions: instructions = ["Prepare all ingredients", "Combine ingredients as appropriate", "Cook until done", "Season to taste and serve"]
    return {'title': title, 'description': description, 'ingredients': ingredients, 'instructions': instructions}

def create_fallback_recipe(ingredients: List[str]) -> Dict[str, Any]:
    return {
        'title': f"Simple {ingredients[0].title()} Dish",
        'description': f"A quick and easy recipe using {', '.join(ingredients)}",
        'ingredients': [f"{ing} - as needed" for ing in ingredients],
        'instructions': [
            "Wash and prepare all ingredients", "Heat oil in a pan over medium heat",
            f"Add {ingredients[0]} to the pan and cook for 3-5 minutes",
            "Add remaining ingredients and cook until tender",
            "Season with salt and pepper to taste", "Serve hot and enjoy!"
        ]
    }

# This is now the main API endpoint
@app.route('/recipe-api', methods=['POST']) # <-- CHANGED ROUTE
def handle_recipe_request():
    try:
        data = request.get_json()
        if not data: return jsonify({'error': 'No data provided'}), 400
        
        ingredients = [ing.lower().strip() for ing in data.get('ingredients', [])]
        force_ai = data.get('forceAI', False)
        
        if not ingredients: return jsonify({'error': 'No ingredients provided'}), 400
        
        db = init_firebase()
        
        if not force_ai:
            db_recipes = search_recipes_in_db(ingredients, db)
            if db_recipes:
                return jsonify({'source': 'database', 'recipes': db_recipes}), 200
        
        ai_recipe = generate_recipe_with_ai(ingredients)
        
        if os.environ.get('SAVE_AI_RECIPES', 'false').lower() == 'true':
            try:
                db.collection('recipes').add({
                    'title': ai_recipe['title'], 'description': ai_recipe['description'],
                    'ingredients': [ing.lower() for ing in ingredients],
                    'instructions': ai_recipe['instructions'],
                    'source': 'ai', 'created_at': firestore.SERVER_TIMESTAMP
                })
            except Exception as e:
                print(f"Failed to save AI recipe: {str(e)}")
        
        return jsonify({'source': 'ai', 'recipe': ai_recipe}), 200
    except Exception as e:
        print(f"API error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

# This is the new, simplified handler for Netlify
def handler(event, context):
    return serverless_wsgi.handle(app, event, context)