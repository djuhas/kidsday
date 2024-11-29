import os
from fastapi import FastAPI, Request, Form, Body, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from aiohttp import ClientSession, ClientTimeout
from datetime import datetime

# Dohvaćanje varijabli iz okruženja
API_KEY = os.getenv('API_KEY')
ENDPOINT = os.getenv('ENDPOINT')
SECRET_KEY = os.getenv('SECRET_KEY')

if not API_KEY or not ENDPOINT or not SECRET_KEY:
    raise ValueError("API_KEY, ENDPOINT i SECRET_KEY moraju biti postavljeni u varijablama okruženja")

app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Montiranje statičkih datoteka
app.mount("/static", StaticFiles(directory="static"), name="static")

# Postavljanje predložaka
templates = Jinja2Templates(directory="templates")


def get_assistant_instructions(level, nickname):
    """Dohvaća upute za asistenta iz varijabli okruženja."""
    instruction_key = f'ASSISTANT_INSTRUCTIONS_LEVEL_{level}'
    instructions = os.getenv(instruction_key, "Ti si zadani asistent.")
    instructions = instructions.replace("{nickname}", nickname)
    return instructions


def get_initial_message(level, nickname):
    """Dohvaća početnu poruku asistenta iz varijabli okruženja."""
    message_key = f'ASSISTANT_INITIAL_MESSAGE_LEVEL_{level}'
    message = os.getenv(message_key, "Pozdrav! Kako ti mogu pomoći?")
    message = message.replace("{nickname}", nickname)
    return message


def get_presets_for_level(level):
    """Dohvaća brze poruke iz varijabli okruženja za određeni level."""
    presets_key = f'PRESETS_LEVEL_{level}'
    presets = os.getenv(presets_key, "")
    return [preset.strip() for preset in presets.split(",") if preset.strip()]


def get_question_for_level(level):
    """Dohvaća pitanje za određeni level iz varijabli okruženja."""
    question_key = f'QUESTION_LEVEL_{level}'
    question = os.getenv(question_key, "Pitanje nije definirano za ovaj level.")
    return question


def is_next_activity(activity_time):
    """Provjerava je li aktivnost sljedeća na rasporedu."""
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
        {"time": "09:00", "activity": "Dobrodošlica"},
        {"time": "10:00", "activity": "Igra 1"},
        {"time": "11:00", "activity": "Pauza"},
        {"time": "12:00", "activity": "Igra 2"},
    ]
    return templates.TemplateResponse("schedule.html", {
        "request": request,
        "nickname": nickname,
        "city": city,
        "schedule": schedule,
        "is_next_activity": is_next_activity,
    })


@app.get("/chat", response_class=HTMLResponse)
async def get_chat(request: Request, message: str = Query(None)):
    nickname = request.session.get('nickname')
    level = request.session.get('level', 1)
    if not nickname:
        return RedirectResponse(url="/")
    conversation = request.session.get('conversation', [])
    if not conversation:
        initial_message = get_initial_message(level, nickname)
        conversation.append({"role": "assistant", "content": initial_message})
        request.session['conversation'] = conversation
    assistant_name = "Magenta"  # Ime asistenta
    presets = get_presets_for_level(level)  # Dohvati brze poruke za trenutni level
    question = get_question_for_level(level)  # Dohvati pitanje za trenutni level
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "conversation": conversation,
        "nickname": nickname,
        "assistant_name": assistant_name,
        "presets": presets,
        "question": question,  # Prosljeđivanje pitanja
        "message": message,  # Prosljeđivanje poruke ako postoji
    })


