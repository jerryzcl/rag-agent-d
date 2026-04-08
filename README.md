# 项目概要

## 项目概述

**AI Contract** 是一个基于 FastAPI 和 AgentScope 构建的智能合同助手后端服务。该项目主要提供流式对话 API，支持检索增强生成（RAG）功能，能够基于法律知识库进行智能问答。

## 技术架构

### 核心技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI |
| AI Agent 框架 | AgentScope (阿里云) |
| 大语言模型 | DashScope (阿里云) |
| 向量数据库 | Qdrant |
| 稀疏向量模型 | FastEmbed (BM25) |
| 文本嵌入 | DashScope Text Embedding V4 |
| 部署 | Docker / Docker Compose |

### 项目结构

```
ai_contract/
├── main.py                      # FastAPI 主入口，流式对话 API
├── models.py                    # Pydantic 数据模型
├── timeout_retry.py             # 异步流式重试逻辑
├── module/
│   └── knowledge_base/          # 知识库 RAG 模块
│       ├── general_knowledge.py # 知识库核心类（支持混合检索）
│       ├── general_rag.py       # RAG 系统（文档处理与索引）
│       ├── _sparse_embedding.py  # BM25 稀疏向量模型
│       └── _reader/             # 文档读取器
│           ├── _document_reader.py  # 通用文档读取/分块
│           └── _word_reader.py      # Word 文档读取
├── dataset/
│   └── qdrant_data/            # Qdrant 向量数据库存储
├── Dockerfile
└── docker-compose.yml
```

## 核心功能

### 1. 流式对话 API (`/agentscope/chat/stream`)

- 支持 SSE (Server-Sent Events) 流式输出
- 集成 AgentScope ReActAgent 智能体
- 支持配置 LLM 参数（模型、温度、思考模式等）
- 包含超时重试机制，防止网络中断

### 2. 知识库 RAG 系统

- **文档支持**: PDF、DOCX、TXT、MD、DOC
- **分段模式**: 
  - `general`: 通用分段
  - `parent_child`: 父子分段（更大块召回，更小块精检）
- **检索模式**:
  - 纯向量检索 (Dense-only)
  - 混合检索 (Hybrid): BM25 关键词 + 向量相似度，使用 RRF 融合排名

### 3. 智能重试机制

- 异步生成器的超时控制
- 指数退避重试策略
- 流式响应中断处理

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/agentscope/chat/stream` | POST | 流式对话接口 |
| `/health` | GET | 健康检查 |

## 请求/响应格式

**RequestModel**:
```python
{
    "system_prompt": "系统提示词",
    "user_msg": "用户消息",
    "llm_config": {
        "model": "模型名称",
        "temperature": 0.7,
        "enable_thinking": false,
        "knowledge": true  # 是否启用知识库
    }
}
```

**ResponseModel**:
```python
{
    "status": 0,  # 0:OK, 1:ERROR, 6:TIMEOUT_RETRY
    "content": "增量内容"
}
```

## 部署方式

项目支持 Docker 部署，可通过 `docker-compose up` 快速启动服务（默认端口 19991）。

---

如需了解更多细节，可以查看具体模块的源码：
- [main.py](file:///d:\project\ai_contract\main.py) - API 入口
- [general_knowledge.py](file:///d:\project\ai_contract\module\knowledge_base\general_knowledge.py) - 知识库核心
- [general_rag.py](file:///d:\project\ai_contract\module\knowledge_base\general_rag.py) - RAG 系统