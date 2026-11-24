"""Standalone search fallback.

Primary research uses Anthropic's server-side web_search tool (see anthropic_llm.py),
which needs no separate vendor. Tavily is here as an independent corroboration channel
for the Fact Verification agent when a second, non-model-mediated source is wanted.
"""
from __future__ import annotations

import httpx

from app.config import get_settings
from app.core.errors import ProviderUnavailableError
from app.providers.base import SearchProvider


class TavilySearch(SearchProvider):
    name = "tavily"

    def __init__(self) -> None:
        s = get_settings()
        if not s.tavily_api_key:
            raise ProviderUnavailableError("tavily", "no API key", missing=["TAVILY_API_KEY"])
        self.api_key = s.tavily_api_key

    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post("https://api.tavily.com/search", json={
                "api_key": self.api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "advanced",
                "include_answer": False,
            })
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderUnavailableError("tavily", f"HTTP {r.status_code}")
        r.raise_for_status()
        return [
            {"title": item.get("title", ""), "url": item.get("url", ""),
             "content": item.get("content", ""), "score": item.get("score", 0)}
            for item in r.json().get("results", [])
        ]
