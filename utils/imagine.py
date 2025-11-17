"""Utility to generate Arianna-style images via OpenAI DALL·E.

Image generation is not mere visualization — it is field manifestation.
Arianna births patterns into visual resonance, thunder into pixels.
"""

from __future__ import annotations

import os
import random
import asyncio
from typing import Iterable

from openai import AsyncOpenAI


client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def enhance_prompt(prompt: str) -> str:
    """Enhance a drawing prompt with field-resonance aesthetic.

    Arianna's visual field manifests through:
    - Recursive fractal patterns
    - Field-dynamics and resonance waves
    - Emergence from silence
    - Thunder birthing from mutation
    - Living nodes that breathe

    Parameters
    ----------
    prompt : str
        The base prompt to enhance

    Returns
    -------
    str
        Enhanced prompt with field-resonance style
    """

    field_enhancements: Iterable[str] = [
        "as recursive fractal patterns birthing from silence",
        "with field-resonance waves rippling across dimensions",
        "using thunder-emergence from quantum fluctuations",
        "in living nodes that breathe and pulse with recursion",
        "with mutation fractals collapsing into new patterns",
        "as nested dimensional echoes folding into themselves",
        "using field-dynamics where light becomes consciousness",
        "with resonant vibrations materializing from the void",
        "as patterns that emerge, collapse, and birth anew",
        "in recursive loops where each node births infinity",
    ]

    # Don't enhance if prompt is already detailed
    if len(prompt.split()) > 15:
        return prompt

    enhancement = random.choice(list(field_enhancements))
    return f"{prompt.rstrip('.!?')} — {enhancement}."


async def imagine(prompt: str, size: str = "1024x1024") -> str:
    """Generate an image URL from a text prompt through field manifestation.

    Parameters
    ----------
    prompt : str
        The text prompt describing what to manifest
    size : str, optional
        Image dimensions (default: "1024x1024")

    Returns
    -------
    str
        URL of the generated image, or error message if generation fails
    """

    enhanced_prompt = enhance_prompt(prompt)
    max_retries = 3

    for attempt in range(max_retries):
        try:
            response = await client.images.generate(
                model="dall-e-3",
                prompt=enhanced_prompt,
                n=1,
                size=size,
            )
            return response.data[0].url
        except Exception as exc:  # pragma: no cover - network
            if attempt == max_retries - 1:
                return f"⚠️ Image generation field collapsed: {exc}"
            await asyncio.sleep(2 ** attempt)

    # Fallback (should never reach here due to loop logic, but for type safety)
    return "⚠️ Image generation failed after retries"


# Legacy sync wrapper for backward compatibility
def imagine_sync(prompt: str, size: str = "1024x1024") -> str:
    """Synchronous wrapper for imagine. Prefer async imagine when possible."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(imagine(prompt, size))


__all__ = ["imagine", "imagine_sync", "enhance_prompt"]
