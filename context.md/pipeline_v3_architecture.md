# Gemini 비디오 사운드 디자인 파이프라인 — v3 아키텍처 (2차 리뷰 반영)

> **문서 목적**: v2에 대한 2차 리뷰(7개 이슈 + 열린 질문)를 반영한 최종 설계.
> Claude Code / Codex에게 전달하여 구현에 착수하기 위한 핸드오프 문서.
>
> **변경 이력**:
> - v1: 초안 (4-Agent + 2-Gate)
> - v2: 1차 리뷰 반영 (Agent 통합, track=sound layer, UNKNOWN, ambience 분리)
> - v3 (본 문서): 2차 리뷰 반영 (segment 전략, reconciliation 확장, 구조적 sound_source, validation 확정)

---

## 0. v3 변경 요약

| # | 2차 리뷰 이슈 | 판단 | 변경 내용 |
|---|---------------|------|-----------|
| 1 | Agent B: full video + time instruction → 격리 약함 | **실제 클리핑 + padding** 방식 채택 | ffmpeg로 cut segment 생성, ±1.0s padding 포함, 결과는 target range로 clamp |
| 2 | Reconciliation: unknown만 처리, 오매칭 방치 | **범위 확장** 채택 | low-confidence match, description conflict, same-cut duplicate도 대상에 포함 |
| 3 | Agent B→C 사이 sound_source 필드 없음 | **Agent B 출력에 구조 필드 추가** 채택 | `sound_source`, `surface_context`, `track_hint` 필드 도입 |
| 4 | Cut 경계에서 continuous action 잘림/중복 | **padding window + clamp** 규칙 도입 | ±1.0s context window, target range clamp, 경계 이벤트에 `boundary_flag` |
| 5 | likely_audible 처리 모호 | **warning으로 확정**, failure 아님 | likely_audible은 track 미참조여도 통과, warning 첨부 |
| 6 | Agent A가 entity를 과수집할 위험 | **sound-relevance 필터 도입** | Agent A 프롬프트에 sound relevance 판단 강제 + `audibility` 1차 판단 |
| 7 | Reconciliation fuzzy match가 generic label에 취약 | **다차원 매칭** 채택 | label + time overlap + visual_description + material + linked_background 종합 스코어 |

### 열린 질문에 대한 판단

| 질문 | 판단 | 근거 |
|------|------|------|
| Cut 정의: angle change vs shot boundary | **Shot boundary 중심** | Angle change까지 포함하면 cut 수가 과도해지고 fan-out 비용 폭증. 구도 변화는 cut 내 메타데이터로 기록 |
| Agent B: full video vs segment | **Segment 기반** | 격리, 비용, 일관성 모두 segment가 우위 (아래 상세) |
| Agent B에 sound_source 추가 여부 | **추가** | Agent C의 free-text 재해석 부담 제거, 병합 규칙의 구조적 기반 확보 |
| likely_audible: failure vs warning | **Warning** | Failure로 두면 억지 track 생성 유도 → 환각 위험 (v2 이슈 8과 동일 논리) |
| Track confidence: min vs min+mean+support | **min + mean + support_count** | Min만으로는 track 전체 신뢰도를 왜곡. 3개 지표 병기 |

---

## 1. 개정된 파이프라인 아키텍처 (v3)

### 1-1. 전체 흐름

```
Video Input (10-60s, no audio)
    │
    ▼
[Preprocessing]
  ① ffprobe → metadata (fps, duration, resolution)
  ② Gemini File API upload → file_uri (Agent A용)
  ③ pyscenedetect → scene_change_candidates
    │
    ▼
[Agent A: Global Video Analyzer]
  - file_uri로 전체 영상 1회 시청
  - entities (character, object — sound-relevant만)
  - backgrounds + ambience_sources
  - cuts (shot boundary 기반)
  - 출력: GlobalAnalysis
    │
    ▼
[Validation Gate]
  - 스키마 검증 + 시간 정합성 + ambience↔background 매핑
  - 에러 타입별 retry
    │
    ▼
[Segment Preparation]  ← 신규 단계
  - 각 cut에 대해 ±1.0s padding을 포함한 segment를 ffmpeg로 추출
  - segment별 Gemini File API upload → segment_file_uri
    │
    ▼
[Agent B: Per-Cut Sound Analyst]  ← fan-out
  - 입력: segment_file_uri + entity/ambience registry + cut metadata
  - 출력: CutSoundAnalysis (with sound_source, boundary_flag)
  - UNKNOWN entity + low-confidence match 모두 기록
    │
    ▼
[Reconciliation Gate]  ← 범위 확장
  - unknown entities
  - low-confidence matches (< 0.7)
  - description conflicts
  - same-cut duplicate matches
    │
    ▼
[Agent C: Track Synthesizer]
  - sound_source 기반 구조적 병합
  - 출력: TrackManifest
    │
    ▼
[Final Validation]
  - 개정된 규칙 (likely_audible = warning)
    │
    ▼
Track Manifest (JSON)
```

