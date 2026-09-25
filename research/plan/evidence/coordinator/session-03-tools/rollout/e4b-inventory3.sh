set -euo pipefail
: "${RELEASE:?}"
c=/opt/dlami/nvme/w3-checkout
echo "w3-checkout at $(git -c safe.directory=$c -C $c rev-parse --short HEAD) ($(git -c safe.directory=$c -C $c status --porcelain | wc -l) dirty)"
CONTAINER=marlin2b-8000 ENGINE=http://127.0.0.1:8000 WEIGHTS=/opt/dlami/nvme/marlin2b bash "$c/models/marlin2b/measure/inventory.sh" > /opt/dlami/nvme/e4b/inventory.txt 2>&1 || echo "inventory exit $?"
chmod 644 /opt/dlami/nvme/e4b/inventory.txt; wc -l /opt/dlami/nvme/e4b/inventory.txt; grep -iE "image|max-num-seqs|digest" /opt/dlami/nvme/e4b/inventory.txt | head -4 | cut -c1-160
