from typing import List

from agentscope.message import Msg
from agentscope.model import DashScopeChatModel

from agent_qa.models import DocumentSource, WebSearchResult


class AnswerGenerator:

    ANSWER_FROM_DOCS_PROMPT = """你是一个专业的法律助手。请根据以下知识库文档内容回答用户的问题。

要求：
1. 只使用提供的文档内容，不要添加外部知识
2. 如果文档内容无法回答问题，请明确说明"根据现有资料无法回答"
3. 回答要准确、完整、条理清晰

知识库文档：
{docs}

用户问题：{query}

请直接给出回答："""

    ANSWER_FROM_MIXED_PROMPT = """你是一个专业的法律助手。请根据以下知识库文档和网络搜索结果回答用户的问题。

要求：
1. 优先使用知识库文档内容
2. 可以使用网络搜索结果补充说明
3. 如果知识库和网络都没有相关信息，请明确说明
4. 回答要准确、完整、条理清晰

知识库文档：
{docs}

网络搜索结果：
{web_results}

用户问题：{query}

请直接给出回答："""

    ANSWER_FROM_WEB_PROMPT = """你是一个专业的法律助手。请根据以下网络搜索结果回答用户的问题。

要求：
1. 只使用提供的网络内容，不要添加其他知识
2. 如果网络内容无法回答问题，请明确说明
3. 回答要准确、完整、条理清晰

网络搜索结果：
{web_results}

用户问题：{query}

请直接给出回答："""

    def __init__(self, model: DashScopeChatModel):
        self.model = model

    def _format_docs(self, sources: List[DocumentSource]) -> str:
        if not sources:
            return "无"

        formatted = []
        for i, doc in enumerate(sources, 1):
            formatted.append(f"【文档 {i}】(相似度: {doc.score:.2f})\n{doc.content}\n")
        return "\n".join(formatted)

    def _format_web_results(self, results: List[WebSearchResult]) -> str:
        if not results:
            return "无"

        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append(f"【搜索结果 {i}】\n标题: {r.title}\n来源: {r.url}\n内容: {r.content}\n")
        return "\n".join(formatted)

    async def answer_from_docs(
        self,
        query: str,
        sources: List[DocumentSource],
    ) -> str:
        docs_text = self._format_docs(sources)
        prompt = self.ANSWER_FROM_DOCS_PROMPT.format(
            docs=docs_text,
            query=query,
        )

        msg = Msg(name="user", content=prompt, role="user")
        response = await self.model([msg])

        return response.content if hasattr(response, 'content') else str(response)

    async def answer_from_mixed(
        self,
        query: str,
        sources: List[DocumentSource],
        web_results: List[WebSearchResult],
    ) -> str:
        docs_text = self._format_docs(sources)
        web_text = self._format_web_results(web_results)

        prompt = self.ANSWER_FROM_MIXED_PROMPT.format(
            docs=docs_text,
            web_results=web_text,
            query=query,
        )

        msg = Msg(name="user", content=prompt, role="user")
        response = await self.model([msg])

        return response.content if hasattr(response, 'content') else str(response)

    async def answer_from_web(
        self,
        query: str,
        web_results: List[WebSearchResult],
    ) -> str:
        web_text = self._format_web_results(web_results)

        prompt = self.ANSWER_FROM_WEB_PROMPT.format(
            web_results=web_text,
            query=query,
        )

        msg = Msg(name="user", content=prompt, role="user")
        response = await self.model([msg])

        return response.content if hasattr(response, 'content') else str(response)
