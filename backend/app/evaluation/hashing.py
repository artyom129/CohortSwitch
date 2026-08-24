import hashlib

DEFAULT_EVALUATION_NAMESPACE = "fv9"
BUCKET_COUNT = 10_000


def stable_bucket(
    project_id: str,
    environment_id: str,
    flag_key: str,
    subject_key: str,
    salt: str,
    *,
    namespace: str = DEFAULT_EVALUATION_NAMESPACE,
) -> int:
    parts = (namespace, project_id, environment_id, flag_key, subject_key, salt)
    digest = hashlib.blake2b(digest_size=16, person=b"cohort-switch")
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return int.from_bytes(digest.digest()[:8], "big") % BUCKET_COUNT
