# Gemini 비디오 사운드 디자인 파이프라인 — v3.1 패치 (3차 리뷰 반영)

> **문서 목적**: v3에 대한 3차 리뷰(4개 핵심 이슈 + 구현 리스크)를 반영한 최종 패치.
> 이 문서는 `pipeline_v3_architecture.md`의 **부분 수정본**이다.
> 변경되지 않는 섹션은 v3 원본을 그대로 유지한다.
>
> **변경 이력**:
> - v1 → v2 → v3 → **v3.1 (본 문서)**: 구현 착수 직전 마지막 고정

---

## 0. v3.1 변경 요약

| # | 3차 리뷰 이슈 | 판단 | 변경 내용 |
|---|---------------|------|-----------|
| 1 | `likely_audible` complete/warning 충돌 | **3-tier 규칙으로 확정** | audible=필수, likely_audible=excluded 등록 필수+warning, visual_only/inactive=excluded 필수 |
| 2 | description conflict 검출용 데이터 부족 | **Agent B에 `observed_*` 필드 추가** | characters_present/objects_present에 관찰 시점 description 포함 |
| 3 | segment copy 모드 시간축 어긋남 | **MVP부터 re-encode 채택** | `-c:v libx264 -crf 18`로 정밀 클리핑, actual_seg_start 검증 포함 |
| 4 | Agent C 병합에 surface_context 미반영 | **surface compatibility check 추가** | should_merge()에 surface 비교 로직 명시, surface 불일치 시 병합 금지 |
| 5 | sound_source taxonomy 불일치 위험 | **2-tier taxonomy 도입** | Agent B에서 L1(닫힌 목록) + L2(자유 기술), Agent C에서 L1 기준 병합 |
| 6 | clamp 코드 순회 중 삭제 버그 | **filter 방식으로 수정** | 리스트 순회 중 삭제 대신 list comprehension |

---

## 1. likely_audible Validation 확정 (이슈 1)

### 문제

v3에서 두 곳이 충돌했다.

**통과 조건 3(a,b,c)** — `likely_audible`이 track 또는 excluded에 있어야 complete:
> (a) audible/likely_audible이고 track에 포함됨
> (b) audible/likely_audible이지만 excluded에 기록됨
> (c) visual_only/inactive이고 excluded에 기록됨

**Warning 조건** — `likely_audible`이 track에도 excluded에도 없어도 warning만:
> `likely_audible_untracked`: likely_audible entity가 track에도 excluded에도 없음 → warning

이 두 규칙은 양립 불가능하다. 하나를 택해야 한다.

### 판단: 3-tier 규칙

| audibility | 요구 사항 | 미충족 시 |
|------------|-----------|-----------|
| `audible` | track에 포함 **필수** (excluded 불가) | **failure** |
| `likely_audible` | track에 포함 **또는** excluded에 사유와 함께 기록 **필수** | **failure** |
| `visual_only` / `inactive` | excluded에 기록 **필수** (track 포함 금지) | **failure** |

**근거**:
- `audible`은 소리가 확실하므로 track이 반드시 존재해야 한다. 없으면 분석 누락.
- `likely_audible`은 "모르겠으니 일단 등록한 것"이므로, Agent C가 판단을 내려야 한다. track을 만들든, excluded에 "분석 결과 소리 발생 없음"이라고 기록하든 — **명시적 판단이 필요**. 아무 곳에도 없으면 판단 자체가 누락된 것이므로 failure.
- `visual_only`/`inactive`는 track에 포함되면 안 된다. 환각 방지.

**v3의 warning 조건 `likely_audible_untracked`는 삭제**. 위 규칙으로 대체되므로 더 이상 필요 없다.

### 개정된 Final Validation 통과 조건

v3의 조건 3을 아래로 교체:

