#!/usr/bin/env bash
# Inside the window, after 40-checkout and before 50-install: M1-L2's conformance
# (tests/m/test_s3.py) against the real media bucket with the box's instance role
# (INFRX_M_S3_LOCAL_CREDS unset), in the runtime image built from RELEASE - the image
# 50-install builds next (same content id, so it is reused). Each case writes under
# test/m1l2/<uuid>/ and empties it; nothing else in the bucket is touched. pytest and its
# four dependencies are uv.lock's wheels, hash-checked, in the throwaway container only.
# Needs RELEASE. A failure here stops the window before anything is installed (91-abort).
set -euo pipefail
: "${RELEASE:?the release commit}"
repo=/home/ubuntu/model-inference
cd "$repo"
[ "$(git -c safe.directory="$PWD" rev-parse HEAD)" = "$RELEASE" ] || { echo "run 40-checkout first" >&2; exit 2; }
docker build -q --provenance=false -f apps/infrx-api/deploy/Dockerfile -t "infrx-runtime:$RELEASE" apps/infrx-api >/dev/null
export INFRX_M_S3_ENDPOINT=https://s3.us-east-1.amazonaws.com
export INFRX_M_S3_BUCKET=${S3_MEDIA_BUCKET:-llm-bootcamp-641134885443}
docker run --rm --network host -v "$repo:/repo:ro" -w /repo/apps/infrx-api \
  -e INFRX_M_S3_ENDPOINT -e INFRX_M_S3_BUCKET --entrypoint sh "infrx-runtime:$RELEASE" -c '
cat > /tmp/req.txt <<REQ
pytest==9.1.1 --hash=sha256:37a86b45efb9a47a61a36449063e8e18d0cab3161329fc099eb21783169c4f0c
pluggy==1.6.0 --hash=sha256:e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746
iniconfig==2.3.0 --hash=sha256:f631c04d2c48c52b84d0d0549c99ff3859c98df65b3101406327ecc7d53fbf12
packaging==26.3 --hash=sha256:d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c
pygments==2.21.0 --hash=sha256:2363c69b61c4a97c838da3b130dcd6468f4848992b21a82f2a63ec34377137d9
REQ
/usr/local/bin/python -m pip install -q --no-cache-dir --disable-pip-version-check \
  --require-hashes --no-deps --only-binary :all: --target /tmp/pt -r /tmp/req.txt
PYTHONPATH=/tmp/pt python -m pytest -q -rs -p no:cacheprovider tests/m/test_s3.py'
echo "real-bucket conformance passed for $RELEASE"
