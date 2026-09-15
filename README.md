# UCBAD-X — 실행 가능한 구현과 재현 절차

문서 `docs/UCBAD-X_설계와검증.html` 의 모든 표를 이 코드로 재생성할 수 있다.

---

## 1. 파일 구성

| 파일 | 역할 |
|---|---|
| `manifest.py` | 캡처 12종의 경로·공격 시각·감염 replica·정답 라벨 |
| `preprocess.py` | `jsonl.gz` → (스냅샷, replica) 집계 pickle |
| `channels.py` | 채널 A1 / A2 / B / C, 그리고 비교용 RW·TV |
| `controller.py` | 임계값 보정 + 판단기 규칙 4개 |
| `evaluate.py` | 문서의 표 재생성 |
| `closed_loop.py` | 닫힌 고리(자동 재학습) 시뮬레이션 |
| `cache/` | 전처리 결과. `preprocess.py --all` 이 채운다 |

의존성: `numpy` 만 있으면 된다. (채널 B 를 오토인코더로 바꾸려면 `torch`)

---

## 2. 실행

```bash
cd ucbadx

# 1) 전처리 — 캡처 12종, 약 5,600만 이벤트. 10~15분 걸린다.
python preprocess.py --all

# 2) 표 재생성
python evaluate.py              # 전부
python evaluate.py identify     # 식별 역전
python evaluate.py attenuation  # 감쇠 법칙
python evaluate.py hidden       # 신원 은닉 공격
python evaluate.py channelc     # C 채널
python evaluate.py controller   # 판단기 전 시나리오

# 3) 닫힌 고리
python closed_loop.py
```

캡처 파일이 없으면 `preprocess.py --all` 이 건너뛰고 경고만 낸다.
개별 파일만 있으면 그것만 처리해도 된다:

```bash
python preprocess.py ../capture_attack/attack_1of4.jsonl.gz cache/cat_1of4.pkl
```

---

## 3. 데이터

캡처는 `rw/tools/capture2.sh` 형식이다. 직접 뜨려면:

```bash
sysdig -j -p'*%evt.rawtime %container.name %evt.type %evt.category
             %fd.directory %fd.filename %fd.type
             %proc.name %proc.cmdline %proc.cwd %proc.exe %proc.args' \
       "container.name contains <접두어>" | gzip > capture.jsonl.gz
```

| key | 경로 | 내용 |
|---|---|---|
| `cat_baseline` | `data/capture/normal_catalogue.jsonl.gz` | catalogue 정상 100분 (200 스냅샷) |
| `cat_1of4` ~ `cat_4of4` | `capture_attack/`, `capture_2of4/`, `capture_3of4/`, `capture_44c/` | 감염 1~4개, 각 30분 |
| `cat_external` | `capture_44/attack_4of4.jsonl.gz` | 마이너가 컨테이너 밖 |
| `cat_drift_workload` | `capture_drift/` | 엔드포인트 확장 (드리프트 시도 1 — 실패) |
| `cat_drift_version` | `capture_v030/` | 이미지 0.3.5→0.3.0 (드리프트 시도 2 — 실패) |
| `fe_n4_norm`, `fe_n4_hidden` | `capture_fe_norm/`, `capture_fe_atk/` | Node 4 정상 / 신원 은닉 채굴 |
| `fe_n10_norm`, `fe_n10_hidden` | `capture_fe_v10/`, `capture_fe_v10_atk/` | Node 10 정상 / 신원 은닉 채굴 |

`fe_n10_norm` 은 **자기 배포 기준으로는 정상**이고,
`fe_n4_norm` 기준으로 보면 **실제 드리프트**다 (신규 syscall 5종, 구성비 TV 0.2046).
드리프트 평가는 `closed_loop.py` 가 Node 4 기준으로 수행한다.

---

## 4. 모델 요약

### 채널

| 채널 | 정의 | 보는 것 |
|---|---|---|
| **A1** | `mean_j \|Ri \ Rj\| / \|Ri\|` (10개 집합 특성) | 누가 남들에게 없는 원소를 갖고 있나 |
| **A2** | `mean_j Σ max(cᵢ−cⱼ,0) / Σcᵢ` (syscall 호출 수) | 누가 남들보다 많이 호출하나 |
| **B** | 정상 기준 구성비 대비 차원별 z 점수의 최댓값 | 평소의 자기와 다른가 |
| **C** | `\|log2( (총 이벤트 / accept 수) / 기준 )\|` | 요청 하나에 드는 일이 변했나 |

A1·A2 는 **비대칭**이다. 대칭 지표(Jaccard, TV)를 쓰면 과반 감염에서 순위가 뒤집힌다.

### 판단기

```
A1∪A2 지목 있음                -> 부분 감염   (지목된 집합 출력)
A 침묵 · B 정상                -> 정상       (학습 버퍼에 적립)
A 침묵 · B 이상 · C 변동        -> 전원 감염
A 침묵 · B 이상 · C 안정        -> 정상 변화   (B 재학습)
```

### 임계값

정상 baseline 만 보고 정한다. 공격 데이터는 쓰지 않는다.

| | 계수 | catalogue | front-end(N4) | front-end(N10) |
|---|---|---|---|---|
| θ_A1 | 정상 최댓값 × 2.00 | 0.1429 | 0.1720 | 0.1333 |
| θ_A2 | 정상 최댓값 × 1.25 | 0.4465 | 0.4391 | 0.6156 |
| θ_B | 정상 최댓값 | 11.57 | 6.14 | 8.47 |
| θ_C | **절대값 0.30** | 공통 | 공통 | 공통 |

