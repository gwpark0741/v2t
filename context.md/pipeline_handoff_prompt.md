# Gemini 기반 비디오 사운드 디자인 에이전트 파이프라인 — 구현 검토 및 계획 수립 요청

## 역할

너는 시니어 백엔드 엔지니어이자 ML 파이프라인 아키텍트다. 아래 파이프라인 설계 초안을 검토하고, 실제 구현 계획을 수립하라.

---

## 1. 프로젝트 개요

### 목적

무음 비디오(10~60초)를 입력받아, 사운드 디자인에 필요한 구조화된 메타데이터를 자동 추출하는 에이전트 파이프라인 구축. 최종 출력은 각 시각적 엔티티(character, object, background)별 사운드 트랙 명세(JSON).

### 핵심 기술 스택 (제안)

- **LLM**: Google Gemini 2.5 Pro / Flash (비디오 이해 + structured output)
- **Runtime**: Python 3.11+
- **Orchestration**: 검토 후 결정 (LangGraph, 커스텀 DAG, 또는 단순 async)
- **Output format**: JSON (스키마 정의 필요)

---

## 2. 파이프라인 아키텍처 초안

### 전체 흐름

```
Video Input (10-60s, no audio)
    │
    ▼
[Preprocessing] ─── 프레임 추출, 메타데이터(fps, duration, resolution)
    │
    ├──────────────────┐
    ▼                  ▼
[Agent 1]          [Agent 2]
Entity Tracker     Cut Detector
(병렬 실행)         (병렬 실행)
    │                  │
    ▼                  ▼
[Validation Gate 1] ─── 스키마 검증 + entity-cut 교차 검증
    │
    ▼
[Agent 3: Per-Cut Sound Analyst] ─── cut 수만큼 fan-out 병렬 처리
    │
    ▼
[Aggregation] ─── cut-level 결과 수집
    │
    ▼
[Agent 4: Track Synthesizer] ─── entity별 트랙 병합
    │
    ▼
[Final Validation] ─── 스키마 + 타임라인 커버리지 검증
    │
    ▼
Sound Track Metadata (JSON)
```

### 각 Agent 상세

#### Preprocessing Agent (코드 기반, LLM 불필요)

- **입력**: 비디오 파일
- **처리**:
  - ffprobe로 메타데이터 추출 (fps, duration, resolution, codec)
  - 균등 간격 프레임 샘플링 (예: 2fps)
  - 장면 전환 감지용 추가 프레임 (ffmpeg scene detection or pyscenedetect)
- **출력**: `{ metadata, frames[], scene_change_candidates[] }`

#### Agent 1: Entity Tracker

- **입력**: 원본 비디오 + 샘플링된 프레임
- **모델**: Gemini 2.5 Pro (비디오 전체 이해 필요)
- **태스크**: 영상 전체를 시간순으로 재생하며, 등장하는 모든 character, key object, background를 추적
- **출력 스키마**:

```json
{
  "characters": [
    {
      "id": "CHAR_001",
      "label": "young woman",
      "visual_description": "shoulder-length black hair, red jacket, white sneakers",
      "entry_time": 0.0,
      "exit_time": 12.5
    }
  ],
  "objects": [
    {
      "id": "OBJ_001",
      "label": "wooden door",
      "visual_description": "brown oak door with brass handle, slightly ajar",
      "entry_time": 3.0,
      "exit_time": 8.0
    }
  ],
  "backgrounds": [
    {
      "id": "BG_001",
      "label": "urban alley",
      "visual_description": "narrow alleyway, wet cobblestone, dim streetlight, nighttime",
      "entry_time": 0.0,
      "exit_time": 15.0
    }
  ]
}
```

#### Agent 2: Cut Detector

- **입력**: 원본 비디오 + 샘플링된 프레임 + scene_change_candidates
- **모델**: Gemini 2.5 Flash (비용 최적화 — 시각적 차이 감지에 집중)
- **태스크**: 모든 화면 전환(hard cut, dissolve, wipe 등) 및 구도 전환(angle change) 감지
- **출력 스키마**:

```json
{
  "cuts": [
    {
      "id": "CUT_001",
      "camera_angle": "medium shot, eye-level, slight dutch angle",
      "start_time": 0.0,
      "end_time": 4.2,
      "transition_type": "hard_cut"
    }
  ]
}
```

#### Validation Gate 1 (코드 기반)

- JSON 스키마 검증 (jsonschema 또는 pydantic)
- 교차 검증:
  - 모든 entity의 entry/exit 시간이 영상 duration 범위 내인지
  - entity 시간 범위가 최소 1개 cut과 겹치는지
  - cut의 start/end가 연속적이며 gap/overlap이 없는지
