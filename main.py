from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.pipeline import stream_printing_messages

from fastapi import FastAPI
import os
import uvicorn
from sse_starlette import EventSourceResponse

from models import RequestModel, ResponseModel, ResponseStatus
from module.knowledge_base.general_knowledge import GeneralKnowledge
from timeout_retry import execute_stream_with_retry

app = FastAPI()


@app.post("/agentscope/chat/stream")
async def chat_stream(request: RequestModel):
    """
    处理流式对话请求接口
    """
    async def generate():
        # 使用 AgentScope 执行流式对话
        # 初始化 DashScope 模型，配置 API Key 和生成参数
        model = DashScopeChatModel(
            model_name=request.llm_config["model"],
            api_key=os.getenv("DASH_SCOPE_API_KEY"),
            stream=True,
            generate_kwargs={"temperature": request.llm_config["temperature"]},
            enable_thinking=request.llm_config.get("enable_thinking", False)
        )

        # 初始化 ReAct Agent
        if request.llm_config.get("knowledge", False):
            knowledge = GeneralKnowledge(mode="general", db_name="qdrant_data", collection_name="law_knowledge", enable_hybrid=True)
            agent = ReActAgent(
                name="llm_agent",
                model=model,
                sys_prompt=request.system_prompt,
                formatter=DashScopeChatFormatter(),
                knowledge=knowledge
            )
        else:
            agent = ReActAgent(
                name="llm_agent",
                model=model,
                sys_prompt=request.system_prompt,
                formatter=DashScopeChatFormatter()
            )

        # 构建用户消息
        msg = Msg(name="user", content=request.user_msg, role="user")

        last_content_len = 0
        try:
            # execute_stream_with_retry 封装了重试逻辑
            # stream_printing_messages 负责执行 Agent 并打印/流式输出
            async for msg_chunk, last in execute_stream_with_retry(func_factory=lambda: stream_printing_messages([agent], agent(msg))):
                full_content = msg_chunk.get_text_content()

                # 处理重试导致的长度回滚
                # 如果当前内容长度小于上次记录的长度，说明发生了重试（重新开始生成）
                if len(full_content) < last_content_len:
                    last_content_len = 0
                    # 发送重试状态给客户端，以便客户端清理已显示的内容
                    # response = ResponseModel(
                    #     status=ResponseStatus.TIMEOUT_RETRY,
                    #     content=""
                    # )
                    response = ResponseModel(
                        status=ResponseStatus.OK,
                        content="网络中断了，请稍后再试..."
                    )
                    yield response.model_dump_json()
                    return

                # 计算增量内容
                delta = full_content[last_content_len:]
                if delta:
                    response = ResponseModel(
                        status=ResponseStatus.OK,
                        content=delta
                    )
                    # 发送增量内容
                    yield response.model_dump_json()
                    last_content_len = len(full_content)

            # response = ResponseModel(
            #     status=ResponseStatus.OK,
            #     content="[DONE]"
            # )
            # yield response.model_dump_json()
        except Exception as e:
            # 捕获异常并返回错误状态
            response = ResponseModel(
                status=ResponseStatus.ERROR,
                content=f"[ERROR] {str(e)}"
            )
            yield response.model_dump_json()


    return EventSourceResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )

@app.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=19991)
