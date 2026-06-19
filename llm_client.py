#!/usr/bin/env python3
"""
Shared LLM client — single Anthropic client config for all modules.
"""
import os
import anthropic


def get_client():
    return anthropic.Anthropic(
        api_key=os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
        base_url=os.environ.get("ANTHROPIC_BASE_URL",
                                "https://api.deepseek.com/anthropic")
    )


def get_model():
    return os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-pro[1m]")


def call_llm(system_prompt: str, user_content: str,
             max_tokens: int = 4096, thinking: bool = False) -> tuple[str, dict]:
    """Single LLM call. Returns (text, usage_dict)."""
    client = get_client()
    response = client.messages.create(
        model=get_model(),
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
        max_tokens=max_tokens,
        thinking={"type": "disabled"} if not thinking else None
    )
    text_blocks = [b.text for b in response.content if b.type == "text"]
    text = "\n".join(text_blocks)
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "total_tokens": response.usage.input_tokens + response.usage.output_tokens
    }
    return text, usage
