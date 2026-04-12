# Gemini 비디오 사운드 디자인 파이프라인 — v3.2 패치 (4차 리뷰 반영, 최종)

> **문서 목적**: v3.1에 대한 4차 리뷰(4개 핵심 + 1개 정합성 이슈)를 반영한 최종 패치.
> 이 패치 이후 구현 착수한다.
>
> **변경 이력**:
> v1 → v2 → v3 → v3.1 → **v3.2 (본 문서)**: 규칙 통합 + 정합성 확정
>
> **문서 구조**: v3 원본 + v3.1 패치 + 본 v3.2 패치로 구성.
> 아래에서 변경되지 않는 섹션은 이전 버전을 그대로 유지한다.

---

## 0. v3.2 변경 요약

| # | 4차 리뷰 이슈 | 판단 | 변경 내용 |
|---|---------------|------|-----------|
| 1 | Agent C 병합 규칙 이중 정의 | **L1/L2 기준으로 단일 canonical rule 작성** | §4(surface)와 §5(L1/L2)를 하나의 `canonical_should_merge()`로 통합 |
| 2 | actual_seg_start "검증"이 사실상 가정 | **re-encode 신뢰 가정으로 문서화 + duration 검증만 유지** | 코드와 문구를 일치시킴 |
| 3 | surface_compatible()가 일부 sound type에 느슨 | **source-aware strictness tier 도입** | L1에 따라 strict/normal/loose 3단계 적용 |
| 4 | Boundary L2 예외가 과도 | **허용 L1 카테고리를 한정** | continuous-nature L1만 boundary L2 mismatch 허용 |
| 5 | 변경 요약표 likely_audible 문구 불일치 | **수정** | "track 또는 excluded 필수, 미충족 시 failure" |

---

## 1. Agent C Canonical Merge Rule — 단일 통합 (이슈 1)

v3.1에서 §4의 surface-aware merge와 §5의 L1/L2 merge가 별도로 존재했다.
이를 **하나의 함수로 통합**한다. 이것이 Agent C의 유일한 병합 판단 기준이다.

```python
def canonical_should_merge(event_a: Action, event_b: Action) -> MergeDecision:
    """Agent C의 유일한 병합 판단 함수.
    
    v3.1의 §4(surface)와 §5(L1/L2)를 통합한 canonical rule.
    이 함수 외에 별도의 병합 로직은 존재하지 않는다.
    
    Returns:
        MergeDecision: merge, split, 또는 review (Agent C LLM 판단 위임)
    """
    
    # ─── 기본 조건 (하나라도 실패하면 즉시 split) ───
    
    same_l1 = event_a.sound_source_l1 == event_b.sound_source_l1
    if not same_l1:
        return MergeDecision.SPLIT
    
    same_entity = (
        event_a.linked_character_id == event_b.linked_character_id and
        event_a.linked_object_id == event_b.linked_object_id
    )
    if not same_entity:
        return MergeDecision.SPLIT
    
    both_continuous = (
        event_a.event_type == "continuous" and
        event_b.event_type == "continuous"
    )
    if not both_continuous:
        return MergeDecision.SPLIT
    
    time_adjacent = abs(event_a.end_time - event_b.start_time) <= 0.4
    if not time_adjacent:
        return MergeDecision.SPLIT
    
    # ─── Surface 호환성 (source-aware strictness) ───
    
    strictness = get_surface_strictness(event_a.sound_source_l1)
    surfaces_ok = surface_compatible(
        event_a.surface_context,
        event_b.surface_context,
        strictness=strictness,
    )
    if not surfaces_ok:
        return MergeDecision.SPLIT
    
    # ─── L2 + Boundary 판단 ───
    
    same_l2 = event_a.sound_source_l2 == event_b.sound_source_l2
    same_label = event_a.action_label == event_b.action_label
    has_boundary = event_a.boundary_flag or event_b.boundary_flag
    
    # Case 1: L2도 같고 action_label도 같음 → 확실한 병합
    if same_l2 and same_label:
        return MergeDecision.MERGE
    
    # Case 2: L2는 다르지만 boundary 상황
    if has_boundary:
        if is_boundary_l2_exception_allowed(event_a.sound_source_l1):
            # continuous-nature L1만 boundary L2 mismatch 허용
            return MergeDecision.MERGE
        else:
            # discrete-nature L1은 boundary여도 L2 다르면 분리
            return MergeDecision.SPLIT
    
    # Case 3: L2 다르고 boundary도 아님
    # → track_hint가 같으면 soft signal로 review 위임
    if event_a.track_hint == event_b.track_hint:
        return MergeDecision.REVIEW  # Agent C LLM이 최종 판단
    
    return MergeDecision.SPLIT


class MergeDecision(str, Enum):
    MERGE = "merge"     # 확실히 병합
    SPLIT = "split"     # 확실히 분리
    REVIEW = "review"   # Agent C LLM에게 최종 판단 위임
```

