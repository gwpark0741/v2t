# Pipeline Context: Video-to-Sound Metadata (v4)

> 코딩 에이전트용 구현 컨텍스트. 이전 버전(v3.3 + Addendum A)과 충돌 시 이 문서를 우선한다.

### ⚡ 이전 버전 대비 주요 변경사항
- **[NEW]** track_id 결정론적 생성 — merge 완료 후 최종 group 기준으로 생성
- **[NEW]** Timestamp grid(0.2s 스냅) 전면 제거 — Gemini 출력 타임스탬프 그대로 사용
- [변경] Reconciliation Gate 제거 → UNKNOWN 처리를 Agent B 내부로 이동
- [변경] L1 13개 → interaction_type 4개 (Ric Viers 기반)
- [변경] Background 엔티티 제거 → AmbienceSource.space_description 흡수
- [변경] Object → KeyObject (실제 사운드 발생 사물만)
- [변경] 시간 인접 조건 제거
- [변경] surface 호환 판단 → Flash 위임
- [변경] confidence 제거
- [변경] 트랙 description → 가장 긴 것 1개 선택

---

## Goal

- Input: 무음 영상 (10–60s)
- Output: `TrackManifest` JSON — T2A 또는 사운드 추천을 위한 트랙 단위 메타데이터
- 핵심 원칙: **track = sound source × interaction_type** — 같은 엔티티라도 interaction_type이 다르면 별도 트랙

---

## Architecture

```
Video Input (10-60s, 무음)
  │
  ▼
[Preprocessing] ── CODE
  ├─ ffprobe 메타데이터 추출
  ├─ AdaptiveDetector (pyscenedetect) → Cut[] 확정
  ├─ Cut(id, start_time, end_time) 생성
  └─ Gemini File API 업로드 → file_uri
  │
  ├─────────────────────────────────────┐ (병렬)
  ▼                                     ▼
[Agent A: Global Analyzer] ── LLM     [Segment Prep] ── CODE
  gemini-2.5-pro, 1 call                ├─ 컷별 ffmpeg 재인코딩
  ├─ 엔티티 레지스트리 생성               │   (-c:v libx264 -crf 18)
  │   Character / KeyObject /           ├─ ±1.0s 패딩
  │   AmbienceSource                    └─ Gemini File API 업로드
  └─ 컷 enrichment
      camera_angle, transition_type,
      camera_notes
  │                                     │
  └──────────────┬──────────────────────┘
                 ▼
[Validation Gate] ── CODE
  스키마 / 타임라인 / 링키지 검증
  오류 시 → Agent A retry
                 │
                 ▼
[Agent B: Per-Cut Analyst] ── LLM
  gemini-2.5-pro × N (max 5 concurrent)
  컷 세그먼트 1개 / 호출
  ├─ 레지스트리 엔티티 매칭 → primary_source_id
  ├─ 미매칭 시: 레지스트리 전체 재검토 후
  │   ├─ REASSIGN_TO_EXISTING: 기존 엔티티 ID로 교체
  │   └─ UNRESOLVED: UNKNOWN_* ID 유지
  ├─ interaction_type 분류 (4개)
  ├─ sound_description 자유 기술
  ├─ surface_context 기술
  └─ 이벤트 타임스탬프 (target range만, boundary_flag 포함)
     ※ 타임스탬프 그리드 스냅 없음 — Gemini 출력값 그대로 사용
                 │
                 ▼
[Agent C: Track Synthesizer] ── CODE + 조건부 LLM (gemini-2.5-flash)
  1. canonical_should_merge() 실행 → 최종 group 확정
  2. 각 group의 대표 surface_context_summary 선정
  3. surface_key 정규화
  4. track_id 생성 (merge 완료 후)
  UNRESOLVED 액션은 merge 제외, PipelineResult에 별도 수집
                 │
                 ▼
[Final Validation] ── CODE
  엔티티 커버리지 / 타임라인 / warnings
  ※ 그리드 검증 없음
  오류 시 → Agent C retry
                 │
                 ▼
PipelineResult (JSON)
  ├─ track_manifest
  ├─ unresolved_unknowns   ← 사용자 확인용
  └─ warnings
```

