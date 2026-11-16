"""Import all 37 mapped models to register Base.metadata."""

from .conversations import (
    Conversation,
    Message,
    MessageCitation,
    Note,
    NoteCitation,
)
from .identity import (
    Course,
    Material,
    User,
    UserSession,
)
from .knowledge import KnowledgeBase
from .learning import (
    Flashcard,
    FlashcardDeck,
    FlashcardReview,
    FlashcardSource,
    MasteryState,
    StudyPlan,
    StudyPlanItem,
)
from .materials import (
    ChunkEmbedding,
    IndexVersion,
    KnowledgePoint,
    KnowledgePointSource,
    MaterialChunk,
    MaterialSection,
    MaterialVersion,
    SourceQuestion,
)
from .model_usage import ModelCallRecord
from .operations import (
    AiJob,
    AiJobAttempt,
    AiWorkflowRun,
    AuditLog,
    IdempotencyKey,
    LearningEvent,
    OutboxEvent,
)
from .quizzes import (
    Quiz,
    QuizAttempt,
    QuizItem,
    QuizItemSource,
    QuizResponse,
    WrongQuestion,
)

__all__ = [
    "KnowledgeBase",
    "User",
    "UserSession",
    "Course",
    "Material",
    "MaterialVersion",
    "MaterialSection",
    "IndexVersion",
    "MaterialChunk",
    "ChunkEmbedding",
    "KnowledgePoint",
    "KnowledgePointSource",
    "SourceQuestion",
    "Conversation",
    "Message",
    "MessageCitation",
    "Note",
    "NoteCitation",
    "Quiz",
    "QuizItem",
    "QuizItemSource",
    "QuizAttempt",
    "QuizResponse",
    "WrongQuestion",
    "FlashcardDeck",
    "Flashcard",
    "FlashcardSource",
    "FlashcardReview",
    "MasteryState",
    "StudyPlan",
    "StudyPlanItem",
    "LearningEvent",
    "AiJob",
    "AiJobAttempt",
    "AiWorkflowRun",
    "OutboxEvent",
    "IdempotencyKey",
    "AuditLog",
    "ModelCallRecord",
]
