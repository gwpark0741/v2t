# Gemini 비디오 사운드 디자인 파이프라인 — v2 아키텍처 (리뷰 반영)

> **문서 목적**: 사전 리뷰(9개 이슈)를 반영하여 재설계한 파이프라인 아키텍처.
> Claude Code / Codex에게 전달하여 구현 검토 및 계획 수립을 요청하기 위한 핸드오프 문서.

---

## 0. 변경 요약

| # | 리뷰 이슈 | 변경 내용 |
|---|-----------|-----------|
| 1 | track = entity가 너무 거침 | track 기준을 `sound source/layer`로 재정의, `entity_refs[]`로 연결 |
| 2 | unknown entity 경로 없음 | Agent 3에 `UNKNOWN_*` 허용 + reconciliation 단계 추가 |
| 3 | background 모델링이 단순함 | `background`(시각)과 `ambience_source`(사운드) 분리 |
| 4 | timestamp 정책 모호 | 0.2s grid, confidence 필드, 병합 규칙 명시 |
| 5 | Agent 3 출력 스키마 약함 | typed arrays 분리, material/surface 구조 필드, action 구조화 |
| 6 | Agent 1+2 병렬의 비용 문제 | MVP에서 Global Video Analyzer로 통합 |
| 7 | retry 전략 단순 | 에러 타입별 재시도 정책 분리 |
| 8 | 전수 entity 강제 포함 → 환각 | entity 상태(`audible`/`visual_only` 등) 도입 |
| 9 | 샘플링 전략 단순 | Gemini 네이티브 비디오 입력 중심 + preprocessing은 보조 |

---

## 1. 개정된 파이프라인 아키텍처

### 1-1. 전체 흐름 (v2)

```
Video Input (10-60s, no audio)
    │
    ▼
[Preprocessing]
  - ffprobe 메타데이터 (fps, duration, resolution)
  - Gemini File API 업로드 (1회, file_uri 획득)
  - pyscenedetect로 scene_change_candidates 추출 (보조 힌트)
    │
    ▼
[Agent A: Global Video Analyzer]  ← Agent 1+2 통합
  - 전체 영상 1회 시청
  - entities (character, object) 추출
  - backgrounds (시각) + ambience_sources (사운드) 추출
  - cuts 분할
  - 출력: GlobalAnalysis
    │
    ▼
[Validation Gate]
  - 스키마 검증
  - 시간 정합성 검증
  - ambience_source ↔ background 매핑 검증
    │
    ├─── 실패 시: 에러 타입별 retry (최대 2회)
    │
    ▼
[Agent B: Per-Cut Sound Analyst]  ← fan-out (cut 수만큼 병렬)
  - 입력: cut 구간 비디오 + entity registry + ambience_source registry
  - 출력: CutSoundAnalysis (per cut)
  - unknown entity 허용 (UNKNOWN_CHAR_CUT003 등)
    │
    ▼
[Reconciliation Gate]  ← 신규 추가
  - unknown entity들을 기존 registry와 매칭 시도
  - 매칭 불가 시 신규 entity로 승격
  - 필요 시 Agent A에게 해당 구간 재확인 요청
    │
    ▼
[Agent C: Track Synthesizer]  ← 역할 재정의
  - 입력: 전체 entity registry + 전체 cut 분석 결과
  - track 기준: sound source / sound layer (entity 단위가 아님)
  - 한 entity에서 여러 track 생성 가능
  - 출력: TrackManifest
    │
    ▼
[Final Validation]
  - 스키마 검증
  - 타임라인 커버리지 (ambience track 기준)
  - entity 상태 검증 (audible/visual_only — 전수 포함 강제 아님)
  - timestamp grid 정합성 (0.2s snap)
    │
    ▼
Sound Track Metadata (JSON)
```

### 1-2. Agent 통합 근거 (Agent 1+2 → Agent A)

