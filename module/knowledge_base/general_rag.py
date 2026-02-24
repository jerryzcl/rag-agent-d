from typing import Dict, Optional, Literal
from pathlib import Path
import asyncio
import hashlib
import os
import json

from agentscope.rag import KnowledgeBase

from module.knowledge_base._reader._document_reader import DocumentReader
from module.knowledge_base.general_knowledge import GeneralKnowledge

class GeneralRAG:
    """
    RAG 系统，支持通用和父子分段两种模式
    
    支持混合检索 (Hybrid Search)：
        - enable_hybrid=True 时启用 BM25 + 向量混合检索
        - 使用 Qdrant 的 Prefetch + RRF Fusion 实现结果融合
    """

    def __init__(
            self,
            dir_path: str = "./legal_library",
            db_path: str = "./qdrant_legal",
            collection_name: str = "rag_collection",
            mode: Literal["general", "parent_child"] = "parent_child",
            custom_regex: Optional[str] = None,
            custom_regex_extra: Optional[str] = None,
            child_max: int = 512,
            parent_max: int = 1024,
            chunk_overlap: int = 0,
            max_concurrency: int = 5,
            knowledge: Optional[GeneralKnowledge] = None,
            enable_hybrid: bool = False,
            sparse_model_name: str = "Qdrant/bm25",
            enable_rerank: bool = False,
            rerank_model: str = "gte-rerank",
    ):
        """
        初始化 RAG 系统

        Args:
            dir_path: 文档目录
            db_path: 向量数据库路径
            collection_name: 集合名称
            mode: 分段模式，"general" 或 "parent_child"
            custom_regex: 自定义正则表达式（可选）
            child_max: 子分段最大长度
            parent_max: 父分段最大长度
            max_concurrency: 最大并发数
            chunk_overlap: 子分段重叠长度（默认0）
            enable_hybrid: 是否启用混合检索 (BM25 + Vector)
            sparse_model_name: 稀疏模型名称，默认 "Qdrant/bm25"
            enable_rerank: 是否启用重排序 (Rerank)
            rerank_model: 重排序模型名称，默认 "gte-rerank"
        """
        self.dir_path = Path(dir_path)
        self.db_path = Path(db_path)
        self.collection_name = collection_name
        self.mode = mode
        self.custom_regex = custom_regex
        self.custom_regex_extra = custom_regex_extra
        self.child_max = child_max
        self.parent_max = parent_max
        self.enable_hybrid = enable_hybrid
        self.sparse_model_name = sparse_model_name
        self.enable_rerank = enable_rerank
        self.rerank_model = rerank_model

        self.knowledge = knowledge
        # self.knowledge_cls = knowledge_cls
        self.reader = DocumentReader(
            mode=mode,
            custom_regex=custom_regex,
            custom_regex_extra=custom_regex_extra,
            child_max=child_max,
            parent_max=parent_max,
            chunk_overlap=chunk_overlap,
        )
        self.file_index_path = self.db_path / "file_index.json"
        self.file_index: Dict[str, str] = self._load_index()
        self.semaphore = asyncio.Semaphore(max_concurrency)

        os.makedirs(self.db_path, exist_ok=True)
        self.dir_path.mkdir(exist_ok=True)

    def _load_index(self) -> Dict[str, str]:
        if self.file_index_path.exists():
            try:
                return json.loads(self.file_index_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                return {}
        return {}

    def _save_index(self):
        self.file_index_path.write_text(json.dumps(self.file_index, ensure_ascii=False, indent=2), encoding="utf-8")

    async def init_knowledge(self):
        """初始化知识库"""
        if self.knowledge is not None:
            return

        self.knowledge = GeneralKnowledge(
            mode=self.mode,
            db_name=self.db_path.name,
            collection_name=self.collection_name,
            embedding_dimensions=1024,
            delete_existing=False,  # 生产环境不删除已存在的集合
            enable_hybrid=self.enable_hybrid,
            sparse_model_name=self.sparse_model_name,
            enable_rerank=self.enable_rerank,
            rerank_model=self.rerank_model,
        )


    async def _process_single_file(self, file_path: Path, force: bool):
        """处理单个文件，受信号量控制"""
        async with self.semaphore:
            file_str = str(file_path)
            current_hash = self._file_hash(file_str)

            if force or self.file_index.get(file_str) != current_hash:
                print(f"更新 → {file_path.name}")
                try:
                    # 使用 file_hash 作为 doc_id，避免文件名冲突
                    docs = await self.reader(file_str, doc_id=current_hash)
                    # 更新索引需在主流程统一处理或加锁，这里暂存结果
                    return docs, file_str, current_hash
                except Exception as e:
                    print(f"处理失败 {file_path.name}: {e}")
                    return [], file_str, None
            else:
                # print(f"跳过未变更 → {file_path.name}")
                return [], file_str, None

    async def update(self, force: bool = False) -> None:
        """智能增量更新：并发处理文件"""
        if not self.knowledge:
            await self.init_knowledge()

        files = list(self.dir_path.rglob("*.pdf")) + \
                list(self.dir_path.rglob("*.docx")) + \
                list(self.dir_path.rglob("*.txt")) + \
                list(self.dir_path.rglob("*.md")) + \
                list(self.dir_path.rglob("*.doc"))

        print(f"扫描到 {len(files)} 个文件，开始处理...")

        tasks = [self._process_single_file(f, force) for f in files]
        results = await asyncio.gather(*tasks)

        all_docs = []
        updated_count = 0

        for docs, file_str, current_hash in results:
            if docs:
                all_docs.extend(docs)
                self.file_index[file_str] = current_hash
                updated_count += 1
            elif current_hash is None and file_str:
                # 处理失败的情况，不更新索引
                pass

        if all_docs:
            print(f"正在存入向量库...")
            await self.knowledge.add_documents(all_docs)
            print(f"本次更新 {updated_count} 个文件，共添加 {len(all_docs)} 条条文")
        else:
            print("无文件变更")

        self._save_index()

    def _file_hash(self, path: str) -> str:
        """高效计算文件哈希（分块读取）"""
        hash_md5 = hashlib.md5()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except:
            return ""

if __name__ == '__main__':

    async def main():
        rag = GeneralRAG(
            dir_path=r"E:\AI合同\外部法典",
            db_path=r"../dataset/qdrant_data",
            collection_name="law_knowledge",
            mode="general",
            custom_regex=r"(?<![\d.])\d+\.\d+(?![\d.])",
            chunk_overlap=100,
            child_max=2048,
            max_concurrency=5,
            enable_hybrid=True
        )

        await rag.update(True)

    asyncio.run(main())
