# Shared Brev model

The `council-gpu` instance runs `llama3.1:8b` on a T4. A separate authenticated
gateway listens on port 11435 and forwards inference calls to Ollama on loopback
port 11434. Brev's HTTP port sharing provides the shared HTTPS base URL. The
gateway runs on Brev, so teammate access does not depend on Nico's laptop.

## Teammate setup

Use the updated Council client with authenticated Brev support. In **Connections**,
choose **Use team Brev model**, paste the team key, and click **Save model**.
The preset supplies these values:

- **Model:** `llama3.1:8b`
- **Base URL:** `https://council-model-api-5l9p30nnv.gobrev.dev` (no `/api` suffix)
- **LLM API key:** the shared model access key supplied privately by Nico
- **Concurrent LLM calls:** `1`

Enter the repository and click **Watch and review**. Council automatically queues
open PRs and changed revisions. No Brev login or local port forward is needed. The same settings can be configured in a local `.env`:

```dotenv
LLM_PROVIDER=brev
OLLAMA_MODEL=llama3.1:8b
OLLAMA_BASE_URL=https://council-model-api-5l9p30nnv.gobrev.dev
LLM_API_KEY=<shared-model-access-key>
MAX_CONCURRENT_LLM_CALLS=1
BUS=memory
```

Use each teammate's own GitHub token separately. The model key is not a Brev
account credential. Keep it out of Git. All callers share the instance's GPU
and credits; the gateway serializes requests and bounds the queue to 16 calls.

## Service management

The gateway runs as a systemd service on `council-gpu`:

```bash
brev exec council-gpu "sudo systemctl status council-model-gateway --no-pager"
```

The gateway source and private `gateway.env` are in
`/home/ubuntu/council-model/` on the instance. `scripts/setup-model-gateway.sh`
installs its dependencies, verifies Cloudflare's official binary checksum, and
starts the gateway and an optional Cloudflare development fallback. Copy the
source and environment file before running it. In Brev's **Access → HTTP Ports**,
create a link for port **11435**, choose a custom hostname, and disable Brev's
browser authentication for direct API calls. The gateway's bearer key remains
required. Never expose the raw Ollama port 11434 through this public link.

Only inference and metadata endpoints are forwarded. Keys are checked before
contacting Ollama; model download, creation, deletion, arbitrary routes, and
streaming calls are blocked. Council uses non-streaming model requests while
its review messages stream separately from each teammate's local Council server.

If using the fallback Cloudflare Quick Tunnel, its hostname changes if the tunnel
restarts, and it has no uptime guarantee. Retrieve its URL from the
`council-model-tunnel` service logs and update settings after a restart. Prefer the
Brev HTTP link when available. The fallback can be stopped once Brev is verified.

[Cloudflare Quick Tunnel documentation](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