| 기준 | 분리 (Agent 1 + Agent 2) | 통합 (Global Video Analyzer) |
|------|--------------------------|------------------------------|
| Gemini 비디오 업로드 | 2회 (비용 2배) | 1회 |
| 컨텍스트 일관성 | 각자 다른 시점에서 해석 가능 | 동일 시청에서 일관된 해석 |
| 프롬프트 복잡도 | 각각 단순 | 통합 시 다소 복잡 |
| Rate limit 부담 | 높음 | 낮음 |
| MVP 적합성 | 낮음 | 높음 |
| 스케일 시 분리 가능성 | - | 추후 분리 용이 (입출력 스키마 동일) |

**결론**: MVP에서는 통합. 출력 스키마를 entity 파트와 cut 파트로 명확히 분리해두면, 추후 독립 Agent로 쪼개는 것은 스키마 변경 없이 가능하다.

### 1-3. 모델 배정

| Agent | 모델 | 근거 |
|-------|------|------|
| Agent A: Global Video Analyzer | gemini-2.5-pro | 전체 영상 이해 + 복합 구조 추출 |
| Agent B: Per-Cut Sound Analyst | gemini-2.5-pro | 세밀한 action/SFX 분석 필요 |
| Agent C: Track Synthesizer | gemini-2.5-flash | 텍스트 기반 병합/재구성, 비디오 불필요 |

---

## 2. 개정된 스키마

### 2-1. Timestamp 정책

```
timestamp_grid: 0.2s (모든 시간값은 0.2s 단위로 snap)
onset: { timestamp: float }  — 단일 시점
continuous: { start_time: float, end_time: float }  — 구간
confidence: float (0.0 ~ 1.0)  — 모든 시간 관련 필드에 동반
```

**병합 규칙 (Agent C)**:
- 동일 entity + 동일 action_label + 인접 cut의 continuous event
- 선행 cut의 end_time과 후행 cut의 start_time 차이가 ≤ 0.4s이면 병합
- 행위가 다르면 (action_label 불일치) 별도 event 유지

### 2-2. Entity 스키마

#### Character

```json
{
  "id": "CHAR_001",
  "label": "young woman in red jacket",
  "visual_description": "shoulder-length black hair, red jacket, white sneakers",
  "entry_exit_intervals": [
    { "start": 0.0, "end": 12.4 },
    { "start": 28.0, "end": 35.6 }
  ],
  "global_action_summary": "walks through alley, opens door, enters building",
  "audibility": "audible",
  "confidence": 0.92
}
```

#### Object

```json
{
  "id": "OBJ_001",
  "label": "wooden door",
  "visual_description": "brown oak door with brass handle",
  "material": "oak wood",
  "surface": "rough-grain wood, painted",
  "mechanism": "hinged, lever handle with spring latch",
  "entry_exit_intervals": [
    { "start": 3.0, "end": 8.0 }
  ],
  "audibility": "audible",
  "confidence": 0.88
}
```

#### Background (시각 식별 전용)

```json
{
  "id": "BG_001",
  "label": "urban alley at night",
  "visual_description": "narrow alleyway, wet cobblestone, dim streetlight",
  "entry_exit_intervals": [
    { "start": 0.0, "end": 15.0 }
  ],
  "linked_ambience_sources": ["AMB_001", "AMB_002", "AMB_003"],
  "confidence": 0.95
}
```

#### Ambience Source (사운드 레이어 전용) — 신규

```json
{
  "id": "AMB_001",
  "label": "distant traffic hum",
  "category": "urban_traffic",
  "space_description": "open sky above narrow canyon-like walls, sound arriving from street ends",
  "distance_profile": "far (50-100m), diffuse",
  "tonal_quality": "low rumble, constant, no distinct vehicles",
  "linked_background_id": "BG_001",
  "entry_exit_intervals": [
    { "start": 0.0, "end": 15.0 }
  ],
  "confidence": 0.85
}
```