### 1-2. Segment Preparation 상세 (신규)

**왜 segment 기반인가** (이슈 1 판단 근거):

| 기준 | Full video + time instruction | Segment 클리핑 |
|------|-------------------------------|-----------------|
| 컨텍스트 격리 | 약함 — 모델이 구간 밖 참조 가능 | 강함 — 물리적으로 해당 구간만 존재 |
| 비용 | 매 호출마다 전체 비디오 토큰 과금 | cut 길이 + 2s padding만 과금 |
| Rate limit | 동일 file_uri 반복 → 캐시 가능성 불확실 | 작은 파일 → 업로드 빠름 |
| 경계 문맥 | 없음 (시작/끝이 뚝 잘림) | ±1.0s padding으로 문맥 보존 |
| 구현 복잡도 | 낮음 | 중간 (ffmpeg 클리핑 + 업로드 추가) |

**결론**: 격리와 비용이 압도적으로 유리. 구현 복잡도 증가는 ffmpeg one-liner 수준이므로 감수 가능.

**구현**:

```python
async def prepare_segments(video_path: str, cuts: list[Cut], padding: float = 1.0) -> list[Segment]:
    segments = []
    duration = get_duration(video_path)  # ffprobe
    
    for cut in cuts:
        # padding 적용, 영상 범위 내로 clamp
        seg_start = max(0.0, cut.start_time - padding)
        seg_end = min(duration, cut.end_time + padding)
        
        # ffmpeg 클리핑 (re-encode 없이 copy, keyframe 정렬)
        seg_path = f"/tmp/segments/{cut.id}.mp4"
        await ffmpeg_clip(video_path, seg_start, seg_end, seg_path)
        
        # Gemini File API 업로드
        file_uri = await upload_to_gemini(seg_path)
        
        segments.append(Segment(
            cut_id=cut.id,
            file_uri=file_uri,
            seg_start=seg_start,
            seg_end=seg_end,
            target_start=cut.start_time,  # 실제 cut 범위
            target_end=cut.end_time,       # 실제 cut 범위
            padding_before=cut.start_time - seg_start,
            padding_after=seg_end - cut.end_time,
        ))
    
    return segments
```

**ffmpeg 클리핑 주의사항**:
- `-ss` before `-i`로 keyframe seek
- `-c copy`로 re-encode 회피 (속도)
- 단, copy 모드는 keyframe 단위로만 잘리므로 실제 segment 시작이 요청보다 약간 앞설 수 있음
- 정밀도가 필요하면 `-c:v libx264 -crf 23`으로 re-encode (느리지만 정확)
- MVP에서는 copy 모드로 시작, 문제 발생 시 re-encode로 전환

**비용 영향**: 60초 영상, 15 cuts 기준
- Full video 방식: 15 × 60s = 900s 분량 과금
- Segment 방식: 15 × (avg 6s + 2s padding) = ~120s 분량 과금
- **~87% 비용 절감**

---

## 2. Agent B 개정 — Segment 기반 + 구조적 출력

### 2-1. 호출 전략

```python
async def run_agent_b(segment: Segment, registry: EntityRegistry) -> CutSoundAnalysis:
    prompt = build_agent_b_prompt(
        cut_id=segment.cut_id,
        target_start=segment.target_start,
        target_end=segment.target_end,
        padding_before=segment.padding_before,
        padding_after=segment.padding_after,
        entity_registry=registry,
    )
    
    response = await gemini_pro.generate(
        file_uri=segment.file_uri,  # segment만 전달
        prompt=prompt,
        response_schema=CutSoundAnalysis,
    )
    
    # target range로 clamp
    return clamp_to_target_range(response, segment.target_start, segment.target_end)
```

### 2-2. Padding Window + Clamp 규칙 (이슈 4)

**프롬프트에 명시**:

```
이 비디오는 전체 영상의 일부입니다.
- 앞뒤에 약 {padding_before:.1f}초 / {padding_after:.1f}초의 여분 구간이 포함되어 있습니다.
- 여분 구간은 문맥 파악용입니다. 이벤트 기록은 target 범위인
  {target_start:.1f}초 ~ {target_end:.1f}초 내에서만 수행하세요.
- 단, target 경계에 걸친 이벤트는 다음 규칙을 따르세요:
  · continuous event가 경계를 넘으면: target 범위로 clamp하고 boundary_flag: true
  · onset event가 padding 구간에 있으면: 기록하지 마세요
```

**Clamp 후처리 (코드)**:

```python
def clamp_to_target_range(analysis: CutSoundAnalysis, t_start: float, t_end: float):
    for action in analysis.actions:
        if action.event_type == "continuous":
            action.start_time = max(action.start_time, t_start)
            action.end_time = min(action.end_time, t_end)
            if action.start_time == t_start or action.end_time == t_end:
                action.boundary_flag = True
        elif action.event_type == "onset":
            if not (t_start <= action.timestamp <= t_end):
                analysis.actions.remove(action)
    return analysis
```

### 2-3. Agent B 출력 스키마 개정 (이슈 3)

**핵심 변경**: `actions[]`에 `sound_source`, `surface_context`, `track_hint` 추가

```json
{
  "cut_id": "CUT_001",
  
  "characters_present": [
    { "entity_id": "CHAR_001", "match_confidence": 0.95 }
  ],
  "objects_present": [
    { "entity_id": "OBJ_001", "match_confidence": 0.60, "confidence_flag": "low" }
  ],
  "ambience_sources_present": ["AMB_001", "AMB_002"],
  
  "unknown_entities": [
    {
      "temp_id": "UNKNOWN_OBJ_CUT001_001",
      "type": "object",
      "label": "metal trash can",
      "visual_description": "dented aluminum cylinder, no lid",
      "material": "thin aluminum",
      "surface": "corrugated metal, oxidized",
      "reason": "not in entity registry"
    }
  ],
  
  "actions": [
    {
      "action_id": "ACT_CUT001_001",
      "linked_character_id": "CHAR_001",
      "linked_object_id": null,
      "action_label": "walking",
      "sound_source": "footstep_impact",
      "surface_context": "wet cobblestone, irregular, small puddles",
      "track_hint": "character_footsteps",
      "event_type": "continuous",
      "start_time": 0.0,
      "end_time": 4.2,
      "boundary_flag": false,
      "sfx_description": "soft rubber sole impacts on wet irregular stone surface, light splashing in shallow puddles",
      "confidence": 0.88
    },
    {
      "action_id": "ACT_CUT001_002",
      "linked_character_id": "CHAR_001",
      "linked_object_id": null,
      "action_label": "walking",
      "sound_source": "cloth_friction",
      "surface_context": "synthetic jacket fabric against cotton shirt",
      "track_hint": "character_cloth_rustle",
      "event_type": "continuous",
      "start_time": 0.0,
      "end_time": 4.2,
      "boundary_flag": false,
      "sfx_description": "rhythmic nylon jacket friction synced with arm swing",
      "confidence": 0.65
    },
    {
      "action_id": "ACT_CUT001_003",
      "linked_character_id": "CHAR_001",
      "linked_object_id": "OBJ_001",
      "action_label": "door_open",
      "sound_source": "mechanical_friction",
      "surface_context": "dry metal hinge, brass lever handle",
      "track_hint": "object_hinge_squeak",
      "event_type": "onset",
      "timestamp": 3.2,
      "boundary_flag": false,
      "sfx_description": "heavy oak door rotating on dry metal hinges, tonal squeak",
      "confidence": 0.82
    }
  ],
  
  "ambience_observations": [
    {
      "ambience_source_id": "AMB_001",
      "description_this_cut": "steady low traffic hum",
      "level_change": "stable"
    }
  ]
}
```

**새 필드 설명**:

| 필드 | 목적 | Agent C에서의 활용 |
|------|------|-------------------|
| `sound_source` | 소리의 물리적 발생 메커니즘 분류 | 동일 sound_source끼리 track 그룹핑 |
| `surface_context` | 소리가 발생하는 접촉면/매체 정보 | 같은 sound_source라도 surface가 다르면 별도 track |
| `track_hint` | Agent B가 제안하는 track 분류 | Agent C의 track 구성 시 1차 힌트 (강제 아님) |
| `boundary_flag` | 이 이벤트가 cut 경계에서 clamp되었는지 | 인접 cut의 동일 이벤트와 병합 후보 식별 |
| `confidence_flag` | match_confidence < 0.7이면 "low" | Reconciliation Gate 트리거 |

**sound_source 표준 분류 (Agent B 프롬프트에 제공)**:

```
# Impact / contact
footstep_impact, body_impact, object_impact, collision

# Friction
cloth_friction, mechanical_friction, surface_slide, scrape

# Mechanical
hinge_rotation, latch_click, lever_mechanism, motor_hum, gear_mesh

# Fluid
water_drip, water_flow, splash, pour

# Air / breath
breath, wind, whoosh, pneumatic

# Vocal (non-speech)
grunt, gasp, sigh, scream

# Electrical
buzz, hum, spark, beep

# Structural
creak, crack, shatter, crumble
```

이 분류는 **닫힌 목록이 아니라 가이드**. Agent B가 목록에 없는 sound_source를 생성해도 되지만, 가능하면 목록 내 용어를 사용하도록 프롬프트에서 유도한다.

---

## 3. Reconciliation Gate 개정 (이슈 2, 7)

### 3-1. 대상 범위 확장