```python
def validate_entity_coverage(
    entities: list[Entity],
    tracks: list[Track],
    excluded: list[ExcludedEntity]
) -> list[ValidationError]:
    errors = []
    
    tracked_ids = {ref.id for track in tracks for ref in track.entity_refs}
    excluded_ids = {e.entity_id for e in excluded}
    excluded_map = {e.entity_id: e for e in excluded}
    
    for entity in entities:
        eid = entity.id
        in_track = eid in tracked_ids
        in_excluded = eid in excluded_ids
        
        if entity.audibility == "audible":
            if not in_track:
                errors.append(ValidationError(
                    type="entity_coverage",
                    severity="failure",
                    entity_id=eid,
                    message=f"audible entity {eid} has no track"
                ))
            if in_excluded:
                errors.append(ValidationError(
                    type="entity_coverage",
                    severity="failure",
                    entity_id=eid,
                    message=f"audible entity {eid} should not be in excluded_entities"
                ))
        
        elif entity.audibility == "likely_audible":
            if not in_track and not in_excluded:
                errors.append(ValidationError(
                    type="entity_coverage",
                    severity="failure",
                    entity_id=eid,
                    message=f"likely_audible entity {eid} must be in track or excluded with reason"
                ))
        
        elif entity.audibility in ("visual_only", "inactive"):
            if not in_excluded:
                errors.append(ValidationError(
                    type="entity_coverage",
                    severity="failure",
                    entity_id=eid,
                    message=f"{entity.audibility} entity {eid} must be in excluded_entities"
                ))
            if in_track:
                errors.append(ValidationError(
                    type="entity_coverage",
                    severity="failure",
                    entity_id=eid,
                    message=f"{entity.audibility} entity {eid} should not have a track"
                ))
    
    return errors
```

### 개정된 Warning 테이블

| Warning | 조건 | 설명 |
|---------|------|------|
| `low_overall_confidence` | 전체 event 중 confidence < 0.5가 20% 이상 | 분석 품질 의심 |
| `excessive_tracks` | track 수 > 30 | 과세분화 의심 |
| `excessive_unknowns` | reconciliation 후에도 UNKNOWN 잔존 | registry 품질 의심 |
| `thin_support` | track의 support_count = 1이고 confidence.min < 0.6 | 단일 약근거 track |
| `high_exclusion_rate` | excluded / total entities > 0.5 | Agent A의 sound-relevance 필터가 너무 느슨한 의심 |

(`likely_audible_untracked` 삭제됨)

---

## 2. Agent B 스키마에 observed_* 필드 추가 (이슈 2)

### 문제

Reconciliation Gate의 `description_conflict` 검출은 "같은 entity_id인데 cut마다 visual description이 다름"을 감지해야 한다. 그런데 Agent B의 `characters_present`/`objects_present`에는 entity_id와 match_confidence만 있고, 해당 cut에서 **실제로 관찰한 외형 정보**가 없다.

### 판단: observed_* 필드 추가

Agent B가 entity를 매칭할 때, registry의 description을 그대로 복사하는 것이 아니라 **해당 segment에서 실제로 관찰한 외형**을 별도로 기록하게 한다.

### 개정된 characters_present / objects_present 스키마

```python
class CharacterPresence(BaseModel):
    entity_id: str                            # CHAR_001
    match_confidence: float
    confidence_flag: Literal["ok", "low"] | None = None  # < 0.7이면 "low"
    observed_visual_description: str          # 신규: 이 cut에서 실제로 관찰한 외형
    observed_action_summary: str | None = None  # 신규: 이 cut에서의 주요 행동 요약

class ObjectPresence(BaseModel):
    entity_id: str                            # OBJ_001
    match_confidence: float
    confidence_flag: Literal["ok", "low"] | None = None
    observed_visual_description: str          # 신규
    observed_material: str | None = None      # 신규: 이 cut에서 관찰된 재질 (registry와 다를 수 있음)
    observed_surface: str | None = None       # 신규: 이 cut에서 관찰된 표면 상태
```

### Agent B 프롬프트 추가 지시

```
[Entity 매칭]
각 entity를 매칭할 때, registry의 설명을 복사하지 마세요.
이 segment에서 실제로 보이는 모습을 독립적으로 기술하세요.

예:
  registry: "shoulder-length black hair, red jacket, white sneakers"
  이 cut에서 관찰: "back view, red jacket visible, hair tied up in ponytail"
  → observed_visual_description: "back view, red jacket visible, hair tied up in ponytail"

registry와 관찰이 다르더라도, 관찰한 그대로 기록하세요.
차이가 크면 match_confidence를 낮게 설정하세요.
```

### Reconciliation의 description_conflict 검출 개정