A1 과 A2 의 계수가 다른 이유: A1 은 정상 노이즈가 0.07~0.09 로 거의 0 이라 ×2 해도
절대 증가폭이 작지만, A2 는 원시 호출수라 부하 편차가 섞여 노이즈가 0.31~0.35 이고
×2 하면 신호(0.53~0.78)를 넘어버린다.

---

## 5. 자동 재학습이 되는 조건

세 가지가 **모두** 만족돼야 한다.

1. **변화가 전 replica 에 균일** — A 가 침묵해야 한다.
   한 replica 만 달라지면 "부분 감염"으로 처리되고 재학습하지 않는다. *(의도된 동작)*
2. **요청당 작업량 변화 < θ_C** — C 가 안정이어야 한다.
   크게 변하면 "전원 감염"으로 처리된다. *(공격 오염 방지 장치)*
3. **20 스냅샷(10분) 연속 유지** — 후보 버퍼가 차야 한다.
   중간에 다른 판정이 끼면 버퍼가 리셋된다.

> **실제로 3번에서 한 번 실패했다.** C 를 *총 이벤트 수*로 정의했을 때,
> 실제 드리프트(이벤트 −9%)가 정상 변동(±6.6%)과 겹쳐 판정이
> `드리프트` ↔ `전원 감염` 을 오갔고 후보 버퍼가 계속 리셋돼 **재학습 0회**가 됐다.
> C 를 *요청당 작업량*으로 바꿔 해결했다. `channels.work_rate()` 주석 참조.

---

## 6. 재현되는 주요 결과

`python evaluate.py` 출력 기준.

**식별 역전** (catalogue)

| 감염 | Jaccard | 간격 | 비대칭 | 간격 |
|---|---:|---:|---:|---:|
| 1/4 | 100% | +0.745 | 100% | +1.107 |
| 2/4 | 72% | −0.001 | 100% | +0.713 |
| 3/4 | **0%** | −0.745 | **100%** | +0.344 |

빈도 축(TV 대칭)에서도 같은 패턴: 100% / 92% / **0%**

**감쇠 법칙** `D(k) = D₁(n−k)/(n−1)` — k=1,2,3,4 에서 오차 **0.0000**

**신원 은닉 공격**

| | Node 4 | Node 10 |
|---|---:|---:|
| 신원 특성 변화 | 없음 | 없음 |
| 새 syscall | `brk` 1종 | 0종 |
| ReplicaWatcher AUC | 0.456 | 0.728 |
| A1(집합) AUC | 0.461 | 0.606 |
| A2(빈도) AUC | **1.000** | **1.000** |
| 감염 집합 복원 | **100%** | **100%** |

**판단기**

| 캡처 | 정답 | 판정 정확도 | 집합 복원 |
|---|---|---:|---:|
| cat_baseline | 정상 | 100% | 100% |
| cat_1of4 | 부분 감염 | 100% | 97% |
| cat_2of4 | 부분 감염 | 100% | 95% |
| cat_3of4 | 부분 감염 | 100% | 97% |
| cat_4of4 | 전원 감염 | 97% | 97% |
| cat_external | 전원 감염 | **67%** | 67% |
| cat_drift_workload | 정상 | 100% | 100% |
| cat_drift_version | 정상 | 100% | 100% |
| fe_n4_norm | 정상 | 100% | 100% |
| fe_n4_hidden | 부분 감염 | **100%** | **100%** |
| fe_n10_norm | 정상 | 100% | 100% |
| fe_n10_hidden | 부분 감염 | **100%** | **100%** |

**닫힌 고리** (`python closed_loop.py`)

| | 정상 | 드리프트 | 공격 탐지 | 재학습 | 공격 오흡수 | 적응 지연 |
|---|---:|---:|---:|---:|---:|---:|
| 정적 B | 95% | **0%** | 100% | — | — | — |
| UCBAD-X | 95% | **62%** | **100%** | 1회 | **0회** | 21 스냅샷 |

---

## 7. 알려진 차이와 주의점

- **`cat_external` 이 67%** 다. 채널 B 를 학습 없는 z 점수로 구현했기 때문이다.
  오토인코더로 바꾸면 100% 가 된다. 신호가 가장 약한 시나리오라 학습형이 유리하다.
  문서 8절의 "무학습 B" 표 참조. 다른 시나리오에서는 둘이 동일하다.
- **`fe_n10_norm` 의 라벨은 배포에 따라 달라진다.** 자기 기준으로는 정상,
  Node 4 기준으로는 드리프트다. `evaluate.py controller` 는 전자를,
  `closed_loop.py` 는 후자를 본다.
- **어휘는 배포 단위로 고정한다.** 캡처마다 등장하는 syscall 종류가 다르므로
  (버전 변경 시 특히) 벡터 차원을 맞추려면 배포 안의 모든 캡처를 합친 어휘를 써야 한다.
  `evaluate.deploy_vocab()` 참조.
- **`NOISE_THREADS`** — Node 4 의 V8 워커 스레드는 간헐적으로 출몰해
  집합 특성을 흔든다(제외하지 않으면 정상 상태에서 RW 오탐 80%).
  `channels.py` 에서 제외 목록으로 관리한다. 다른 런타임에서는 목록을 늘려야 할 수 있다.
- **전이 스냅샷은 버린다.** 공격 투입 시점이 걸친 스냅샷은 평가에서 제외한다.

---

## 8. 미검증 항목

- **Kubernetes** — Docker Compose 에서만 검증했다. k8s 환경은 `docs/rw-report.html` 참조.
- **공격 유형** — 크립토마이닝 1종. 다만 식별 역전은 지표의 대칭성에서 오므로 공격 유형과 무관하다.
- **탐지 하한** — 감염 replica 이벤트가 2.7~4.3배 증가한 조건에서만 측정했다.
- **run 반복** — 시나리오당 1회.
