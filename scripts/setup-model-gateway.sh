#!/usr/bin/env bash
# Run on the Brev instance after copying model_gateway.py and gateway.env here.
set -euo pipefail
gateway_dir=/home/ubuntu/council-model
cd "$gateway_dir"
chmod 700 "$gateway_dir"
chmod 600 gateway.env
if ! python3 -m venv venv; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv
  python3 -m venv venv
fi
venv/bin/pip install --quiet fastapi uvicorn httpx
python3 - <<'PY'
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

request = Request('https://api.github.com/repos/cloudflare/cloudflared/releases/latest',
                  headers={'User-Agent': 'Council-model-setup'})
with urlopen(request, timeout=30) as response:
    release = json.load(response)
asset = next(asset for asset in release['assets'] if asset['name'] == 'cloudflared-linux-amd64')
expected = asset.get('digest', '')
if not expected.startswith('sha256:'):
    raise SystemExit('Official release is missing its SHA256 digest; installation stopped.')
with urlopen(asset['browser_download_url'], timeout=60) as response:
    binary = response.read()
if hashlib.sha256(binary).hexdigest() != expected.removeprefix('sha256:'):
    raise SystemExit('Cloudflared checksum mismatch; installation stopped.')
path = Path('cloudflared')
path.write_bytes(binary)
path.chmod(0o755)
print('Verified official cloudflared release:', release['tag_name'])
PY
sudo tee /etc/systemd/system/council-model-gateway.service >/dev/null <<'UNIT'
[Unit]
Description=Authenticated Council model gateway
After=network-online.target ollama.service
Wants=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/council-model
EnvironmentFile=/home/ubuntu/council-model/gateway.env
ExecStart=/home/ubuntu/council-model/venv/bin/python -m uvicorn model_gateway:from_environment --factory --host 0.0.0.0 --port 11435 --no-access-log
Restart=on-failure
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now council-model-gateway
python3 - <<'PY'
import time
from urllib.request import Request, urlopen
from pathlib import Path

token = next(line.split('=', 1)[1] for line in Path('gateway.env').read_text().splitlines()
             if line.startswith('MODEL_GATEWAY_TOKEN='))
for attempt in range(20):
    try:
        request = Request('http://127.0.0.1:11435/api/tags', headers={'Authorization': 'Bearer ' + token})
        with urlopen(request, timeout=5) as response:
            assert response.status == 200
        print('Authenticated gateway ready.')
        break
    except Exception:
        if attempt == 19:
            raise SystemExit('Gateway did not become ready; tunnel was not started.')
        time.sleep(1)
PY
sudo tee /etc/systemd/system/council-model-tunnel.service >/dev/null <<'UNIT'
[Unit]
Description=Council shared model HTTPS tunnel
After=network-online.target council-model-gateway.service
Requires=council-model-gateway.service
Wants=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/council-model
ExecStart=/home/ubuntu/council-model/cloudflared tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:11435
Restart=on-failure
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now council-model-tunnel
sudo systemctl is-active council-model-gateway council-model-tunnel
