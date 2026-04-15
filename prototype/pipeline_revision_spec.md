# Pipeline Revision Spec: sfx/ambience + Description-Based Track Grouping
> 기존 파이프라인(v5) 대비 변경사항 위주 구현 명세. Codex 전달용.
> 충돌 시 이 문서 우선. Stage 01~04 변경 없음.

---

## 변경 요약

| 항목 | 기존 | 변경 |
|------|------|------|
| interaction_type | hard_effect / foley / background / electronic | sfx / ambience |
| surface_context | Action 필드, merge 판단 기준 | 완전 제거 (필드/프롬프트/payload/리포트 전부) |
| Stage 06 1차 버킷 키 | source_id + interaction_type | source_id + interaction_type + event_type |
| event_type 분리 | 없음 | onset / continuous 항상 별도 버킷, 별도 트랙 |
| Stage 06 판단 기준 | surface 문자열 pair 비교 (Flash) | description 기반 LLM 그룹 판단 (버킷당 1회) |
| LLM 호출 단위 | variant pair당 1회 | 1차 버킷당 1회 |
| track_id | source__interaction__surface_key | source__sfx__event_type[__desc_key[__hash]] |
| surface_judge.py | surface pair 비교 | 삭제 → track_judge.py로 대체 |
| SurfaceJudgment | Flash pair 판단 로그 | 삭제 → TrackGroupJudgment로 대체 |
| Stage 06 부가 산출물 | surface_judgments.json | track_group_judgments.json |

---

## 1. models.py

### interaction_type enum 교체

```python
# 기존
interaction_type: Literal["hard_effect", "foley", "background", "electronic"]

# 변경
interaction_type: Literal["sfx", "ambience"]
```

### Action 스키마 — surface_context 제거

```python
class Action(BaseModel):
    action_id: str
    cut_id: str
    primary_source_id: str
    unknown_resolution: UnknownResolution | None
    interaction_type: Literal["sfx", "ambience"]
    sound_description: str          # 재질/접촉 정보 포함 (surface_context 역할 흡수)
    observed_visual_description: str
    event: OnsetEvent | ContinuousEvent
    boundary_flag: bool
    # surface_context 제거
```

### Track 스키마 — surface_context_summary 제거

```python
class Track(BaseModel):
    track_id: str
    track_type: Literal["sfx", "ambience"]
    source_entity_id: str
    interaction_type: str
    sound_description: str
    # surface_context_summary 제거
    events: list[OnsetEvent | ContinuousEvent]
```

### TrackGroupJudgment 추가 (SurfaceJudgment 대체)

```python
class TrackGroupResult(BaseModel):
    action_ids: list[str]
    reason: str

class TrackGroupJudgment(BaseModel):
    group_key: str                        # "{source_id}__sfx__{event_type}"
    input_action_ids: list[str]
    output_groups: list[TrackGroupResult]
    model: str | None
    source: Literal["single_action", "llm", "llm_error"]
```

### AgentCResult 수정

```python
class AgentCResult(BaseModel):
    pipeline_result: PipelineResult
    track_group_judgments: list[TrackGroupJudgment]   # surface_judgments 대체
    llm_call_count: int                               # flash_call_count 대체
    total_llm_latency_ms: float
    per_call_llm_latency_ms: list[float]
    llm_usage: dict                                   # 토큰 사용량
    estimated_llm_cost_usd: float
    # cache_hit_count 제거
```

---

## 2. agent_b_runtime.py — 프롬프트 수정

### Rule 4: interaction_type 정의 교체

```
# 기존
hard_effect  — physical contact, collision, mechanism
foley        — body movement, footsteps, clothing, biological motion
background   — ambient environment, no specific source
electronic   — motors, signals, electronic devices

# 변경
sfx      — all individual sound events: physical contact, impact, friction,
            footsteps, clothing, body movement, mechanisms, electronic devices.
            Describe materials in contact naturally within sound_description.
ambience — continuous spatial sound with no specific source:
            environment, crowd, wind, room tone, background layers.
```

### Rule 5: surface_context 규칙 전체 제거

Rule 5를 삭제하고 sound_description 지시로 흡수.

### sound_description 지시 강화

```
sound_description:
  - Describe the sound clearly in one sentence.
  - For sfx, include material and contact information naturally when relevant.

  Good: "Sharp click as plastic ball strikes wooden table"
  Good: "Metal sword blade scrapes against lacquered wooden scabbard"
  Good: "Rubber shoe sole squeaks on polished tile floor"
  Bad:  "Ball hits table"
  Bad:  "Sword sound"
```

