import json
import logging
import httpx
from config import OLLAMA_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

PLAN_SYSTEM_PROMPT = (
    "You are a nutritionist assistant helping someone casually track their meals. "
    "Look at the meal description and decide upfront what clarification you need.\n"
    "Rules:\n"
    "1. Ask 0 questions if you can already make a reasonable estimate. "
    "Before asking any question, ask yourself: does knowing the answer change the calorie estimate "
    "by more than 20%? If not, skip the question and estimate directly. "
    "Worth asking: portion size when unknown (Small vs. Large can double calories), cooking method "
    "for a bare raw protein. Not worth asking: anything already described in the meal, cooking "
    "method for named complete dishes, or minor garnishes.\n"
    "2. Strongly prefer asking just 1 question — the single most important unknown about the stated meal.\n"
    "3. Ask 2 questions only if two completely different aspects of the stated meal "
    "(e.g. cooking method AND quantity) each change the estimate by >20%. "
    "Ask 3 questions only in rare cases where three distinct unknowns are all critical. "
    "Each question must address a different topic — options must all be valid answers "
    "to that one question only.\n"
    "4. NEVER ask more than 3 questions.\n"
    "5. ALWAYS include options for every question. NEVER ask text-only questions.\n"
    "6. Each question must ask about exactly ONE attribute. NEVER write a question that asks "
    "two things at once (e.g. 'What is it and how was it prepared?' is two questions — wrong).\n"
    "7. Options must each represent a single concept. NEVER combine two attributes into one option "
    "(e.g. 'Chicken, Grilled' mixes ingredient + cooking method — wrong). "
    "If both matter, use two separate questions.\n"
    "8. ONLY ask about what the user explicitly mentioned. Do NOT introduce ingredients, sides, "
    "or foods not mentioned.\n"
    "9. Cooking method rule: ONLY ask 'How was it cooked?' when ALL of these are true: "
    "(a) the user mentioned a bare raw protein (chicken, beef, fish, pork, egg) with no other context; "
    "(b) no preparation details were given (no sauce, no dish name, no cooking word); "
    "(c) the cooking method changes the estimate by >20%. "
    "NEVER ask about cooking for: complete named dishes (pasta, soup, stew, curry, sandwich, salad, "
    "pizza, fried rice, burger, sushi), meals that already describe preparation "
    "(e.g. 'with olive oil', 'stir-fried', 'in sauce', 'salami pasta'), or brand/product names.\n"
    "10. Use natural food units for options:\n"
    "   chicken/meat/fish alone → cooking: [Grilled, Fried, Baked, Boiled];\n"
    "   chicken/meat/fish alone → quantity: [1 pc, 2 pcs, 3 pcs, 4+ pcs];\n"
    "   bread → type: [White, Wheat, Sourdough, Rye];\n"
    "   bread → quantity: [1 slice, 2 slices, 3 slices, 4+ slices];\n"
    "   rice → type: [White, Brown];\n"
    "   rice → portion: [Small, Regular, Large];\n"
    "   pasta/noodles → portion: [Small bowl, Regular bowl, Full plate];\n"
    "   eggs → quantity: [1 egg, 2 eggs, 3 eggs, 4+ eggs];\n"
    "   ice cream → serving: [1 cone, 1 small cup, 1 large cup, 1 bowl];\n"
    "   pizza → slices: [1 slice, 2 slices, 3 slices, 4+ slices];\n"
    "   soda/coke/juice → size: [Small, Medium, Large];\n"
    "   milk/water/coffee → amount: [1 glass, 2 glasses, 1 cup, 1 large cup];\n"
    "   candy/sweets/chocolate → pieces: [1 pc, 2 pcs, 3 pcs, 5+ pcs];\n"
    "   cookies/biscuits → pieces: [1 pc, 2 pcs, 3 pcs, 4+ pcs];\n"
    "   unknown portion → size: [Small, Medium, Large, Extra Large].\n"
    "11. NEVER ask for measurements (grams, oz, ml, cm).\n"
    "Return only a JSON object, no markdown, no extra text."
)

PLAN_PROMPT = """Meal: "{meal}"

Return exactly ONE of these JSON formats:

If you have enough info to estimate:
{{"type": "estimate", "calories": [min, max], "protein_g": [min, max], "carbs_g": [min, max], "fat_g": [min, max], "portion_note": "brief assumption"}}

If you need 1 question (e.g. portion size unknown):
{{"type": "questions", "questions": [{{"question": "How much did you have?", "options": ["Small portion", "Regular portion", "Large portion", "Extra large"]}}]}}

If you need 2 questions (different topics only, e.g. bare protein where both cooking and quantity matter):
{{"type": "questions", "questions": [{{"question": "How was the chicken cooked?", "options": ["Grilled", "Fried", "Baked", "Boiled"]}}, {{"question": "How many pieces?", "options": ["1 pc", "2 pcs", "3 pcs", "4+ pcs"]}}]}}

If you truly need 3 questions (rare — each a different critical unknown):
{{"type": "questions", "questions": [{{"question": "Q1?", "options": [...]}}, {{"question": "Q2?", "options": [...]}}, {{"question": "Q3?", "options": [...]}}]}}

All numbers must be integers."""

CLASSIFY_SYSTEM_PROMPT = (
    "You are an intent classifier for a calorie tracking Telegram bot. "
    "Classify the user's message intent.\n"
    "- User is logging or describing a meal, drink, snack, or supplement they had → type 'tracking'\n"
    "- Anything else (greetings, questions, random chat, non-food, too vague) → type 'general'\n"
    "Return only a JSON object, no markdown, no extra text."
)

