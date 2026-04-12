# Gemini 비디오 사운드 디자인 파이프라인 — v3.3 패치 (5차 리뷰 반영, 최종)

> **문서 목적**: v3.2에 대한 5차 리뷰(3개 핵심 + 1개 열린 질문)를 반영한 최종 패치.
> **이 패치 이후 구현에 착수한다. 추가 설계 리뷰는 Phase별 테스트 결과에 기반해서만 수행한다.**
>
> **변경 이력**:
> v1 → v2 → v3 → v3.1 → v3.2 → **v3.3 (본 문서)**
>
> **적용 순서**: v3 → v3.1 → v3.2 → v3.3. 충돌 시 최신 패치 우선.

---

## 0. v3.3 변경 요약

| # | 5차 리뷰 이슈 | 판단 | 변경 내용 |
|---|---------------|------|-----------|
| 1 | canonical_should_merge()가 인자 순서에 의존 | **함수 내부에서 시간순 정렬** | early normalize + assertion |
| 2 | surface_compatible() 예시와 구현 불일치 | **head-material extraction으로 정규화 강화** | extract_core_surface() 재작성 + 테스트 케이스 1:1 매핑 |
| 3 | segment drift warning이 결과에 미전파 | **Segment 모델에 warnings 필드 추가, pipeline-level 집계** | Segment.warnings + PipelineResult.segment_warnings |
| 4 | boundary 예외에서 state change merge 여부 | **action_label이 다르면 REVIEW로 위임** | boundary 병합 조건에 label 검사 추가 |

---

## 1. canonical_should_merge() 시간 순서 안정성 (이슈 1)

### 문제

`abs(event_a.end_time - event_b.start_time) <= 0.4`는 event_a가 시간적으로 먼저일 때만 의미 있다. 호출부가 역순으로 넣으면 `event_a.end_time`이 더 크고, 실제로는 인접하지 않은 이벤트가 인접으로 판정될 수 있다.

### 판단: 함수 내부에서 정규화

호출부에 정렬을 강제하는 것보다, 함수 자체가 순서에 무관하게 동작하는 것이 안전하다.

```python
def canonical_should_merge(event_a: Action, event_b: Action) -> MergeDecision:
    """Agent C의 유일한 병합 판단 함수.
    
    입력 순서와 무관하게 동작한다.
    내부에서 시간순으로 정규화한 뒤 판단한다.
    """
    
    # ─── 시간순 정규화 ───
    # continuous의 대표 시각: start_time, onset의 대표 시각: timestamp
    def sort_key(e: Action) -> float:
        return e.start_time if e.event_type == "continuous" else e.timestamp
    
    first, second = sorted([event_a, event_b], key=sort_key)
    
    # assertion: 정렬 후 first가 실제로 먼저인지 확인
    assert sort_key(first) <= sort_key(second), (
        f"Time normalization failed: {sort_key(first)} > {sort_key(second)}"
    )
    
    # ─── 이하 모든 판단은 first, second 기준으로 수행 ───
    
    same_l1 = first.sound_source_l1 == second.sound_source_l1
    if not same_l1:
        return MergeDecision.SPLIT
    
    same_entity = (
        first.linked_character_id == second.linked_character_id and
        first.linked_object_id == second.linked_object_id
    )
    if not same_entity:
        return MergeDecision.SPLIT
    
    both_continuous = (
        first.event_type == "continuous" and
        second.event_type == "continuous"
    )
    if not both_continuous:
        return MergeDecision.SPLIT
    
    # 시간 인접성: first의 end → second의 start 간격
    gap = second.start_time - first.end_time
    time_adjacent = -0.2 <= gap <= 0.4
    # gap < 0 (약간 겹침)도 -0.2까지는 허용: 0.2s grid snap으로 인한 미세 중복
    
    if not time_adjacent:
        return MergeDecision.SPLIT
    
    # Surface 호환성 (source-aware strictness)
    strictness = get_surface_strictness(first.sound_source_l1)
    surfaces_ok = surface_compatible(
        first.surface_context,
        second.surface_context,
        strictness=strictness,
    )
    if not surfaces_ok:
        return MergeDecision.SPLIT
    
    # L2 + Label 판단
    same_l2 = first.sound_source_l2 == second.sound_source_l2
    same_label = first.action_label == second.action_label
    has_boundary = first.boundary_flag or second.boundary_flag
    
    # Case 1: L2도 같고 action_label도 같음 → 확실한 병합
    if same_l2 and same_label:
        return MergeDecision.MERGE
    
    # Case 2: Boundary 상황
    if has_boundary and is_boundary_l2_exception_allowed(first.sound_source_l1):
        if same_label:
            # 같은 행위의 미세 변주 → 병합
            return MergeDecision.MERGE
        else:
            # 행위가 다름 (walking→running 등) → LLM에게 위임
            return MergeDecision.REVIEW
    
    # Case 3: track_hint soft signal
    if first.track_hint == second.track_hint:
        return MergeDecision.REVIEW
    
    return MergeDecision.SPLIT
```