```python
def detect_description_conflicts(
    entity_id: str,
    cut_analyses: list[CutSoundAnalysis],
    registry: EntityRegistry
) -> bool:
    """cross-cut 비교로 description conflict 검출"""
    
    observations = []
    for analysis in cut_analyses:
        for presence in analysis.characters_present + analysis.objects_present:
            if presence.entity_id == entity_id:
                observations.append({
                    "cut_id": analysis.cut_id,
                    "observed_desc": presence.observed_visual_description,
                    "observed_material": getattr(presence, "observed_material", None),
                    "confidence": presence.match_confidence,
                })
    
    if len(observations) < 2:
        return False
    
    # 관찰 간 유사도 계산
    descs = [o["observed_desc"] for o in observations]
    pairwise_sims = []
    for i in range(len(descs)):
        for j in range(i + 1, len(descs)):
            pairwise_sims.append(cosine_sim(embed(descs[i]), embed(descs[j])))
    
    avg_sim = sum(pairwise_sims) / len(pairwise_sims)
    
    # 유사도가 낮으면 conflict
    # threshold는 Phase 2에서 튜닝. 초기값 0.6
    return avg_sim < DESCRIPTION_CONFLICT_THRESHOLD  # default 0.6
```

---

## 3. Segment 클리핑 전략 확정 (이슈 3)

### 문제

`-c copy` 모드는 keyframe 기준으로만 잘리므로, 실제 segment 시작이 요청보다 앞당겨질 수 있다. 이때 프롬프트에 전달하는 `padding_before`와 실제 영상의 시간축이 어긋나면 0.2s grid snap이 무의미해진다.

### 판단: MVP부터 re-encode 채택

| 기준 | `-c copy` | re-encode (`-c:v libx264`) |
|------|-----------|---------------------------|
| 시간 정밀도 | keyframe 단위 (±0.5~2.0s 오차) | 프레임 단위 (~0.04s 오차 at 24fps) |
| 처리 속도 | ~즉시 | ~1-3초/segment (CPU) |
| 전체 파이프라인 영향 | 15 segments × 0s = 0s | 15 segments × 2s = ~30s |
| timestamp 신뢰도 | 위험 | 안전 |

**결론**: 60초 영상의 15 segments를 re-encode하는 데 ~30초. Gemini API 호출 지연(~45-90초)과 비교하면 파이프라인 전체에서 무시 가능한 수준. timestamp 정확도가 전체 파이프라인의 근간이므로 정밀도를 우선한다.

### 개정된 segment 생성 코드

```python
import asyncio
import json
import subprocess
from pathlib import Path

async def prepare_segments(
    video_path: str,
    cuts: list[Cut],
    padding: float = 1.0,
    output_dir: str = "/tmp/segments",
) -> list[Segment]:
    """각 cut에 대해 re-encode 방식으로 정밀 segment를 생성한다."""
    
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    duration = await get_duration(video_path)
    
    segments = []
    for cut in cuts:
        seg_start = max(0.0, cut.start_time - padding)
        seg_end = min(duration, cut.end_time + padding)
        seg_duration = seg_end - seg_start
        seg_path = f"{output_dir}/{cut.id}.mp4"
        
        # re-encode로 정밀 클리핑
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{seg_start:.3f}",
            "-i", video_path,
            "-t", f"{seg_duration:.3f}",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-an",  # 오디오 제거 (원본이 무음이지만 안전 장치)
            "-movflags", "+faststart",
            seg_path,
        ]
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            raise SegmentPrepError(
                cut_id=cut.id,
                message=f"ffmpeg failed: {stderr.decode()[-500:]}"
            )
        
        # 실제 segment 시작/종료를 ffprobe로 검증
        actual_duration = await get_duration(seg_path)
        actual_seg_start = seg_start  # re-encode이므로 요청값과 동일
        actual_seg_end = seg_start + actual_duration
        
        # 검증: 실제 duration이 요청 대비 0.1s 이상 차이나면 warning
        expected_duration = seg_duration
        drift = abs(actual_duration - expected_duration)
        
        segments.append(Segment(
            cut_id=cut.id,
            file_path=seg_path,
            file_uri=None,  # upload는 별도 단계
            actual_seg_start=actual_seg_start,
            actual_seg_end=actual_seg_end,
            target_start=cut.start_time,
            target_end=cut.end_time,
            padding_before=cut.start_time - actual_seg_start,
            padding_after=actual_seg_end - cut.end_time,
            timing_drift=drift,
        ))
    
    return segments


async def upload_segments(
    segments: list[Segment],
    gemini_client: GeminiClient,
    max_concurrency: int = 5,
) -> list[Segment]:
    """segment 파일들을 Gemini File API에 업로드한다."""
    
    semaphore = asyncio.Semaphore(max_concurrency)
    
    async def upload_one(seg: Segment) -> Segment:
        async with semaphore:
            seg.file_uri = await gemini_client.upload_file(seg.file_path)
            return seg
    
    return await asyncio.gather(*[upload_one(s) for s in segments])
```