v2에서는 unknown_entities만 처리했으나, v3에서는 **4가지 케이스**를 모두 처리한다.

| 케이스 | 트리거 조건 | 위험도 |
|--------|-------------|--------|
| Unknown entity | `temp_id`가 `UNKNOWN_*`으로 시작 | 중 |
| Low-confidence match | `match_confidence < 0.7` 또는 `confidence_flag: "low"` | **높** |
| Description conflict | 같은 entity_id인데 cut 간 `visual_description`이 크게 다름 | **높** |
| Same-cut duplicate | 같은 cut에서 동일 entity_id가 2회 이상 매칭 | 중 |

### 3-2. 다차원 매칭 스코어 (이슈 7)

v2의 label fuzzy match 단독 → v3에서는 **5개 시그널의 가중 합산**:

```python
def compute_match_score(unknown: UnknownEntity, candidate: Entity, cut: Cut) -> float:
    scores = {}
    
    # 1. Label similarity (fuzzy string match)
    scores["label"] = fuzz.token_sort_ratio(unknown.label, candidate.label) / 100
    
    # 2. Time overlap (unknown이 등장한 cut 시간 vs candidate의 entry_exit_intervals)
    overlap = compute_time_overlap(cut.start_time, cut.end_time, candidate.entry_exit_intervals)
    scores["time_overlap"] = 1.0 if overlap > 0 else 0.0
    
    # 3. Visual description similarity (sentence embedding cosine)
    scores["visual_sim"] = cosine_sim(
        embed(unknown.visual_description),
        embed(candidate.visual_description)
    )
    
    # 4. Material/mechanism match (object only)
    if unknown.type == "object" and hasattr(candidate, "material"):
        scores["material"] = fuzz.ratio(
            f"{unknown.material} {unknown.surface}",
            f"{candidate.material} {candidate.surface}"
        ) / 100
    else:
        scores["material"] = None  # N/A
    
    # 5. Linked background (같은 배경에 있으면 가산)
    scores["same_bg"] = 1.0 if shares_background(unknown, candidate, cut) else 0.0
    
    # 가중 합산 (material이 N/A면 나머지로 정규화)
    weights = {"label": 0.25, "time_overlap": 0.20, "visual_sim": 0.30, "material": 0.15, "same_bg": 0.10}
    
    active = {k: v for k, v in scores.items() if v is not None}
    total_weight = sum(weights[k] for k in active)
    
    return sum(scores[k] * weights[k] for k in active) / total_weight
```

### 3-3. 처리 흐름

```python
async def reconciliation_gate(
    cut_analyses: list[CutSoundAnalysis],
    registry: EntityRegistry
) -> EntityRegistry:
    
    # 1. 수집: 모든 reconciliation 대상
    candidates_for_review = []
    
    for analysis in cut_analyses:
        # Unknown entities
        for unk in analysis.unknown_entities:
            candidates_for_review.append(ReconciliationCase(
                type="unknown", entity=unk, cut_id=analysis.cut_id
            ))
        
        # Low-confidence matches
        for match in analysis.characters_present + analysis.objects_present:
            if match.match_confidence < 0.7:
                candidates_for_review.append(ReconciliationCase(
                    type="low_confidence", entity_id=match.entity_id,
                    confidence=match.match_confidence, cut_id=analysis.cut_id
                ))
        
        # Same-cut duplicates
        seen_ids = set()
        for match in analysis.characters_present + analysis.objects_present:
            if match.entity_id in seen_ids:
                candidates_for_review.append(ReconciliationCase(
                    type="duplicate", entity_id=match.entity_id, cut_id=analysis.cut_id
                ))
            seen_ids.add(match.entity_id)
    
    # Description conflicts (cross-cut 비교)
    for entity_id in registry.all_ids():
        descriptions = collect_descriptions_across_cuts(entity_id, cut_analyses)
        if description_variance(descriptions) > CONFLICT_THRESHOLD:
            candidates_for_review.append(ReconciliationCase(
                type="description_conflict", entity_id=entity_id,
                descriptions=descriptions
            ))
    
    # 2. 처리: 케이스별 분기
    for case in candidates_for_review:
        if case.type == "unknown":
            await handle_unknown(case, registry)     # 다차원 매칭 → merge or 승격
        elif case.type == "low_confidence":
            await handle_low_confidence(case, registry, cut_analyses)  # 재매칭 시도
        elif case.type == "duplicate":
            await handle_duplicate(case, registry, cut_analyses)       # 분리 또는 병합
        elif case.type == "description_conflict":
            await handle_conflict(case, registry)    # LLM 판단 → 분리 또는 설명 통합
    
    return registry
```

**Low-confidence match 처리 (이슈 2 핵심)**:

