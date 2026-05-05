from sec_toolbox.throttle import TokenBucket


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def test_bucket_allows_capacity_immediately():
    clock = FakeClock()
    bucket = TokenBucket(rate=10, capacity=10, time_fn=clock.time, sleep_fn=clock.sleep)
    for _ in range(10):
        bucket.acquire()
    assert clock.sleeps == []


def test_bucket_blocks_when_empty_then_refills():
    clock = FakeClock()
    bucket = TokenBucket(rate=10, capacity=10, time_fn=clock.time, sleep_fn=clock.sleep)
    for _ in range(10):
        bucket.acquire()
    bucket.acquire()
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] >= 0.099  # ~1/rate seconds


def test_bucket_refills_continuously_over_time():
    clock = FakeClock()
    bucket = TokenBucket(rate=10, capacity=10, time_fn=clock.time, sleep_fn=clock.sleep)
    for _ in range(10):
        bucket.acquire()
    clock.t += 0.5  # half a second of real time → 5 tokens back
    for _ in range(5):
        bucket.acquire()
    assert clock.sleeps == []  # didn't have to sleep