**background와 ambience_source의 관계**:
- background 1개 → ambience_source N개 (1:N)
- background는 시각적으로 "어디인가"를 식별
- ambience_source는 "그 장소에서 들리는 소리 층"을 각각 분리
- 예: "urban alley at night" (BG_001) → distant traffic (AMB_001) + dripping water (AMB_002) + wind through passage (AMB_003)

#### Entity 상태 (audibility)

```
audible         — 명확한 소리 발생원
likely_audible  — 소리가 날 가능성 있으나 불확실
visual_only     — 화면에 보이지만 소리 발생원 아님 (배경 건물, 정적 소품 등)
inactive        — 등장하지만 활동 없음 (앉아있는 인물 등)
```

### 2-3. Cut 스키마

```json
{
  "id": "CUT_001",
  "camera_angle": "medium shot, eye-level",
  "start_time": 0.0,
  "end_time": 4.2,
  "transition_in": "none",
  "transition_out": "hard_cut",
  "confidence": 0.90
}
```

### 2-4. Cut-Level Analysis 스키마 (Agent B 출력)

```json
{
  "cut_id": "CUT_001",
  "characters_present": [
    {
      "entity_id": "CHAR_001",
      "match_confidence": 0.95
    }
  ],
  "objects_present": [
    {
      "entity_id": "OBJ_001",
      "match_confidence": 0.90
    }
  ],
  "ambience_sources_present": ["AMB_001", "AMB_002"],
  "unknown_entities": [
    {
      "temp_id": "UNKNOWN_OBJ_CUT001_001",
      "type": "object",
      "label": "metal trash can",
      "visual_description": "dented aluminum cylinder, no lid",
      "material": "thin aluminum",
      "surface": "corrugated metal",
      "reason": "not in entity registry"
    }
  ],
  "actions": [
    {
      "action_id": "ACT_CUT001_001",
      "linked_character_id": "CHAR_001",
      "linked_object_id": null,
      "action_label": "walking",
      "event_type": "continuous",
      "start_time": 0.0,
      "end_time": 4.2,
      "sfx_description": "soft rubber sole impacts on wet irregular stone surface, light splashing in shallow puddles",
      "confidence": 0.88
    },
    {
      "action_id": "ACT_CUT001_002",
      "linked_character_id": "CHAR_001",
      "linked_object_id": "OBJ_001",
      "action_label": "door_open",
      "event_type": "onset",
      "timestamp": 3.2,
      "sfx_description": "heavy oak door rotating on dry metal hinges, brass lever handle depressed and released",
      "confidence": 0.82
    }
  ],
  "ambience_observations": [
    {
      "ambience_source_id": "AMB_001",
      "description_this_cut": "steady low traffic hum, slightly louder as camera faces street end",
      "level_change": "stable"
    },
    {
      "ambience_source_id": "AMB_002",
      "description_this_cut": "intermittent water drip, 2-3 drops audible in this segment",
      "level_change": "stable"
    }
  ]
}
```

### 2-5. Final Track 스키마 (Agent C 출력) — 핵심 변경