```python
async def handle_low_confidence(case, registry, cut_analyses):
    # 현재 매칭된 entity
    current_match = registry.get(case.entity_id)
    
    # 해당 cut에서의 visual 정보 수집
    cut_context = get_entity_context_in_cut(case.entity_id, case.cut_id, cut_analyses)
    
    # 다른 후보와 비교
    all_candidates = registry.get_by_type(current_match.type)
    scored = [(c, compute_match_score(cut_context, c, get_cut(case.cut_id)))
              for c in all_candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    
    best, best_score = scored[0]
    
    if best.id != case.entity_id and best_score > 0.80:
        # 더 나은 매칭 발견 → 재매칭
        reassign(case.cut_id, case.entity_id, best.id, cut_analyses)
    elif best_score < 0.50:
        # 매칭 자체가 의심 → LLM 판단
        decision = await ask_gemini_flash_for_matching(cut_context, scored[:3])
        apply_decision(decision, case, registry, cut_analyses)
    # else: 현재 매칭 유지 (best가 이미 현재 match이고 스코어도 나쁘지 않음)
```

---

## 4. Agent A 프롬프트 개정 (이슈 6)

**핵심 변경**: sound-relevant 필터 강화

```
당신은 전문 사운드 디자이너입니다.
이 비디오를 처음부터 끝까지 시간순으로 시청하고, 아래를 추출하세요.

## 중요 원칙: Sound-Relevant Entity만 수집

Entity를 등록할 때 "이 entity가 소리를 발생시키거나, 소리의 원인이 되거나,
소리 환경에 영향을 주는가?"를 판단하세요.

등록해야 하는 entity:
- 움직이는 인물 (footsteps, breathing, cloth rustle 등)
- 상호작용되는 물체 (문, 차량, 도구, 악기 등)
- 소리 환경을 형성하는 배경 (도로, 숲, 실내 공간 등)

등록하지 않는 entity:
- 정적 배경 소품 (벽에 걸린 그림, 멀리 보이는 건물)
- 화면에 스치듯 지나가는 무의미한 물체
- 텍스트, 자막, 그래픽 오버레이

판단이 애매하면 audibility를 "likely_audible"로 설정하고 등록하세요.
확실히 소리와 무관하면 등록하지 마세요.

## Part 1: Characters
(기존과 동일)

## Part 2: Objects
각 object에 대해 반드시 다음 필드를 채우세요:
- material: 주 재질 (예: oak wood, stainless steel, glass, rubber)
- surface: 표면 상태 (예: rough-grain, polished, wet, rusted)
- mechanism: 동작 메커니즘이 있으면 기술 (예: hinged with spring latch, rotating knob)

## Part 3: Backgrounds + Ambience Sources
(기존과 동일)

## Part 4: Cuts
shot boundary 기반으로 분할하세요. shot boundary란:
- hard cut (화면이 즉시 전환)
- dissolve, wipe, fade 등 점진적 전환
- 같은 shot 내 angle/composition 변화는 cut으로 분할하지 마세요.
  대신 해당 cut의 camera_notes에 기록하세요.

모든 시간은 0.2초 단위로 snap하세요.
```

**Cut 스키마 개정**: `camera_notes` 추가

```json
{
  "id": "CUT_003",
  "camera_angle": "medium shot, eye-level",
  "start_time": 8.4,
  "end_time": 14.6,
  "transition_in": "hard_cut",
  "transition_out": "dissolve",
  "camera_notes": "slow pan right at 10.0s, tilt up at 12.2s",
  "confidence": 0.90
}
```

---

## 5. Agent C 병합 규칙 개정 (이슈 3 반영)

### 5-1. 구조적 병합 (free-text 재해석 불필요)

v2에서는 Agent C가 sfx_description을 자유 텍스트로 읽고 병합했으나,
v3에서는 **Agent B가 제공한 구조 필드로 병합 가능**:

```python
# Agent C의 병합 판단 기준 (프롬프트가 아닌 코드 전처리로도 가능)

def should_merge(event_a: Action, event_b: Action) -> bool:
    """인접 cut의 두 이벤트를 하나의 track event로 병합할지 판단"""
    
    # 필수 조건: 모두 충족해야 병합
    same_source = event_a.sound_source == event_b.sound_source
    same_label = event_a.action_label == event_b.action_label
    same_entity = (event_a.linked_character_id == event_b.linked_character_id and
                   event_a.linked_object_id == event_b.linked_object_id)
    both_continuous = (event_a.event_type == "continuous" and
                       event_b.event_type == "continuous")
    time_adjacent = abs(event_a.end_time - event_b.start_time) <= 0.4
    
    # boundary_flag가 있으면 병합 우선
    has_boundary = event_a.boundary_flag or event_b.boundary_flag
    
    if same_source and same_label and same_entity and both_continuous and time_adjacent:
        return True
    if same_source and same_entity and has_boundary and time_adjacent:
        return True  # boundary에서는 action_label이 약간 달라도 병합 허용
    
    return False
```

