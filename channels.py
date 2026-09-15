"""
UCBAD-X 네 채널.

A1  동료 x 집합 x 비대칭   누가 남들에게 없는 원소를 갖고 있나
A2  동료 x 빈도 x 비대칭   누가 남들보다 많이 호출하나
B   자기 x 빈도            평소의 자기와 다른가
C   요청당 작업량          요청 하나에 드는 일이 변했나

비교를 위해 참고 구현도 포함:
RW   동료 x 집합 x 대칭 (ReplicaWatcher 원본)
TV   동료 x 빈도 x 대칭 (대칭 지표가 빈도 축에서도 역전하는지 확인용)
"""
import numpy as np

# 간헐적으로 출몰하는 런타임 스레드. 집합 특성에서 제외하면 RW 오탐이 사라진다.
# (Node 4 의 V8 워커 스레드. 제외하지 않으면 정상 상태에서 80% 오탐)
NOISE_THREADS = {"V WorkerThread", "V WorkerThread server.js"}

ACCEPT_SYSCALLS = {"accept4", "accept"}


# ---------------------------------------------------------------- 집합 축
def _clean(feature, values, strip_threads):
    s = set(values)
    if strip_threads and feature in ("procs", "commands"):
        s -= NOISE_THREADS
    return s


def _jaccard(a, b):
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _containment(a, b):
    """|a \\ b| / |a| — 비대칭. a 가 가진 것 중 b 에 없는 비율."""
    return 0.0 if not a else len(a - b) / len(a)


def rw_scores(snapshot_sets, replicas, features, strip_threads=True):
    """ReplicaWatcher 원본. 대칭 Jaccard. replica별 점수 dict 반환."""
    S = {r: {f: _clean(f, snapshot_sets[r].get(f, []), strip_threads) for f in features}
         for r in replicas}
    return {i: float(np.linalg.norm(
        [np.mean([1 - _jaccard(S[i][f], S[j][f]) for j in replicas if j != i]) for f in features]))
        for i in replicas}


def a1_scores(snapshot_sets, replicas, features, strip_threads=True):
    """A1. 포함관계 기반 비대칭. replica별 점수 dict."""
    S = {r: {f: _clean(f, snapshot_sets[r].get(f, []), strip_threads) for f in features}
         for r in replicas}
    return {i: float(np.linalg.norm(
        [np.mean([_containment(S[i][f], S[j][f]) for j in replicas if j != i]) for f in features]))
        for i in replicas}


# ---------------------------------------------------------------- 빈도 축
def tv_scores(counts, replicas):
    """대칭 TV 거리. 비교용 — 과반 감염에서 역전한다."""
    p = {r: counts[r] / counts[r].sum() for r in replicas}
    return {i: float(np.mean([0.5 * np.abs(p[i] - p[j]).sum() for j in replicas if j != i]))
            for i in replicas}


def a2_scores(counts, replicas):
    """A2. 호출 수 초과분 비율. 비대칭."""
    return {i: float(np.mean([np.maximum(counts[i] - counts[j], 0).sum() / counts[i].sum()
                              for j in replicas if j != i]))
            for i in replicas}


# ---------------------------------------------------------------- 자기 참조
class ChannelB:
    """정상 기준 구성비 대비 차원별 z 점수의 최댓값. 학습 없음.

    오토인코더로 바꿔도 결과는 거의 같다(외부 동거 시나리오에서만 AE 가 유리).
    문서 8절 참조.
    """

    def __init__(self, buffer_counts, replicas):
        P = np.array([c[r] / c[r].sum() for c in buffer_counts for r in replicas])
        self.mu = P.mean(0)
        self.sd = np.maximum(P.std(0), 1e-5)
        self.threshold = max(self.score_one(c[r]) for c in buffer_counts for r in replicas)

    def score_one(self, v):
        return float(np.abs((v / v.sum() - self.mu) / self.sd).max())

    def scores(self, counts, replicas):
        return {r: self.score_one(counts[r]) for r in replicas}

    def fired(self, counts, replicas, quorum=0.25):
        """replica 중 quorum 이상이 임계값을 넘으면 True."""
        hits = [self.score_one(counts[r]) > self.threshold for r in replicas]
        return float(np.mean(hits)) >= quorum - 1e-9


# ---------------------------------------------------------------- 작업률
def work_rate(counts, replicas, vocab):
    """요청당 이벤트 수 = 전체 이벤트 / accept 수.

    총 이벤트 수를 그대로 쓰면 실제 드리프트(이벤트 -9%)와 정상 변동(±6.6%)이
    겹쳐 52% 오판한다. 요청 수로 나누면 분리된다.
    """
    idx = [i for i, k in enumerate(vocab) if k in ACCEPT_SYSCALLS]
    total = sum(counts[r].sum() for r in replicas)
    acc = sum(counts[r][idx].sum() for r in replicas)
    return total / max(acc, 1)


def c_score(counts, replicas, vocab, reference):
    return abs(np.log2(work_rate(counts, replicas, vocab) / reference))
