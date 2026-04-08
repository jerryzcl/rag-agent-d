import json
import os
from typing import List

import aiohttp

from agent_qa.models import WebSearchResult


class DashScopeWebSearch:

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("DASH_SCOPE_WEB_SEARCH_KEY")
        self.base_url = "https://dashscope.aliyuncs.com/api/v1/services/search/web-search"

    async def search(self, query: str, num_results: int = 5) -> List[WebSearchResult]:
        if not self.api_key:
            raise ValueError("DASH_SCOPE_WEB_SEARCH_KEY 环境变量未设置")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": "web-search",
            "input": {
                "query": query,
            },
            "parameters": {
                "num_results": num_results,
            },
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status != 200:
                        raise Exception(f"Web Search API 错误: {response.status}")

                    data = await response.json()
                    return self._parse_results(data)
        except Exception as e:
            print(f"Web Search 错误: {e}")
            return []

    def _parse_results(self, data: dict) -> List[WebSearchResult]:
        results = []

        try:
            output = data.get("output", {})
            items = output.get("choices", [{}])[0].get("message", {}).get("tool_calls", [])

            search_results = items[0].get("function", {}).get("arguments", {})
            if isinstance(search_results, str):
                search_results = json.loads(search_results)

            for item in search_results.get("web_search_result", []):
                results.append(
                    WebSearchResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        content=item.get("content", ""),
                    )
                )
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            print(f"解析 Web Search 结果失败: {e}")

        return results