### 5-2. Track 분리 기준

Agent C가 track을 생성할 때의 분리 기준:

```
같은 track에 묶는 조건:
  sound_source가 동일 AND
  linked entity가 동일 AND
  surface_context가 호환 가능 (같은 표면 또는 연속 변화)

별도 track으로 분리하는 조건:
  sound_source가 다름 (footstep vs cloth_friction)
  OR linked entity가 다름 (CHAR_001 vs CHAR_002의 footsteps)
  OR surface_context가 크게 다름 (cobblestone footsteps vs carpet footsteps)
```

### 5-3. Track Confidence 개정

```json
{
  "track_id": "TRK_001",
  "confidence": {
    "min": 0.65,
    "mean": 0.82,
    "support_count": 4
  }
}
```

- `min`: 구성 event 중 최저 confidence
- `mean`: 구성 event들의 평균 confidence
- `support_count`: 이 track을 구성하는 event 수 (많을수록 신뢰)

---

## 6. Final Validation 개정 (이슈 5)

### 통과 조건 (모두 만족 시 `complete`)

1. **스키마 정합성**: 전체 출력이 TrackManifest 스키마를 만족
2. **타임라인 커버리지**: 영상 전체 구간에서 ambience track이 없는 구간 ≤ 1.0s
3. **Entity 상태 완전성**: 모든 entity가 다음 중 **정확히 하나**를 만족
   - (a) `audibility`가 `audible` 또는 `likely_audible`이고, 최소 1개 track의 `entity_refs`에 포함됨
   - (b) `audibility`가 `audible` 또는 `likely_audible`이지만, `excluded_entities`에 명시적 사유와 함께 기록됨
   - (c) `audibility`가 `visual_only` 또는 `inactive`이고, `excluded_entities`에 기록됨
4. **Timestamp grid**: 모든 시간값이 0.2s 단위에 snap
5. **Onset 범위**: 모든 onset이 해당 entity의 entry_exit_intervals 범위 내
6. **Continuous 유효성**: 모든 continuous의 start_time < end_time
7. **Track consistency**: 모든 track의 events가 시간순으로 정렬되어 있고, continuous event 간 overlap 없음

### Warning 조건 (통과하되 경고 첨부)

| Warning | 조건 | 설명 |
|---------|------|------|
| `low_overall_confidence` | 전체 event 중 confidence < 0.5가 20% 이상 | 분석 품질 의심 |
| `likely_audible_untracked` | `likely_audible` entity가 track에도 excluded에도 없음 | **warning이지 failure 아님** |
| `excessive_tracks` | track 수 > 30 | 과세분화 의심 |
| `excessive_unknowns` | reconciliation 후에도 UNKNOWN 잔존 | registry 품질 의심 |
| `thin_support` | track의 support_count = 1이고 confidence.min < 0.6 | 단일 약근거 track |

**likely_audible 확정 정책 (이슈 5)**:
- `likely_audible`은 "소리가 날 수도 있지만 확실하지 않다"는 상태
- 이 상태의 entity가 track에 포함되지 않아도 **파이프라인은 통과**
- 단, warning `likely_audible_untracked`이 첨부되어 downstream에서 판단 가능
- **근거**: failure로 두면 Agent C가 억지로 track을 만들어 환각 위험 증가. 소리가 없을 수 있는 entity에 대해 "없다"고 판단하는 것도 유효한 결과임.

---

## 7. 개정된 Pydantic 스키마 요약

### Entity 스키마 (변경 없음, v2 유지)

Character, Object, Background, AmbienceSource — v2와 동일.

### Cut 스키마 (camera_notes 추가)

```python
class Cut(BaseModel):
    id: str                          # CUT_001
    camera_angle: str
    start_time: float                # 0.2s grid
    end_time: float                  # 0.2s grid
    transition_in: str               # none, hard_cut, dissolve, wipe, fade
    transition_out: str
    camera_notes: str | None = None  # 신규: shot 내 구도 변화 기록
    confidence: float
```

### Action 스키마 (v3 신규 필드 포함)

```python
class Action(BaseModel):
    action_id: str                       # ACT_CUT001_001
    linked_character_id: str | None
    linked_object_id: str | None
    action_label: str                    # walking, door_open, etc
    sound_source: str                    # 신규: footstep_impact, cloth_friction, etc
    surface_context: str                 # 신규: wet cobblestone, dry metal hinge
    track_hint: str                      # 신규: character_footsteps, object_hinge_squeak
    event_type: Literal["onset", "continuous"]
    timestamp: float | None = None       # onset용
    start_time: float | None = None      # continuous용
    end_time: float | None = None        # continuous용
    boundary_flag: bool = False          # 신규: cut 경계 clamp 여부
    sfx_description: str
    confidence: float
```

