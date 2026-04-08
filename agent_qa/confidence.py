from typing import List, Tuple

from agentscope.rag import Document

from enums import ConfidenceLevel


class ConfidenceEvaluator:

    def __init__(
        self,
        correct_threshold: float = 0.7,
        incorrect_threshold: float = 0.4,
    ):
        self.correct_threshold = correct_threshold
        self.incorrect_threshold = incorrect_threshold

    def evaluate(self, docs: List[Document]) -> Tuple[ConfidenceLevel, List[Document]]:
        if not docs:
            return ConfidenceLevel.INCORRECT, []

        top_score = docs[0].score

        if top_score is None:
            return ConfidenceLevel.INCORRECT, []

        if top_score >= self.correct_threshold:
            relevant_docs = [d for d in docs if d.score is not None and d.score >= self.correct_threshold]
            return ConfidenceLevel.CORRECT, relevant_docs[:3]
        elif top_score >= self.incorrect_threshold:
            return ConfidenceLevel.AMBIGUOUS, docs[:3]
        else:
            return ConfidenceLevel.INCORRECT, []
