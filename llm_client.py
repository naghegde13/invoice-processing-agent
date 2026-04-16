"""
llm_client.py - Multi-LLM client with Grok primary and OpenAI fallback.
Tries Grok first if GROK_API_KEY is set, falls back to OpenAI automatically.
"""
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

GROK_API_KEY = os.getenv("GROK_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Grok uses OpenAI-compatible API
grok_client = OpenAI(
    api_key=GROK_API_KEY,
    base_url="https://api.x.ai/v1",
) if GROK_API_KEY else None

openai_client = OpenAI(api_key=OPENAI_API_KEY)


def call_llm(system: str, user_message: str, max_tokens: int = 1500) -> tuple[str, str]:
    """
    Calls Grok if available, falls back to OpenAI.
    Returns (response_text, model_used)
    """
    # Try Grok first
    if grok_client:
        try:
            response = grok_client.chat.completions.create(
                model="grok-3",
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_message}
                ]
            )
            return response.choices[0].message.content.strip(), "grok-3"
        except Exception as e:
            print(f"[LLM] Grok failed ({e}), falling back to OpenAI...")

    # Fall back to OpenAI
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message}
        ]
    )
    return response.choices[0].message.content.strip(), "gpt-4o"