> **제거됨**: Reconciliation Gate, Background 엔티티, 0.2s 타임스탬프 그리드

---

## Entity Schema (Agent A 출력)

### Character
```python
class Character:
    id: str                    # char_001, char_002, ...
    label: str
    visual_description: str
    entry_exit_intervals: list[Interval]
    audibility: Literal["audible", "likely_audible", "visual_only", "inactive"]
```

### KeyObject
소리를 실제로 발생시키는 핵심 사물만 등록. 정적 소품 / 배경 장식 제외.
등록 기준: "이 오브젝트가 영상 내에서 실제로 소리를 발생시키는가?"
```python
class KeyObject:
    id: str                    # obj_001, obj_002, ...
    label: str
    visual_description: str
    material: str
    surface: str
    has_mechanism: bool
    audibility: Literal["audible", "likely_audible", "visual_only", "inactive"]
```

### AmbienceSource
특정 출처 없는 공간 환경음 레이어. Background 개념을 흡수.
```python
class AmbienceSource:
    id: str                    # amb_001, amb_002, ...
    label: str
    space_description: str     # 장소 정체성 포함 (기존 Background 역할 흡수)
    distance_profile: str      # near / mid / far
    tonal_quality: str
```

> **제거됨**: Background 엔티티. space_description이 장소 정체성을 흡수.

---

## Cut Enrichment (Agent A 출력, 컷별)

```python
class CutEnrichment:
    cut_id: str
    camera_angle: str
    transition_type: str
    camera_notes: str
```

Agent A는 Cut 경계를 수정하지 않는다. Preprocessing에서 확정된 값을 그대로 사용.

---

## Action Schema (Agent B 출력, 액션별)

```python
class UnknownResolution:
    suggestion: Literal[
        "REASSIGN_TO_EXISTING",  # 레지스트리 기존 엔티티와 동일 판단
        "UNRESOLVED"             # 판단 불가, UNKNOWN 유지
    ]
    suggested_entity_id: str | None  # REASSIGN 시 대상 레지스트리 ID
    reason: str                      # 판단 근거 (디버깅용)

class Action:
    action_id: str
    cut_id: str
    primary_source_id: str          # 레지스트리 ID 또는 UNKNOWN_*
    unknown_resolution: UnknownResolution | None
    # primary_source_id가 UNKNOWN_*인 경우에만 작성
    # REASSIGN이면 primary_source_id를 suggested_entity_id로 교체
    # UNRESOLVED이면 UNKNOWN_* 유지
    interaction_type: Literal[
        "hard_effect",              # 사물 간 물리 접촉, 충돌, 메커니즘 작동
        "foley",                    # 신체 접촉, 발소리, 의상, 생체 움직임
        "background",               # 공간 환경음, 특정 출처 없는 지속음
        "electronic"                # 전자 기기, 모터, 신호음
    ]
    sound_description: str          # 자유 기술 (T2A 프롬프트 재료)
    surface_context: str | None     # hard_effect / foley에서 기술, 나머지 null
    observed_visual_description: str  # 레지스트리와 독립적으로 관찰 기술
    event: OnsetEvent | ContinuousEvent
    boundary_flag: bool             # 컷 경계에 걸친 이벤트
```

### UNKNOWN 처리 규칙 (Agent B 내부)

```
미매칭 발생
  │
  ├─ 레지스트리 전체를 다시 보고 유사 엔티티 탐색
  │   기준: observed_visual_description ↔ visual_description 비교
  │
  ├─ 유사 엔티티 발견 + 확신 있음
  │   → suggestion: REASSIGN_TO_EXISTING
  │     suggested_entity_id: 해당 레지스트리 ID
  │
  └─ 유사 엔티티 없음 또는 확신 없음
      → suggestion: UNRESOLVED
        primary_source_id: UNKNOWN_{TYPE}_CUT{NNN}_{SEQ} 유지
```

보수적 원칙: 확신이 없으면 REASSIGN하지 않고 UNRESOLVED로 남깁니다.
Agent B는 레지스트리에 새 엔티티를 추가할 권한이 없습니다.

