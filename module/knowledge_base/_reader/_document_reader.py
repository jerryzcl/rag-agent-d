import asyncio

from agentscope.message import TextBlock
from agentscope.rag import Document, DocMetadata
from agentscope.rag._reader._reader_base import ReaderBase

import re
from pathlib import Path
from typing import List, Optional, Literal
import fitz  # PyMuPDF
# from docx import Document as DocxDocument

from module.knowledge_base._reader._word_reader import WordReader


def _merge_splits(splits: List[str], sep: str, max_len: int) -> List[str]:
    """把小于 max_len 的文本块合并成尽可能大的块"""
    merged = []
    cur, cur_len = [], 0
    for s in splits:
        s_len = len(s)
        add_len = s_len + (len(sep) if cur else 0)
        if cur_len + add_len > max_len:
            if cur:
                merged.append(sep.join(cur).strip())
            cur, cur_len = [s], s_len
        else:
            cur.append(s)
            cur_len += add_len
    if cur:
        merged.append(sep.join(cur).strip())
    return [m for m in merged if m]


def recursive_split(
        text: str,
        max_len: int,
        separators: List[str] = None,
) -> List[str]:
    """递归文本分段，按分隔符切分"""
    if separators is None:
        separators = ["\n\n", "\n", "。", ". ", " ", ""]
    if len(text) <= max_len:
        return [text.strip()] if text.strip() else []

    # 找第一个能匹配的分隔符
    cur_sep = separators[-1]
    next_seps = []
    for i, s in enumerate(separators):
        if s == "":
            cur_sep = s
            break
        if s in text:  # 简单字符串匹配
            cur_sep = s
            next_seps = separators[i + 1:]
            break

    if cur_sep:
        if cur_sep == " ":
            splits = re.split(r" +", text)
        else:
            splits = text.split(cur_sep)
            splits = [item + cur_sep if i < len(splits) else item for i, item in enumerate(splits)]
    else:
        splits = list(text)
    if cur_sep == "\n":
        splits = [s.strip() for s in splits if s != ""]
    else:
        splits = [s.strip() for s in splits if (s not in {"", "\n"})]

    good = []
    final = []

    if cur_sep != "":
        for s in splits:
            if len(s) < max_len:
                good.append(s)
            else:
                # 遇到大块时，先合并并清空累积的小块
                if good:
                    final.extend(_merge_splits(good, cur_sep, max_len))
                    good = []

                # 递归处理当前大块，或者如果没有后续分隔符则保留
                if next_seps:
                    final.extend(recursive_split(s, max_len, next_seps))
                else:
                    final.append(s)

        # 循环结束后处理剩余的小块
        if good:
            final.extend(_merge_splits(good, cur_sep, max_len))
    else:
        # 对应参考代码中 separator 为空字符串的逻辑分支
        # 由于当前函数没有 overlap 参数，这里直接利用 _merge_splits 将字符列表合并为块
        final.extend(_merge_splits(splits, "", max_len))

    return final


def create_chunks_with_overlap(text: str, chunk_size: int, overlap: int) -> List[str]:
    """
    使用滑动窗口创建有重叠的文本块

    Args:
        text: 要切分的文本
        chunk_size: 每个块的最大大小
        overlap: 相邻块之间的重叠字符数

    Returns:
        有重叠的文本块列表，每个块大小 <= chunk_size

    原理：
        - 第一个块: text[0:chunk_size]
        - 第二个块: text[chunk_size-overlap:2*chunk_size-overlap]
        - 第三个块: text[2*chunk_size-2*overlap:3*chunk_size-2*overlap]
        ...
    """
    if overlap >= chunk_size:
        raise ValueError(f"overlap ({overlap}) 必须小于 chunk_size ({chunk_size})")

    if overlap < 0:
        overlap = 0

    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        # 每次取 chunk_size 长度
        end = min(start + chunk_size, text_len)
        chunk = text[start:end].strip()

        if chunk:
            chunks.extend(recursive_split(chunk, chunk_size))

        # 如果到达末尾，退出
        if end >= text_len:
            break

        # 下一个块的起始位置：向后退 overlap 个字符形成重叠
        start = end - overlap

    return chunks


