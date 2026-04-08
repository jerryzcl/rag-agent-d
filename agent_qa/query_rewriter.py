from agentscope.message import Msg
from agentscope.model import DashScopeChatModel


class QueryRewriter:

    REWRITE_PROMPT_TEMPLATE = """请将以下用户问题改写为更适合搜索的版本。
要求：保留核心意图，去除口语化表达，提取关键法律实体（如合同法、民法典等）。

原问题：{query}

请直接输出改写后的查询，不要有其他解释。"""

    def __init__(self, model: DashScopeChatModel):
        self.model = model

    async def rewrite(self, query: str) -> str:
        prompt = self.REWRITE_PROMPT_TEMPLATE.format(query=query)

        msg = Msg(name="system", content=prompt, role="system")
        user_msg = Msg(name="user", content="请改写这个问题", role="user")

        response = await self.model([msg, user_msg])

        rewritten = response.content if hasattr(response, 'content') else str(response)
        return rewritten.strip()