### interaction_type 기반 근거
Ric Viers, *The Sound Effects Bible* (2008)의 실무 분류 체계 기반.
Sound Design Effects는 무음 영상에서 자연 발생하지 않아 제외. 4개 채택.

| interaction_type | Ric Viers 대응 | surface strictness |
|---|---|---|
| hard_effect | Hard Effects | strict — 표면이 소리 결정 핵심 |
| foley | Foley | normal — 신체 부위 기준 부분 적용 |
| background | Background Effects | loose — 표면 무관 |
| electronic | Electronic Effects | loose — 표면 무관 |

---

## Track Merge Logic: canonical_should_merge()

**시간 인접 조건 없음.** 씬 전환 후 재등장한 같은 소스의 같은 interaction_type은 MERGE.
**UNRESOLVED 액션은 merge 판단에서 제외.** PipelineResult.unresolved_unknowns에 별도 수집.

```
1. primary_source_id 동일?           → no: SPLIT (코드)
2. interaction_type 동일?            → no: SPLIT (코드)
3. background / electronic?          → MERGE (코드)
4. hard_effect / foley?              → Flash에 surface 호환 판단 위임
   Flash 응답: COMPATIBLE → MERGE / INCOMPATIBLE → SPLIT
   판단 불확실 → INCOMPATIBLE (보수적)
```

### Flash 호출 포맷 (surface 호환 판단)

```
입력:
  surface_context_a: "wet cobblestone with small puddles"
  surface_context_b: "damp stone floor, slightly wet"
  interaction_type: "hard_effect"

지시:
  두 surface_context가 음향적으로 동일한 표면을 가리키는지 판단하라.
  표현이 달라도 같은 물리적 표면이면 COMPATIBLE.
  재질이나 상태가 달라 다른 소리가 날 것이면 INCOMPATIBLE.
  확실하지 않으면 INCOMPATIBLE.

기대 응답:
  { "result": "COMPATIBLE" | "INCOMPATIBLE", "reason": "..." }
```

---

## Track Schema (Agent C 출력)

### track_id 생성 규칙

**순번(trk_001) 방식 사용 금지.** 재실행/병합 시 안정성이 없습니다.

track_id는 merge가 완전히 끝난 뒤, 최종 group의 대표값을 기준으로 생성합니다.

```python
# 생성 순서
# 1. canonical_should_merge()로 최종 group 확정
# 2. group의 대표 surface_context_summary 선정 (가장 긴 것)
# 3. surface_key 정규화: 소문자, 공백→언더스코어, 특수문자 제거
# 4. track_id 조합

if interaction_type in {"background", "electronic"}:
    track_id = f"{source_entity_id}__{interaction_type}"
else:  # hard_effect, foley
    surface_key = normalize(surface_context_summary)
    track_id = f"{source_entity_id}__{interaction_type}__{surface_key}"
```

예시:
```
char_001__foley__tile_floor
char_001__foley__grass          ← 같은 char_001이지만 surface가 달라 별도 트랙
char_001__hard_effect__wood_table
obj_002__hard_effect__metal_rail
amb_001__background
obj_010__electronic
```

왜 raw surface_context를 그대로 쓰면 안 되는가:
- "wet cobblestone with puddles"와 "damp stone floor, slightly wet"은
  Flash 판단상 COMPATIBLE → 같은 group으로 MERGE됨
- MERGE된 group은 대표 surface_context_summary 하나를 가짐
- 그 대표값을 normalize한 surface_key를 사용하므로 문자열 불일치 문제 없음

```python
def normalize_surface_key(surface: str) -> str:
    s = surface.lower().strip()
    s = re.sub(r'[^a-z0-9\s]', '', s)
    s = re.sub(r'\s+', '_', s)
    return s[:50]  # 길이 제한
```

```python
class Track:
    track_id: str                   # 결정론적 ID (위 규칙으로 생성)
    track_type: Literal["sfx", "ambience"]
    # track_type 판단: AmbienceSource를 가리키면 ambience, 나머지 sfx
    source_entity_id: str
    interaction_type: str
    sound_description: str          # 대표 description (가장 긴 것 선택)
    surface_context_summary: str | None
    events: list[OnsetEvent | ContinuousEvent]
```