### Segment Pydantic 모델 개정

```python
class Segment(BaseModel):
    cut_id: str
    file_path: str
    file_uri: str | None = None
    actual_seg_start: float       # re-encode 후 실제 시작 시각 (원본 기준)
    actual_seg_end: float         # re-encode 후 실제 종료 시각
    target_start: float           # cut의 실제 시작
    target_end: float             # cut의 실제 종료
    padding_before: float         # target_start - actual_seg_start
    padding_after: float          # actual_seg_end - target_end
    timing_drift: float = 0.0    # 요청 대비 실제 duration 차이
```

### Agent B 프롬프트의 시간 지시도 actual 기준으로 전환

```python
def build_agent_b_prompt(segment: Segment, registry: EntityRegistry) -> str:
    return f"""
이 비디오 segment는 원본 영상의 일부입니다.

Segment 정보:
- segment 전체 길이: {segment.actual_seg_end - segment.actual_seg_start:.1f}초
- 앞쪽 padding: {segment.padding_before:.1f}초
- 뒤쪽 padding: {segment.padding_after:.1f}초
- **분석 대상 구간**: segment 시작 후 {segment.padding_before:.1f}초 ~ {segment.padding_before + (segment.target_end - segment.target_start):.1f}초

분석 대상 구간 밖의 내용은 문맥 파악 용도로만 사용하세요.
이벤트 기록은 반드시 분석 대상 구간 내에서만 수행하세요.
모든 timestamp는 원본 영상 기준 절대 시간으로 기록하세요.
(이 segment의 분석 대상 구간은 원본 기준 {segment.target_start:.1f}초 ~ {segment.target_end:.1f}초입니다.)

...(이하 기존 프롬프트)
"""
```

### Clamp 코드 버그 수정 (순회 중 삭제 → filter)

```python
def clamp_to_target_range(analysis: CutSoundAnalysis, t_start: float, t_end: float) -> CutSoundAnalysis:
    """target range 밖의 onset을 제거하고, continuous를 clamp한다."""
    
    clamped_actions = []
    
    for action in analysis.actions:
        if action.event_type == "continuous":
            action.start_time = max(action.start_time, t_start)
            action.end_time = min(action.end_time, t_end)
            
            # clamp 후 유효하지 않은 구간 제거
            if action.start_time >= action.end_time:
                continue
            
            if action.start_time == t_start or action.end_time == t_end:
                action.boundary_flag = True
            
            clamped_actions.append(action)
        
        elif action.event_type == "onset":
            if t_start <= action.timestamp <= t_end:
                clamped_actions.append(action)
            # else: 범위 밖 onset은 버림 (순회 중 삭제 대신 포함하지 않음)
    
    analysis.actions = clamped_actions
    return analysis
```

---

## 4. Agent C 병합 규칙에 surface_context 반영 (이슈 4)

### 문제

v3의 `should_merge()`는 `sound_source + action_label + entity + time`만 보고, `surface_context`를 검사하지 않는다. 같은 인물이 cobblestone 위를 걷다가 carpet 위를 걷는 경우, sound_source(footstep_impact)와 entity(CHAR_001)는 같지만 소리가 완전히 다르므로 별도 track이어야 한다.

### 판단: surface compatibility check 추가