### AgentBResponse 스키마 수정

`Action` 구조 변경에 맞춰 `surface_context` 필드 제거.

### validation 유지 항목

```
cut_id, action_id, source linkage, unknown resolution,
local timestamp 범위 (0.0 ~ cut_duration)
surface_context 관련 validation 전부 제거
```

---

## 3. merge_rules.py — 단순화

surface 관련 로직 전부 제거. `source + interaction_type` 동일성 판단만 유지.

```python
def canonical_should_merge(action_a: Action, action_b: Action) -> bool | None:
    """
    source_id / interaction_type 동일 여부 판단.
    ambience → True (즉시 MERGE)
    sfx      → None (synthesizer에서 TrackJudge 위임)
    """
    if action_a.primary_source_id != action_b.primary_source_id:
        return False
    if action_a.interaction_type != action_b.interaction_type:
        return False
    if action_a.interaction_type == "ambience":
        return True
    return None  # sfx → synthesizer 처리

# 제거 대상:
# default_surface_compatibility()
# surface 관련 분기 전부
```

**주의:** sfx의 경우 synthesizer가 TrackJudge를 직접 호출하므로,
`canonical_should_merge()`는 sfx 경로에서 호출되지 않는다.
ambience와 source/interaction 불일치 early-exit 용도로만 남긴다.

---

## 4. synthesizer.py — 핵심 변경

### 전체 흐름

```python
def synthesize_tracks(
    actions: list[Action],
    entity_registry: EntityRegistry,
    track_judge: TrackJudge,
) -> PipelineResult:

    # 1. UNRESOLVED 분리
    resolved, unresolved = split_resolved(actions)

    # 2. 1차 버킷팅
    # key: (source_entity_id, interaction_type, event_type)
    # event_type이 다르면 항상 별도 버킷 → 별도 트랙 (TrackJudge 없이)
    buckets: dict[tuple, list[Action]] = defaultdict(list)
    for action in resolved:
        event_type = action.event.type   # "onset" | "continuous"
        key = (action.primary_source_id, action.interaction_type, event_type)
        buckets[key].append(action)

    # 3. 버킷별 처리
    tracks = []
    for (source_id, itype, etype), bucket_actions in buckets.items():

        if itype == "ambience":
            # LLM 없이 즉시 단일 트랙 확정
            tracks.append(build_track(source_id, itype, etype, bucket_actions))
            continue

        # sfx: TrackJudge 호출
        groups = track_judge.judge_group(bucket_actions, source_id, itype, etype)
        total = len(groups)
        for idx, group_actions in enumerate(groups):
            tracks.append(build_track(source_id, itype, etype, group_actions,
                                      group_index=idx, total_groups=total))

    return PipelineResult(
        track_manifest=TrackManifest(tracks=tracks),
        unresolved_unknowns=unresolved,
        warnings=[],
    )
```

### track_id 생성 규칙

```python
def build_track_id(
    source_id: str,
    interaction_type: str,
    event_type: str,
    group_actions: list[Action],
    group_index: int = 0,
    total_groups: int = 1,
) -> str:

    if interaction_type == "ambience":
        return f"{source_id}__ambience"

    base = f"{source_id}__sfx__{event_type}"

    if total_groups == 1:
        return base

    # 다중 그룹: 대표 description으로 desc_key 생성
    rep = max(group_actions, key=lambda a: len(a.sound_description))
    desc_key = normalize_key(rep.sound_description)

    # desc_key 충돌 방지: 정렬된 action_id 목록의 short hash 추가
    action_ids_str = ",".join(sorted(a.action_id for a in group_actions))
    short_hash = hashlib.sha1(action_ids_str.encode()).hexdigest()[:6]

    return f"{base}__{desc_key}__{short_hash}"


def normalize_key(text: str) -> str:
    """description → track_id용 key. 40자 제한."""
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", "_", s)
    return s[:40]
```

---

## 5. track_judge.py — 신규 (surface_judge.py 대체)

### 클래스 구조

