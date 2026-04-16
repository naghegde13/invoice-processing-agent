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

# Grok exposes OpenAI-compatible API (same SDK works for both providers)
# Only instantiate Grok client if GROK_API_KEY is configured; None indicates Grok unavailable
grok_client = OpenAI(
    api_key=GROK_API_KEY,
    base_url="https://api.x.ai/v1",
) if GROK_API_KEY else None

# OpenAI client always available (fallback provider)
openai_client = OpenAI(api_key=OPENAI_API_KEY)


def call_llm(system: str, user_message: str, max_tokens: int = 1500) -> tuple[str, str]:
    """
    Multi-provider fallback: Grok primary (faster, cheaper), OpenAI reliable fallback.
    Returns (response_text, model_used) for audit trail and cost tracking.
    
    Strategy: Try Grok first, swallow transient errors, fall back to OpenAI on any failure.
    Caller never sees availability issues—transparent failover.
    """
    # Try Grok first (preferred: faster, lower cost per token)
    # If GROK_API_KEY not set, grok_client is None and we skip to OpenAI
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
            # Log and swallow: any Grok failure (rate limit, network, auth, model error) triggers fallback
            # Caller app continues as if Grok doesn't exist; no interruption
            print(f"[LLM] Grok failed ({e}), falling back to OpenAI...")

    # Fall back to OpenAI (reliable, always available backup when Grok unavailable or fails)
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message}
        ]
    )
    return response.choices[0].message.content.strip(), "gpt-4o"