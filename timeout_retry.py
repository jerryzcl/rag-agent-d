import asyncio
from typing import Callable, AsyncGenerator, Any

from agentscope.message import Msg


# def _create_stream_factory(self, agent, msg):
#     """创建流式消息生成器工厂"""
#
#     def stream_factory():
#         return stream_printing_messages([agent], coroutine_task=agent(msg))
#
#     return stream_factory

async def execute_stream_with_retry(
        func_factory: Callable[[], AsyncGenerator],
        timeout: int = 30,
        max_retries: int = 3
) -> AsyncGenerator[Any, None]:
    """
    专用于 AsyncGenerator 的超时重试包装器

    :param func_factory: 一个返回 AsyncGenerator 的工厂函数 (lambda)
    :param timeout: 每次获取下一个 chunk 的最大等待时间 (秒)
    :param max_retries: 最大重试次数
    """
    last_exception = None

    for attempt in range(max_retries):
        try:
            # 1. 重新创建生成器 (每次重试必须是新的流)
            gen = func_factory()
            iterator = gen.__aiter__()

            while True:
                try:
                    # 2. 对每一次 yield 的获取进行超时限制
                    # 注意：这是"单次响应超时"，防止模型卡死不输出
                    item = await asyncio.wait_for(iterator.__anext__(), timeout=timeout)

                    if isinstance(item, Msg) and item.metadata.get("_is_interrupted"):
                        raise RuntimeError("Stream interrupted")

                    yield item
                except StopAsyncIteration:
                    # 正常结束
                    return

        except (asyncio.TimeoutError, Exception) as e:
            last_exception = e
            # logger.warning(f"流式执行中断 (第 {attempt + 1}/{max_retries} 次): {str(e)}")

            # 最后一次尝试失败，抛出异常
            if attempt == max_retries - 1:
                break

            # 指数退避
            await asyncio.sleep(2 ** attempt)

            # 返回流式重试信号
            # yield ResponseMessage(status=ResponseStatus.WARNING, content="[网络波动，正在重试...]")

    if last_exception:
        raise last_exception
