# Infra — data storage

Bulk processed data (the CAISO bid parquet, LMP, CEMS extract, watermark) lives in a
**private** S3 bucket, not in this repo. The data is public CAISO information on a 90-day
lag, but we don't ship a pre-cleaned copy in a public repo.

## Bucket

- Name: `power-visual-data-507024406243` (account `507024406243`, region `us-west-1`)
- All public access blocked; versioning enabled.
- Layout mirrors `data/processed/`:
  ```
  s3://power-visual-data-507024406243/
    bids_dam/YYYY-MM-DD.parquet
    lmp/<node-slug>.parquet
    cems_targets.parquet
    _watermark.json
  ```

## Local use

Credentials come from the standard AWS chain (`~/.aws/credentials`, never the repo).

```bash
export POWER_DATA_BUCKET=power-visual-data-507024406243
python pipeline/s3_sync.py pull     # hydrate data/processed/ from S3
python pipeline/s3_sync.py push     # upload local bulk data to S3
```

If `POWER_DATA_BUCKET` is unset, the whole pipeline runs local-only (dev/tests).

## CI credential (action required — see repo owner)

The scheduled `daily-oasis-pull` workflow needs an AWS credential, supplied as **GitHub
encrypted secrets** (never committed):

- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` — repo secrets
- `POWER_DATA_BUCKET` — repo variable (already set to the bucket name; not sensitive)

**Recommended:** create a dedicated least-privilege CI user rather than using a personal
key. With an admin credential configured locally:

```bash
aws iam create-user --user-name power-visual-ci
aws iam put-user-policy --user-name power-visual-ci \
  --policy-name s3-bucket-access --policy-document file://infra/s3-bucket-policy.json
aws iam create-access-key --user-name power-visual-ci   # -> put these in GitHub secrets
```

Then set the secrets (values read from stdin so they never hit the shell history):

```bash
gh secret set AWS_ACCESS_KEY_ID   --repo CatAuditor/power   # paste when prompted
gh secret set AWS_SECRET_ACCESS_KEY --repo CatAuditor/power  # paste when prompted
```

`infra/s3-bucket-policy.json` scopes that user to read/write objects in this one bucket
and nothing else.