```json
{
  "tracks": [
    {
      "track_id": "TRK_001",
      "track_type": "sfx",
      "track_label": "Woman footsteps — wet cobblestone",
      "sound_source": "footstep_impact",
      "entity_refs": [
        { "id": "CHAR_001", "role": "actor" }
      ],
      "sound_description": "soft rubber sole footsteps on wet cobblestone, moderate walking pace, light puddle splashes on impact",
      "events": [
        {
          "event_type": "continuous",
          "start_time": 0.0,
          "end_time": 12.4,
          "description": "walking on wet cobblestone, steady pace",
          "confidence": 0.88
        },
        {
          "event_type": "continuous",
          "start_time": 28.0,
          "end_time": 33.2,
          "description": "walking resumes after door interaction, same surface",
          "confidence": 0.80
        }
      ]
    },
    {
      "track_id": "TRK_002",
      "track_type": "sfx",
      "track_label": "Woman cloth rustle",
      "sound_source": "cloth_movement",
      "entity_refs": [
        { "id": "CHAR_001", "role": "actor" }
      ],
      "sound_description": "synthetic jacket fabric friction, correlated with arm swing during walk",
      "events": [
        {
          "event_type": "continuous",
          "start_time": 0.0,
          "end_time": 12.4,
          "description": "rhythmic jacket rustle synced with walking",
          "confidence": 0.70
        }
      ]
    },
    {
      "track_id": "TRK_003",
      "track_type": "sfx",
      "track_label": "Door — hinge squeak",
      "sound_source": "mechanical_friction",
      "entity_refs": [
        { "id": "OBJ_001", "role": "source" },
        { "id": "CHAR_001", "role": "actor" }
      ],
      "sound_description": "dry metal hinge rotating under load, tonal squeak",
      "events": [
        {
          "event_type": "onset",
          "timestamp": 3.2,
          "description": "door opens, hinge squeak ~0.8s duration",
          "confidence": 0.82
        }
      ]
    },
    {
      "track_id": "TRK_004",
      "track_type": "sfx",
      "track_label": "Door — latch mechanism",
      "sound_source": "mechanical_click",
      "entity_refs": [
        { "id": "OBJ_001", "role": "source" }
      ],
      "sound_description": "brass spring latch retracting and releasing against metal strike plate",
      "events": [
        {
          "event_type": "onset",
          "timestamp": 3.0,
          "description": "handle depressed, latch retracts",
          "confidence": 0.75
        },
        {
          "event_type": "onset",
          "timestamp": 7.8,
          "description": "door closes, latch catches in strike plate",
          "confidence": 0.78
        }
      ]
    },
    {
      "track_id": "TRK_005",
      "track_type": "ambience",
      "track_label": "Distant traffic bed",
      "sound_source": "environment_traffic",
      "entity_refs": [
        { "id": "AMB_001", "role": "source" },
        { "id": "BG_001", "role": "environment" }
      ],
      "sound_description": "constant low-frequency urban traffic rumble, diffuse, no distinct vehicles",
      "events": [
        {
          "event_type": "continuous",
          "start_time": 0.0,
          "end_time": 15.0,
          "description": "steady traffic bed throughout alley sequence",
          "confidence": 0.85
        }
      ]
    },
    {
      "track_id": "TRK_006",
      "track_type": "ambience",
      "track_label": "Water drip",
      "sound_source": "environment_water",
      "entity_refs": [
        { "id": "AMB_002", "role": "source" },
        { "id": "BG_001", "role": "environment" }
      ],
      "sound_description": "intermittent single water drops hitting stone surface, slight reverb from walls",
      "events": [
        {
          "event_type": "continuous",
          "start_time": 0.0,
          "end_time": 15.0,
          "description": "sporadic drips, ~2-3 per second, random timing",
          "confidence": 0.72
        }
      ]
    }
  ],
  "excluded_entities": [
    {
      "entity_id": "OBJ_003",
      "label": "brick wall",
      "audibility": "visual_only",
      "reason": "static background element, no interaction or sound emission"
    }
  ]
}
```

**track 설계 핵심 변경점**:
- `track = sound source/layer`이지, `track = entity`가 아님
- 한 entity(CHAR_001)에서 footsteps(TRK_001) + cloth rustle(TRK_002) 등 복수 track 생성
- 한 track에 복수 entity 참조 가능 (door hinge → OBJ_001 + CHAR_001)
- `excluded_entities`로 소리 없는 엔티티를 명시적으로 제외

---

## 3. Agent별 상세 설계

### 3-1. Agent A: Global Video Analyzer

**입력**:
- Gemini File API로 업로드된 video file_uri
- preprocessing 메타데이터 (duration, fps, scene_change_candidates)

**프롬프트 설계 핵심**:

