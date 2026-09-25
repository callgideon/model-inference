O=/opt/dlami/nvme/e4b/20260924T202924Z
date -u +%FT%TZ
grep -E '^\[FAIL' $O/certify.log | cut -c1-3000
echo "--- soak progress: $(wc -l < $O/work/soak-raw.jsonl 2>/dev/null) rows; last row time:"; tail -1 $O/work/soak-raw.jsonl 2>/dev/null | cut -c1-300
echo "--- log tail:"; tail -5 $O/certify.log | cut -c1-300
docker ps --format '{{.Names}} {{.Status}}' | grep -i -E 'certify|gateway|worker|vllm|valkey'