### sound_description 선택 규칙

같은 트랙으로 MERGE된 액션들은 surface 호환 검사를 통과한 것으로, 본질적으로 같은 소리입니다.

**Temporal consistency를 위해 트랙 전체에 단일 description을 사용합니다.**

```python
track.sound_description = max(
    merged_actions,
    key=lambda a: len(a.sound_description)
).sound_description

track.surface_context_summary = max(
    (a for a in merged_actions if a.surface_context),
    key=lambda a: len(a.surface_context),
    default=None
)?.surface_context
```

---

## PipelineResult Schema

```python
class UnresolvedUnknown:
    unknown_id: str                  # UNKNOWN_OBJECT_CUT003_1
    cut_id: str
    observed_visual_description: str
    interaction_type: str
    sound_description: str
    # 사용자가 확인 후 수동 판단 또는 추가 gate 도입 근거로 사용

class PipelineResult:
    track_manifest: TrackManifest
    unresolved_unknowns: list[UnresolvedUnknown]
    warnings: list[WarningItem]
```

---

## Final Output: T2A Text Description

```
{sound_description}. Surface: {surface_context_summary}. {event_description}. Duration: {computed}s
```

예시:
```
"Leather shoe footsteps on ceramic tile floor, steady walking pace.
Surface: hard leather sole on smooth ceramic tile.
7 footstep impacts across two scenes. Duration: 5.8s"
```

- sfx 트랙 → 개별 사운드 이벤트 생성
- ambience 트랙 → loopable background bed 생성

---

## LLM vs CODE 분류

| 단계 | 실행 | 모델 | LLM 호출 조건 |
|---|---|---|---|
| Preprocessing | CODE | — | never |
| Agent A | LLM | gemini-2.5-pro | always (1 call) |
| Validation Gate | CODE | — | never |
| Segment Prep | CODE | — | never |
| Agent B | LLM | gemini-2.5-pro | always (N calls, max 5 concurrent) |
| Agent C | CODE + Flash | gemini-2.5-flash | hard_effect/foley surface 호환 판단마다 |
| Final Validation | CODE | — | never |

---

## Cut Detection Config

```json
{
  "detector": "AdaptiveDetector",
  "params": {
    "adaptive_threshold": 4,
    "min_scene_len": 30,
    "min_content_val": 15.0
  }
}
```

- adaptive_threshold 4: 기본값 3.0보다 보수적, 과분할 방지
- min_scene_len 30: 24fps 기준 ~1.25s, 마이크로컷 방지
- min_content_val 15.0: 정적/블랙 프레임 무시
- 한계: 2s 이상 slow dissolve 미감지 → threshold 3.5로 조정 검토

---

## Audibility Rules

| 상태 | 규칙 |
|---|---|
| audible | 트랙에 반드시 포함. excluded 불가. |
| likely_audible | 트랙 포함 OR excluded (사유 필수) 중 하나 선택. |
| visual_only / inactive | excluded 필수. 트랙 포함 불가. |

---

## Warning Schema

```python
class WarningItem:
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    context: dict
```

---

## Implementation Phases

### Phase 0: Infrastructure (1–2일)
- Pydantic v2 스키마 (전체 모델)
- Gemini API wrapper (upload, generate with schema)
- ffmpeg / pyscenedetect 유틸리티
- AdaptiveDetector config 검증

### Phase 1: Agent A (2–3일)
- 가설: 엔티티 추출 + 컷 enrichment 1 call로 가능
- 성공 기준: 엔티티 recall >80%, KeyObject 과등록 없음
- 실패 시: 엔티티 추출 / 컷 enrichment 2 call로 분리

### Phase 2: Agent B + Segments (3–4일)
- 가설: 세그먼트 기반 팬아웃으로 일관된 분석 가능
- 성공 기준: 엔티티 매칭 >85%, UNRESOLVED <15%, interaction_type 일관성
- 실패 시: 패딩 증가, interaction_type closed list 강제
- 모니터링: unresolved_unknowns 빈도 — 높으면 Reconciliation Gate 추가 검토