```python
def surface_compatible(surface_a: str, surface_b: str) -> bool:
    """두 surface_context가 같은 track으로 묶일 수 있는지 판단한다.
    
    완전 일치가 아니라 '호환성'을 본다.
    예: "wet cobblestone" vs "cobblestone" → 호환 (젖은 정도 차이)
    예: "cobblestone" vs "thick carpet" → 비호환 (완전히 다른 표면)
    """
    
    # 1차: 정확 일치 또는 한쪽이 다른 쪽의 부분 문자열
    if surface_a == surface_b:
        return True
    if surface_a in surface_b or surface_b in surface_a:
        return True
    
    # 2차: 핵심 표면 재질 추출 후 비교
    core_a = extract_core_surface(surface_a)  # "wet cobblestone" → "cobblestone"
    core_b = extract_core_surface(surface_b)  # "dry cobblestone" → "cobblestone"
    
    if core_a == core_b:
        return True
    
    # 3차: 표면 카테고리 비교 (같은 카테고리면 호환)
    SURFACE_CATEGORIES = {
        "stone": ["cobblestone", "marble", "granite", "concrete", "brick", "tile"],
        "wood": ["hardwood", "plywood", "oak", "pine", "bamboo", "parquet"],
        "metal": ["steel", "iron", "aluminum", "brass", "copper"],
        "soft": ["carpet", "rug", "grass", "sand", "soil", "mud", "fabric"],
        "glass": ["glass", "crystal", "mirror"],
        "water": ["puddle", "shallow water", "wet surface"],
    }
    
    cat_a = categorize_surface(core_a, SURFACE_CATEGORIES)
    cat_b = categorize_surface(core_b, SURFACE_CATEGORIES)
    
    # 같은 카테고리 내 → 호환 (wet marble vs dry tile → 둘 다 stone → 호환)
    # 다른 카테고리 → 비호환 (cobblestone vs carpet → stone vs soft → 비호환)
    return cat_a is not None and cat_a == cat_b


def extract_core_surface(surface: str) -> str:
    """수식어를 제거하고 핵심 재질만 추출한다."""
    modifiers = ["wet", "dry", "rough", "smooth", "polished", "rusted",
                 "corrugated", "painted", "worn", "cracked", "dirty", "clean"]
    words = surface.lower().split()
    core_words = [w for w in words if w not in modifiers and w not in (",", "and")]
    return " ".join(core_words) if core_words else surface
```

### 개정된 should_merge()

```python
def should_merge(event_a: Action, event_b: Action) -> bool:
    """인접 cut의 두 이벤트를 하나의 track event로 병합할지 판단한다."""
    
    same_source = event_a.sound_source == event_b.sound_source
    same_label = event_a.action_label == event_b.action_label
    same_entity = (event_a.linked_character_id == event_b.linked_character_id and
                   event_a.linked_object_id == event_b.linked_object_id)
    both_continuous = (event_a.event_type == "continuous" and
                       event_b.event_type == "continuous")
    time_adjacent = abs(event_a.end_time - event_b.start_time) <= 0.4
    has_boundary = event_a.boundary_flag or event_b.boundary_flag
    
    # 신규: surface 호환성 검사
    surfaces_ok = surface_compatible(event_a.surface_context, event_b.surface_context)
    
    # surface가 비호환이면 어떤 경우에도 병합 금지
    if not surfaces_ok:
        return False
    
    # 일반 병합: 모든 조건 충족
    if same_source and same_label and same_entity and both_continuous and time_adjacent:
        return True
    
    # boundary 병합: action_label 약간 달라도 허용하되 surface는 필수 호환
    if same_source and same_entity and has_boundary and time_adjacent:
        return True
    
    return False
```

### Track 분리 기준 개정

```
같은 track에 묶는 조건:
  sound_source가 동일 AND
  linked entity가 동일 AND
  surface_context가 호환 (surface_compatible() = True)

별도 track으로 분리하는 조건 (하나라도 해당하면 분리):
  sound_source가 다름
  OR linked entity가 다름
  OR surface_context가 비호환 (surface_compatible() = False)

예시:
  CHAR_001 footstep on "wet cobblestone" (CUT_001)
  + CHAR_001 footstep on "cobblestone" (CUT_002)
  → 같은 track (stone 카테고리, 수식어만 다름)

  CHAR_001 footstep on "wet cobblestone" (CUT_001)
  + CHAR_001 footstep on "thick carpet" (CUT_005)
  → 별도 track (stone vs soft, 완전히 다른 소리)
```

---

## 5. sound_source Taxonomy 확정 (이슈 5)

### 문제

v3의 sound_source 분류가 가이드 수준이라 cut마다 granularity가 다를 수 있다. `mechanical_friction` vs `hinge_rotation` vs `latch_click` — 같은 문을 분석해도 cut마다 다른 레벨로 분류될 수 있다.