### track_hint의 역할 (이슈 5 — 질문 5)

`track_hint`는 **병합/분리의 hard condition이 아니라 soft signal**로만 사용한다.

- hard condition: L1, entity, time, surface, boundary 여부
- soft signal: track_hint, L2

`REVIEW`가 반환되면 Agent C는 두 이벤트의 sfx_description을 비교하여 최종 판단한다. 이 경우에만 LLM이 개입하고, `MERGE`/`SPLIT`은 코드 레벨에서 확정된다.

**REVIEW 비율 목표**: 전체 병합 판단의 10% 이하. 이를 초과하면 L2 taxonomy가 불일치하다는 신호이므로 Agent B 프롬프트를 튜닝한다.

---

## 2. Boundary L2 Exception — 허용 범위 한정 (이슈 4)

### 문제

v3.1에서는 "boundary 상황이면 L2 mismatch도 병합 허용"이 모든 L1에 적용되었다.
그러나 `mechanism` 안의 `hinge_rotation`과 `latch_click`은 물리적으로 완전히 다른 소리이므로,
boundary라는 이유만으로 합치면 안 된다.

### 판단: continuous-nature vs discrete-nature 분리

L1 카테고리를 소리의 본질에 따라 두 그룹으로 나눈다:

```python
# Continuous-nature: 소리가 본질적으로 지속적이고, cut 경계에서 끊겼다가 이어질 가능성이 높음
# → boundary 상황에서 L2 mismatch 허용 (같은 소리의 미세한 변주일 가능성)
CONTINUOUS_NATURE_L1 = {
    "footstep",         # 걷기가 이어지는 중 cut 전환
    "body_movement",    # 옷 마찰이 이어지는 중
    "friction",         # 끌림/슬라이딩이 이어지는 중
    "rolling",          # 구름이 이어지는 중
    "motor",            # 엔진/모터가 계속 돌아가는 중
    "liquid",           # 물 흐름이 이어지는 중
    "gas",              # 바람이 이어지는 중
    "ambience_element", # 환경음이 이어지는 중
}

# Discrete-nature: 소리가 본질적으로 순간적/개별적이고, L2가 다르면 다른 소리일 가능성이 높음
# → boundary 상황에서도 L2 mismatch 시 분리
DISCRETE_NATURE_L1 = {
    "impact",           # 충돌은 각각 다른 이벤트
    "mechanism",        # hinge_rotation ≠ latch_click
    "vocal",            # 숨 ≠ 한숨 ≠ 신음
    "deformation",      # 깨짐 ≠ 구겨짐
    "electronic",       # 비프 ≠ 스파크
}


def is_boundary_l2_exception_allowed(l1: str) -> bool:
    return l1 in CONTINUOUS_NATURE_L1
```

### 예시

| Event A | Event B | Boundary? | 판단 | 근거 |
|---------|---------|-----------|------|------|
| footstep / heel_strike | footstep / toe_push | Yes | **MERGE** | footstep은 continuous-nature, 같은 걸음의 다른 위상 |
| mechanism / hinge_rotation | mechanism / latch_click | Yes | **SPLIT** | mechanism은 discrete-nature, 물리적으로 다른 소리 |
| motor / diesel_idle | motor / diesel_accel | Yes | **MERGE** | motor는 continuous-nature, 같은 엔진의 상태 변화 |
| impact / door_slam | impact / body_fall | Yes | **SPLIT** | impact는 discrete-nature, 다른 충돌 |
| friction / cloth_slide | friction / rope_pull | No | **SPLIT** | boundary 아닌데 L2 다름 |