```
당신은 전문 사운드 디자이너입니다.
이 비디오를 처음부터 끝까지 시간순으로 시청하고, 아래를 추출하세요.

[Part 1: Entities]
새로운 인물, 물체, 배경이 등장할 때마다 즉시 기록하세요.
- 이미 기록된 entity가 재등장하면 entry_exit_intervals에 새 구간을 추가하세요.
- 각 entity의 audibility를 판단하세요.
- object는 material, surface, mechanism 필드를 반드시 채우세요.

[Part 2: Ambience Sources]
각 background(시각적 배경)에서 들릴 것으로 예상되는 소리 층을 분리하세요.
- 하나의 배경에서 여러 ambience_source가 나올 수 있습니다.
- 각 소스의 distance_profile과 tonal_quality를 기술하세요.

[Part 3: Cuts]
화면 전환(hard cut, dissolve, wipe)과 구도 전환(angle change)을 모두 감지하세요.
- scene_change_candidates를 참고하되, 그것만 의존하지 마세요.
- 모든 시간은 0.2초 단위로 snap하세요.

출력은 반드시 아래 JSON 스키마를 따르세요.
(스키마 삽입)
```

**출력 타입**: `GlobalAnalysis`

```python
class GlobalAnalysis(BaseModel):
    characters: list[Character]
    objects: list[Object]
    backgrounds: list[Background]
    ambience_sources: list[AmbienceSource]
    cuts: list[Cut]
    video_duration: float
```

### 3-2. Agent B: Per-Cut Sound Analyst

**입력 (per instance)**:
- 원본 video file_uri + cut의 start_time/end_time (프롬프트에서 구간 지정)
- entity_registry: Agent A의 characters + objects (참조 사전)
- ambience_registry: Agent A의 ambience_sources (참조 사전)
- cut 메타데이터

**프롬프트 설계 핵심**:

```
이 비디오의 {start_time}초 ~ {end_time}초 구간만 분석하세요.

[Entity 매칭]
아래 entity registry에서 이 구간에 등장하는 것을 찾으세요.
매칭이 확실하면 해당 ID를 사용하세요.
매칭이 불확실하면 match_confidence를 낮게 설정하세요.
registry에 없는 entity가 보이면, UNKNOWN_{TYPE}_CUT{NNN}_{SEQ} 형식으로 임시 ID를 부여하고
unknown_entities에 기록하세요.

[Action 추적]
모든 소리를 발생시키는 행위를 추적하세요.
- onset: 순간적 사건 (문 닫힘, 발 구르기, 충돌) → timestamp
- continuous: 지속적 사건 (걷기, 물 흐름, 기계 작동) → start_time, end_time
- 모든 시간은 0.2초 단위로 snap하세요.

[SFX Description]
각 action에 대해: 객체, 행위, 재질, 표면을 포함한 구체적 SFX 묘사를 작성하세요.

[Ambience Observation]
이 구간에서 각 ambience_source가 어떻게 들리는지 관찰하세요.
레벨 변화(stable, increasing, decreasing, intermittent)를 명시하세요.

(entity_registry JSON 삽입)
(ambience_registry JSON 삽입)
```

**출력 타입**: `CutSoundAnalysis`

**fan-out 전략**:
- 전체 비디오를 1회 File API 업로드 → file_uri 재사용
- 각 cut에 대해 프롬프트에서 시간 구간을 지정 (비디오 재업로드 불필요)
- 동시 호출 수 제한: max_concurrency = 5 (Gemini rate limit 고려)
- 실패한 cut만 개별 retry

### 3-3. Reconciliation Gate — 신규

**트리거**: Agent B 결과 수집 후, unknown_entities가 1개 이상 존재할 때

**처리 로직 (코드 기반 + 선택적 LLM 호출)**:

```python
for unknown in all_unknown_entities:
    # 1차: 규칙 기반 매칭
    candidates = fuzzy_match(unknown.label, entity_registry, threshold=0.7)
    
    if len(candidates) == 1 and candidates[0].score > 0.85:
        # 자동 병합
        merge(unknown, candidates[0])
    elif len(candidates) > 0:
        # 2차: LLM 판단 요청 (visual_description 비교)
        decision = ask_gemini_flash(unknown, candidates)
        if decision.action == "merge":
            merge(unknown, decision.target)
        else:
            promote_to_new_entity(unknown)
    else:
        # 완전 신규
        promote_to_new_entity(unknown)
```