- 실패 시: 해당 구간만 재분석 요청 (최대 2회 retry)

#### Agent 3: Per-Cut Sound Analyst (fan-out)

- **입력 (per instance)**: 해당 cut 구간의 비디오 + Agent 1의 entity 목록 (참조 사전) + cut 메타데이터
- **모델**: Gemini 2.5 Pro
- **태스크**:
  1. 해당 cut에 등장하는 character, object, background를 entity 목록에서 매칭 (새 ID 생성 금지)
  2. Key action 추적: type(onset/continuous), 정확한 timestamp
  3. Ambience description 작성: 공간감, 거리감, 분위기
  4. SFX description 작성: 각 entity별로 (객체, 행위, 재질, 표면)
- **출력 스키마**:

```json
{
  "cut_id": "CUT_001",
  "present_entities": ["CHAR_001", "OBJ_001", "BG_001"],
  "actions": [
    {
      "entity_id": "CHAR_001",
      "action": "footsteps on wet cobblestone",
      "event_type": "continuous",
      "start_time": 0.0,
      "end_time": 4.2
    },
    {
      "entity_id": "OBJ_001",
      "action": "door creaks open",
      "event_type": "onset",
      "timestamp": 3.2
    }
  ],
  "ambience": {
    "background_id": "BG_001",
    "description": "quiet urban alley at night, distant traffic hum, faint dripping water, enclosed reverberant space"
  },
  "sfx": [
    {
      "entity_id": "CHAR_001",
      "description": "soft rubber sole impacts on wet irregular stone surface, light splashing in shallow puddles"
    },
    {
      "entity_id": "OBJ_001",
      "description": "heavy oak door rotating on dry metal hinges, brass handle clicking against strike plate"
    }
  ]
}
```

#### Agent 4: Track Synthesizer

- **입력**: 전체 entity 목록 + 모든 cut-level 분석 결과
- **모델**: Gemini 2.5 Pro
- **태스크**:
  1. 동일 entity의 cut별 결과를 하나의 track으로 병합
  2. 연속된 cut에서 같은 continuous action이 이어지면 timestamp 통합
  3. onset 이벤트는 정확한 초 단위 유지
  4. 최종 sound description을 각 track에 종합 서술
- **출력 스키마 (최종)**:

```json
{
  "tracks": [
    {
      "track_id": "TRK_001",
      "track_label": "Young woman footsteps",
      "entity_id": "CHAR_001",
      "entity_type": "character",
      "sound_description": "soft rubber sole footsteps on wet cobblestone, light puddle splashes, pace: moderate walking",
      "events": [
        {
          "event_type": "continuous",
          "start_time": 0.0,
          "end_time": 12.5,
          "description": "walking on wet cobblestone"
        }
      ]
    },
    {
      "track_id": "TRK_002",
      "track_label": "Wooden door interaction",
      "entity_id": "OBJ_001",
      "entity_type": "object",
      "sound_description": "heavy oak door with dry metal hinges and brass hardware",
      "events": [
        {
          "event_type": "onset",
          "timestamp": 3.2,
          "description": "door creaks open, hinge squeal + handle click"
        },
        {
          "event_type": "onset",
          "timestamp": 7.8,
          "description": "door slams shut, heavy wood impact + latch catch"
        }
      ]
    },
    {
      "track_id": "TRK_003",
      "track_label": "Urban alley ambience",
      "entity_id": "BG_001",
      "entity_type": "background",
      "sound_description": "nighttime urban alley atmosphere, enclosed reverberant space",
      "events": [
        {
          "event_type": "continuous",
          "start_time": 0.0,
          "end_time": 15.0,
          "description": "distant traffic hum, faint dripping water, subtle wind through narrow passage"
        }
      ]
    }
  ]
}
```

#### Final Validation (코드 기반)

- JSON 스키마 검증
- 타임라인 커버리지: 영상 전체 구간에 최소 1개 ambience track이 존재하는지
- Entity 커버리지: Agent 1에서 추출된 모든 entity가 최소 1개 track에 포함되는지
- Timestamp 정합성: onset이 해당 entity의 entry/exit 범위 내인지, continuous의 start < end인지

---

## 3. 검토 요청 사항

아래 항목들을 구체적으로 검토하고, 각각에 대해 판단과 근거를 제시하라.

### 3-1. 아키텍처 검토

