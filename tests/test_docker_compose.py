from pathlib import Path
import re


def _service_block(compose_text: str, service_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service_name)}:\n(?P<body>.*?)(?=^  [\w-]+:|\Z)",
        compose_text,
    )
    assert match is not None, f"service {service_name!r} missing"
    return match.group("body")


def test_api_and_worker_share_env_file_and_host_gateway():
    compose_text = Path("docker-compose.yml").read_text(encoding="utf-8")

    for service_name in ("api", "worker"):
        block = _service_block(compose_text, service_name)
        assert "env_file:" in block
        assert "- .env" in block
        assert "extra_hosts:" in block
        assert "host.docker.internal:host-gateway" in block
