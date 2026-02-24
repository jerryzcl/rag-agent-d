"""
通用知识库模块

支持两种检索模式：
1. 纯向量检索 (Dense-only): 默认模式，使用语义相似度
2. 混合检索 (Hybrid): BM25 关键词匹配 + 向量相似度，使用 RRF 融合排名

使用示例:
    # 纯向量模式 (向后兼容)
    knowledge = GeneralKnowledge(mode="general", db_name="qdrant_data", collection_name="law")
    
    # 混合检索模式
    knowledge = GeneralKnowledge(
        mode="general",
        db_name="qdrant_data",
        collection_name="law_hybrid",
        enable_hybrid=True,
        delete_existing=True  # 首次使用需重建集合
    )
"""

from agentscope.rag import SimpleKnowledge, Document, DocMetadata, QdrantStore
from agentscope.embedding import DashScopeTextEmbedding, FileEmbeddingCache
from agentscope.message import TextBlock

from pathlib import Path
from typing import List, Dict, Any, Optional, Literal
import os
import asyncio
import uuid
from qdrant_client import AsyncQdrantClient, QdrantClient, models
from qdrant_client.local.async_qdrant_local import AsyncQdrantLocal
import dashscope
from http import HTTPStatus


class GeneralKnowledge(SimpleKnowledge):
    """
    继承 SimpleKnowledge，支持纯向量检索和混合检索 (Hybrid Search)
    
    混合检索结合:
    - Dense Vector: 语义相似度 (DashScope text-embedding-v4)
    - Sparse Vector: BM25 关键词匹配 (FastEmbed Qdrant/bm25)
    
    使用 Qdrant 的 Prefetch + RRF Fusion 实现结果融合。
    """

    # 命名向量配置
    DENSE_VECTOR_NAME = "dense"
    SPARSE_VECTOR_NAME = "sparse"

    def __init__(
            self,
            mode: Literal["general", "parent_child"],
            db_name: str,
            collection_name: str,
            embedding_dimensions: int = 1024,
            api_key: Optional[str] = None,
            delete_existing: bool = False,
            enable_hybrid: bool = False,
            sparse_model_name: str = "Qdrant/bm25",
            enable_rerank: bool = False,
            rerank_model: str = "gte-rerank",
    ):
        """
        初始化知识库实例
        
        Args:
            mode: 分段模式，"general" 或 "parent_child"
            db_name: 向量数据库名称
            collection_name: 集合名称
            embedding_dimensions: 嵌入向量维度
            api_key: DashScope API Key
            delete_existing: 是否删除已存在的集合
            enable_hybrid: 是否启用混合检索 (BM25 + Vector)
            sparse_model_name: 稀疏模型名称，默认 "Qdrant/bm25"
            enable_rerank: 是否启用重排序 (Rerank)
            rerank_model: 重排序模型名称，默认 "gte-rerank"
        """
        self.db_path = Path(__file__).resolve().parent.parent / "dataset" / db_name
        self.collection_name = collection_name
        self.embedding_dimensions = embedding_dimensions
        self.enable_hybrid = enable_hybrid
        self.enable_rerank = enable_rerank
        self.rerank_model = rerank_model
        self.mode = mode

        # 如果需要，删除已存在的集合（使用同步客户端）
        if delete_existing:
            sync_client = QdrantClient(path=self.db_path)
            if sync_client.collection_exists(collection_name):
                sync_client.delete_collection(collection_name)
            sync_client.close()  # 显式关闭，释放文件锁

        # ========== 混合检索模式：手动创建集合 ==========
        if enable_hybrid:
            self._init_hybrid_collection()
            
            # 初始化稀疏向量模型
            from module.knowledge_base._sparse_embedding import SparseEmbeddingModel
            self.sparse_model = SparseEmbeddingModel(
                model_name=sparse_model_name,
                cache_dir=str(self.db_path / "sparse_model_cache"),
            )
        else:
            self.sparse_model = None
        
        # 创建 QdrantStore (用于兼容 AgentScope 接口)
        # 注意：混合模式下 QdrantStore 仅用于获取 client，实际写入使用自定义逻辑
        self.store = QdrantStore(
            location=None,
            collection_name=collection_name,
            dimensions=embedding_dimensions,
            client_kwargs={
                "path": self.db_path,
            }
        )
        
        # 创建稠密嵌入模型 (DashScope)
        self.embedding_model = DashScopeTextEmbedding(
            model_name="text-embedding-v4",
            api_key=api_key or os.environ.get("DASHSCOPE_API_KEY"),
            embedding_cache=FileEmbeddingCache(
                cache_dir=os.path.join(self.db_path, "embedding_cache"),
                max_file_number=1000,
                max_cache_size=1024
            ),
        )
        
        # 调用父类初始化
        super().__init__(
            embedding_store=self.store,
            embedding_model=self.embedding_model,
        )

    def _init_hybrid_collection(self) -> None:
        """
        初始化混合检索集合 (Named Vectors)
        
        创建包含 dense 和 sparse 两个命名向量的集合。
        """
        sync_client = QdrantClient(path=self.db_path)
        
        if not sync_client.collection_exists(self.collection_name):
            # 创建集合，使用命名向量配置
            sync_client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    self.DENSE_VECTOR_NAME: models.VectorParams(
                        size=self.embedding_dimensions,
                        distance=models.Distance.COSINE,
                    ),
                },
                sparse_vectors_config={
                    self.SPARSE_VECTOR_NAME: models.SparseVectorParams(
                        modifier=models.Modifier.IDF,  # 使用 IDF 修正 BM25 权重
                    ),
                },
            )
            print(f"[Hybrid] 已创建集合: {self.collection_name}")
        
        sync_client.close()

    async def retrieve(
            self,
            query: str,
            limit: int = 5,
            score_threshold: Optional[float] = None,
            **kwargs,
    ) -> List[Document]:
        """
        检索文档
        
        - 混合模式：使用 Prefetch + RRF Fusion 结合 BM25 和向量检索
        - 纯向量模式：仅使用语义相似度
        - parent_child 模式下自动去重父分段

        Args:
            query: 查询文本
            limit: 返回结果数量
            score_threshold: 分数阈值

        Returns:
            去重后的 Document 列表
        """
        # Step 1: 生成稠密向量
        embedding_res = await self.embedding_model([TextBlock(type="text", text=query)])
        dense_vec = embedding_res.embeddings[0]

        _client = self.store.get_client()

        # Step 2: 执行检索
        if self.enable_hybrid and self.sparse_model:
            # ========== 混合检索 ==========
            sparse_vec = self.sparse_model.embed_query(query)
            
            raw_points = await _client.query_points(
                collection_name=self.store.collection_name,
                prefetch=[
                    # 稀疏向量检索 (BM25)
                    models.Prefetch(
                        query=sparse_vec,
                        using=self.SPARSE_VECTOR_NAME,
                        limit=limit * 2,
                        score_threshold=1.5  # <--- BM25 最小有效分（取决于库大小）
                    ),
                    # 稠密向量检索 (语义)
                    models.Prefetch(
                        query=dense_vec,
                        using=self.DENSE_VECTOR_NAME,
                        limit=limit * 2,
                        score_threshold=0.45 # <--- 语义相似度门槛
                    ),
                ],
                # 使用 RRF 融合两路结果
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit * 3,  # 多拿一些用于去重
                # score_threshold=score_threshold,
                **kwargs,
            )
        else:
            # ========== 纯向量检索 (向后兼容) ==========
            raw_points = await _client.query_points(
                collection_name=self.store.collection_name,
                query=dense_vec,
                limit=limit * 3,
                score_threshold=score_threshold,
                **kwargs,
            )

        # Step 3: 解析 Qdrant point -> Document
        raw_docs = self._parse_points_to_documents(raw_points)

        # Step 4: 去重逻辑 (Parent-Child)
        if self.mode == "parent_child":
            # parent_child 模式：先按 parent_text 去重
            # 注意：如果启用了 rerank，我们应该先去重再 rerank，还是先 rerank 再去重？
            # 策略：先去重，确保送给 rerank 的是独特的父文档，节省 token
            candidates = self._deduplicate_parent_child(raw_docs, limit * 2) # 多留一些给 rerank
        else:
            candidates = raw_docs

        # Step 5: 重排序 (Rerank)
        if self.enable_rerank and candidates:
            final_docs = await self._rerank_documents(query, candidates, top_n=limit)
            final_docs = [d for d in final_docs if d.score > 0.1]
        else:
            final_docs = candidates[:limit]

        return final_docs

    async def _rerank_documents(self, query: str, documents: List[Document], top_n: int) -> List[Document]:
        """
        使用 DashScope Rerank 模型对文档进行重排序
        """
        if not documents:
            return []

        # 提取文本内容
        doc_texts = [doc.metadata.content.get("text", "") for doc in documents]
        
        # 限制每批次文档数量 (DashScope 限制通常为 100)
        # 这里简单处理，假设 candidates 数量不会特别巨大
        
        try:
            # DashScope Rerank 目前是同步调用，但在 async 函数中运行是可以的
            # 或者使用 loop.run_in_executor 避免阻塞
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: dashscope.TextReRank.call(
                    model=self.rerank_model,
                    query=query,
                    documents=doc_texts,
                    top_n=top_n,
                    return_documents=False # 我们只需要索引和分数
                )
            )

            if resp.status_code == HTTPStatus.OK:
                # 根据返回的 output.results (index, relevance_score) 重新构建列表
                reranked_docs = []
                for item in resp.output.results:
                    idx = item.index
                    score = item.relevance_score
                    
                    original_doc = documents[idx]
                    # 更新分数为 rerank 分数
                    original_doc.score = score
                    reranked_docs.append(original_doc)
                
                return reranked_docs
            else:
                print(f"[Rerank Error] Code: {resp.code}, Message: {resp.message}")
                return documents[:top_n] # 降级：返回原始排序
                
        except Exception as e:
            print(f"[Rerank Exception] {e}")
            return documents[:top_n]

    def _parse_points_to_documents(self, raw_points) -> List[Document]:
        """
        将 Qdrant 查询结果解析为 Document 列表
        """
        raw_docs: List[Document] = []
        
        for point in raw_points.points:
            payload: dict = point.payload

            # 安全提取字段
            content_dict = payload.get("content", {})
            text_content = content_dict.get("text", "") if isinstance(content_dict, dict) else str(content_dict)

            # 提取额外字段
            parent_text = payload.get("parent_text", "")

            # 重建 DocMetadata
            clean_meta = DocMetadata(
                content=TextBlock(type="text", text=text_content),
                doc_id=payload.get("doc_id", ""),
                chunk_id=payload.get("chunk_id", 0),
                total_chunks=payload.get("total_chunks", 0),
            )

            # 把额外字段挂回去
            clean_meta.parent_text = parent_text

            doc = Document(
                metadata=clean_meta,
                embedding=point.vector if hasattr(point, "vector") else None,
                score=point.score,
            )
            raw_docs.append(doc)

        return raw_docs

    def _deduplicate_parent_child(self, raw_docs: List[Document], limit: int) -> List[Document]:
        """
        parent_child 模式下按 parent_text 去重
        """
        seen: Dict[str, str] = {}  # key: parent_text, value: doc_id
        order: List[str] = []

        for d in raw_docs:
            parent_text = d.metadata.get("parent_text")
            if not parent_text:
                continue

            if parent_text not in seen:
                seen[parent_text] = d.metadata.doc_id
                order.append(parent_text)

        # 构造最终返回的父块文档
        out = []
        for i, parent_text in enumerate(order):
            if i >= limit:
                break

            original_doc_id = seen[parent_text]

            out.append(
                Document(
                    metadata=DocMetadata(
                        content=TextBlock(type="text", text=parent_text),
                        doc_id=original_doc_id,
                        chunk_id=i,
                        total_chunks=len(order),
                    ),
                    score=0.99 - i * 0.01,
                )
            )
        return out

    async def add_documents(
        self,
        documents: list[Document],
        **kwargs: Any,
    ) -> None:
        """
        添加文档到知识库
        
        混合模式下同时写入 Dense 和 Sparse 向量。

        Args:
            documents: Document 列表
        """
        if not documents:
            return

        # 校验模态支持
        for doc in documents:
            if (
                doc.metadata.content["type"]
                not in self.embedding_model.supported_modalities
            ):
                raise ValueError(
                    f"The embedding model {self.embedding_model.model_name} "
                    f"does not support {doc.metadata.content['type']} data.",
                )

        # 获取稠密向量
        res_embeddings = await self.embedding_model(
            [_.metadata.content for _ in documents],
        )

        if self.enable_hybrid and self.sparse_model:
            # ========== 混合模式：使用命名向量 ==========
            texts = [doc.metadata.content.get("text", "") for doc in documents]
            sparse_vectors = self.sparse_model.embed(texts)

            # 构建 PointStruct 列表
            points = []
            for doc, dense_vec, sparse_vec in zip(documents, res_embeddings.embeddings, sparse_vectors):
                # 构建 payload (与原逻辑保持一致)
                payload = {
                    "content": doc.metadata.content,
                    "doc_id": doc.metadata.doc_id,
                    "chunk_id": doc.metadata.chunk_id,
                    "total_chunks": doc.metadata.total_chunks,
                }
                
                # 添加额外字段 (如 parent_text)
                if "parent_text" in doc.metadata:
                    payload["parent_text"] = doc.metadata["parent_text"]

                points.append(
                    models.PointStruct(
                        id=str(uuid.uuid4()),
                        vector={
                            self.DENSE_VECTOR_NAME: dense_vec,
                            self.SPARSE_VECTOR_NAME: sparse_vec,
                        },
                        payload=payload,
                    )
                )

            # 写入 Qdrant
            _client = self.store.get_client()
            await _client.upsert(
                collection_name=self.store.collection_name,
                points=points,
            )
        else:
            # ========== 纯向量模式 (向后兼容) ==========
            for doc, embedding in zip(documents, res_embeddings.embeddings):
                doc.embedding = embedding

            await self.store.add(documents)

    async def delete_document(self, file_hash: str) -> None:
        """
        根据文件哈希删除文档
        
        Args:
            file_hash: 文件哈希值 (对应 doc_id)，使用哈希值可避免文件名冲突
        """
        _client = self.store.get_client()
        await _client.delete(
            collection_name=self.store.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="doc_id",
                            match=models.MatchValue(value=file_hash),
                        )
                    ]
                )
            )
        )


