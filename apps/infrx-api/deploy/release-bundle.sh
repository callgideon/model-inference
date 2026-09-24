#!/usr/bin/env bash
# Ship one commit to the box through the project bucket (W3's route): a self-contained git
# bundle of the commit (its whole history, one ref), a sha256 manifest beside it, and the
# box-side step that fetches, verifies and makes the commit available to 40-checkout.sh.
#
#   apps/infrx-api/deploy/release-bundle.sh <40-hex commit>        # coordinator host
#
# Uploads s3://$BUCKET/$PREFIX<name>.bundle and <name>.sha256 (name = the commit, or NAME
# for a test object) and writes <out>/<name>.fetch.sh, the box step, for
# `infra/rollout/ssm.sh <out>/<name>.fetch.sh`. Touches neither the box nor this repository's
# refs. NO_UPLOAD=1 builds, checks and prints only.
set -euo pipefail
sha=${1:?usage: release-bundle.sh <40-hex commit>}
[[ $sha =~ ^[0-9a-f]{40}$ ]] || { echo "not a full commit id: $sha" >&2; exit 2; }
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(git -C "$here" rev-parse --show-toplevel)
git -C "$repo" cat-file -e "$sha^{commit}" 2>/dev/null || { echo "no commit $sha in $repo" >&2; exit 2; }
BUCKET=${BUCKET:-llm-bootcamp-641134885443}
PREFIX=${PREFIX:-releases/}
name=${NAME:-$sha}
out=${OUT:-$(mktemp -d)}
mkdir -p "$out"
REF=refs/infrx/release
aws() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
        aws --region us-east-1 "$@"; }

# A bundle needs a ref, and this repository's refs are not ours to add: a throwaway repo
# that borrows its objects (alternates) carries the one ref.
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git init -q --bare "$tmp/src"
objects=$(cd "$repo" && cd "$(git rev-parse --git-common-dir)" && pwd)/objects
echo "$objects" > "$tmp/src/objects/info/alternates"
git -C "$tmp/src" update-ref "$REF" "$sha"
git -C "$tmp/src" bundle create -q "$out/$name.bundle" "$REF"
( cd "$out" && sha256sum "$name.bundle" > "$name.sha256" )

# The round trip the box will make, here first: an empty repository gets the commit.
git init -q --bare "$tmp/check"
git -C "$tmp/check" fetch -q "$out/$name.bundle" "$REF:$REF"
[ "$(git -C "$tmp/check" rev-parse "$REF")" = "$sha" ] || { echo "the bundle does not yield $sha" >&2; exit 3; }

url=s3://$BUCKET/$PREFIX$name
cat > "$out/$name.fetch.sh" <<EOF
#!/usr/bin/env bash
# Box, as root: fetch release $sha into the checkout. The working tree is not touched
# (40-checkout.sh does that inside the window); the bundle is checked against its manifest.
set -euo pipefail
d=\${RELEASES_DIR:-/opt/dlami/nvme/releases}
repo=\${BOX_REPO:-/home/ubuntu/model-inference}
mkdir -p "\$d"
aws s3 cp --only-show-errors --region us-east-1 "$url.bundle" "\$d/$name.bundle"
aws s3 cp --only-show-errors --region us-east-1 "$url.sha256" "\$d/$name.sha256"
( cd "\$d" && sha256sum -c "$name.sha256" )
chmod 0644 "\$d/$name.bundle"
sudo -u ubuntu git -C "\$repo" bundle verify -q "\$d/$name.bundle"
sudo -u ubuntu git -C "\$repo" fetch -q "\$d/$name.bundle" "+$REF:refs/infrx/releases/$sha"
[ "\$(sudo -u ubuntu git -C "\$repo" rev-parse "refs/infrx/releases/$sha^{commit}")" = $sha ]
echo "release $sha is in \$repo; the working tree is unchanged"
EOF

if [ -z "${NO_UPLOAD:-}" ]; then
  aws s3 cp --only-show-errors "$out/$name.bundle" "$url.bundle"
  aws s3 cp --only-show-errors "$out/$name.sha256" "$url.sha256"
fi
echo "release $sha"
echo "bundle $out/$name.bundle ($(stat -c %s "$out/$name.bundle") bytes)"
echo "sha256 $(cut -d' ' -f1 "$out/$name.sha256")"
echo "${NO_UPLOAD:+not }uploaded $url.bundle $url.sha256"
echo "box step $out/$name.fetch.sh:"
cat "$out/$name.fetch.sh"