---

## 3. Source-Aware Surface Compatibility (이슈 3)

### 문제

v3.1의 `surface_compatible()`는 모든 sound type에 동일한 카테고리 매칭을 적용했다.
하지만 `footstep`에서 cobblestone vs tile은 명확히 다른 소리인 반면,
`ambience_element`에서는 같은 stone 카테고리면 차이가 무의미하다.

### 판단: L1별 strictness tier

```python
class SurfaceStrictness(str, Enum):
    STRICT = "strict"    # exact core match 필요 (같은 카테고리 내에서도 core 재질이 같아야 함)
    NORMAL = "normal"    # 같은 카테고리면 호환 (v3.1 기본 동작)
    LOOSE = "loose"      # surface 무관하게 호환 (환경음 등)


# L1별 strictness 매핑
SURFACE_STRICTNESS_MAP: dict[str, SurfaceStrictness] = {
    # Strict: 표면 차이가 소리에 직접 영향
    "footstep": SurfaceStrictness.STRICT,
    "impact": SurfaceStrictness.STRICT,
    "rolling": SurfaceStrictness.STRICT,
    "friction": SurfaceStrictness.STRICT,
    
    # Normal: 카테고리 수준에서 구분
    "mechanism": SurfaceStrictness.NORMAL,
    "deformation": SurfaceStrictness.NORMAL,
    "body_movement": SurfaceStrictness.NORMAL,
    "liquid": SurfaceStrictness.NORMAL,
    
    # Loose: 표면이 소리에 미미한 영향
    "motor": SurfaceStrictness.LOOSE,
    "vocal": SurfaceStrictness.LOOSE,
    "gas": SurfaceStrictness.LOOSE,
    "electronic": SurfaceStrictness.LOOSE,
    "ambience_element": SurfaceStrictness.LOOSE,
}


def get_surface_strictness(l1: str) -> SurfaceStrictness:
    return SURFACE_STRICTNESS_MAP.get(l1, SurfaceStrictness.NORMAL)
```

### 개정된 surface_compatible()

```python
def surface_compatible(
    surface_a: str,
    surface_b: str,
    strictness: SurfaceStrictness = SurfaceStrictness.NORMAL,
) -> bool:
    """두 surface_context가 같은 track으로 묶일 수 있는지 판단한다.
    strictness에 따라 허용 범위가 달라진다."""
    
    # Loose: 항상 호환
    if strictness == SurfaceStrictness.LOOSE:
        return True
    
    # 정확 일치: 모든 strictness에서 호환
    if surface_a == surface_b:
        return True
    
    core_a = extract_core_surface(surface_a)
    core_b = extract_core_surface(surface_b)
    
    # Strict: core 재질이 정확히 같아야 호환
    # "cobblestone" vs "cobblestone" → True
    # "cobblestone" vs "tile" → False (둘 다 stone이지만 core가 다름)
    if strictness == SurfaceStrictness.STRICT:
        return core_a == core_b
    
    # Normal: 같은 카테고리면 호환
    # "cobblestone" vs "tile" → True (둘 다 stone)
    cat_a = categorize_surface(core_a)
    cat_b = categorize_surface(core_b)
    return cat_a is not None and cat_a == cat_b


SURFACE_CATEGORIES = {
    "stone": ["cobblestone", "marble", "granite", "concrete", "brick", "tile", "slate", "pebble"],
    "wood": ["hardwood", "plywood", "oak", "pine", "bamboo", "parquet", "plank"],
    "metal": ["steel", "iron", "aluminum", "brass", "copper", "tin"],
    "soft": ["carpet", "rug", "grass", "sand", "soil", "mud", "fabric", "foam", "felt"],
    "glass": ["glass", "crystal", "mirror", "ceramic"],
    "water": ["puddle", "shallow water", "wet surface", "ice"],
}


def categorize_surface(core: str) -> str | None:
    """core surface를 카테고리로 분류한다. 매칭 안 되면 None."""
    core_lower = core.lower()
    for category, members in SURFACE_CATEGORIES.items():
        if any(m in core_lower for m in members):
            return category
    return None
```