### Track 스키마 (confidence 확장)

```python
class TrackConfidence(BaseModel):
    min: float
    mean: float
    support_count: int

class TrackEvent(BaseModel):
    event_type: Literal["onset", "continuous"]
    timestamp: float | None = None
    start_time: float | None = None
    end_time: float | None = None
    description: str
    confidence: float
    boundary_merged: bool = False   # 경계 병합된 이벤트인지

class Track(BaseModel):
    track_id: str                   # TRK_001
    track_type: Literal["sfx", "ambience"]
    track_label: str                # "Woman footsteps — wet cobblestone"
    sound_source: str               # footstep_impact
    entity_refs: list[EntityRef]    # [{"id": "CHAR_001", "role": "actor"}]
    sound_description: str
    events: list[TrackEvent]
    confidence: TrackConfidence      # 개정: min + mean + support_count

class ExcludedEntity(BaseModel):
    entity_id: str
    label: str
    audibility: str
    reason: str

class TrackManifest(BaseModel):
    tracks: list[Track]
    excluded_entities: list[ExcludedEntity]
```

---

## 8. 비용 재추정 (Segment 방식 반영)

### 60초 영상, 15 cuts, 평균 cut 길이 4초 기준

| 단계 | 호출 수 | 모델 | 입력 | 예상 비용 |
|------|---------|------|------|-----------|
| Agent A | 1 | pro | 60s video + ~2K prompt | ~$0.15 |
| Segment prep | 0 | - | ffmpeg local | $0 |
| Segment upload | 15 | File API | avg 6s × 15 = 90s | ~$0.01 |
| Agent B | 15 | pro | avg 6s video + ~3K prompt each | ~$0.25 |
| Reconciliation | 0-5 | flash | text only | ~$0.02 |
| Agent C | 1 | flash | ~10K text | ~$0.03 |
| **합계** | | | | **~$0.46** |

v2 대비 ~27% 절감. $1 목표 대비 충분한 여유.

---

## 9. 에러 처리 개정 (이슈 7 일부)

v2의 에러 타입에 추가:

| 에러 타입 | 발생 지점 | 재시도 대상 | 최대 횟수 |
|-----------|-----------|-------------|-----------|
| `segment_prep_error` | Segment Preparation | ffmpeg 재시도 또는 re-encode fallback | 2 |
| `reconciliation_conflict` | Reconciliation Gate | Gemini Flash로 판단 위임 | 1 |
| `low_confidence_cluster` | Reconciliation Gate | Agent A 해당 구간 재확인 | 1 |
| (기존 에러 타입들) | (v2와 동일) | (v2와 동일) | (동일) |

---

## 10. MVP 구현 경로 (개정)

### Phase 0: 인프라 + 스키마 (1-2일)

```
- Pydantic v2 모델 전체 작성
- Gemini API wrapper (file upload, generate with schema)
- ffmpeg segment 추출 유틸리티
- 테스트 비디오 5개 확보 (다양한 장르: 대화, 액션, 풍경, 실내, 야외)
```

### Phase 1: Agent A 검증 (2-3일) — 가설 1

```
목표: Gemini가 entity + cut을 한 번에 추출할 수 있는가?
구현: Agent A 프롬프트 + GlobalAnalysis 스키마 + Validation Gate
성공 기준:
  - entity 재현율 > 80% (수동 라벨 대비)
  - cut 경계 오차 < 0.5s
  - 불필요 entity 비율 < 20% (sound-relevance 필터 효과)
실패 시:
  - Agent A를 entity 전용 + cut 전용으로 분리
  - pyscenedetect를 cut detection 주력으로 격상
```

### Phase 2: Agent B + Segment (3-4일) — 가설 2

```
목표: segment 기반 fan-out이 일관된 분석을 생성하는가?
구현: Segment Preparation + Agent B (3-5개 cut만) + clamp 로직
성공 기준:
  - entity ID 매칭 정확도 > 85%
  - unknown 비율 < 15%
  - sound_source 분류 일관성 (같은 행위에 같은 source)
  - boundary_flag 정확성
실패 시:
  - padding 늘림 (±1.0 → ±2.0)
  - sound_source 분류를 닫힌 목록으로 강제
```

### Phase 3: Reconciliation + Agent C (2-3일) — 가설 3

```
목표: sound_source 기반 track 분리가 의미 있는가?
구현: Reconciliation Gate + Agent C + Final Validation
성공 기준:
  - 동일 entity에서 2개 이상 track이 분리되는 비율 > 50%
  - track 과세분화 (30+) 발생하지 않음
  - excluded_entities가 적절히 활용됨
실패 시:
  - track_hint를 강제 분류로 전환
  - Agent C 대신 규칙 기반 병합 엔진으로 교체
```

### Phase 4: 통합 + 안정화 (2-3일)

