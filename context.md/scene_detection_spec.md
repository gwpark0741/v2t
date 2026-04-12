# Scene Detection 모듈 기획서

## 목적

영상 분석 파이프라인의 앞단에서 컷(씬) 분할을 수행하는 모듈을 구현한다.

---

## 배경

현재 ContentDetector(기본 threshold 27.0)를 사용 중이며, 다음 문제가 있다.

- **과분할**: 큰 동작이나 빠른 움직임이 있을 때 실제 씬 전환이 아님에도 컷으로 판정됨
- **원인**: ContentDetector는 프레임 간 변화량의 절대값을 고정 threshold와 비교하기 때문에, 액션이 많은 구간에서 민감하게 반응함

---

## 변경 사항

### 1. Detector 변경

ContentDetector → **AdaptiveDetector**로 교체한다.

| 항목 | ContentDetector (기존) | AdaptiveDetector (변경) |
|------|----------------------|------------------------|
| 판정 기준 | 프레임 간 변화량 > 고정 threshold | 프레임 간 변화량 > 주변 평균 × threshold |
| 기본 threshold | 27.0 | 3.0 |
| 특성 | 절대 기준, 장르별 편차 큼 | 상대 기준, 영상 톤에 자동 적응 |

AdaptiveDetector는 주변 프레임 대비 상대적 변화를 보기 때문에, 액션이 많은 구간 내의 자잘한 움직임은 무시하고 실제 씬 전환만 잡아낼 수 있다.

### 2. 파라미터 설정

```python
from scenedetect import SceneManager
from scenedetect.detectors import AdaptiveDetector

detector = AdaptiveDetector(
    adaptive_threshold=3.5,    # 기본값 3.0에서 상향 → 과분할 억제
    min_scene_len=30,          # 최소 씬 길이 (프레임 수). 30fps 기준
    window_width=2,            # 평균 계산에 사용할 주변 프레임 범위
    min_content_val=15.0,      # 이 변화량 미만은 아예 무시 (노이즈 필터)
)
```

각 파라미터의 역할:

- **adaptive_threshold (3.5)**: 주변 평균의 몇 배를 넘어야 컷으로 판정하는지. 3.0(기본)에서 3.5로 올려 과분할을 줄인다. 실험 결과에 따라 3.0~5.0 범위에서 조정 가능.
- **min_scene_len (30)**: 이 프레임 수 이하의 짧은 씬은 생성하지 않는다. 과분할 방지의 안전장치.
- **window_width (2)**: 평균 계산 윈도우 크기. 기본값 유지.
- **min_content_val (15.0)**: 변화량이 이 값 미만이면 컷 후보에서 제외. 미세한 노이즈성 변화를 걸러낸다.

### 3. 출력 형식

분할된 각 씬에 대해 다음 정보를 반환한다.

```json
{
  "scenes": [
    {
      "scene_index": 0,
      "start_time": "00:00:00.000",
      "end_time": "00:00:12.500",
      "start_frame": 0,
      "end_frame": 375,
      "duration_sec": 12.5
    }
  ],
  "total_scenes": 1,
  "detector": "AdaptiveDetector",
  "params": {
    "adaptive_threshold": 3.5,
    "min_scene_len": 30,
    "min_content_val": 15.0
  }
}
```

---

## 튜닝 가이드

구현 후 테스트 영상으로 결과를 확인하며 아래 기준으로 조정한다.

| 증상 | 조치 |
|------|------|
| 여전히 과분할 (컷이 너무 많음) | adaptive_threshold 올리기 (3.5 → 4.0~5.0) 또는 min_scene_len 늘리기 |
| 과소분할 (컷이 너무 적음) | adaptive_threshold 내리기 (3.5 → 3.0) 또는 min_content_val 내리기 |
| 노이즈성 분할이 남아있음 | min_content_val 올리기 (15.0 → 20.0) |

---

## 참고

- pyscenedetect 공식 문서: https://www.scenedetect.com
- 이 모듈은 영상 분석 파이프라인의 앞단(전처리)으로, 결과의 안정성과 재현성이 최우선이다
- 이후 단계에서 LLM 기반 씬 병합을 적용할 수 있으므로, 약간의 과분할은 과소분할보다 낫다