class DocumentReader(ReaderBase):
    """支持 PDF/DOCX/TXT/MD，支持 general 和 parent_child 两种分段模式"""

    def __init__(
            self,
            mode: Literal["general", "parent_child"] = "general",
            custom_regex: Optional[str] = None,
            custom_regex_extra: Optional[str] = None,
            chunk_overlap: int = 0,
            child_max: int = 512,
            parent_max: int = 1024,
    ):
        """
        初始化阅读器

        Args:
            mode: 分段模式，"general" 或 "parent_child"
            custom_regex: 自定义正则表达式（可选），用于切分文本
            child_max: 子分段最大长度
            parent_max: 父分段最大长度（仅在 parent_child 模式下使用）
        """
        self.mode = mode
        self.custom_regex = custom_regex
        self.custom_regex_extra = custom_regex_extra
        self.child_max = child_max
        self.parent_max = parent_max
        self.chunk_overlap = chunk_overlap

    async def _load(self, path: str) -> str:
        """读取文件内容"""
        suf = Path(path).suffix.lower()
        try:
            if suf == ".pdf":
                doc = fitz.open(path)
                return "\n".join(p.get_text() for p in doc)
            if suf == ".docx":
                word_reader = WordReader()
                doc = await word_reader(path)
                return "".join(p for p in doc if p.strip())
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"Error reading {path}: {e}")
            return ""

    def _split_by_regex(self, text: str, regex: Optional[str] = None) -> List[str]:
        """使用正则表达式切分文本，保留匹配的分隔符"""
        if not regex:
            return []
        pat = re.compile(regex)
        matches = list(pat.finditer(text))
        if not matches:
            return []

        chunks = []
        start = 0
        for m in matches:
            if start < m.start():
                # 从上一个位置到匹配结束的内容（包含匹配的文本）
                chunks.append(text[start:m.start()])
            start = m.start()

        if start < len(text):
            chunks.append(text[start:])

        return [c.strip() for c in chunks if c.strip()]

    async def __call__(self, file_path: str, doc_id: Optional[str] = None) -> List[Document]:
        """
        读取文件并分段，返回 Document 列表

        分段策略（已修正）：
        1. 先使用正则切分（如果提供了 custom_regex）
        2. 对每个正则切分后的块，再用 recursive_split 确保不超过 chunk_size

        在 general 模式下：
            - 先正则切分 -> 再对每块 recursive_split(child_max)
        在 parent_child 模式下：
            - 父分段：先正则切分 -> 再对每块 recursive_split(parent_max)
            - 子分段：对每个父分段先正则切分 -> 再对每块 recursive_split(child_max)
        
        Args:
            file_path: 文件路径
            doc_id: 文档唯一标识符 (建议使用 file_hash)，若不提供则默认使用文件名
        """
        text = await self._load(file_path)
        if not text.strip():
            return []

        file_name = Path(file_path).name
        # 如果未提供 doc_id，默认使用文件名 (向后兼容)
        effective_doc_id = doc_id if doc_id else file_name

        if self.mode == "general":
            # 通用分段模式
            # 步骤1: 先用正则切分（如果有）
            if self.custom_regex:
                regex_chunks = self._split_by_regex(text, self.custom_regex)
                if regex_chunks:
                    # 步骤2: 对每个正则切分后的块再用滑动窗口或 recursive_split
                    final_chunks = []
                    for chunk in regex_chunks:
                        if self.chunk_overlap > 0 and len(chunk) > self.child_max:
                            # 使用滑动窗口创建有 overlap 的块
                            sub_chunks = create_chunks_with_overlap(chunk, self.child_max, self.chunk_overlap)
                            final_chunks.extend(sub_chunks)
                        elif len(chunk) > self.child_max:
                            # 不需要 overlap，使用普通递归切分
                            sub_chunks = recursive_split(chunk, self.child_max)
                            final_chunks.extend(sub_chunks)
                        else:
                            final_chunks.append(chunk)
                else:
                    # 正则没匹配到，直接处理整个文本
                    if self.chunk_overlap > 0:
                        final_chunks = create_chunks_with_overlap(text, self.child_max, self.chunk_overlap)
                    else:
                        final_chunks = recursive_split(text, self.child_max)
            else:
                # 没有正则，直接处理整个文本
                if self.chunk_overlap > 0:
                    final_chunks = create_chunks_with_overlap(text, self.child_max, self.chunk_overlap)
                else:
                    final_chunks = recursive_split(text, self.child_max)

            return [
                Document(
                    metadata=DocMetadata(
                        content=TextBlock(type="text", text=c),
                        doc_id=effective_doc_id,
                        chunk_id=i,
                        total_chunks=len(final_chunks),
                    )
                )
                for i, c in enumerate(final_chunks)
            ]

        # parent_child 模式
        # 步骤1: 父分段切分
        parents = []
        if self.custom_regex:
            regex_parents = self._split_by_regex(text, self.custom_regex)
            if regex_parents:
                parents = regex_parents
            else:
                parents = recursive_split(text, self.parent_max)
        else:
            parents = recursive_split(text, self.parent_max)

        # 步骤2: 对每个父分段切分子分段
        docs, gid = [], 0
        for parent in parents:
            # 子分段：先尝试正则切分，再 recursive_split
            children = []
            if self.custom_regex_extra:
                regex_children = self._split_by_regex(parent, self.custom_regex_extra)
                if regex_children:
                    for chunk in regex_children:
                        if len(chunk) > self.child_max:
                            sub_chunks = recursive_split(chunk, self.child_max)
                            children.extend(sub_chunks)
                        else:
                            children.append(chunk)
                else:
                    children = recursive_split(parent, self.child_max)
            else:
                children = recursive_split(parent, self.child_max)

            for child in children:
                meta = DocMetadata(
                    content=TextBlock(type="text", text=child),
                    doc_id=effective_doc_id,
                    chunk_id=gid,
                    total_chunks=0,  # 在最后会更新
                )
                # 使用字典式赋值添加额外字段
                meta["parent_text"] = parent
                docs.append(Document(metadata=meta))
                gid += 1

        # 更新 total_chunks
        for doc in docs:
            doc.metadata.total_chunks = len(docs)

        return docs

    def get_doc_id(self, file_path: str) -> str:
        """获取文档 ID（用于增量更新）"""
        return str(Path(file_path).stat().st_mtime)

if __name__ == '__main__':
    async def test():
        reader = DocumentReader()
        doc = await reader._load(r"E:\15_写制度\分类的制度文件\test\（01）FSZY-XX-PP-005 信息工作安全管理制度（2024.11.08受控）.docx")
        print(doc)

    asyncio.run(test())