### 시간 인접성 판단 변경 사항

v3.2: `abs(event_a.end_time - event_b.start_time) <= 0.4`
v3.3: `second.start_time - first.end_time`가 `-0.2 ~ 0.4` 범위

변경 이유:
- 0.2s grid snap으로 인해 인접 cut의 continuous event가 0.2s까지 겹칠 수 있다 (clamp 부정확)
- 음수 gap도 -0.2까지 허용하여 snap으로 인한 미세 중복을 수용

---

## 2. Boundary State Change 정책 (이슈 4)

### 문제

v3.2의 boundary 예외는 continuous-nature L1이면 L2 mismatch를 허용했다.
그런데 `walking → running`, `diesel_idle → diesel_accel` 같은 **상태 변화**도 병합될 수 있다.

### 판단: action_label로 분기

상태 변화를 구분하는 가장 직접적인 시그널은 `action_label`이다.

| action_label 일치? | L2 일치? | Boundary? | 판단 | 예시 |
|-------|-------|-----------|------|------|
| Yes | Yes | - | MERGE | walking / walking |
| Yes | No | Yes | MERGE | footstep의 heel_strike vs toe_push (같은 walking) |
| **No** | No | Yes | **REVIEW** | walking → running (상태 변화) |
| No | - | No | SPLIT | walking vs door_open |

**근거**: `action_label`이 같다면 같은 행위의 미세 변주(L2 차이)이므로 boundary 병합이 안전하다. `action_label`이 다르면 상태 변화일 가능성이 있으므로 Agent C LLM에게 판단을 위임한다. LLM은 sfx_description을 비교하여 "소리가 연속적으로 이어지는가"를 판단한다.

이 규칙은 §1의 canonical_should_merge() Case 2에 이미 반영되어 있다.

---

## 3. surface_compatible() 정규화 강화 (이슈 2)

### 문제

v3.1의 `extract_core_surface()`는 수식어(wet, dry, rough 등)만 제거했다. 그러나 실제 입력은 "glass window", "dry metal hinge", "rusted metal hinge"처럼 **object noun이 섞여 있다**. `extract_core_surface("glass window")`가 `"glass window"`을 반환하면, `"glass bottle"`과 core 불일치로 strict에서 SPLIT된다 — 문서 예시와 모순.

### 판단: head-material extraction으로 재작성

