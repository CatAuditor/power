"""Sync bulk processed data to/from a private S3 bucket (keeps it out of the repo).

The CAISO bid data is public but we don't want to hand a pre-cleaned copy to
anyone but us, so the bulk parquet lives in a private bucket instead of git.
Small, repo-appropriate artifacts (map/public/plants.json, resource_map.json)
stay in git — only these bulk paths are synced:

  data/processed/bids_dam/*.parquet   -> s3://$POWER_DATA_BUCKET/bids_dam/
  data/processed/lmp/*.parquet        -> s3://$POWER_DATA_BUCKET/lmp/
  data/processed/cems_targets.parquet -> s3://$POWER_DATA_BUCKET/cems_targets.parquet
  data/processed/_watermark.json      -> s3://$POWER_DATA_BUCKET/_watermark.json

Config via environment (never hardcoded, never committed):
  POWER_DATA_BUCKET  required to enable S3; unset => all functions no-op
  AWS_REGION         optional, default us-west-1
  Credentials come from the standard AWS chain (~/.aws or GH Actions secrets).

If POWER_DATA_BUCKET is unset the pipeline runs fully local (dev/tests).
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"

# (local path relative to PROCESSED, s3 key prefix or exact key, is_dir)
SYNC_TARGETS = [
    ("bids_dam", "bids_dam", True),
    ("lmp", "lmp", True),
    ("cems_targets.parquet", "cems_targets.parquet", False),
    ("_watermark.json", "_watermark.json", False),
]


def bucket() -> str | None:
    return os.environ.get("POWER_DATA_BUCKET")


def enabled() -> bool:
    return bool(bucket())


def _client():
    import boto3
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-west-1"))


def _list_keys(s3, prefix: str) -> dict[str, int]:
    """key -> size for everything under prefix."""
    out = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket(), Prefix=prefix):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = obj["Size"]
    return out


def push(verbose: bool = True) -> int:
    """Upload local bulk data to S3. Uploads files absent or size-differing.

    Returns number of objects uploaded.
    """
    if not enabled():
        return 0
    s3 = _client()
    n = 0
    for local_rel, key_base, is_dir in SYNC_TARGETS:
        local = PROCESSED / local_rel
        if is_dir:
            if not local.exists():
                continue
            remote = _list_keys(s3, key_base + "/")
            for f in sorted(local.glob("*")):
                key = f"{key_base}/{f.name}"
                if remote.get(key) == f.stat().st_size:
                    continue
                s3.upload_file(str(f), bucket(), key)
                n += 1
        else:
            if not local.exists():
                continue
            remote = _list_keys(s3, key_base)
            if remote.get(key_base) != local.stat().st_size:
                s3.upload_file(str(local), bucket(), key_base)
                n += 1
    if verbose:
        print(f"s3 push: {n} object(s) -> s3://{bucket()}", flush=True)
    return n


def pull(verbose: bool = True) -> int:
    """Download S3 bulk data to local, skipping files already present same-size.

    Returns number of objects downloaded.
    """
    if not enabled():
        return 0
    s3 = _client()
    n = 0
    for local_rel, key_base, is_dir in SYNC_TARGETS:
        local = PROCESSED / local_rel
        if is_dir:
            local.mkdir(parents=True, exist_ok=True)
            for key, size in _list_keys(s3, key_base + "/").items():
                dest = PROCESSED / key
                if dest.exists() and dest.stat().st_size == size:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(bucket(), key, str(dest))
                n += 1
        else:
            for key, size in _list_keys(s3, key_base).items():
                if key != key_base:
                    continue
                if local.exists() and local.stat().st_size == size:
                    continue
                local.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(bucket(), key, str(local))
                n += 1
    if verbose:
        print(f"s3 pull: {n} object(s) <- s3://{bucket()}", flush=True)
    return n


def pull_watermark() -> bool:
    """Fetch just the watermark from S3 (authoritative state for a fresh runner)."""
    if not enabled():
        return False
    s3 = _client()
    dest = PROCESSED / "_watermark.json"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket(), "_watermark.json", str(dest))
        return True
    except Exception:
        return False  # no watermark yet (first run)


if __name__ == "__main__":
    import sys
    if not enabled():
        raise SystemExit("POWER_DATA_BUCKET not set")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "push"
    print(pull() if cmd == "pull" else push())