- [ ] Agent 분리 단위가 적절한가? 합치거나 더 쪼개야 할 Agent가 있는가?
- [ ] Agent 1과 2의 병렬 실행이 실제로 이점이 있는가? (Gemini API rate limit, 비디오 업로드 중복 비용 고려)
- [ ] Agent 3의 fan-out 전략이 현실적인가? (60초 영상 = 최대 ~20개 cut → 20개 동시 API 호출의 비용과 rate limit)
- [ ] Validation Gate의 재시도(retry) 전략은 어떻게 구현해야 하는가?
- [ ] 단일 Gemini 호출의 비디오 길이/프레임 수 제한을 고려할 때, preprocessing에서 어떤 최적화가 필요한가?

### 3-2. Gemini API 활용 검토

- [ ] `gemini-2.5-pro`와 `gemini-2.5-flash`의 비디오 이해 능력 차이가 이 태스크에서 유의미한가?
- [ ] Gemini의 `response_mime_type: "application/json"` + `response_schema`로 위 스키마들을 강제할 수 있는가? 제한 사항은?
- [ ] 비디오를 Gemini에 전달하는 방식: File API upload vs inline base64 vs YouTube URL — 각 Agent별 최적 전략은?
- [ ] Agent 3 fan-out 시, 전체 비디오를 매번 업로드할 것인가, 아니면 cut 단위로 잘라서 보낼 것인가?
- [ ] Gemini의 비디오 timestamp 정확도는 어느 수준인가? 보정이 필요한가?

### 3-3. 데이터 흐름 및 스키마 검토

- [ ] 위 JSON 스키마에 누락된 필드가 있는가?
- [ ] Agent 간 데이터 전달 시 직렬화/역직렬화 전략
- [ ] Entity ID 네이밍 컨벤션과 충돌 방지
- [ ] 에러 발생 시 부분 결과 보존 전략

### 3-4. 구현 기술 스택 검토

- [ ] 오케스트레이션: LangGraph vs 커스텀 async DAG vs Prefect vs 단순 asyncio — 이 규모에 적합한 선택은?
- [ ] Pydantic v2 모델로 스키마 정의 + 검증을 통합하는 것이 적절한가?
- [ ] 테스트 전략: 각 Agent를 단위 테스트하려면 어떤 mock/fixture가 필요한가?
- [ ] 로깅/모니터링: 각 Agent의 실행 시간, 토큰 사용량, 재시도 횟수를 어떻게 추적할 것인가?

---

## 4. 요청 산출물

위 검토를 완료한 후, 아래 산출물을 작성하라.

### 4-1. 아키텍처 결정 문서 (ADR)

각 검토 항목에 대한 판단, 근거, trade-off 정리

### 4-2. 구현 계획서

- 디렉토리 구조
- 모듈별 책임과 인터페이스
- Pydantic 스키마 정의 (초안)
- Agent별 프롬프트 템플릿 (초안)
- 구현 순서 (어떤 모듈부터 만들 것인가, 의존관계 기반)

### 4-3. 리스크 레지스터

| 리스크 | 영향 | 대응 방안 |
|--------|------|-----------|
| Gemini timestamp 부정확 | 사운드 싱크 불일치 | ? |
| Fan-out rate limit 초과 | 파이프라인 실패 | ? |
| ... | ... | ... |

### 4-4. 프로토타입 우선순위

MVP로 먼저 검증해야 할 가설 3가지와, 각 가설을 검증할 최소 코드 범위를 제안하라.

---

## 5. 제약 조건

- Gemini API만 사용 (OpenAI, Anthropic API 사용 불가 — 파이프라인 내부 한정)
- 실행 환경: 단일 서버 (GPU 불필요, CPU + 네트워크 I/O 중심)
- 예산 감안: 60초 영상 1건 처리당 Gemini API 비용이 $1 이하를 목표
- Python 단일 언어로 구현
- 외부 ML 모델(YOLO, SAM 등) 사용은 가능하나, Gemini 단독으로 해결 가능한 부분은 Gemini로 처리

---

## 6. 참고 — 프롬프트 설계 힌트

각 Agent의 system prompt 작성 시 참고할 핵심 지침:

- **Agent 1**: "영상을 처음부터 끝까지 시간순으로 재생하며, 새로운 인물/객체/배경이 등장할 때마다 즉시 기록하라. 이미 기록된 엔티티가 재등장하면 exit_time만 갱신하라."
- **Agent 2**: "화면의 밝기, 구도, 피사체 크기가 급격히 변하는 모든 지점을 감지하라. dissolve, wipe 등 점진적 전환도 놓치지 마라."
- **Agent 3**: "새로운 entity ID를 생성하지 마라. 반드시 제공된 entity 목록에서 매칭하라. 매칭이 불확실하면 가장 유사한 entity에 confidence score를 붙여라."
- **Agent 4**: "동일 entity가 연속된 cut에서 같은 행위를 하면 하나의 continuous event로 병합하라. 행위가 변하면 별도 event로 분리하라."