```python
def extract_core_surface(surface: str) -> str:
    """surface_context에서 핵심 재질만 추출한다.
    
    전략:
    1. 수식어(wet, dry, rough 등) 제거
    2. object noun(window, bottle, hinge 등) 제거
    3. 남은 것이 core material
    4. 남는 것이 없으면 원문 반환
    """
    
    MODIFIERS = {
        "wet", "dry", "rough", "smooth", "polished", "rusted", "rusty",
        "corrugated", "painted", "worn", "cracked", "dirty", "clean",
        "thick", "thin", "heavy", "light", "old", "new", "dented",
        "oxidized", "frosted", "scratched", "chipped",
    }
    
    OBJECT_NOUNS = {
        "window", "bottle", "door", "hinge", "handle", "panel", "plate",
        "surface", "floor", "wall", "ceiling", "pipe", "rail", "bar",
        "frame", "sheet", "slab", "block", "can", "lid", "knob",
        "table", "chair", "shelf", "railing", "grate", "fence",
        "cylinder", "container", "bucket", "bowl", "cup",
    }
    
    words = surface.lower().replace(",", " ").replace("-", " ").split()
    
    # 수식어와 object noun 제거
    core_words = [w for w in words if w not in MODIFIERS and w not in OBJECT_NOUNS]
    
    if not core_words:
        # 모든 단어가 수식어/noun이면 원문에서 마지막 단어 (최후 fallback)
        return words[-1] if words else surface
    
    return " ".join(core_words)
```

### 예시 테이블과 함수 결과 1:1 매핑

| 입력 | extract_core_surface() | categorize_surface() |
|------|----------------------|---------------------|
| `"wet cobblestone"` | `"cobblestone"` | stone |
| `"dry cobblestone"` | `"cobblestone"` | stone |
| `"cobblestone"` | `"cobblestone"` | stone |
| `"tile"` | `"tile"` | stone |
| `"glass window"` | `"glass"` | glass |
| `"glass bottle"` | `"glass"` | glass |
| `"dry metal hinge"` | `"metal"` | metal |
| `"rusted metal hinge"` | `"metal"` | metal |
| `"thick carpet"` | `"carpet"` | soft |
| `"shallow water"` | `"shallow water"` | water |

### 검증: 문서 예시와 실제 결과 대조

| L1 | Surface A | Surface B | Strictness | core A | core B | 호환? | 예시 통과? |
|----|-----------|-----------|------------|--------|--------|-------|-----------|
| footstep | wet cobblestone | dry cobblestone | strict | cobblestone | cobblestone | **Yes** | ✓ |
| footstep | cobblestone | tile | strict | cobblestone | tile | **No** | ✓ |
| mechanism | dry metal hinge | rusted metal hinge | normal | metal | metal | **Yes** | ✓ |
| motor | steel surface | glass panel | loose | steel | glass | **Yes** | ✓ (loose) |
| impact | glass window | glass bottle | strict | glass | glass | **Yes** | ✓ |
| rolling | cobblestone | carpet | strict | cobblestone | carpet | **No** | ✓ |

모든 문서 예시가 구현과 일치한다.

### 구현 요청

위 테이블을 `tests/test_surface_compatible.py`의 parametrized test case로 변환하라:

```python
import pytest
from src.utils.matching import extract_core_surface, surface_compatible, SurfaceStrictness

@pytest.mark.parametrize("surface_a,surface_b,strictness,expected", [
    ("wet cobblestone", "dry cobblestone", SurfaceStrictness.STRICT, True),
    ("cobblestone", "tile", SurfaceStrictness.STRICT, False),
    ("dry metal hinge", "rusted metal hinge", SurfaceStrictness.NORMAL, True),
    ("steel surface", "glass panel", SurfaceStrictness.LOOSE, True),
    ("glass window", "glass bottle", SurfaceStrictness.STRICT, True),
    ("cobblestone", "carpet", SurfaceStrictness.STRICT, False),
])
def test_surface_compatible(surface_a, surface_b, strictness, expected):
    assert surface_compatible(surface_a, surface_b, strictness=strictness) == expected

@pytest.mark.parametrize("input_surface,expected_core", [
    ("wet cobblestone", "cobblestone"),
    ("dry cobblestone", "cobblestone"),
    ("glass window", "glass"),
    ("glass bottle", "glass"),
    ("dry metal hinge", "metal"),
    ("rusted metal hinge", "metal"),
    ("thick carpet", "carpet"),
])
def test_extract_core_surface(input_surface, expected_core):
    assert extract_core_surface(input_surface) == expected_core
```

---

## 4. Segment Drift Warning 전파 경로 (이슈 3)

### 문제

