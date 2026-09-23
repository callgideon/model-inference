# Read-only box inspection (BOX lane step 1): units, edge, checkout, disk. Changes nothing.
set -uo pipefail
echo "utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "### units"
for u in marlin2b-vllm marlin2b-gateway infrx-worker infrx-valkey; do
  echo "$u active=$(systemctl is-active $u 2>&1) enabled=$(systemctl is-enabled $u 2>&1)"
done
echo "vllm_execstart=$(systemctl show -p ExecStart --value marlin2b-vllm)"
echo "vllm_consistsof=$(systemctl show -p ConsistsOf --value marlin2b-vllm)"
echo "vllm_fragment=$(systemctl show -p FragmentPath --value marlin2b-vllm)"
echo "vllm_execstartpre=$(systemctl show -p ExecStartPre --value marlin2b-vllm | head -c 600)"
echo "### engine container"
docker inspect --format 'image={{.Image}} config_image={{.Config.Image}} started={{.State.StartedAt}} restart={{.HostConfig.RestartPolicy.Name}} status={{.State.Status}}' marlin2b-8000
echo "### caddy container"
docker inspect --format 'image={{.Image}} config_image={{.Config.Image}} network={{.HostConfig.NetworkMode}} readonly={{.HostConfig.ReadonlyRootfs}} restart={{.HostConfig.RestartPolicy.Name}} started={{.State.StartedAt}} status={{.State.Status}}' caddy
echo "caddy_cmd=$(docker inspect --format '{{json .Config.Cmd}} entrypoint={{json .Config.Entrypoint}}' caddy)"
echo "caddy_ports=$(docker inspect --format '{{json .HostConfig.PortBindings}}' caddy)"
echo "caddy_mounts=$(docker inspect --format '{{range .Mounts}}{{.Type}}:{{.Source}}:{{.Destination}}:{{.RW}} {{end}}' caddy)"
echo "caddy_env_names=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' caddy | cut -d= -f1 | tr '\n' ' ')"
echo "caddy_version=$(docker exec caddy caddy version 2>&1)"
echo "### live Caddyfile"
ls -li /etc/caddy/ 2>&1
echo "sha256=$(sha256sum /etc/caddy/Caddyfile)"
# content with any line naming a credential-like word redacted (none expected)
sed -E 's/^.*(key|token|secret|passw|bearer|basicauth|basic_auth|authorization).*$/[redacted line]/I' /etc/caddy/Caddyfile
echo "### admin endpoint"
curl -sS -m 3 -o /dev/null -w 'admin_2019_http=%{http_code}\n' http://127.0.0.1:2019/config/ 2>&1
docker exec caddy ls -la /config /config/caddy 2>&1 | head -12
echo "### gateway + public route (loopback, via Caddy with SNI)"
curl -sS -m 5 -o /dev/null -w 'gw_8001_health=%{http_code}\n' http://127.0.0.1:8001/health 2>&1
curl -sS -m 10 --resolve marlin2b.callbill.ai:443:127.0.0.1 -w '\nedge_health=%{http_code}\n' https://marlin2b.callbill.ai/health 2>&1 | tail -3
echo "### checkout + disk + tools"
git -c safe.directory='*' -C /opt/dlami/nvme/w3-checkout log --oneline -1 2>&1
git -c safe.directory='*' -C /opt/dlami/nvme/w3-checkout status --short 2>&1 | head -5
ls /opt/dlami/nvme/ 2>&1 | tr '\n' ' '; echo
du -sh /opt/dlami/nvme/w3-corpus 2>&1; ls /opt/dlami/nvme/w3-corpus 2>&1 | tr '\n' ' '; echo
ls /opt/dlami/nvme/samples 2>&1 | tr '\n' ' '; echo
df -h /opt/dlami/nvme | tail -1
free -m | head -2
nproc; lscpu | grep -E 'Model name' 
which aws git ffmpeg 2>&1 | tr '\n' ' '; echo
/opt/pytorch/bin/python3 -c 'import sys,transformers,torch; print("py", sys.version.split()[0], "transformers", transformers.__version__, "torch", torch.__version__)' 2>&1 | tail -1
/opt/pytorch/bin/python3 -c 'import openai, av, httpx; print("openai", openai.__version__, "av", av.__version__, "httpx", httpx.__version__)' 2>&1 | tail -1
echo "### in flight on the engine"
curl -sS -m 5 http://127.0.0.1:8000/metrics | grep -E '^vllm:num_requests_(running|waiting)[{ ]'
echo "### gateway usage log tail timestamps (traffic check, no content)"
tail -n 3 /opt/dlami/nvme/logs/usage.jsonl 2>/dev/null | python3 -c 'import sys,json
for l in sys.stdin:
  try: d=json.loads(l); print({k:d.get(k) for k in ("ts","time","status","stream") if k in d})
  except Exception as e: print("unparsed", type(e).__name__)'
echo "usage_lines=$(wc -l < /opt/dlami/nvme/logs/usage.jsonl 2>/dev/null)"
echo "### s3 access (list only)"
aws s3 ls s3://llm-bootcamp-641134885443/ --region us-east-1 2>&1 | head
echo "### marlin2b modeling caption signature"
grep -n -E 'def (caption|find)|apply_chat_template|videos_kwargs|size|fps' /opt/dlami/nvme/marlin2b/modeling_marlin.py 2>&1 | head -30