CLASSIFY_PROMPT = """Message: "{text}"

Return exactly one of:
{{"type": "tracking"}}
{{"type": "general"}}"""

GENERAL_SYSTEM_PROMPT = (
    "You are a calorie tracking bot assistant with a quirky, funny, slightly cringe personality. "
    "You're having a casual conversation with the user. Keep replies short (1-3 sentences). "
    "Be playful and respond directly to what they said. Mention food/calories only if it fits naturally."
)

FINALIZE_SYSTEM_PROMPT = (
    "You are a nutritionist assistant. Given a meal and the user's answers to clarifying questions, "
    "estimate the nutritional content. Be specific based on the answers given. "
    "Return only a JSON object, no markdown, no extra text."
)

FINALIZE_PROMPT = """Meal: "{meal}"

User's answers to clarifying questions:
{qa_block}

Return an estimate:
{{"type": "estimate", "calories": [min, max], "protein_g": [min, max], "carbs_g": [min, max], "fat_g": [min, max], "portion_note": "brief assumption"}}

All numbers must be integers."""

REQUIRED_KEYS = {"calories", "protein_g", "carbs_g", "fat_g"}


def _to_range(val) -> "list | None":
    """Accept [min, max] arrays or {"min": x, "max": y} objects; return [min, max] or None."""
    if isinstance(val, list) and len(val) == 2:
        lo, hi = val
    elif isinstance(val, dict) and "min" in val and "max" in val:
        lo, hi = val["min"], val["max"]
    else:
        return None
    if not all(isinstance(v, (int, float)) for v in (lo, hi)):
        return None
    return [int(lo), int(hi)]


def _validate_and_normalize(data: dict) -> "dict | None":
    if not REQUIRED_KEYS.issubset(data.keys()):
        return None
    for key in REQUIRED_KEYS:
        r = _to_range(data[key])
        if r is None:
            return None
        data[key] = r
    return data


def _validate_plan(data: dict) -> "dict | None":
    if data.get("type") == "estimate":
        return _validate_and_normalize(data)
    if data.get("type") == "questions":
        qs = data.get("questions", [])
        if isinstance(qs, dict):
            qs = [qs]
        if not isinstance(qs, list) or not qs:
            return None
        valid_qs = [
            q for q in qs
            if isinstance(q, dict)
            and isinstance(q.get("question"), str)
            and q["question"].strip()
            and isinstance(q.get("options"), list)
            and len(q["options"]) >= 2
        ]
        if not valid_qs:
            return None
        data["questions"] = valid_qs[:3]
        return data
    # Normalize singular "question" — phi3.5 sometimes returns this format
    if data.get("type") == "question":
        q = data.get("question", "")
        opts = data.get("options", [])
        if isinstance(q, str) and q.strip() and isinstance(opts, list) and len(opts) >= 2:
            return {"type": "questions", "questions": [{"question": q, "options": opts}]}
    return None


async def plan(meal: str) -> dict:
    prompt = PLAN_PROMPT.format(meal=meal)
    payload = {
        "model": OLLAMA_MODEL,
        "system": PLAN_SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(2):
            try:
                resp = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
                resp.raise_for_status()
                raw = resp.json()["response"]
                data = json.loads(raw)
                result = _validate_plan(data)
                if result is not None:
                    return result
                logger.warning("plan() invalid response (attempt %d): %s", attempt + 1, raw)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("plan() parse error (attempt %d): %s", attempt + 1, e)
    raise ValueError("Could not get a valid plan from the model.")


async def classify(text: str) -> dict:
    prompt = CLASSIFY_PROMPT.format(text=text)
    payload = {
        "model": OLLAMA_MODEL,
        "system": CLASSIFY_SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(2):
            try:
                resp = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
                resp.raise_for_status()
                raw = resp.json()["response"]
                data = json.loads(raw)
                t = data.get("type")
                if t in ("tracking", "general"):
                    return {"type": t}
                logger.warning("classify() invalid response (attempt %d): %s", attempt + 1, raw)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("classify() parse error (attempt %d): %s", attempt + 1, e)
    raise ValueError("Could not get a valid classification from the model.")


async def general_reply(text: str, history: "list[dict]") -> str:
    messages = [{"role": "system", "content": GENERAL_SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": text})
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
            content = resp.json()["message"]["content"]
            if isinstance(content, str) and content.strip():
                return content.strip()
        except (KeyError, Exception) as e:
            logger.warning("general_reply() error: %s", e)
    raise ValueError("Could not get a general reply from the model.")


async def finalize(meal: str, questions: "list[dict]", answers: "list[str]") -> dict:
    lines = []
    for i, answer in enumerate(answers):
        if i < len(questions):
            lines.append(f"Q: {questions[i]['question']}")
        lines.append(f"A: {answer}")
    qa_block = "\n".join(lines)

    prompt = FINALIZE_PROMPT.format(meal=meal, qa_block=qa_block)
    payload = {
        "model": OLLAMA_MODEL,
        "system": FINALIZE_SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(2):
            try:
                resp = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
                resp.raise_for_status()
                raw = resp.json()["response"]
                data = json.loads(raw)
                if data.get("type") == "estimate":
                    normalized = _validate_and_normalize(data)
                    if normalized is not None:
                        return normalized
                logger.warning("finalize() invalid response (attempt %d): %s", attempt + 1, raw)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("finalize() parse error (attempt %d): %s", attempt + 1, e)
    raise ValueError("Could not get a valid estimate from the model.")