@app.post("/chat")
async def post_chat(request: Request, data: dict = Body(...)):
    user_input = data.get('user_input')
    nickname = request.session.get('nickname')
    level = request.session.get('level', 1)
    if not nickname:
        return JSONResponse({"error": "Neautorizirano"}, status_code=401)
    conversation = request.session.get('conversation', [])
    instructions = get_assistant_instructions(level, nickname)
    # Provjera i dodavanje 'system' poruke ako ne postoji
    if not any(msg['role'] == 'system' and msg['content'] == instructions for msg in conversation):
        conversation.insert(0, {"role": "system", "content": instructions})
    conversation.append({"role": "user", "content": user_input})
    max_history = 10
    conversation_to_send = conversation[:1] + conversation[-(max_history - 1):]
    assistant_response = await get_llm_response(conversation_to_send)
    conversation.append({"role": "assistant", "content": assistant_response})
    request.session['conversation'] = conversation
    return JSONResponse({"assistant_response": assistant_response})


@app.post("/next_level")
async def next_level(request: Request, keyword: str = Form(...)):
    """
    Provjera ključne riječi i prelazak na sljedeći level ako je ispravna.
    Ako je trenutni level 9 i ključna riječ je ispravna, preusmjerava na stranicu uspjeha.
    """
    nickname = request.session.get('nickname')
    level = request.session.get('level', 1)

    if not nickname:
        return JSONResponse({"success": False, "message": "Neautorizirano"}, status_code=401)

    # Dohvati ispravnu ključnu riječ za trenutni level iz varijabli okruženja
    correct_keyword_key = f'KEYWORD_LEVEL_{level}'
    correct_keyword = os.getenv(correct_keyword_key, "").strip().lower()  # Pretvori u lowercase radi lakše usporedbe

    # Provjeri unesenu ključnu riječ (ignoriraj velika/mala slova)
    if keyword.strip().lower() == correct_keyword:
        if level == 9:
            # Ako je ovo posljednji level, postavi poruku i preusmjeri na stranicu uspjeha
            message = "BRAVO! Završili ste sve odjele."
            return JSONResponse({"success": True, "message": message, "next_url": "/success"}, status_code=200)
        else:
            # Prelazak na sljedeći level s porukom
            level += 1
            request.session['level'] = level
            request.session['conversation'] = []  # Resetiranje razgovora za novi level
            message = "BRAVO! Idemo u sljedeći odjel."
            return JSONResponse({"success": True, "message": message, "next_url": "/chat"}, status_code=200)
    else:
        # Ako ključna riječ nije ispravna, dodaj poruku u razgovor
        conversation = request.session.get('conversation', [])
        conversation.append({
            "role": "assistant",
            "content": "Pogrešna ključna riječ. Pokušajte ponovno."
        })
        request.session['conversation'] = conversation
        presets = get_presets_for_level(level)
        question = get_question_for_level(level)
        assistant_name = "Magenta"
        return JSONResponse({
            "success": False,
            "message": "Pogrešna ključna riječ. Pokušajte ponovno.",
            "conversation": conversation,
            "presets": presets,
            "question": question,
            "assistant_name": assistant_name,
            "nickname": nickname
        }, status_code=200)


@app.get("/success", response_class=HTMLResponse)
async def success(request: Request, message: str = Query(None)):
    """Prikazuje stranicu uspjeha nakon završetka levela."""
    nickname = request.session.get('nickname')
    if not nickname:
        return RedirectResponse(url="/")
    return templates.TemplateResponse("uspjeh.html", {
        "request": request,
        "nickname": nickname,
        "message": message,  # Prosljeđivanje poruke ako postoji
    })


@app.post("/restart")
async def restart(request: Request):
    """Resetira sesiju i preusmjerava korisnika na početnu stranicu."""
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
    timeout = ClientTimeout(total=60)  # Povećajte timeout na 60 sekundi
    async with ClientSession(timeout=timeout) as session:
        try:
            async with session.post(ENDPOINT, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data['choices'][0]['message']['content']
                else:
                    # Rukovanje greškom bez logiranja
                    return "Došlo je do greške pri komunikaciji s AI modelom."
        except Exception:
            # Rukovanje iznimkom bez logiranja
            return "Došlo je do greške pri komunikaciji s AI modelom."


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