```python
class TrackJudge:
    def __init__(self, flash_client, model: str = "gemini-2.5-flash"):
        self._judgments: list[TrackGroupJudgment] = []
        self._llm_call_count: int = 0
        self._latencies: list[float] = []
        self.flash_client = flash_client
        self.model = model

    @property
    def llm_call_count(self) -> int:
        return self._llm_call_count

    def get_judgments(self) -> list[TrackGroupJudgment]:
        return list(self._judgments)

    def get_warnings(self) -> list[WarningItem]:
        return list(self._warnings)

    def judge_group(
        self,
        actions: list[Action],
        source_id: str,
        interaction_type: str,
        event_type: str,
    ) -> list[list[Action]]:
        group_key = f"{source_id}__sfx__{event_type}"

        # action 1개: LLM 없이 즉시 반환
        if len(actions) == 1:
            self._record(group_key, actions, [[actions[0]]], "single_action", model=None)
            return [actions]

        # LLM 호출
        payload = self._build_payload(actions, source_id, interaction_type, event_type)
        raw, latency_ms = self._call_llm(payload)
        self._latencies.append(latency_ms)
        self._llm_call_count += 1

        result = self._parse_response(raw, actions)
        self._record(group_key, actions, result.groups, result.source, model=self.model)

        if result.source == "llm_error":
            self._warnings.append(WarningItem(
                code="TRACK_JUDGE_ERROR",
                severity="warning",
                message=result.error,
                context={"group_key": group_key},
            ))

        return self._resolve_groups(actions, result)
```

### LLM System Prompt

```python
SYSTEM_PROMPT = """\
You are a sound track grouping judge for a video sound design pipeline.

Given a list of sound actions that share the same source entity, interaction type,
and event type, decide which actions belong to the same audio track.

A track represents sounds that can be covered by a single audio asset.
Group actions that describe acoustically equivalent events.
Actions with clearly different acoustic character must be in separate groups.

Rules:
- Every input action_id must appear in exactly one group.
- When uncertain, keep actions in separate groups (conservative).
- Use cut_id to understand temporal context across the video.

Respond ONLY with valid JSON. No explanation outside the JSON.
{
  "groups": [
    {
      "action_ids": ["act_CUT001_001", "act_CUT003_002"],
      "reason": "<one sentence>"
    }
  ]
}
"""
```

### LLM 입력 payload

```python
def _build_payload(
    actions: list[Action],
    source_id: str,
    interaction_type: str,
    event_type: str,
) -> str:
    return json.dumps({
        "group_context": {
            "source_entity_id": source_id,
            "interaction_type": interaction_type,
            "event_type": event_type,
        },
        "actions": [
            {
                "action_id": a.action_id,
                "cut_id": a.cut_id,
                "sound_description": a.sound_description,
                "observed_visual_description": a.observed_visual_description,
            }
            for a in actions
        ],
    }, ensure_ascii=False, indent=2)
```

### 응답 파싱 및 fallback

```python
def _parse_response(raw: str, actions: list[Action]) -> LLMGroupResult:
    """
    파싱 실패 / action_id 불일치 → 모든 action 개별 그룹 fallback (보수적).
    """
    try:
        data = json.loads(raw.strip())
        groups = data.get("groups", [])
        if not groups:
            raise ValueError("empty groups")

        input_ids = {a.action_id for a in actions}
        output_ids = {aid for g in groups for aid in g["action_ids"]}
        if input_ids != output_ids:
            raise ValueError(f"action_id mismatch: {input_ids ^ output_ids}")

        return LLMGroupResult(groups=groups, source="llm")

    except Exception as e:
        fallback = [{"action_ids": [a.action_id], "reason": "fallback"} for a in actions]
        return LLMGroupResult(groups=fallback, source="llm_error", error=str(e))
```

### WarningItem 발행 규칙

| 상황 | code | severity |
|------|------|----------|
| LLM 응답 파싱 실패 | `TRACK_JUDGE_PARSE_ERROR` | warning |
| LLM API 에러 | `TRACK_JUDGE_API_ERROR` | warning |
| action_id mismatch | `TRACK_JUDGE_ID_MISMATCH` | warning |

---

## 6. agent_c.py 수정

```python
async def run_stage_06_agent_c(
    agent_b_result: AgentBAllCutsResult,
    entity_registry: EntityRegistry,
    flash_client,
    flash_model: str = "gemini-2.5-flash",
    artifacts: StageArtifacts | None = None,
) -> AgentCResult:

    track_judge = TrackJudge(flash_client=flash_client, model=flash_model)

    pipeline_result = synthesize_tracks(
        actions=agent_b_result.all_actions,
        entity_registry=entity_registry,
        track_judge=track_judge,
    )

    pipeline_result.warnings.extend(track_judge.get_warnings())

    result = AgentCResult(
        pipeline_result=pipeline_result,
        track_group_judgments=track_judge.get_judgments(),
        llm_call_count=track_judge.llm_call_count,
        total_llm_latency_ms=sum(track_judge._latencies),
        per_call_llm_latency_ms=track_judge._latencies,
        llm_usage=track_judge.get_usage(),
        estimated_llm_cost_usd=track_judge.get_estimated_cost(),
    )

    if artifacts:
        artifacts.write_output(result)
        artifacts.write_json("track_group_judgments",
                             [j.model_dump() for j in result.track_group_judgments])

    return result
```