### 예시

| L1 | Surface A | Surface B | Strictness | 호환? | 근거 |
|----|-----------|-----------|------------|-------|------|
| footstep | wet cobblestone | dry cobblestone | strict | **Yes** | core 동일 (cobblestone) |
| footstep | cobblestone | tile | strict | **No** | core 다름 (strict에서는 stone 카테고리 불충분) |
| mechanism | dry metal hinge | rusted metal hinge | normal | **Yes** | 같은 metal 카테고리 |
| motor | steel surface | glass panel | loose | **Yes** | motor는 surface 무관 |
| impact | glass window | glass bottle | strict | **Yes** | core 동일 (glass) |
| rolling | cobblestone | carpet | strict | **No** | core 다름 |

---

## 4. Segment Timing — 문서와 코드 정합화 (이슈 2)

### 판단: re-encode 신뢰 가정으로 명시, duration 검증만 유지

re-encode (`-ss` before `-i`, `-c:v libx264`)는 프레임 단위 정밀도를 제공하므로,
실제 segment 시작 시각이 요청값과 다를 가능성은 극히 낮다 (오차 < 1 frame = ~0.04s at 24fps).
별도 시작 시각 검증 로직을 추가하는 것은 과잉이다.

**확정 정책**:

```
1. re-encode 방식이므로 actual_seg_start = 요청한 seg_start로 신뢰한다.
   별도 시작 시각 검증은 수행하지 않는다.

2. duration만 검증한다.
   실제 duration과 요청 duration의 차이가 0.3s를 초과하면 warning을 기록한다.
   0.5s를 초과하면 해당 segment를 재생성한다.

3. 만약 Phase 2 테스트에서 시작 시각 drift가 관측되면,
   ffprobe로 first frame PTS를 추출하는 검증을 추가한다.
   (현 시점에서는 불필요하다는 판단)
```

### 개정된 코드 (코멘트 수정)

```python
# re-encode이므로 시작 시각은 요청값을 신뢰한다.
# 별도 시작 시각 검증은 수행하지 않는다 (re-encode 정밀도 < 1 frame).
actual_seg_start = seg_start

# duration만 검증한다.
actual_duration = await get_duration(seg_path)
drift = abs(actual_duration - expected_duration)

if drift > 0.5:
    raise SegmentPrepError(
        cut_id=cut.id,
        message=f"duration drift {drift:.3f}s exceeds 0.5s threshold"
    )

timing_warning = drift > 0.3  # warning 기록용
```

---

## 5. 변경 요약표 정합성 수정 (이슈 5)

v3.1의 §0 변경 요약표에서 이슈 1 행을 아래로 교체:

| 변경 전 (v3.1) | 변경 후 (v3.2) |
|----------------|----------------|
| `likely_audible=excluded 등록 필수+warning` | `audible=track 필수, likely_audible=track 또는 excluded 필수(미충족 시 failure), visual_only/inactive=excluded 필수` |

---

## 6. v3.1 → v3.2 차분 요약

| v3.1 섹션 | 변경 유형 | v3.2 내용 |
|----------|----------|----------|
| §4 should_merge() | **삭제** | 본 문서 §1의 `canonical_should_merge()`로 대체 |
| §5 should_merge() (L1/L2 버전) | **삭제** | 위와 동일 — 단일 함수로 통합 |
| §4 surface_compatible() | **교체** | 본 문서 §3의 source-aware 버전으로 대체 |
| §5 L1/L2 boundary 예외 | **교체** | 본 문서 §2의 CONTINUOUS/DISCRETE 분리로 대체 |
| §3 segment actual_seg_start 코멘트 | **수정** | 본 문서 §4의 "신뢰 가정" 문구로 교체 |
| §0 변경 요약표 이슈 1 행 | **수정** | 본 문서 §5 |

---

## 7. 전체 문서 최종 구성

