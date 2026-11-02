from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserFacingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MaterialReadStatus(StrEnum):
    SUCCESS = "SUCCESS"
    INCOMPLETE = "INCOMPLETE"
    NOT_FOUND = "NOT_FOUND"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    STORAGE_ERROR = "STORAGE_ERROR"


class SourceStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class AuditStatus(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    NEEDS_INFO = "NEEDS_INFO"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED = "BLOCKED"


class AuditCheckStatus(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    NEEDS_INFO = "NEEDS_INFO"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class RuleSeverity(StrEnum):
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class RuleKind(StrEnum):
    STRUCTURED = "STRUCTURED"
    SEMANTIC = "SEMANTIC"


class RuleOperator(StrEnum):
    REQUIRED = "REQUIRED"
    EQUALS = "EQUALS"
    IN = "IN"
    CONTAINS_ANY = "CONTAINS_ANY"
    BEFORE = "BEFORE"
    AFTER = "AFTER"
    REGEX = "REGEX"


class RuleOutcome(StrEnum):
    PASSED = "PASSED"
    VIOLATED = "VIOLATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNCERTAIN = "UNCERTAIN"
    MISSING = "MISSING"
    ERROR = "ERROR"


class SemanticOutcome(StrEnum):
    PASSED = "PASSED"
    VIOLATED = "VIOLATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNCERTAIN = "UNCERTAIN"


class ScheduleQueryStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    CONFLICT_WITH_ALTERNATIVE = "CONFLICT_WITH_ALTERNATIVE"
    NO_FEASIBLE_SLOT = "NO_FEASIBLE_SLOT"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    STORAGE_ERROR = "STORAGE_ERROR"


class ScheduleEntryStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"


class OccupancyKind(StrEnum):
    HARD = "HARD"
    SOFT = "SOFT"


class ScheduleRecordType(StrEnum):
    SCHEDULE = "SCHEDULE"
    DRAFT = "DRAFT"


class DraftStatus(StrEnum):
    READY = "READY"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"


class DraftSaveStatus(StrEnum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    SAVED = "SAVED"
    EXISTING = "EXISTING"
    REJECTED = "REJECTED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    STORAGE_ERROR = "STORAGE_ERROR"


class ConfirmationReason(StrEnum):
    ALTERNATIVE_TIME = "ALTERNATIVE_TIME"
    RULE_WARNING = "RULE_WARNING"


class PublicationStatus(StrEnum):
    NOT_PUBLISHED = "NOT_PUBLISHED"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"


class ToolName(StrEnum):
    READ_MATERIAL = "read_material"
    CHECK_AUDIT_RULES = "check_audit_rules"
    QUERY_SCHEDULE_SLOTS = "query_schedule_slots"
    SAVE_SCHEDULE_DRAFT = "save_schedule_draft"


class AgentAction(StrEnum):
    READ_MATERIAL = "read_material"
    CHECK_AUDIT_RULES = "check_audit_rules"
    QUERY_SCHEDULE_SLOTS = "query_schedule_slots"
    SAVE_SCHEDULE_DRAFT = "save_schedule_draft"
    FINISH = "finish"


class AgentMessageRole(StrEnum):
    SYSTEM = "SYSTEM"
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    TOOL = "TOOL"


class ToolError(StrictModel):
    code: str
    message: str
    retryable: bool = False


class DataCompleteness(StrictModel):
    complete: bool
    missing_fields: list[str] = Field(default_factory=list)


class MaterialRecord(StrictModel):
    asset_id: str
    title: str | None = None
    platform: str | None = None
    source_status: str | None = None
    content: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None
    updated_at: datetime | None = None


class MaterialView(StrictModel):
    asset_id: str
    title: str | None = None
    platform: str | None = None
    source_status: SourceStatus | None = None
    metadata: dict[str, Any] | None = None
    content_excerpt: str | None = None
    content_truncated: bool = False
    updated_at: datetime | None = None


class MaterialReadInput(StrictModel):
    asset_id: str = Field(min_length=1, max_length=64)


class MaterialReadResult(StrictModel):
    status: MaterialReadStatus
    material: MaterialView | None = None
    data_completeness: DataCompleteness | None = None
    error: ToolError | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class StructuredRuleCondition(StrictModel):
    clause_index: int
    field_path: str
    operator: RuleOperator
    expected_value: Any = None


class AuditRuleRecord(StrictModel):
    rule_id: str
    rule_name: str
    enabled: bool
    rule_kind: RuleKind
    severity: RuleSeverity
    applies_to: list[str] | None = None
    conditions: list[StructuredRuleCondition] = Field(default_factory=list)
    policy_text: str | None = None
    reason: str
    priority: int
    rule_version: int = 1
    effective_from: datetime | None = None
    effective_to: datetime | None = None


class RuleEvaluation(StrictModel):
    rule_id: str
    rule_name: str
    rule_kind: RuleKind
    severity: RuleSeverity
    outcome: RuleOutcome
    reason: str
    evidence_spans: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evaluator: str
    error_code: str | None = None


class SemanticRuleJudgment(StrictModel):
    rule_id: str
    outcome: SemanticOutcome
    reason: str
    evidence_spans: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)


class SemanticEvaluationBatch(StrictModel):
    judgments: list[SemanticRuleJudgment] = Field(default_factory=list)


class AuditSummary(StrictModel):
    applicable_rule_count: int = 0
    structured_rule_count: int = 0
    semantic_rule_count: int = 0
    checked_rule_count: int = 0
    passed_rule_count: int = 0
    warning_violation_count: int = 0
    blocking_violation_count: int = 0
    uncertain_rule_count: int = 0
    missing_rule_count: int = 0
    error_rule_count: int = 0


class AuditCheckInput(StrictModel):
    asset_id: str = Field(min_length=1, max_length=64)
    requested_at: datetime
    timezone: str = Field(min_length=1)


class AuditCheckResult(StrictModel):
    status: AuditCheckStatus
    rule_results: list[RuleEvaluation] = Field(default_factory=list)
    summary: AuditSummary = Field(default_factory=AuditSummary)
    error: ToolError | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ConflictRecord(StrictModel):
    record_type: ScheduleRecordType
    record_id: str
    asset_id: str
    publish_at: datetime
    publish_at_utc: datetime
    occupied_until: datetime
    occupied_until_utc: datetime
    duration_minutes: int = Field(ge=1)
    status: str
    occupancy: OccupancyKind


class AlternativeSlot(StrictModel):
    publish_at: datetime
    publish_at_utc: datetime
    occupied_until: datetime
    occupied_until_utc: datetime
    duration_minutes: int = Field(ge=1)
    timezone: str
    requires_confirmation: bool = True


class ScheduleQueryInput(StrictModel):
    asset_id: str = Field(min_length=1, max_length=64)
    requested_at: datetime
    timezone: str = Field(min_length=1)
    search_until: datetime | None = None


class ScheduleQueryContext(StrictModel):
    idempotency_key: str
    scope_id: str = "global"
    occupancy_minutes: int = Field(default=30, ge=1, le=1440)
    slot_step_minutes: int = Field(default=30, ge=1, le=1440)
    max_alternatives: int = Field(default=3, ge=1, le=10)


class ScheduleQueryResult(StrictModel):
    status: ScheduleQueryStatus
    requested_at: datetime | None = None
    requested_at_utc: datetime | None = None
    timezone: str | None = None
    scope_id: str | None = None
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    alternatives: list[AlternativeSlot] = Field(default_factory=list)
    error: ToolError | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class DraftView(StrictModel):
    draft_id: str
    asset_id: str
    scope_id: str
    requested_at: datetime
    proposed_at: datetime
    duration_minutes: int = Field(default=30, ge=1)
    timezone: str
    audit_status: AuditStatus
    risk_rule_ids: list[str] = Field(default_factory=list)
    risk_reasons: list[str] = Field(default_factory=list)
    draft_status: DraftStatus
    requires_confirmation: bool
    confirmation_reasons: list[ConfirmationReason] = Field(default_factory=list)
    publication_status: PublicationStatus = PublicationStatus.NOT_PUBLISHED
    created_at: datetime


class SaveDraftInput(StrictModel):
    asset_id: str = Field(min_length=1, max_length=64)
    proposed_at: datetime
    timezone: str = Field(min_length=1)


class SaveDraftContext(StrictModel):
    run_id: str
    idempotency_key: str
    scope_id: str = "global"
    requested_at: datetime
    duration_minutes: int = Field(default=30, ge=1, le=1440)
    audit_status: AuditStatus
    risk_rule_ids: list[str] = Field(default_factory=list)
    risk_reasons: list[str] = Field(default_factory=list)
    schedule_check_status: ScheduleQueryStatus
    allowed_alternatives: list[datetime] = Field(default_factory=list)


class SaveDraftResult(StrictModel):
    status: DraftSaveStatus
    draft: DraftView | None = None
    error: ToolError | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class AgentMessage(StrictModel):
    role: AgentMessageRole
    content: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    arguments: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime


class RunRequest(StrictModel):
    run_id: str
    workspace_id: str
    scope_id: str
    asset_id: str
    requested_at: datetime
    requested_at_utc: datetime
    timezone: str
    search_until: datetime | None = None
    idempotency_key: str


class ToolCallRecord(StrictModel):
    step: int
    tool_name: ToolName
    arguments: dict[str, Any]
    result_status: str
    summary: dict[str, Any]
    error_code: str | None = None
    decision_basis: str | None = None
    created_at: datetime


class RunState(StrictModel):
    request: RunRequest
    existing_draft: DraftView | None = None
    material_result: MaterialReadResult | None = None
    audit_result: AuditCheckResult | None = None
    schedule_result: ScheduleQueryResult | None = None
    draft_result: SaveDraftResult | None = None
    step_count: int = 0
    tool_call_count: int = 0
    failed_retry_count: int = 0
    last_action_signature: str | None = None
    last_observation_signature: str | None = None
    run_status: RunStatus = RunStatus.RUNNING
    pending_items: list[ConfirmationReason] = Field(default_factory=list)
    tool_history: list[ToolCallRecord] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)
    unfinished_reason: str | None = None


class AgentDecision(StrictModel):
    action: Literal[
        "read_material",
        "check_audit_rules",
        "query_schedule_slots",
        "save_schedule_draft",
        "finish",
    ]
    arguments: dict[str, Any] = Field(default_factory=dict)
    decision_basis: str = Field(default="", max_length=300)


class RunReport(UserFacingModel):
    run_id: str
    asset_id: str
    audit_status: AuditStatus | None = None
    risk_reasons: list[str] = Field(default_factory=list)
    requested_at: datetime
    proposed_at: datetime | None = None
    occupancy_minutes: int = Field(default=30, ge=1)
    requires_confirmation: bool = False
    confirmation_reasons: list[ConfirmationReason] = Field(default_factory=list)
    draft_id: str | None = None
    draft_status: DraftStatus | None = None
    draft_save_status: DraftSaveStatus = DraftSaveStatus.NOT_ATTEMPTED
    publication_status: PublicationStatus = PublicationStatus.NOT_PUBLISHED
    run_status: RunStatus
    unfinished_reason: str | None = None
    trace_path: str | None = None
    tool_steps: int = 0
    model_steps: int = 0
    model_mode: str = "mock"
