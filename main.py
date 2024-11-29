import os
from fastapi import FastAPI, Request, Form, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from aiohttp import ClientSession, ClientTimeout
from datetime import datetime

# Fetching variables from the environment
API_KEY = os.getenv('API_KEY')
ENDPOINT = os.getenv('ENDPOINT')
SECRET_KEY = os.getenv('SECRET_KEY')

if not API_KEY or not ENDPOINT or not SECRET_KEY:
    raise ValueError("API_KEY, ENDPOINT, and SECRET_KEY must be set in the environment variables")

app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Mounting static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setting up templates
templates = Jinja2Templates(directory="templates")


def get_assistant_instructions(level, nickname):
    """Fetches assistant instructions from environment variables."""
    instruction_key = f'ASSISTANT_INSTRUCTIONS_LEVEL_{level}'
    instructions = os.getenv(instruction_key, "You are the default assistant.")
    instructions = instructions.replace("{nickname}", nickname)
    return instructions


def get_initial_message(level, nickname):
    """Fetches the assistant's initial message from environment variables."""
    message_key = f'ASSISTANT_INITIAL_MESSAGE_LEVEL_{level}'
    message = os.getenv(message_key, "Hello! How can I assist you?")
    message = message.replace("{nickname}", nickname)
    return message


def get_presets_for_level(level):
    """Fetches quick messages from environment variables for a specific level."""
    presets_key = f'PRESETS_LEVEL_{level}'
    presets = os.getenv(presets_key, "")
    return [preset.strip() for preset in presets.split(",") if preset.strip()]


def get_question_for_level(level):
    """Fetches the question for a specific level from environment variables."""
    question_key = f'QUESTION_LEVEL_{level}'
    question = os.getenv(question_key, "Question is not defined for this level.")
    return question


def is_next_activity(activity_time):
    """Checks if an activity is next on the schedule."""
    current_time = datetime.now().strftime("%H:%M")
    return activity_time > current_time


@app.get("/", response_class=HTMLResponse)
async def get_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/schedule")
async def post_schedule(request: Request, nickname: str = Form(...), city: str = Form(...)):
    request.session['nickname'] = nickname
    request.session['city'] = city
    request.session['level'] = 1
    return RedirectResponse(url="/schedule", status_code=303)


@app.get("/schedule", response_class=HTMLResponse)
async def get_schedule(request: Request):
    nickname = request.session.get('nickname')
    city = request.session.get('city')
    if not nickname or not city:
        return RedirectResponse(url="/")
    schedule = [
        {"time": "09:00", "activity": "Welcome"},
        {"time": "10:00", "activity": "Game 1"},
        {"time": "11:00", "activity": "Break"},
        {"time": "12:00", "activity": "Game 2"},
    ]
    return templates.TemplateResponse("schedule.html", {
        "request": request,
        "nickname": nickname,
        "city": city,
        "schedule": schedule,
        "is_next_activity": is_next_activity,
    })


@app.get("/chat", response_class=HTMLResponse)
async def get_chat(request: Request):
    nickname = request.session.get('nickname')
    level = request.session.get('level', 1)
    if not nickname:
        return RedirectResponse(url="/")
    conversation = request.session.get('conversation', [])
    if not conversation:
        initial_message = get_initial_message(level, nickname)
        conversation.append({"role": "assistant", "content": initial_message})
        request.session['conversation'] = conversation
    assistant_name = "Magenta"  # Assistant's name
    presets = get_presets_for_level(level)  # Get quick messages for the current level
    question = get_question_for_level(level)  # Get the question for the current level
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "conversation": conversation,
        "nickname": nickname,
        "assistant_name": assistant_name,
        "presets": presets,
        "question": question,  # Passing the question
    })


@app.post("/chat")
async def post_chat(request: Request, data: dict = Body(...)):
    user_input = data.get('user_input')
    nickname = request.session.get('nickname')
    level = request.session.get('level', 1)
    if not nickname:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conversation = request.session.get('conversation', [])
    instructions = get_assistant_instructions(level, nickname)
    # Check if 'system' message with instructions exists
    if not any(msg['role'] == 'system' and msg['content'] == instructions for msg in conversation):
        conversation.insert(0, {"role": "system", "content": instructions})
    conversation.append({"role": "user", "content": user_input})
    max_history = 10
    conversation_to_send = conversation[:1] + conversation[-(max_history-1):]
    assistant_response = await get_llm_response(conversation_to_send)
    conversation.append({"role": "assistant", "content": assistant_response})
    request.session['conversation'] = conversation
    return JSONResponse({"assistant_response": assistant_response})


@app.post("/next_level")
async def next_level(request: Request, keyword: str = Form(...)):
    """
    Checks the keyword and proceeds to the next level if correct.
    If the current level is 9 and the keyword is correct, redirects to the success page.
    """
    nickname = request.session.get('nickname')
    level = request.session.get('level', 1)

    if not nickname:
        return RedirectResponse(url="/")

    # Fetch the correct keyword for the current level from environment variables
    correct_keyword_key = f'KEYWORD_LEVEL_{level}'
    correct_keyword = os.getenv(correct_keyword_key, "").strip().lower()  # Convert to lowercase for comparison

    # Check the entered keyword (case-insensitive)
    if keyword.strip().lower() == correct_keyword:
        if level == 9:
            # If this is the last level, redirect to the success page
            return RedirectResponse(url="/success", status_code=303)
        else:
            # Proceed to the next level
            level += 1
            request.session['level'] = level
            request.session['conversation'] = []  # Reset conversation for the new level
            return RedirectResponse(url="/chat", status_code=303)
    else:
        # If the keyword is incorrect, add a message to the conversation
        conversation = request.session.get('conversation', [])
        conversation.append({
            "role": "assistant",
            "content": "Incorrect keyword. Please try again."
        })
        request.session['conversation'] = conversation
        presets = get_presets_for_level(level)
        question = get_question_for_level(level)
        assistant_name = "Magenta"
        return templates.TemplateResponse("chat.html", {
            "request": request,
            "conversation": conversation,
            "nickname": nickname,
            "assistant_name": assistant_name,
            "presets": presets,
            "question": question
        })


@app.get("/success", response_class=HTMLResponse)
async def success(request: Request):
    """Displays the success page after completing the level."""
    nickname = request.session.get('nickname')
    if not nickname:
        return RedirectResponse(url="/")
    return templates.TemplateResponse("success.html", {
        "request": request,
        "nickname": nickname
    })


@app.post("/restart")
async def restart(request: Request):
    """Resets the session and redirects the user to the home page."""
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


async def get_llm_response(conversation_history):
    headers = {
        "Content-Type": "application/json",
        "api-key": API_KEY
    }
    payload = {
        "messages": conversation_history,
        "max_tokens": 800,
        "temperature": 0.7,
        "top_p": 0.95,
    }
    timeout = ClientTimeout(total=60)  # Increase timeout to 60 seconds
    async with ClientSession(timeout=timeout) as session:
        try:
            async with session.post(ENDPOINT, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data['choices'][0]['message']['content']
                else:
                    # Handle error without logging
                    return "There was an error communicating with the AI model."
        except Exception:
            # Handle exception without logging
            return "There was an error communicating with the AI model."


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