구현 담당자에게 전달할 문서 세트:

```
1. pipeline_v3_architecture.md     ← 기본 구조, 스키마, Agent 설계, MVP 계획
2. pipeline_v3_1_patch.md          ← likely_audible 확정, observed_*, re-encode, L1/L2
3. pipeline_v3_2_patch.md (본 문서) ← canonical merge rule, source-aware surface, boundary 한정
```

**적용 순서**: v3 → v3.1 패치 → v3.2 패치.
충돌 시 최신 패치가 우선한다.

---

## 8. 최종 설계 확정 사항 체크리스트

구현 착수 전, 아래가 모두 확정되었음을 확인한다.

### 아키텍처

- [x] Agent A (Global Video Analyzer) — 1회 호출, entity + cut 동시 추출
- [x] Agent B (Per-Cut Sound Analyst) — segment 기반 fan-out, re-encode 클리핑
- [x] Reconciliation Gate — unknown + low-confidence + conflict + duplicate (4가지)
- [x] Agent C (Track Synthesizer) — sound source/layer 기반, flash 모델

### 스키마

- [x] Entity: character, object, background, ambience_source (시각/사운드 분리)
- [x] Entity audibility: audible / likely_audible / visual_only / inactive
- [x] Cut: shot boundary 기반, camera_notes로 구도 변화 기록
- [x] Action: sound_source_l1 (닫힌 13종) + sound_source_l2 (자유 기술)
- [x] Action: surface_context, track_hint, boundary_flag
- [x] Agent B presence: observed_visual_description, observed_material, observed_surface
- [x] Track: entity_refs[], confidence(min/mean/support_count), surface_context_summary
- [x] TrackManifest: tracks[] + excluded_entities[]

### 병합 규칙

- [x] 단일 canonical_should_merge() — L1/L2 + surface(source-aware) + boundary(nature-aware)
- [x] Surface strictness: strict (footstep, impact, rolling, friction) / normal / loose
- [x] Boundary L2 exception: continuous-nature L1만 허용
- [x] track_hint: soft signal (REVIEW 트리거), hard condition 아님
- [x] REVIEW 시 Agent C LLM 판단, 비율 목표 < 10%

### Validation

- [x] audible → track 필수 (failure)
- [x] likely_audible → track 또는 excluded 필수 (failure)
- [x] visual_only/inactive → excluded 필수 (failure)
- [x] likely_audible_untracked warning 삭제
- [x] Timestamp 0.2s grid, onset 범위, continuous 유효성, track consistency

### Segment

- [x] re-encode (libx264 CRF 18, MVP부터)
- [x] ±1.0s padding, target range clamp
- [x] actual_seg_start = 요청값 신뢰 (re-encode 가정)
- [x] duration drift > 0.5s → 재생성, > 0.3s → warning

### 미확정 (Phase별 튜닝)

- [ ] DESCRIPTION_CONFLICT_THRESHOLD (초기 0.6)
- [ ] surface 카테고리 멤버 목록 (Phase 3)
- [ ] max_concurrency (초기 5)
- [ ] padding 크기 (초기 ±1.0s)
- [ ] L1 taxonomy 확장 여부 (Phase 2-3)
- [ ] REVIEW 비율 10% 초과 시 대응 (Phase 3)

---

## 9. 검토 요청 사항 (구현 담당자용)

v3 §13 + v3.1 §8의 기존 요청에 추가:

11. **canonical_should_merge() 구현**: `src/gates/merge_rules.py`에 본 문서의 통합 함수 + CONTINUOUS/DISCRETE 매핑 + source-aware surface 구현
12. **L1 validator 강화**: Pydantic validator에서 `sound_source_l1`이 13종 목록에 포함되는지 검증 + surface strictness 매핑 자동 연결
13. **MergeDecision.REVIEW 처리**: Agent C 프롬프트에 "REVIEW로 넘어온 이벤트 쌍"을 제시하고 병합/분리를 판단하게 하는 로직
14. **통합 테스트 시나리오**: 본 문서의 §2 예시 테이블(footstep merge, mechanism split 등)을 test case로 구현
