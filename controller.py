"""
UCBAD-X 판단기.

핵심 아이디어: A 의 침묵은 "이상 없음"이 아니라 "replica 사이에 비대칭이 없음"이다.
비대칭이 없는데 각자는 평소와 다르다면 전원이 같이 변한 것이고,
그것이 공격인지 정상 변화인지는 요청당 작업량(C)이 가른다.

  A1∪A2 지목 있음                  -> 부분 감염    (지목된 집합 반환)
  A 침묵 · B 정상                  -> 정상        (학습 버퍼에 적립)
  A 침묵 · B 이상 · C 변동          -> 전원 감염
  A 침묵 · B 이상 · C 안정          -> 정상 변화    (B 재학습 트리거)
"""
import numpy as np
import channels as ch


# ------------------------------------------------------------------ 임계값
# A1 은 정상 노이즈가 0.07~0.09 로 거의 0 이라 x2 해도 절대 증가폭이 작다.
# A2 는 원시 호출수를 쓰므로 부하 편차가 섞여 정상 노이즈가 0.31~0.35 이고,
# x2 하면 신호(0.53~0.78)를 넘어버린다. 그래서 계수를 다르게 잡는다.
FACTOR_A1 = 2.00
FACTOR_A2 = 1.25
THETA_C   = 0.30    # 배포 무관 절대값. 정상·드리프트 5개 캡처에서 오탐 0%
QUORUM_B  = 0.25    # replica 중 이 비율 이상이 B 임계값을 넘으면 B=이상


class Thresholds:
    def __init__(self, a1, a2, c_ref, chan_b):
        self.a1 = a1
        self.a2 = a2
        self.c_ref = c_ref      # 정상 구간의 요청당 작업량 중앙값
        self.b = chan_b

    @classmethod
    def calibrate(cls, baseline_snapshots, replicas, features, vocab):
        """정상 baseline 만 보고 임계값을 정한다. 공격 데이터는 쓰지 않는다."""
        a1 = FACTOR_A1 * max(max(ch.a1_scores(s, replicas, features).values())
                             for _, s in baseline_snapshots)
        a2 = FACTOR_A2 * max(max(ch.a2_scores(c, replicas).values())
                             for c, _ in baseline_snapshots)
        c_ref = float(np.median([ch.work_rate(c, replicas, vocab)
                                 for c, _ in baseline_snapshots]))
        b = ch.ChannelB([c for c, _ in baseline_snapshots], replicas)
        return cls(a1, a2, c_ref, b)

    def __repr__(self):
        return (f"Thresholds(A1={self.a1:.4f}, A2={self.a2:.4f}, "
                f"B={self.b.threshold:.2f}, C={THETA_C}, c_ref={self.c_ref:.1f})")


# ------------------------------------------------------------------ 판정
def verdict(counts, sets, replicas, features, vocab, th):
    """한 스냅샷을 판정한다.  반환: (판정문자열, 지목된 replica 집합, 진단값)"""
    a1 = ch.a1_scores(sets, replicas, features)
    a2 = ch.a2_scores(counts, replicas)
    flagged = {r for r in replicas if a1[r] > th.a1} | {r for r in replicas if a2[r] > th.a2}
    b_high = th.b.fired(counts, replicas, QUORUM_B)
    c = ch.c_score(counts, replicas, vocab, th.c_ref)
    diag = dict(a1=max(a1.values()), a2=max(a2.values()), b=b_high, c=c)

    if flagged:
        return "부분 감염", flagged, diag
    if not b_high:
        return "정상", set(), diag
    if c > THETA_C:
        return "전원 감염", set(replicas), diag
    return "정상 변화", set(), diag


# ------------------------------------------------------------------ 감염 비율
def infection_ratio(a1_or_a2_scores, replicas, d1, n=None):
    """감쇠 법칙  D(k) = D1 * (n-k)/(n-1)  을 뒤집어 k 를 추정한다.

    d1 : 단일 감염일 때의 기준 점수 (배포별로 한 번 측정)
    지목된 replica 수를 세는 것이 더 직접적이지만, 점수 크기로도
    교차 확인할 수 있다는 것을 보이기 위한 함수.
    """
    n = n or len(replicas)
    d = max(a1_or_a2_scores.values())
    if d1 <= 0:
        return None
    return n - (n - 1) * d / d1
