from enum import IntEnum

from pydantic import BaseModel

class ResponseStatus(IntEnum):
    """
        响应状态枚举，增强可读性
        正常返回为0，异常返回为1，匹配到敏感词返回2，反问前端返回为3，深度思考过程返回4，不属于该智能体范围返回5；超时重试返回6
        """
    OK = 0
    ERROR = 1
    SENSITIVE_WORD = 2
    REQUERY_FRONT_WEB = 3
    THINKING = 4
    OUT_OF_SCOPE = 5
    TIMEOUT_RETRY = 6

class RequestModel(BaseModel):
    system_prompt: str
    user_msg: str
    llm_config: dict

class ResponseModel(BaseModel):
    status: ResponseStatus
    content: str