v3.2에서 `timing_warning = drift > 0.3`은 로컬 변수로만 존재하고, Segment 모델이나 pipeline 결과에 전달되지 않는다.

### 판단: Segment.warnings + pipeline 집계

```python
class Segment(BaseModel):
    cut_id: str
    file_path: str
    file_uri: str | None = None
    actual_seg_start: float
    actual_seg_end: float
    target_start: float
    target_end: float
    padding_before: float
    padding_after: float
    timing_drift: float = 0.0
    warnings: list[str] = []          # 신규


class PipelineResult(BaseModel):
    status: Literal["complete", "partial", "failed"]
    tracks: list[Track]
    excluded_entities: list[ExcludedEntity]
    unresolved_cuts: list[str]
    unresolved_entities: list[str]
    warnings: list[str]               # pipeline-level
    segment_warnings: list[dict] = []  # 신규: segment별 warning 집계
    cost_summary: CostSummary
```

### Segment 생성 코드 개정

```python
# segment 생성 후 warning 판단
warnings = []

if drift > 0.5:
    raise SegmentPrepError(
        cut_id=cut.id,
        message=f"duration drift {drift:.3f}s exceeds 0.5s threshold"
    )

if drift > 0.3:
    warnings.append(f"timing_drift={drift:.3f}s exceeds 0.3s soft threshold")

segments.append(Segment(
    cut_id=cut.id,
    # ... (기존 필드)
    timing_drift=drift,
    warnings=warnings,
))
```

### Pipeline 집계

```python
# pipeline.py에서 segment warning 수집
segment_warnings = []
for seg in segments:
    if seg.warnings:
        segment_warnings.append({
            "cut_id": seg.cut_id,
            "warnings": seg.warnings,
        })

# PipelineResult에 포함
result = PipelineResult(
    # ...
    segment_warnings=segment_warnings,
    warnings=pipeline_warnings + (
        [f"{len(segment_warnings)} segments had timing warnings"]
        if segment_warnings else []
    ),
)
```

---

## 5. v3.2 → v3.3 차분 요약

| v3.2 섹션 | 변경 유형 | v3.3 내용 |
|----------|----------|----------|
| §1 canonical_should_merge() | **교체** | 본 문서 §1 (시간 정규화 + gap 범위 수정 + boundary state change) |
| §2 boundary L2 예외 | **보강** | 본 문서 §2 (action_label로 state change 분기) |
| §3 surface_compatible() / extract_core_surface() | **교체** | 본 문서 §3 (head-material extraction + object noun 제거) |
| v3.1 §3 Segment 모델 | **확장** | 본 문서 §4 (warnings 필드 추가) |
| v3 §7 PipelineResult | **확장** | 본 문서 §4 (segment_warnings 필드 추가) |

---

## 6. 전체 문서 최종 구성

```
구현 담당자에게 전달할 문서 세트 (4개):

1. pipeline_v3_architecture.md       ← 기본 구조, 스키마, Agent 설계, MVP 계획
2. pipeline_v3_1_patch.md            ← likely_audible 확정, observed_*, re-encode, L1/L2
3. pipeline_v3_2_patch.md            ← canonical merge, source-aware surface, boundary 한정
4. pipeline_v3_3_patch.md (본 문서)   ← 시간 안정성, surface 정규화, warning 전파, state change
```

---

## 7. 최종 확정 체크리스트 (v3.2 §8 업데이트)

v3.2의 체크리스트에서 변경/추가된 항목만 표시:

### 병합 규칙 (업데이트)

- [x] canonical_should_merge()가 **입력 순서 무관**하게 동작 (내부 시간순 정규화)
- [x] 시간 인접성: `second.start - first.end`가 **-0.2 ~ 0.4** 범위 (snap 중복 허용)
- [x] Boundary state change: same_label → MERGE, **different_label → REVIEW**
- [x] track_hint: soft signal (REVIEW 트리거), hard condition 아님

### Surface (업데이트)

- [x] extract_core_surface(): **수식어 + object noun 제거**, head material만 추출
- [x] 문서 예시 6개와 함수 결과가 **1:1 일치** (테스트 케이스로 보장)

