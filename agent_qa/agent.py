from typing import List

from agentscope.model import DashScopeChatModel
from agentscope.rag import Document
from module.knowledge_base.general_knowledge import GeneralKnowledge

from enums import ConfidenceLevel
from confidence import ConfidenceEvaluator
from query_rewriter import QueryRewriter
from web_search import DashScopeWebSearch
from answer_generator import AnswerGenerator
from agent_qa.models import AnswerResult, DocumentSource, WebSearchResult

class KnowledgeBaseQAAgent:

    def __init__(
        self,
        model: DashScopeChatModel,
        knowledge: GeneralKnowledge,
        correct_threshold: float = 0.7,
        incorrect_threshold: float = 0.4,
    ):
        self.model = model
        self.knowledge = knowledge
        self.confidence_eval = ConfidenceEvaluator(
            correct_threshold=correct_threshold,
            incorrect_threshold=incorrect_threshold,
        )
        self.query_rewriter = QueryRewriter(model)
        self.web_search = DashScopeWebSearch()
        self.answer_generator = AnswerGenerator(model)

    def _doc_to_source(self, doc: Document) -> DocumentSource:
        content = doc.metadata.content.get("text", "") if doc.metadata.content else ""
        return DocumentSource(
            content=content,
            score=doc.score,
            doc_id=doc.metadata.doc_id,
        )

    async def ask(self, query: str) -> AnswerResult:
        docs = await self.knowledge.retrieve(query, limit=5)

        confidence, relevant_docs = self.confidence_eval.evaluate(docs)

        if confidence == ConfidenceLevel.CORRECT:
            return await self._handle_correct(query, relevant_docs)
        elif confidence == ConfidenceLevel.AMBIGUOUS:
            return await self._handle_ambiguous(query, relevant_docs)
        else:
            return await self._handle_incorrect(query, docs)

    async def _handle_correct(
        self,
        query: str,
        docs: List[Document],
    ) -> AnswerResult:
        sources = [self._doc_to_source(d) for d in docs]

        answer = await self.answer_generator.answer_from_docs(query, sources)

        return AnswerResult(
            answer=answer,
            confidence=ConfidenceLevel.CORRECT.value,
            sources=sources,
            used_web_search=False,
        )

    async def _handle_ambiguous(
        self,
        query: str,
        docs: List[Document],
    ) -> AnswerResult:
        sources = [self._doc_to_source(d) for d in docs]

        web_results = await self.web_search.search(query)

        if web_results:
            answer = await self.answer_generator.answer_from_mixed(
                query, sources, web_results,
            )
            return AnswerResult(
                answer=answer,
                confidence=ConfidenceLevel.AMBIGUOUS.value,
                sources=sources,
                web_results=web_results,
                used_web_search=True,
            )
        else:
            answer = await self.answer_generator.answer_from_docs(query, sources)
            return AnswerResult(
                answer=answer,
                confidence=ConfidenceLevel.AMBIGUOUS.value,
                sources=sources,
                used_web_search=False,
            )

    async def _handle_incorrect(
        self,
        query: str,
        docs: List[Document],
    ) -> AnswerResult:
        if docs:
            rewritten_query = await self.query_rewriter.rewrite(query)
            web_results = await self.web_search.search(rewritten_query)
        else:
            web_results = await self.web_search.search(query)

        if web_results:
            answer = await self.answer_generator.answer_from_web(query, web_results)
            return AnswerResult(
                answer=answer,
                confidence=ConfidenceLevel.INCORRECT.value,
                web_results=web_results,
                used_web_search=True,
            )
        else:
            return AnswerResult(
                answer="抱歉，无法找到相关信息",
                confidence=ConfidenceLevel.INCORRECT.value,
                used_web_search=False,
            )