**신규 entity 승격 시**:
- 새 ID 발급 (CHAR_NNN, OBJ_NNN)
- entity_registry에 추가
- audibility 판단 수행

### 3-4. Agent C: Track Synthesizer — 역할 재정의

**핵심 변경**: entity 병합기 → sound layer 구성기

**입력**:
- 최종 entity_registry (reconciliation 완료)
- 전체 cut 분석 결과

**프롬프트 설계 핵심**:

```
당신은 전문 사운드 디자이너입니다.
아래 cut별 분석 결과를 바탕으로, 최종 사운드 트랙을 구성하세요.

[트랙 구성 원칙]
- track = entity가 아닙니다. track = sound source 또는 sound layer입니다.
- 하나의 character에서 footsteps, cloth rustle, breathing 등 별도 track을 생성하세요.
- 하나의 object에서 hinge squeak, impact, latch click 등 별도 track을 생성하세요.
- 각 ambience_source는 독립 track이 됩니다.
- track에는 관련 entity_refs[]를 연결하세요.

[이벤트 병합 규칙]
- 동일 sound_source + 동일 action_label의 continuous event가
  인접 cut에서 시간 차이 ≤ 0.4s이면 하나의 event로 병합하세요.
- onset event는 병합하지 마세요.
- 병합 시 confidence는 구성 event 중 최저값을 사용하세요.

[제외 처리]
- audibility가 visual_only 또는 inactive인 entity는 track을 생성하지 마세요.
- 대신 excluded_entities에 이유와 함께 기록하세요.

(entity_registry JSON 삽입)
(전체 cut 분석 결과 JSON 삽입)
```

**출력 타입**: `TrackManifest`

**모델**: gemini-2.5-flash (비디오 입력 없음, 텍스트 기반 재구성이므로 flash로 충분)

---

## 4. 에러 처리 및 Retry 정책

### 4-1. 에러 타입 분류

| 에러 타입 | 발생 지점 | 재시도 대상 | 최대 횟수 |
|-----------|-----------|-------------|-----------|
| `schema_error` | 모든 Agent | 동일 Agent 재호출 | 2 |
| `timestamp_error` | Validation Gate | 동일 Agent 재호출 (시간 보정 힌트 포함) | 2 |
| `cut_boundary_error` | Validation Gate | Agent A 재실행 (cut 파트만) | 1 |
| `entity_matching_error` | Agent B | 해당 cut만 재분석 | 2 |
| `unknown_reconciliation_fail` | Reconciliation | Gemini Flash로 판단 위임 | 1 |
| `rate_limit_error` | 모든 API 호출 | exponential backoff | 3 |
| `persistent_failure` | 어디든 | 부분 결과 보존 + `unresolved` 플래그 | 0 |

### 4-2. 부분 결과 보존

```python
class PipelineResult(BaseModel):
    status: Literal["complete", "partial", "failed"]
    tracks: list[Track]                    # 성공한 track들
    unresolved_cuts: list[str]             # 분석 실패한 cut ID들
    unresolved_entities: list[str]         # reconciliation 실패한 entity ID들
    warnings: list[str]                    # 비치명적 경고
    cost_summary: CostSummary              # API 호출 비용 추적
```

---

## 5. Final Validation 개정

### 통과 조건 (모두 만족 시 `complete`)

1. **스키마 정합성**: 전체 출력이 TrackManifest 스키마를 만족
2. **타임라인 커버리지**: 영상 전체 구간(0 ~ duration)에서 ambience track이 존재하지 않는 구간이 1.0s 이하
3. **Entity 상태 완전성**: 모든 entity가 다음 중 하나를 만족
   - 최소 1개 track에서 entity_refs로 참조됨 (audible/likely_audible)
   - excluded_entities에 이유와 함께 기록됨 (visual_only/inactive)
