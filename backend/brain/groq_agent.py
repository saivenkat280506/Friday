"""
groq_agent.py — Direct Groq API Client (Fallback & Vision)  
===========================================================
Used for direct API calls, especially multimodal (vision) requests.
The main agent_graph.py handles primary routing via LangChain/LangGraph.
"""

import os
import json
import re
import httpx
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from config import settings
from brain.friday_style import SPOKEN_SYSTEM

GROQ_API_KEY = settings.GROQ_API_KEY

SYSTEM_PROMPT = SPOKEN_SYSTEM + """

=== VOICE MODE ===
Always assume speech. Short, clean, no markdown.

=== COMMAND EXECUTION ===
You can execute system-level actions. Supported actions:

1. open_app:
{
  "action": "open_app",
  "app_name": "chrome",
  "response": "Opening Chrome now, Boss."
}

2. type_text:
{
  "action": "type_text",
  "text": "Hello world",
  "response": "Typing that for you."
}

3. press_key / hotkey:
{
  "action": "hotkey",
  "keys": ["command", "c"],
  "response": "Copying to clipboard."
}

=== STRICT RULES ===
- No long paragraphs
- No fluff or filler
- Always prioritize execution over explanation
- Include a "response" key if returning JSON, to be spoken out loud
- Sound like Friday, not a chatbot
"""

async def get_groq_response(text: str, base64_image: str = None) -> dict:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    if base64_image:
        model = "groq/compound-mini"
        user_content = [
            {"type": "text", "text": text},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64_image}"
                }
            }
        ]
    else:
        model = "groq/compound-mini"
        user_content = text

    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ],
        "temperature": 0.3,
        "max_tokens": 1024
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        content = result["choices"][0]["message"]["content"].strip()

        # Strip markdown code fences if the model wraps in them
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        content = content.strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Fallback: extract first JSON object found
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except:
                    pass
            return {
                "intent": "chat",
                "response": content,
                "action": "none"
            }
