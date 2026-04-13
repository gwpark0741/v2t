from __future__ import annotations

from pathlib import Path
from typing import Literal, List, Optional, Union, Dict, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EntityAudibility = Literal["audible", "likely_audible", "visual_only", "inactive"]
InteractionType = Literal["hard_effect", "foley", "background", "electronic"]
TrackType = Literal["sfx", "ambience"]
DistanceProfile = Literal["near", "mid", "far"]
WarningSeverity = Literal["error", "warning", "info"]
StageName = Literal[
    "stage_01_local_preprocessing",
    "stage_02_full_video_asset",
    "stage_03_agent_a",
    "stage_04_segment_prep",
    "stage_05_agent_b",
    "stage_06_agent_c",
    "stage_07_final",
]
StageExecutionStatus = Literal["pending", "running", "completed", "failed", "skipped"]
T = TypeVar("T")


class Interval(BaseModel):
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def check_order(self) -> "Interval":
        if self.end_time < self.start_time:
            raise ValueError("end_time must be >= start_time")
        return self


class VideoMetadata(BaseModel):
    """전처리 단계에서 영상의 기본 물리 정보를 보관합니다."""

    video_path: str
    fps: float = Field(gt=0.0)
    frame_count: int = Field(gt=0)
    duration_seconds: float = Field(gt=0.0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid")


class Cut(BaseModel):
    """PySceneDetect 결과를 기반으로 확정된 컷 경계를 표현합니다."""

    id: str
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def check_order(self) -> "Cut":
        if self.end_time <= self.start_time:
            raise ValueError("cut end_time must be > start_time")
        return self


class PreprocessingResult(BaseModel):
    """전처리 출력의 단일 계약: 영상 메타데이터 + authoritative cut 목록."""

    video_metadata: VideoMetadata
    cuts: List[Cut]
    video_url: str = Field(min_length=1)
    video_mime_type: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @field_validator("cuts")
    def non_empty_cuts(cls, value: List[Cut]) -> List[Cut]:
        if not value:
            raise ValueError("PreprocessingResult must contain at least one cut")
        return value


class LocalPreprocessingResult(BaseModel):
    """Stage 01 출력 계약: 로컬 분석 결과만 포함합니다."""

    video_metadata: VideoMetadata
    cuts: List[Cut]
    video_path: str
    video_mime_type: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @field_validator("cuts")
    def non_empty_cuts(cls, value: List[Cut]) -> List[Cut]:
        if not value:
            raise ValueError("LocalPreprocessingResult must contain at least one cut")
        return value


class FullVideoAssetResult(BaseModel):
    """Stage 02 출력 계약: 업로드된 전체 영상 자산 정보."""

    local: LocalPreprocessingResult
    video_url: str = Field(min_length=1)
    gemini_file_name: str = Field(min_length=1)
    upload_timestamp_utc: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class SegmentClip(BaseModel):
    cut_id: str
    local_clip_path: str = Field(min_length=1)
    clip_video_url: str = Field(min_length=1)
    clip_gemini_file_name: str = Field(min_length=1)
    clip_video_mime_type: str = Field(min_length=1)
    padded_start_time: float = Field(ge=0.0)
    padded_end_time: float = Field(ge=0.0)
    actual_padding_start: float = Field(ge=0.0)
    actual_padding_end: float = Field(ge=0.0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def check_order(self) -> "SegmentClip":
        if self.padded_end_time <= self.padded_start_time:
            raise ValueError("padded_end_time must be > padded_start_time")
        return self


class SkippedCut(BaseModel):
    cut_id: str
    reason: str = Field(min_length=1)
    error_detail: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class SegmentPrepResult(BaseModel):
    clips: List[SegmentClip]
    skipped_cuts: List[SkippedCut] = Field(default_factory=list)
    warnings: List["WarningItem"] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator("clips")
    def non_empty_clips(cls, value: List[SegmentClip]) -> List[SegmentClip]:
        if not value:
            raise ValueError("No clips were successfully prepared")
        return value


class StageStatus(BaseModel):
    stage: StageName
    stage_dir: str
    status: StageExecutionStatus
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None

    model_config = ConfigDict(extra="forbid")


class RunManifest(BaseModel):
    run_id: str
    video_path: str
    created_at_utc: str
    entry_stage: str | None = None
    stages: List[StageStatus]

    model_config = ConfigDict(extra="forbid")


class StageWarningsRecord(BaseModel):
    stage: StageName
    generated_at_utc: str
    warnings: List["WarningItem"]

    model_config = ConfigDict(extra="forbid")


class StageErrorRecord(BaseModel):
    stage: StageName
    failed_at_utc: str
    attempt_count: int = Field(ge=1)
    retryable: bool
    error_class: str
    message: str
    context: Dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class LoadedStageBundle(BaseModel, Generic[T]):
    stage: StageName
    status: StageExecutionStatus
    stage_dir: Path
    output_path: Path | None = None
    warnings_path: Path | None = None
    error_path: Path | None = None
    report_path: Path | None = None
    output: T | None = None
    warnings: List["WarningItem"] = Field(default_factory=list)
    error: StageErrorRecord | None = None

    model_config = ConfigDict(extra="forbid")


class AgentARequest(BaseModel):
    """Agent A 호출에 전달되는 최소 입력 계약."""

    video_url: str = Field(min_length=1)
    video_mime_type: str = Field(min_length=1)
    video_metadata: VideoMetadata
    cuts: List[Cut]

    model_config = ConfigDict(extra="forbid")

    @field_validator("cuts")
    def non_empty_cuts(cls, value: List[Cut]) -> List[Cut]:
        if not value:
            raise ValueError("AgentARequest must contain at least one cut")
        return value


class OnsetEvent(BaseModel):
    type: Literal["onset"]
    timestamp: float = Field(ge=0.0)

    model_config = ConfigDict(extra="forbid")


class ContinuousEvent(BaseModel):
    type: Literal["continuous"]
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def check_order(self) -> "ContinuousEvent":
        if self.end_time < self.start_time:
            raise ValueError("continuous event end_time must be >= start_time")
        return self


class Character(BaseModel):
    id: str
    label: str
    visual_description: str
    entry_exit_intervals: List[Interval]
    audibility: EntityAudibility

    model_config = ConfigDict(extra="forbid")


class KeyObject(BaseModel):
    id: str
    label: str
    visual_description: str
    material: str
    surface: str
    has_mechanism: bool
    audibility: EntityAudibility

    model_config = ConfigDict(extra="forbid")


class AmbienceSource(BaseModel):
    id: str
    label: str
    space_description: str
    distance_profile: DistanceProfile
    tonal_quality: str

    model_config = ConfigDict(extra="forbid")


class EntityRegistry(BaseModel):
    characters: List[Character] = Field(default_factory=list)
    key_objects: List[KeyObject] = Field(default_factory=list)
    ambience_sources: List[AmbienceSource] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class AgentAResponse(BaseModel):
    """Agent A 출력 계약: 전역 entity registry."""

    entity_registry: EntityRegistry

    model_config = ConfigDict(extra="forbid")


class UnknownResolution(BaseModel):
    suggestion: Literal["REASSIGN_TO_EXISTING", "UNRESOLVED"]
    suggested_entity_id: Optional[str] = None
    reason: str

    model_config = ConfigDict(extra="forbid")


class Action(BaseModel):
    action_id: str
    cut_id: str
    primary_source_id: str
    unknown_resolution: Optional[UnknownResolution] = None
    interaction_type: InteractionType
    sound_description: str
    surface_context: Optional[str]
    observed_visual_description: str
    event: Union[OnsetEvent, ContinuousEvent]
    boundary_flag: bool

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def ensure_unknown_resolution(self) -> "Action":
        if self.primary_source_id.startswith("UNKNOWN_") and self.unknown_resolution is None:
            raise ValueError("unknown_resolution must be provided when primary_source_id indicates UNKNOWN")
        return self


class Track(BaseModel):
    track_id: str
    track_type: TrackType
    source_entity_id: str
    interaction_type: InteractionType
    sound_description: str
    surface_context_summary: Optional[str]
    events: List[Union[OnsetEvent, ContinuousEvent]]

    model_config = ConfigDict(extra="forbid")

    @field_validator("events")
    def non_empty_events(cls, value: List[Union[OnsetEvent, ContinuousEvent]]) -> List[Union[OnsetEvent, ContinuousEvent]]:
        if not value:
            raise ValueError("Track must contain at least one event")
        return value


class TrackManifest(BaseModel):
    tracks: List[Track]

    model_config = ConfigDict(extra="forbid")


class UnresolvedUnknown(BaseModel):
    unknown_id: str
    cut_id: str
    observed_visual_description: str
    interaction_type: InteractionType
    sound_description: str

    model_config = ConfigDict(extra="forbid")


class WarningItem(BaseModel):
    code: str
    severity: WarningSeverity
    message: str
    context: Dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class PipelineResult(BaseModel):
    track_manifest: TrackManifest
    unresolved_unknowns: List[UnresolvedUnknown]
    warnings: List[WarningItem]

    model_config = ConfigDict(extra="forbid")
