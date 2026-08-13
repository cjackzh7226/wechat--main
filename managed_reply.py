from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, Sequence, TypeAlias

from candidate_judge import Judgement
from knowledge_base import KnowledgeCandidate, KnowledgeRecord, OrgScope


NO_MATCH_REPLY = "暂未查到对应答案"
DecisionKind: TypeAlias = Literal["answer", "no_answer", "technical_failure"]


class KnowledgeRepositoryProtocol(Protocol):
    def candidates(
        self, question: str, recent_context: str, limit: int = 3
    ) -> list[KnowledgeCandidate]: ...

    def get_active(self, record_id: str) -> KnowledgeRecord | None: ...


class CandidateJudgeProtocol(Protocol):
    def judge(
        self, question: str, candidates: Sequence[KnowledgeCandidate]
    ) -> Judgement | None: ...


@dataclass(frozen=True)
class ReplyDecision:
    kind: DecisionKind
    text: str
    record_id: str = ""
    answer_hash: str = ""
    reason: str = ""
    score: int = 0
    org_scope: OrgScope = "unknown"

    def __post_init__(self) -> None:
        if self.kind not in {"answer", "no_answer", "technical_failure"}:
            raise ValueError("invalid managed reply decision kind")
        if self.kind == "answer":
            if not self.text or not self.record_id or not self.answer_hash:
                raise ValueError("answer decision requires text, record_id and answer_hash")
        elif self.kind == "no_answer":
            if self.text != NO_MATCH_REPLY or self.record_id or self.answer_hash:
                raise ValueError("no_answer decision must use the exact no-match contract")
        elif self.text or self.record_id or self.answer_hash:
            raise ValueError("technical_failure decision must not contain sendable text")

    @classmethod
    def answer(cls, record: KnowledgeRecord, candidate: KnowledgeCandidate) -> "ReplyDecision":
        return cls(
            kind="answer",
            text=record.reply,
            record_id=record.record_id,
            answer_hash=record.answer_hash,
            reason="approved_candidate_reloaded",
            score=candidate.score,
            org_scope=record.org_scope,
        )

    @classmethod
    def no_answer(cls, reason: str) -> "ReplyDecision":
        return cls(kind="no_answer", text=NO_MATCH_REPLY, reason=reason)

    @classmethod
    def technical_failure(cls, reason: str) -> "ReplyDecision":
        return cls(kind="technical_failure", text="", reason=reason)


class ManagedReplyService:
    def __init__(
        self,
        repository: KnowledgeRepositoryProtocol,
        judge: CandidateJudgeProtocol,
        *,
        candidate_limit: int = 3,
    ) -> None:
        if type(candidate_limit) is not int or candidate_limit <= 0:
            raise ValueError("candidate_limit must be a positive integer")
        self.repository = repository
        self.judge = judge
        self.candidate_limit = candidate_limit

    def decide(self, current_question: str, recent_context: str) -> ReplyDecision:
        try:
            candidates = self.repository.candidates(
                current_question, recent_context, self.candidate_limit
            )
        except Exception:
            return ReplyDecision.technical_failure("repository_unavailable")
        if not candidates:
            return ReplyDecision.no_answer("no_eligible_candidate")

        try:
            judgement = self.judge.judge(current_question, candidates)
        except Exception:
            judgement = None
        if not isinstance(judgement, Judgement):
            return ReplyDecision.no_answer("judge_failed_or_invalid")
        if not judgement.consistent:
            return ReplyDecision.no_answer("judge_inconsistent")
        if not 0 <= judgement.chosen_index < len(candidates):
            return ReplyDecision.no_answer("judge_index_out_of_range")

        selected = candidates[judgement.chosen_index]
        try:
            record = self.repository.get_active(selected.record_id)
        except Exception:
            return ReplyDecision.technical_failure("repository_unavailable")
        if (
            record is None
            or record.status != "active"
            or record.record_id != selected.record_id
            or record.answer_hash != selected.answer_hash
        ):
            return ReplyDecision.technical_failure(
                "record_changed_after_judgement"
            )
        return ReplyDecision.answer(record, selected)

    def validate_for_send(self, decision: ReplyDecision) -> bool:
        """Recheck queued decisions immediately before any sender side effect."""
        if not isinstance(decision, ReplyDecision):
            return False
        if decision.kind == "no_answer":
            return decision.text == NO_MATCH_REPLY
        if decision.kind != "answer":
            return False
        try:
            record = self.repository.get_active(decision.record_id)
        except Exception:
            return False
        return bool(
            record is not None
            and record.status == "active"
            and record.record_id == decision.record_id
            and record.answer_hash == decision.answer_hash
            and record.reply == decision.text
        )