### Phase 3: Agent C (2–3일)
- 가설: interaction_type 기반 트랙 분리가 의미있는 구분 생성
- 성공 기준: 엔티티당 멀티 트랙 >50%, 과분할 없음 (트랙 30개 이상 방지)
- 실패 시: surface strictness 기준 재조정

### Phase 4: Integration (2–3일)
- asyncio 오케스트레이션 (병렬 세그먼트 준비 포함)
- 에러 핸들링, retry, 비용 모니터링
- 영상 10개 end-to-end 테스트

---

## Open Tuning Parameters

| 파라미터 | 초기값 | 결정 시점 |
|---|---|---|
| max_concurrency | 5 | Phase 2 |
| padding_size | ±1.0s | Phase 2 |
| AdaptiveDetector adaptive_threshold | 4.0 | Phase 1 |
| surface strictness 세부 기준 | hard_effect=strict, foley=normal | Phase 3 |
| normalize_surface_key 길이 제한 | 50자 | Phase 3 |

---

## Key Design Decisions

| 결정 | 채택 | 기각 | 근거 |
|---|---|---|---|
| 엔티티 분류 | Character / KeyObject / AmbienceSource | + Background | Background를 AmbienceSource.space_description이 흡수 |
| Object 범위 | KeyObject (실제 사운드 발생 사물만) | 모든 오브젝트 | 불필요한 엔티티 제거, 레지스트리 경량화 |
| 사운드 분류 | interaction_type 4개 (Ric Viers 기반) | L1 13개 | 논문 근거 확보, 과세분화 방지 |
| 시간 인접 조건 | 제거 | gap -0.2~0.4s | 씬 전환 후 재등장 요소가 같은 트랙이어야 함 |
| 트랙 동일성 기준 | source_id + interaction_type + surface | L1+L2+surface+시간 | 단순화, 목적과 일치 |
| track_id 생성 | merge 후 결정론적 생성 | 순번(trk_001) | 재실행/병합 시 순번은 불안정 |
| track_id 포맷 | background/electronic: source+type, hard_effect/foley: source+type+surface_key | source+type만 | hard_effect/foley는 surface가 트랙 동일성에 포함됨 |
| surface_key 기준 | 대표 surface_context_summary normalize | raw surface_context 직접 사용 | raw 문자열은 표현 차이로 동일 surface도 다른 key가 됨 |
| 컷 감지 | pyscenedetect (결정론적) | Gemini (시맨틱) | 안정성, 재현성, 세그먼트 병렬 준비 가능 |
| 세그먼트 전달 | 재인코딩 클립 | 전체 영상+시간 지시 | 프레임 정확도, 87% 비용 절감 |
| UNKNOWN 처리 위치 | Agent B Pro 호출 내부 | 별도 Reconciliation Gate | 추가 단계/비용 제거 |
| UNKNOWN 결과 | REASSIGN / UNRESOLVED 2가지 | CREATE_NEW 포함 | Agent B는 레지스트리 추가 권한 없음 |
| UNRESOLVED 보존 | PipelineResult에 별도 수집 | 트랙 포함 / 드랍 | 사용자 확인 후 gate 추가 여부 결정 |
| surface 호환 판단 | Flash 위임 | 코드 텍스트 비교 | 자유 텍스트는 코드 비교 불가 |
| 트랙 description 선택 | 가장 긴 description 1개 선택 | LLM 통합 / 전부 보존 | Temporal consistency 보장, 추가 비용 없음 |
| confidence | 제거 | 계산 후 보존 | 프로토타입 단계에서 실제 사용처 없음 |
| 타임스탬프 그리드 | 제거 | 0.2s 스냅 강제 | merge 판단에 타임스탬프 미사용, T2A에 영향 없음 |

---

## Cost Estimate

60s 영상, ~15 컷 기준: **~$0.44** (목표 $1 이하)

| 단계 | 호출 수 | 모델 | 비용 |
|---|---|---|---|
| Agent A | 1 | Pro | ~$0.15 |
| Agent B | 15 | Pro | ~$0.25 |
| Agent C (surface 판단) | hard_effect/foley 트랙 수만큼 | Flash | ~$0.04 |