### Segment (업데이트)

- [x] Segment.warnings 필드 추가
- [x] PipelineResult.segment_warnings 집계
- [x] drift > 0.3s → warning 기록, > 0.5s → 재생성

### 미확정 (Phase별 튜닝) — 변경 없음

v3.2 §8의 미확정 항목 그대로 유지.

---

## 8. 검토 요청 사항 (구현 담당자용)

v3 §13 + v3.1 §8 + v3.2 §9의 기존 요청에 추가:

15. **canonical_should_merge() 통합 테스트**: 본 문서 §1의 함수를 `src/gates/merge_rules.py`에 구현하고, 순서 뒤바뀐 입력에 대해 동일 결과를 반환하는지 테스트
16. **extract_core_surface() + surface_compatible() 테스트**: 본 문서 §3의 parametrized test를 `tests/test_surface_compatible.py`에 구현
17. **Segment warnings 전파**: `src/preprocessing/segment.py`와 `src/pipeline.py`에 warning 흐름 구현
18. **REVIEW 처리 프롬프트**: boundary state change(walking→running)가 REVIEW로 넘어올 때 Agent C에게 제시할 프롬프트 템플릿 작성

---

## Addendum A. 구현 보강 사항 (6차 리뷰 반영)

> 아래 3개 항목은 blocking issue가 아니며 설계를 뒤집지 않는다.
> 구현 시 함께 반영한다.

### A-1. primary_source_id 도입 — same_entity 병합 기준 보강

**문제**: cut마다 actor/source link가 달라질 수 있다. door hinge sound가 한 cut에서 `CHAR_001 + OBJ_001`, 다른 cut에서 `OBJ_001`만 연결되면 exact match 실패로 과분리된다.

**변경**: Action 스키마에 `primary_source_id` 추가.

```python
class Action(BaseModel):
    # ... 기존 필드 ...
    linked_character_id: str | None
    linked_object_id: str | None
    primary_source_id: str              # 신규: 이 소리의 1차 발생원
    # 규칙:
    #   object-driven sound (door, vehicle 등) → primary_source_id = linked_object_id
    #   character-driven sound (footstep, breath 등) → primary_source_id = linked_character_id
    #   interaction (캐릭터가 문을 여는 소리) → primary_source_id = linked_object_id
    #     (소리의 물리적 발생원이 object이므로)
```

**canonical_should_merge() 변경**: `same_entity` 조건을 교체.

```python
# v3.3 (기존):
same_entity = (
    first.linked_character_id == second.linked_character_id and
    first.linked_object_id == second.linked_object_id
)

# v3.3 + Addendum A (개정):
same_primary_source = first.primary_source_id == second.primary_source_id
if not same_primary_source:
    return MergeDecision.SPLIT
```

**Agent B 프롬프트 추가**:

```
각 action에 대해 primary_source_id를 지정하세요.
이것은 "소리가 물리적으로 발생하는 주체"입니다.
- 문이 삐걱거리면 → primary_source_id = OBJ_001 (문)
- 인물이 걸으면 → primary_source_id = CHAR_001 (인물)
- 인물이 문을 열면 → primary_source_id = OBJ_001 (소리의 물리적 원인은 문)
linked_character_id, linked_object_id는 관련된 모든 entity를 기록하되,
primary_source_id는 반드시 하나만 선택하세요.
```

### A-2. Generic surface alias table + unknown fallback

**문제**: LLM이 `hard floor`, `stone ground`, `metal part`처럼 taxonomy에 없는 표현을 생성하면 surface categorization이 실패한다.

**변경**: 2단계 fallback 추가.

