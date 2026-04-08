from typing import List

from pydantic import BaseModel


class DocumentSource(BaseModel):
    content: str
    score: float
    doc_id: str


class WebSearchResult(BaseModel):
    title: str
    url: str
    content: str


class AnswerResult(BaseModel):
    answer: str
    confidence: str
    sources: List[DocumentSource] = []
    web_results: List[WebSearchResult] = []
    used_web_search: bool = False