### 판단: 2-tier taxonomy

Agent B에서 자유 분류하되, 표준화된 상위 카테고리(L1)를 필수로 포함시킨다. Agent C는 L1 기준으로 병합하고, L2는 description 보강에만 활용한다.

### Taxonomy 정의

```python
# L1: 닫힌 목록 (Agent B가 반드시 이 중 하나를 선택)
SOUND_SOURCE_L1 = [
    # Physical contact
    "impact",           # 충돌, 타격, 착지
    "friction",         # 마찰, 슬라이딩, 문지름
    "rolling",          # 구름, 회전 접촉
    
    # Mechanical
    "mechanism",        # 힌지, 래치, 레버, 기어, 잠금장치
    "motor",            # 전동기, 엔진, 진동
    
    # Fluid
    "liquid",           # 물, 액체 — 방울, 흐름, 튀김
    "gas",              # 바람, 공기압, 증기
    
    # Human body
    "footstep",         # 발걸음 (surface에 따라 소리 다름)
    "body_movement",    # 옷 마찰, 관절, 몸 움직임
    "vocal",            # 비언어 발성 — 숨, 한숨, 신음
    
    # Material deformation
    "deformation",      # 깨짐, 구겨짐, 찢어짐, 휘어짐
    
    # Electrical / electronic
    "electronic",       # 버즈, 비프, 스파크, 험
    
    # Environment
    "ambience_element", # 환경음 요소 (새소리, 벌레소리, 교통 등)
]

# L2: 자유 기술 (Agent B가 L1 하위에서 구체적으로 명시)
# 예: L1="mechanism", L2="hinge_rotation"
# 예: L1="mechanism", L2="latch_click"
# 예: L1="footstep", L2="heel_strike"
# 예: L1="impact", L2="door_slam"
```

### Action 스키마 개정

```python
class Action(BaseModel):
    action_id: str
    linked_character_id: str | None
    linked_object_id: str | None
    action_label: str
    sound_source_l1: str              # 개정: L1 닫힌 목록에서 선택
    sound_source_l2: str              # 개정: L1 하위의 자유 기술
    surface_context: str
    track_hint: str
    event_type: Literal["onset", "continuous"]
    timestamp: float | None = None
    start_time: float | None = None
    end_time: float | None = None
    boundary_flag: bool = False
    sfx_description: str
    confidence: float
```

### Agent B 프롬프트 추가

```
[Sound Source 분류]
각 action의 소리 원인을 2단계로 분류하세요.

sound_source_l1: 아래 목록에서 반드시 하나를 선택하세요.
  impact, friction, rolling, mechanism, motor,
  liquid, gas, footstep, body_movement, vocal,
  deformation, electronic, ambience_element

sound_source_l2: L1 하위에서 더 구체적으로 기술하세요. 자유 형식입니다.
  예: L1=mechanism → L2=hinge_rotation
  예: L1=footstep → L2=heel_strike_on_stone
  예: L1=impact → L2=door_slam_wood

L1은 반드시 위 목록에서 선택하세요. 목록에 없는 값은 사용하지 마세요.
L2는 자유롭게 기술하되, 한 영상 내에서 같은 소리 원인은 같은 L2를 사용하세요.
```

### Agent C 병합에서의 활용

```python
def should_merge(event_a: Action, event_b: Action) -> bool:
    # L1이 같아야 병합 가능 (필수)
    same_l1 = event_a.sound_source_l1 == event_b.sound_source_l1
    
    if not same_l1:
        return False
    
    # L2가 다르면 warning은 붙되 L1이 같으면 병합은 허용
    # (Agent C가 최종 판단)
    l2_match = event_a.sound_source_l2 == event_b.sound_source_l2
    
    # ... (기존 조건: same_entity, time_adjacent, surfaces_ok)
    
    # L2 불일치 시 boundary 병합만 허용, 일반 병합은 금지
    if not l2_match and not has_boundary:
        return False
    
    # ...
```

### Track 스키마에도 L1/L2 반영

```python
class Track(BaseModel):
    track_id: str
    track_type: Literal["sfx", "ambience"]
    track_label: str
    sound_source_l1: str                # 개정
    sound_source_l2: str                # 개정: 가장 빈번한 L2 또는 Agent C가 정규화
    entity_refs: list[EntityRef]
    sound_description: str
    surface_context_summary: str        # 신규: 이 track의 대표 surface 요약
    events: list[TrackEvent]
    confidence: TrackConfidence
```

