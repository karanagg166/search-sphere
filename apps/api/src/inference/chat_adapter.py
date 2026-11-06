"""Compatibility boundary for existing grounded generation/rewrite providers."""

from types import SimpleNamespace

from src.inference.cohere_gateway import GENERATION_MODEL, CohereGateway


class CohereChatAdapter:
    def __init__(self, api_key=None, gateway=None):
        self.gateway = gateway or CohereGateway(api_key=api_key)

    async def chat(self, *, message, preamble, model=GENERATION_MODEL, **kwargs):
        if model != GENERATION_MODEL:
            raise ValueError("Generation must use the pinned Cohere model")
        text = await self.gateway.chat(
            [{"role": "system", "content": preamble}, {"role": "user", "content": message}],
            max_tokens=1024,
            json_output=kwargs.get("response_format", {}).get("type") == "json_object",
        )
        return SimpleNamespace(text=text)

    async def chat_stream(self, **kwargs):
        # Buffer a complete provider response. Citation/permission validation belongs
        # before the API emits any content; never expose unvalidated upstream deltas.
        response = await self.chat(**kwargs)
        yield SimpleNamespace(event_type="text-generation", text=response.text)