4. **Timestamp grid**: 모든 시간값이 0.2s 단위에 snap되어 있음
5. **Onset 범위**: 모든 onset timestamp가 해당 entity의 entry_exit_intervals 범위 내
6. **Continuous 유효성**: 모든 continuous event의 start_time < end_time

### 경고 조건 (통과하되 warning 첨부)

- confidence < 0.5인 event가 전체의 20% 이상
- 단일 track의 event 수가 30개 이상 (과도한 세분화 의심)
- likely_audible entity가 어떤 track에도 참조되지 않음

---

## 6. 비용 추정

### 60초 영상 기준 (worst case)

| 단계 | 호출 수 | 모델 | 예상 토큰 | 예상 비용 |
|------|---------|------|-----------|-----------|
| Agent A | 1 | pro | ~8K out | ~$0.15 |
| Agent B | ~15 cuts | pro | ~3K out × 15 | ~$0.45 |
| Reconciliation | 0-3 | flash | ~1K out × 3 | ~$0.01 |
| Agent C | 1 | flash | ~5K out | ~$0.02 |
| **합계** | | | | **~$0.63** |

→ $1 이하 목표 달성 가능. 단, Agent B의 fan-out 수가 20을 넘으면 위험.

---

## 7. MVP 구현 우선순위

### 가설 1: Gemini가 비디오에서 entity + cut을 한 번에 추출할 수 있는가?

- **검증 범위**: Agent A만 구현 + Pydantic 스키마 + 5개 테스트 비디오
- **성공 기준**: entity 재현율 > 80%, cut 경계 오차 < 0.5s
- **실패 시**: Agent 1/2 분리 또는 pyscenedetect 병용 강화

### 가설 2: cut 단위 fan-out이 일관된 SFX description을 생성하는가?

- **검증 범위**: Agent A + Agent B (3개 cut만) + entity registry 전달
- **성공 기준**: entity ID 매칭 정확도 > 90%, unknown 비율 < 15%
- **실패 시**: entity registry 전달 방식 개선 또는 few-shot 예시 추가

### 가설 3: sound source 단위 track 분리가 LLM으로 가능한가?

- **검증 범위**: Agent C만 구현 + 수동 작성한 cut 분석 결과 입력
- **성공 기준**: 동일 entity에서 의미 있는 sound layer 분리가 일어나는가
- **실패 시**: Agent B에서 sound_source 힌트를 미리 태깅하도록 프롬프트 수정

### 구현 순서

```
Phase 1: 스키마 정의 (Pydantic models) + Agent A 프롬프트 개발 + 테스트
Phase 2: Agent B fan-out + Reconciliation Gate
Phase 3: Agent C + Final Validation
Phase 4: 오케스트레이션 통합 + 에러 처리 + 비용 모니터링
```

---

## 8. 검토 요청 사항

이 문서를 받은 구현 담당자(Claude Code / Codex)에게 요청하는 사항:

1. 위 Pydantic 스키마를 실제 Python 코드로 작성하라
2. Agent A의 프롬프트를 Gemini API 호출 가능한 형태로 구체화하라
3. fan-out 오케스트레이션을 asyncio 기반으로 설계하라 (LangGraph 없이 시작)
4. 가설 1을 검증할 수 있는 최소 테스트 하네스를 만들어라
5. 디렉토리 구조와 모듈 분리안을 제안하라
6. 리스크 레지스터를 아래 형식으로 완성하라

| 리스크 | 확률 | 영향 | 대응 방안 |
|--------|------|------|-----------|
| Gemini timestamp 오차 > 0.5s | 중 | 높 | ? |
| Fan-out rate limit 초과 | 중 | 중 | ? |
| Entity matching 실패율 > 20% | 중 | 높 | ? |
| Track 과세분화 (30+ tracks) | 낮 | 중 | ? |
| Gemini structured output 스키마 미지원 필드 | 중 | 중 | ? |