---

## 6. 리스크 레지스터 추가

v3의 기존 항목에 추가:

| 리스크 | 확률 | 영향 | 대응 방안 |
|--------|------|------|-----------|
| re-encode segment 품질 열화 | 낮 | 낮 | CRF 18은 시각적으로 무손실에 가까움, Gemini 분석에 영향 없음 |
| observed_* 필드가 LLM 출력 크기 증가 | 중 | 낮 | 토큰 증가 ~15%, 비용 영향 미미 ($0.46 → ~$0.53) |
| surface_compatible() 오분류 | 중 | 중 | Phase 3에서 10개 비디오 테스트 후 카테고리/threshold 튜닝 |
| L1 taxonomy 부족 (새로운 sound type) | 낮 | 낮 | L2에서 수용, 축적 후 L1에 카테고리 추가 |

---

## 7. v3 → v3.1 전체 차분 요약

구현 담당자는 v3 원본에서 아래 섹션만 교체하면 된다:

| v3 섹션 | 변경 유형 | v3.1 해당 섹션 |
|---------|----------|---------------|
| §6 Final Validation 통과 조건 3 | **교체** | 본 문서 §1 (3-tier 규칙) |
| §6 Warning 테이블 | **교체** | 본 문서 §1 (likely_audible_untracked 삭제) |
| §2.3 Agent B 출력 스키마 characters_present | **확장** | 본 문서 §2 (observed_* 추가) |
| §2.3 Agent B 출력 스키마 objects_present | **확장** | 본 문서 §2 (observed_* 추가) |
| §1.2 Segment Preparation 코드 | **교체** | 본 문서 §3 (re-encode + actual 검증) |
| §2.2 Agent B 프롬프트 시간 지시 | **교체** | 본 문서 §3 (actual 기준) |
| §2.2 Clamp 코드 | **교체** | 본 문서 §3 (filter 방식) |
| §5.1 should_merge() | **교체** | 본 문서 §4 (surface check 추가) |
| §5.2 Track 분리 기준 | **교체** | 본 문서 §4 (surface 비호환 시 분리) |
| §2.3 sound_source (단일 필드) | **교체** | 본 문서 §5 (L1 + L2 2-tier) |
| §7 Action 스키마 | **교체** | 본 문서 §5 (sound_source → L1/L2) |
| §7 Track 스키마 | **확장** | 본 문서 §5 (L1/L2 + surface_context_summary) |
| §11 리스크 레지스터 | **추가** | 본 문서 §6 (4개 항목 추가) |

---

## 8. 검토 요청 사항 (구현 담당자용)

v3의 §13 검토 요청 사항은 그대로 유효하다. 추가로:

7. **observed_* 필드 포함한 스키마 최종본**: `src/schemas/analysis.py`의 `CharacterPresence`, `ObjectPresence` 업데이트
8. **surface_compatible() 구현**: `src/utils/matching.py`에 surface 카테고리 매핑 + 호환성 판단 구현
9. **L1 taxonomy validator**: Agent B 출력의 `sound_source_l1`이 닫힌 목록에 포함되는지 검증하는 Pydantic validator
10. **re-encode segment 품질 검증**: CRF 18 segment를 Gemini에 넣었을 때 분석 품질이 원본 대비 열화하지 않는지 Phase 1에서 함께 검증

---

## 9. 남은 미확정 사항 (구현 중 결정)

아래 항목은 코드를 짜면서 데이터를 보고 결정하는 것이 맞다. 문서에서 미리 고정하지 않는다.

| 항목 | 현재 초기값 | 결정 시점 |
|------|------------|-----------|
| DESCRIPTION_CONFLICT_THRESHOLD | 0.6 | Phase 2 (Agent B 결과 확인 후) |
| surface_compatible() 카테고리 목록 | 6개 카테고리 | Phase 3 (다양한 비디오 테스트 후) |
| max_concurrency | 5 | Phase 2 (rate limit 실측 후) |
| padding 크기 | ±1.0s | Phase 2 (경계 분석 품질 확인 후) |
| L1 taxonomy | 13개 카테고리 | Phase 2-3 (분류 일관성 확인 후 추가/병합) |
