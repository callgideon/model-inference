#!/usr/bin/env bash
# Shared configuration. Every value is overridable from the environment so the
# same checkout works on an AWS node, a laptop, and a bare-metal box.

# Deliberately NOT defaulted: this repo is public, and the bucket name embeds an
# AWS account id. Export S3_BUCKET to enable the fast path; without it the
# downloader simply uses Hugging Face, which is the correct behaviour off-AWS
# anyway.
S3_BUCKET="${S3_BUCKET:-}"
S3_PREFIX="${S3_PREFIX:-weights}"
AWS_REGION="${AWS_REGION:-us-east-1}"

# Weights land on fast local disk, not the root volume. On a p6 node this is the
# RAID0 of the instance-store NVMe; override it anywhere else.
WEIGHTS_ROOT="${WEIGHTS_ROOT:-/mnt/nvme}"
