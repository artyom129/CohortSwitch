import json
import subprocess
import sys

from backend.app.evaluation.hashing import stable_bucket


def bucket(subject: str) -> int:
    return stable_bucket("project", "production", "new_checkout", subject, "salt")


def test_same_subject_always_has_same_bucket() -> None:
    assert {bucket("user_42") for _ in range(100)} == {bucket("user_42")}


def test_bucket_is_stable_in_a_separate_process() -> None:
    program = (
        "from backend.app.evaluation.hashing import stable_bucket;"
        "import json;"
        "print(json.dumps(stable_bucket('project','production','new_checkout','user_42','salt')))"
    )
    output = subprocess.check_output([sys.executable, "-c", program], text=True)  # noqa: S603
    assert json.loads(output) == bucket("user_42")


def test_property_style_distribution_is_close_to_ten_percent() -> None:
    enabled = sum(bucket(f"subject-{index}") < 1000 for index in range(10_000))
    assert 850 <= enabled <= 1150


def test_rollout_growth_is_monotonic() -> None:
    five_percent = {subject for subject in range(5000) if bucket(str(subject)) < 500}
    ten_percent = {subject for subject in range(5000) if bucket(str(subject)) < 1000}
    twenty_percent = {subject for subject in range(5000) if bucket(str(subject)) < 2000}
    assert five_percent <= ten_percent <= twenty_percent


def test_bucket_boundaries_are_reachable_and_bounded() -> None:
    buckets = [bucket(f"boundary-{index}") for index in range(20_000)]
    assert min(buckets) >= 0
    assert max(buckets) <= 9999
    assert any(value < 500 for value in buckets)
    assert any(value >= 500 for value in buckets)
