"""The saved model configuration shared by setup checks and review execution."""
import os

TEAM_MODEL_URL = "https://council-model-api-5l9p30nnv.gobrev.dev"
KEY_NAMES = {"brev": "BREV_API_KEY", "cisco": "CISCO_API_KEY", "paid": "PAID_API_KEY"}
MODEL_NAMES = {"brev": "OLLAMA_MODEL", "cisco": "CISCO_MODEL", "paid": "PAID_MODEL"}


def provider_key(provider):
    legacy = os.getenv("LLM_API_KEY", "") if provider == os.getenv("LLM_PROVIDER", "brev").lower() else ""
    return os.getenv(KEY_NAMES[provider], legacy).strip()


def provider_config(provider):
    legacy_model = os.getenv("LLM_MODEL", "") if provider == os.getenv("LLM_PROVIDER", "brev").lower() else ""
    return {
        "model": os.getenv(MODEL_NAMES[provider], "llama3.1:8b" if provider == "brev" else legacy_model).strip(),
        "base_url": (os.getenv("OLLAMA_BASE_URL", TEAM_MODEL_URL) if provider == "brev"
                     else os.getenv("LLM_BASE_URL", "") if provider == "cisco" else "").strip(),
    }


def runtime_settings():
    provider = os.getenv("LLM_PROVIDER", "brev").lower()
    if provider not in KEY_NAMES:
        raise ValueError("Choose a supported model provider in Connections.")
    config = provider_config(provider)
    key = provider_key(provider)
    if provider == "brev":
        options = {"api_base": config["base_url"]}
        if key:
            options.update(api_key=key, extra_headers={"Authorization": f"Bearer {key}"})
        return f"ollama/{config['model']}", options
    if provider == "cisco":
        return f"litellm_proxy/{config['model']}", {"api_base": config["base_url"], "api_key": key}
    return config["model"], {"api_key": key}