if __name__ == '__main__':
    async def test_hybrid():
        """测试混合检索"""
        print("=== 测试混合检索 ===")
        knowledge = GeneralKnowledge(
            mode="general",
            db_name="qdrant_data",
            collection_name="law_knowledge_hybrid",
            enable_hybrid=True,
            delete_existing=True,  # 测试时重建集合
        )
        
        # 添加测试文档
        test_docs = [
            Document(
                metadata=DocMetadata(
                    content=TextBlock(type="text", text="合同是双方当事人依法达成的协议，受法律保护。"),
                    doc_id="test_doc",
                    chunk_id=0,
                    total_chunks=2,
                )
            ),
            Document(
                metadata=DocMetadata(
                    content=TextBlock(type="text", text="违约责任是指违反合同义务应承担的法律后果。"),
                    doc_id="test_doc",
                    chunk_id=1,
                    total_chunks=2,
                )
            ),
        ]
        
        await knowledge.add_documents(test_docs)
        print("文档已添加")
        
        # 检索测试
        docs = await knowledge.retrieve("合同是什么")
        print(f"检索结果: {len(docs)} 条")
        for doc in docs:
            print(f"  - {doc.metadata.content.get('text', '')[:50]}...")

    async def test_pure_vector():
        """测试纯向量检索 (向后兼容)"""
        print("\n=== 测试纯向量检索 ===")
        knowledge = GeneralKnowledge(
            mode="general",
            db_name="qdrant_data",
            collection_name="law_knowledge",
        )
        docs = await knowledge.retrieve("合同是什么")
        print(f"检索结果: {len(docs)} 条")

    asyncio.run(test_hybrid())