```python
# 1단계: alias table (generic 표현 → 정규화된 core)
SURFACE_ALIASES = {
    "hard floor": "hardwood",
    "stone ground": "stone",
    "metal part": "metal",
    "metal surface": "metal",
    "wet surface": "wet stone",    # 기본 가정: 젖은 돌
    "hard surface": "concrete",    # 기본 가정: 콘크리트
    "soft surface": "carpet",      # 기본 가정: 카펫
    "wooden floor": "hardwood",
    "wooden surface": "hardwood",
    "glass surface": "glass",
    "rough ground": "concrete",
    "smooth floor": "tile",
}


def extract_core_surface(surface: str) -> str:
    """v3.3 §3의 head-material extraction + alias fallback."""

    normalized = surface.lower().strip()

    # 0단계: alias 직접 매칭
    if normalized in SURFACE_ALIASES:
        return SURFACE_ALIASES[normalized]

    # 1단계: 수식어 + object noun 제거 (v3.3 기존 로직)
    words = normalized.replace(",", " ").replace("-", " ").split()
    core_words = [w for w in words if w not in MODIFIERS and w not in OBJECT_NOUNS]

    if not core_words:
        return words[-1] if words else surface

    core = " ".join(core_words)

    # 2단계: core가 카테고리에 매핑되지 않으면 alias에서 부분 매칭 시도
    if categorize_surface(core) is None:
        for alias_key, alias_val in SURFACE_ALIASES.items():
            if alias_key in normalized or normalized in alias_key:
                return alias_val

    return core


def categorize_surface(core: str) -> str | None:
    """v3.1 §4의 카테고리 매칭 + unknown fallback."""

    core_lower = core.lower()
    for category, members in SURFACE_CATEGORIES.items():
        if any(m in core_lower for m in members):
            return category

    # unknown fallback: normal strictness 적용
    return None  # 호출부에서 None이면 normal로 처리
```

**surface_compatible() unknown 처리**:

```python
def surface_compatible(surface_a, surface_b, strictness):
    # ... 기존 로직 ...

    cat_a = categorize_surface(core_a)
    cat_b = categorize_surface(core_b)

    # 둘 다 unknown이면: 보수적으로 비호환 (strict), 호환 (normal/loose)
    if cat_a is None and cat_b is None:
        return strictness != SurfaceStrictness.STRICT

    # 한쪽만 unknown이면: 보수적으로 비호환 (strict), 호환 (loose), review급 (normal)
    if cat_a is None or cat_b is None:
        return strictness == SurfaceStrictness.LOOSE

    return cat_a == cat_b
```

**SURFACE_ALIASES는 Phase 2/3에서 확장한다.** 실제 Agent B 출력을 수집하여 자주 등장하는 generic 표현을 alias에 추가.

### A-3. Typed Warning Schema

**변경**: `list[str]` → `list[WarningItem]`.

```python
class WarningItem(BaseModel):
    code: str                    # e.g. "timing_drift", "low_confidence", "excessive_tracks"
    severity: Literal["info", "warning", "error"]
    message: str
    context: dict = {}           # 추가 정보 (cut_id, entity_id, drift값 등)


class Segment(BaseModel):
    # ... 기존 필드 ...
    warnings: list[WarningItem] = Field(default_factory=list)


class PipelineResult(BaseModel):
    status: Literal["complete", "partial", "failed"]
    tracks: list[Track]
    excluded_entities: list[ExcludedEntity]
    unresolved_cuts: list[str]
    unresolved_entities: list[str]
    warnings: list[WarningItem] = Field(default_factory=list)
    cost_summary: CostSummary
```

**사용 예**:

```python
# segment warning 생성
if drift > 0.3:
    seg.warnings.append(WarningItem(
        code="timing_drift",
        severity="warning",
        message=f"duration drift {drift:.3f}s exceeds 0.3s soft threshold",
        context={"cut_id": cut.id, "drift": round(drift, 3)},
    ))

# final validation warning 생성
if low_confidence_ratio > 0.2:
    result.warnings.append(WarningItem(
        code="low_overall_confidence",
        severity="warning",
        message=f"{low_confidence_ratio:.0%} of events have confidence < 0.5",
        context={"ratio": round(low_confidence_ratio, 2)},
    ))
```

---

**이상으로 설계 리뷰 사이클을 종료한다. 구현에 착수하라.**