---

## 7. 아티팩트 / 리포트

### Stage 06 산출물 변경

```
surface_judgments.json  →  track_group_judgments.json
```

### agent_c_report.py

- surface judgments 섹션 → track group judgments 섹션으로 교체
- 컬럼: `group_key`, `input_action_count`, `output_group_count`, `source`, `reason`
- 기존 surface 관련 컬럼 전부 제거

### Stage 05 report

- `surface_context` 컬럼 제거

### dual-read (report 경로 한정)

```python
# report generator에서만 적용. runtime model은 새 스키마 단방향.

def load_stage06_judgments(output: dict) -> list[dict]:
    # 새 run
    if "track_group_judgments" in output:
        return output["track_group_judgments"]
    # 기존 run fallback
    if "surface_judgments" in output:
        return _convert_surface_to_group_format(output["surface_judgments"])
    return []

def load_llm_metrics(output: dict) -> dict:
    # 새 run
    if "llm_call_count" in output:
        return {
            "call_count": output["llm_call_count"],
            "total_latency_ms": output.get("total_llm_latency_ms", 0),
        }
    # 기존 run fallback
    return {
        "call_count": output.get("flash_call_count", 0),
        "total_latency_ms": output.get("total_flash_latency_ms", 0),
    }
```

---

## 8. 삭제 대상

```
surface_judge.py              전체 삭제
SurfaceJudgment (models.py)   삭제
surface_judgments.json        신규 run에서 미생성
Action.surface_context        삭제
Track.surface_context_summary 삭제
AgentCResult.cache_hit_count  삭제
merge_rules.py                surface 비교 로직 전부 삭제
```

---

## 9. 테스트 수정

### 기존 테스트 일괄 값 교체

```python
"hard_effect" → "sfx"
"foley"       → "sfx"
"electronic"  → "sfx"
"background"  → "ambience"
surface_context 관련 fixture 전부 제거
```

### test_track_judge.py 신규

```
action 1개          → LLM 없이 단일 그룹 반환, source="single_action"
LLM 정상 응답       → 지정 그룹 반환, source="llm"
파싱 실패           → 개별 그룹 fallback, source="llm_error", warning 발행
API 에러            → 개별 그룹 fallback, warning 발행
action_id mismatch  → 개별 그룹 fallback, warning 발행
```

### test_synthesizer.py 수정

```
1차 버킷 키: (source_id, interaction_type, event_type) 확인
onset / continuous 항상 별도 버킷 확인
ambience → TrackJudge 미호출, 즉시 단일 트랙 확인
sfx → TrackJudge.judge_group() 호출 확인
다중 그룹 → desc_key + short_hash track_id 생성 확인
desc_key 충돌 없음 확인
```

### regression scenario (unit, TrackJudge mock)

```
video 13 성격:
  서로 다른 sound description 묶음 → 과병합 없이 별도 그룹 반환

video 07 singer 성격:
  vocal description + cloth description → 별도 그룹 반환

video 07 train 성격:
  동일 source 반복 기계음 → TrackJudge가 단일 그룹 반환
```

regression은 TrackJudge를 mock한 unit test로 구현.
live run은 수동 smoke 검증으로 분리.

---

## 10. 구현 순서

```
1. models.py
   interaction_type enum 교체
   Action / Track surface 필드 제거
   TrackGroupJudgment / AgentCResult 추가

2. agent_b_runtime.py
   프롬프트 Rule 4 교체, Rule 5 제거
   sound_description 지시 강화
   AgentBResponse 스키마 수정

3. merge_rules.py
   surface 로직 제거, 단순화

4. track_judge.py
   신규 작성

5. synthesizer.py
   1차 버킷팅 key 변경
   ambience 즉시 확정
   sfx → TrackJudge 연결
   track_id 생성 규칙 변경

6. agent_c.py
   SurfaceJudge → TrackJudge 교체
   AgentCResult 새 필드 반영

7. agent_c_report.py / tab_06_tracks.py
   surface → track group judgment로 교체

8. 기존 테스트 일괄 값 교체
   test_track_judge.py 신규
   regression scenario 추가

9. surface_judge.py 삭제
   __init__.py / artifacts.py symbol 정리
```
