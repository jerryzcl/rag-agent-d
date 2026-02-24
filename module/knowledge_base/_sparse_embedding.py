"""
稀疏向量 (Sparse Embedding) 封装模块

基于 FastEmbed 实现 BM25 稀疏向量生成，用于混合检索 (Hybrid Search)。

使用方法：
    model = SparseEmbeddingModel()
    vectors = model.embed(["合同条款", "违约责任"])
    # vectors 是 list[SparseVector]，可直接用于 Qdrant 存储
"""

from typing import List, Optional
from qdrant_client import models

try:
    from fastembed import SparseTextEmbedding
except ImportError:
    raise ImportError(
        "fastembed 未安装。请执行: pip install fastembed"
    )


class SparseEmbeddingModel:
    """
    BM25 稀疏向量生成器
    
    封装 FastEmbed 的 SparseTextEmbedding，提供与项目其他 Embedding 模型一致的接口。
    
    Args:
        model_name: 稀疏模型名称，默认 "Qdrant/bm25"
                    可选值: "Qdrant/bm25", "prithvida/Splade_PP_en_v1" 等
    
    示例:
        >>> model = SparseEmbeddingModel()
        >>> vectors = model.embed(["合同是什么"])
        >>> print(vectors[0].indices[:5])  # 输出 token 索引
    """
    
    def __init__(
        self, 
        model_name: str = "Qdrant/bm25",
        cache_dir: Optional[str] = None,
    ):
        """
        初始化稀疏向量模型
        
        Args:
            model_name: 模型名称
            cache_dir: 模型缓存目录（可选）
        """
        self.model_name = model_name
        
        # FastEmbed 初始化参数
        init_kwargs = {"model_name": model_name}
        if cache_dir:
            init_kwargs["cache_dir"] = cache_dir
            
        self.model = SparseTextEmbedding(**init_kwargs)
    
    def embed(self, texts: List[str]) -> List[models.SparseVector]:
        """
        批量生成稀疏向量
        
        Args:
            texts: 文本列表
            
        Returns:
            SparseVector 列表，每个元素包含:
                - indices: 非零元素的索引 (token ID)
                - values: 对应的权重值 (BM25 分数)
        """
        if not texts:
            return []
        
        # FastEmbed 返回生成器，转为列表
        embeddings = list(self.model.embed(texts))
        
        # 转换为 Qdrant SparseVector 格式
        sparse_vectors = []
        for emb in embeddings:
            sparse_vectors.append(
                models.SparseVector(
                    indices=emb.indices.tolist(),
                    values=emb.values.tolist()
                )
            )
        
        return sparse_vectors
    
    def embed_query(self, query: str) -> models.SparseVector:
        """
        生成单个查询的稀疏向量
        
        Args:
            query: 查询文本
            
        Returns:
            SparseVector 对象
        """
        return self.embed([query])[0]


if __name__ == "__main__":
    # 简单测试
    model = SparseEmbeddingModel()
    
    test_texts = [
        "合同是双方当事人达成的协议",
        "违约责任是指违反合同义务应承担的法律后果"
    ]
    
    vectors = model.embed(test_texts)
    
    for i, vec in enumerate(vectors):
        print(f"文本 {i+1}:")
        print(f"  非零元素数量: {len(vec.indices)}")
        print(f"  前5个索引: {vec.indices[:5]}")
        print(f"  前5个权重: {[round(v, 4) for v in vec.values[:5]]}")
