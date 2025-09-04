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

app = Flask(__name__)
CORS(app)

# Initialize Firebase Admin SDK
def init_firebase():
    """Initialize Firebase connection using environment variables"""
    try:
        # Check if already initialized
        firebase_admin.get_app()
    except ValueError:
        # Initialize with service account credentials from environment
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

# Together AI configuration
TOGETHER_API_KEY = os.environ.get('TOGETHER_API_KEY')
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
MODEL_NAME = "meta-llama/Llama-3-8B-Instruct-Turbo"

def search_recipes_in_db(ingredients: List[str], db) -> List[Dict[str, Any]]:
    """
    Search for recipes in Firestore that match the given ingredients
    """
    try:
        recipes_ref = db.collection('recipes')
        
        # Query for recipes that contain all the provided ingredients
        matching_recipes = []
        all_recipes = recipes_ref.stream()
        
        for doc in all_recipes:
            recipe_data = doc.to_dict()
            recipe_ingredients = [ing.lower() for ing in recipe_data.get('ingredients', [])]
            
            # Check if all user ingredients are in the recipe
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
    """
    Generate a recipe using Together AI's Llama model
    """
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
        "max_tokens": 800,
        "temperature": 0.7,
        "top_p": 0.9,
        "top_k": 50,
        "repetition_penalty": 1.1,
        "stop": ["<|eot_id|>"]
    }
    
    try:
        response = requests.post(TOGETHER_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        
        result = response.json()
        recipe_text = result['choices'][0]['message']['content']
        
        # Parse the AI response
        return parse_ai_recipe(recipe_text, ingredients)
        
    except Exception as e:
        print(f"AI generation error: {str(e)}")
        # Return a fallback recipe
        return create_fallback_recipe(ingredients)

def parse_ai_recipe(recipe_text: str, original_ingredients: List[str]) -> Dict[str, Any]:
    """
    Parse the AI-generated recipe text into structured format
    """
    lines = recipe_text.split('\n')
    
    title = "AI-Generated Recipe"
    description = "A delicious recipe created just for you!"
    ingredients = []
    instructions = []
    
    current_section = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if '**Title:**' in line:
            title = line.replace('**Title:**', '').replace('**', '').strip()
        elif '**Description:**' in line:
            description = line.replace('**Description:**', '').replace('**', '').strip()
        elif '**Ingredients List:**' in line:
            current_section = 'ingredients'
        elif '**Instructions:**' in line:
            current_section = 'instructions'
        elif current_section == 'ingredients' and line.startswith('-'):
            ingredients.append(line[1:].strip())
        elif current_section == 'instructions' and line[0].isdigit():
            # Remove the number and period from the start
            instruction = line.split('.', 1)[1].strip() if '.' in line else line
            instructions.append(instruction)
    
    # If parsing failed, ensure we have some content
    if not ingredients:
        ingredients = [f"{ing} - as needed" for ing in original_ingredients]
    if not instructions:
        instructions = [
            "Prepare all ingredients",
            "Combine ingredients as appropriate",
            "Cook until done",
            "Season to taste and serve"
        ]
    
    return {
        'title': title,
        'description': description,
        'ingredients': ingredients,
        'instructions': instructions
    }

def create_fallback_recipe(ingredients: List[str]) -> Dict[str, Any]:
    """
    Create a basic fallback recipe if AI generation fails
    """
    return {
        'title': f"Simple {ingredients[0].title()} Dish",
        'description': f"A quick and easy recipe using {', '.join(ingredients)}",
        'ingredients': [f"{ing} - as needed" for ing in ingredients],
        'instructions': [
            "Wash and prepare all ingredients",
            f"Heat oil in a pan over medium heat",
            f"Add {ingredients[0]} to the pan and cook for 3-5 minutes",
            "Add remaining ingredients and cook until tender",
            "Season with salt and pepper to taste",
            "Serve hot and enjoy!"
        ]
    }

@app.route('/', methods=['POST'])
def handle_recipe_request():
    """
    Main API endpoint for recipe requests
    """
    try:
        # Parse request data
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        ingredients = data.get('ingredients', [])
        force_ai = data.get('forceAI', False)
        
        if not ingredients:
            return jsonify({'error': 'No ingredients provided'}), 400
        
        # Normalize ingredients to lowercase
        ingredients = [ing.lower().strip() for ing in ingredients]
        
        # Initialize Firebase
        db = init_firebase()
        
        # If not forcing AI, search database first
        if not force_ai:
            db_recipes = search_recipes_in_db(ingredients, db)
            
            if db_recipes:
                return jsonify({
                    'source': 'database',
                    'recipes': db_recipes
                }), 200
        
        # No database matches or forced AI generation
        ai_recipe = generate_recipe_with_ai(ingredients)
        
        # Optionally save the AI-generated recipe to the database
        if os.environ.get('SAVE_AI_RECIPES', 'false').lower() == 'true':
            try:
                db.collection('recipes').add({
                    'title': ai_recipe['title'],
                    'description': ai_recipe['description'],
                    'ingredients': [ing.lower() for ing in ingredients],
                    'instructions': ai_recipe['instructions'],
                    'source': 'ai',
                    'created_at': firestore.SERVER_TIMESTAMP
                })
            except Exception as e:
                print(f"Failed to save AI recipe: {str(e)}")
        
        return jsonify({
            'source': 'ai',
            'recipe': ai_recipe
        }), 200
        
    except Exception as e:
        print(f"API error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

# Netlify serverless function handler
def handler(event, context):
    """
    AWS Lambda handler for Netlify Functions
    """
    from werkzeug.wrappers import Request, Response
    from werkzeug.serving import WSGIRequestHandler
    
    # Convert Lambda event to WSGI environ
    environ = {
        'REQUEST_METHOD': event.get('httpMethod', 'GET'),
        'SCRIPT_NAME': '',
        'PATH_INFO': '/',
        'QUERY_STRING': event.get('queryStringParameters', ''),
        'CONTENT_TYPE': event.get('headers', {}).get('content-type', ''),
        'CONTENT_LENGTH': str(len(event.get('body', ''))),
        'SERVER_NAME': 'localhost',
        'SERVER_PORT': '8888',
        'SERVER_PROTOCOL': 'HTTP/1.1',
        'wsgi.version': (1, 0),
        'wsgi.url_scheme': 'https',
        'wsgi.input': None,
        'wsgi.errors': None,
        'wsgi.multithread': False,
        'wsgi.multiprocess': False,
        'wsgi.run_once': True,
    }
    
    # Add headers
    for key, value in event.get('headers', {}).items():
        key = key.upper().replace('-', '_')
        if key not in ('CONTENT_TYPE', 'CONTENT_LENGTH'):
            environ[f'HTTP_{key}'] = value
    
    # Handle body
    if event.get('body'):
        from io import BytesIO
        environ['wsgi.input'] = BytesIO(event['body'].encode('utf-8'))
    
    # Process request through Flask app
    response_data = []
    response_headers = []
    
    def start_response(status, headers):
        response_headers.append(status)
        response_headers.extend(headers)
    
    app_iter = app(environ, start_response)
    response_data = b''.join(app_iter)
    
    # Convert WSGI response to Lambda response
    status_code = int(response_headers[0].split()[0])
    headers = {}
    for header in response_headers[1:]:
        headers[header[0]] = header[1]
    
    return {
        'statusCode': status_code,
        'headers': headers,
        'body': response_data.decode('utf-8')
    }

if __name__ == '__main__':
    app.run(debug=True)