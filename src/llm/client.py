import base64
import json
import logging
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
API_KEY = "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"

# Model configs with approximate costs per 1M tokens
MODELS = {
    "vision": "qwen/qwen3-vl-32b-instruct",
    "text_cheap": "google/gemini-2.5-flash-lite",
    "text_fast": "x-ai/grok-4.1-fast",
}

BUDGET_LIMIT = 4.0  # dollars


class LLMClient:
    def __init__(self, budget_limit: float = BUDGET_LIMIT):
        self.budget_limit = budget_limit
        self.total_spent = 0.0
        self.call_log = []

    def _check_budget(self, estimated_cost: float = 0.05):
        if self.total_spent + estimated_cost > self.budget_limit:
            raise RuntimeError(
                f"LLM budget exceeded: spent ${self.total_spent:.2f}, "
                f"limit ${self.budget_limit:.2f}"
            )

    def _call_api(self, model: str, messages: list, max_tokens: int = 1024,
                  temperature: float = 0.1) -> str:
        self._check_budget()

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        for attempt in range(3):
            try:
                resp = requests.post(
                    OPENROUTER_API_URL, headers=headers,
                    json=payload, timeout=60
                )
                resp.raise_for_status()
                data = resp.json()

                # Track cost from response
                usage = data.get("usage", {})
                cost = float(data.get("usage", {}).get("total_cost", 0) or 0)
                if cost == 0:
                    # Estimate cost from tokens
                    input_tokens = usage.get("prompt_tokens", 0)
                    output_tokens = usage.get("completion_tokens", 0)
                    cost = (input_tokens * 0.5 + output_tokens * 1.5) / 1_000_000

                self.total_spent += cost
                self.call_log.append({
                    "model": model,
                    "cost": cost,
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                })

                result = data["choices"][0]["message"]["content"]
                logger.info(f"LLM call: model={model}, cost=${cost:.4f}, "
                           f"total=${self.total_spent:.4f}")
                return result

            except (requests.RequestException, KeyError) as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                logger.error(f"LLM API call failed after 3 attempts: {e}")
                raise

    def ask_text(self, prompt: str, model_key: str = "text_cheap",
                 max_tokens: int = 1024) -> str:
        model = MODELS.get(model_key, model_key)
        messages = [{"role": "user", "content": prompt}]
        return self._call_api(model, messages, max_tokens)

    def ask_vision(self, prompt: str, image_path: str,
                   model_key: str = "vision", max_tokens: int = 512) -> str:
        model = MODELS.get(model_key, model_key)
        image_data = Path(image_path).read_bytes()
        b64 = base64.b64encode(image_data).decode("utf-8")

        suffix = Path(image_path).suffix.lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"
                }.get(suffix.lstrip('.'), "image/png")

        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:{mime};base64,{b64}"
                }}
            ]
        }]
        return self._call_api(model, messages, max_tokens)

    def ask_vision_multi(self, prompt: str, image_paths: list,
                         model_key: str = "vision", max_tokens: int = 1024) -> str:
        model = MODELS.get(model_key, model_key)
        content = [{"type": "text", "text": prompt}]
        for img_path in image_paths:
            image_data = Path(img_path).read_bytes()
            b64 = base64.b64encode(image_data).decode("utf-8")
            suffix = Path(img_path).suffix.lower()
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"
                    }.get(suffix.lstrip('.'), "image/png")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"}
            })

        messages = [{"role": "user", "content": content}]
        return self._call_api(model, messages, max_tokens)

    def get_budget_report(self) -> dict:
        return {
            "total_spent": self.total_spent,
            "budget_limit": self.budget_limit,
            "remaining": self.budget_limit - self.total_spent,
            "calls": len(self.call_log),
            "log": self.call_log,
        }