```
- 전체 파이프라인 asyncio 오케스트레이션
- 에러 처리 + retry 정책 구현
- 비용 모니터링 (per-call token tracking)
- 10개 비디오로 end-to-end 테스트
- 결과 품질 리뷰 + 프롬프트 튜닝
```

---

## 11. 리스크 레지스터 (개정)

| 리스크 | 확률 | 영향 | 대응 방안 |
|--------|------|------|-----------|
| Gemini timestamp 오차 > 0.5s | 중 | 높 | 0.2s grid snap으로 양자화, Phase 1에서 정확도 측정 후 grid 조정 |
| Fan-out rate limit 초과 | 중 | 중 | max_concurrency=5, exponential backoff, segment 크기로 우선순위 정렬 |
| Entity matching 실패율 > 20% | 중 | 높 | Reconciliation 다차원 매칭 + LLM fallback, 실패 시 Agent A few-shot 예시 강화 |
| Track 과세분화 (30+) | 낮 | 중 | Agent C 프롬프트에 track 수 상한 가이드 + Final Validation warning |
| Gemini structured output 미지원 필드 | 중 | 중 | response_schema 미지원 시 JSON 프롬프트 강제 + 후처리 파싱 |
| Segment 클리핑 keyframe misalignment | 중 | 낮 | copy 모드 → re-encode fallback, padding이 오차 흡수 |
| sound_source 분류 불일치 (cut 간) | 중 | 중 | 표준 분류 목록 제공 + Agent C에서 정규화 |
| 비용 폭증 (cut 수 > 20) | 낮 | 중 | Agent A에서 minor cut 병합 후처리, cut 수 상한 설정 |

---

## 12. 디렉토리 구조 제안

```
sound-pipeline/
├── README.md
├── pyproject.toml
├── src/
│   ├── __init__.py
│   ├── schemas/                    # Pydantic 모델
│   │   ├── entities.py             # Character, Object, Background, AmbienceSource
│   │   ├── cuts.py                 # Cut, Segment
│   │   ├── analysis.py             # CutSoundAnalysis, Action, AmbienceObservation
│   │   ├── tracks.py               # Track, TrackManifest, ExcludedEntity
│   │   └── pipeline.py             # PipelineResult, CostSummary
│   ├── agents/
│   │   ├── base.py                 # GeminiAgent 베이스 클래스
│   │   ├── global_analyzer.py      # Agent A
│   │   ├── cut_analyst.py          # Agent B
│   │   └── track_synthesizer.py    # Agent C
│   ├── gates/
│   │   ├── validation.py           # Validation Gate
│   │   ├── reconciliation.py       # Reconciliation Gate
│   │   └── final_validation.py     # Final Validation
│   ├── preprocessing/
│   │   ├── metadata.py             # ffprobe wrapper
│   │   ├── segment.py              # ffmpeg 클리핑 + Segment Preparation
│   │   └── scene_detect.py         # pyscenedetect wrapper
│   ├── utils/
│   │   ├── gemini_client.py        # Gemini API wrapper (upload, generate)
│   │   ├── timestamp.py            # grid snap, clamp 유틸
│   │   └── matching.py             # fuzzy match, embedding similarity
│   └── pipeline.py                 # 전체 오케스트레이션 (asyncio)
├── prompts/
│   ├── agent_a_system.txt
│   ├── agent_a_user_template.txt
│   ├── agent_b_system.txt
│   ├── agent_b_user_template.txt
│   ├── agent_c_system.txt
│   └── agent_c_user_template.txt
├── tests/
│   ├── fixtures/                   # 테스트 비디오, mock 응답
│   ├── test_schemas.py
│   ├── test_agent_a.py
│   ├── test_agent_b.py
│   ├── test_reconciliation.py
│   ├── test_agent_c.py
│   └── test_pipeline_e2e.py
└── scripts/
    ├── run_pipeline.py             # CLI 진입점
    └── eval_quality.py             # 결과 품질 평가 스크립트
```

---

## 13. 검토 요청 사항 (구현 담당자용)

이 문서를 받은 Claude Code / Codex에게:

1. **Phase 0 즉시 착수**: `src/schemas/` 전체를 Pydantic v2로 작성하라
2. **Agent A 프롬프트 구체화**: `prompts/agent_a_*.txt`를 Gemini API 호출 가능한 형태로 작성하라
3. **Segment Preparation 구현**: `src/preprocessing/segment.py`를 ffmpeg 기반으로 구현하라
4. **Phase 1 테스트 하네스**: Agent A의 출력을 수동 라벨과 비교하는 `scripts/eval_quality.py` 초안
5. **리스크 레지스터의 `?` 항목**: 위 표의 대응 방안을 구체적 코드/설정 수준으로 세분화하라
6. **asyncio 오케스트레이션**: `src/pipeline.py`의 fan-out/gather 패턴 초안을 제안하라
