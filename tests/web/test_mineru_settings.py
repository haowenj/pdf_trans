import httpx
from dataclasses import replace

from pdf_trans.web.mineru_connection import discover_mineru_endpoint
from pdf_trans.web.mineru_settings import MinerUSettingsRepository


def test_manual_settings_survive_restart_and_reset_to_environment(repository, web_settings):
    defaults = replace(web_settings, mineru_url="http://env.local:7100", mineru_api_key="env-key")
    first = MinerUSettingsRepository(repository.sessions, defaults)
    assert first.get().url == "http://env.local:7100"
    assert first.get().api_key == "env-key"

    first.save(url="http://10.0.0.8:40005", gpustack_model="mineru4", api_key="saved-key")
    second = MinerUSettingsRepository(repository.sessions, defaults)
    assert second.get().url == "http://10.0.0.8:40005"
    assert second.get().api_key == "saved-key"
    second.save(url="http://10.0.0.9:40006", gpustack_model="mineru4", api_key="")
    assert second.get().api_key == "saved-key"

    second.reset()
    assert first.get().url == "http://env.local:7100"
    assert first.get().api_key == "env-key"


def test_settings_page_and_save_hide_secret(web_client):
    assert 'href="/settings"' in web_client.get("/").text
    response = web_client.post(
        "/settings/mineru",
        data={"url": "http://mineru.local:40005", "gpustack_model": "mineru4", "api_key": "private-key"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = web_client.get("/settings")
    assert "http://mineru.local:40005" in page.text
    assert "private-key" not in page.text
    assert "使用手动配置" in page.text
    assert web_client.app.state.mineru_settings.get().api_key == "private-key"
    assert web_client.post("/settings/mineru/reset", follow_redirects=False).status_code == 303
    assert "使用环境配置" in web_client.get("/settings").text


def test_settings_reject_cross_origin_and_invalid_url(web_client):
    assert web_client.post(
        "/settings/mineru", data={"url": "http://ok:7100"}, headers={"Origin": "http://foreign"}
    ).status_code == 403
    assert web_client.post(
        "/settings/mineru", data={"url": "file:///tmp/nope"}
    ).status_code == 400


def test_probe_uses_translation_gpustack_credentials_without_saving(web_client, monkeypatch):
    monkeypatch.setenv("TRANSLATION_BASE_URL", "http://gpu.local/v1")
    monkeypatch.setenv("TRANSLATION_API_KEY", "admin-secret")
    seen = []

    def handle(request):
        seen.append((str(request.url), request.headers.get("authorization")))
        return httpx.Response(200, json={"items": [{
            "id": 7, "name": "mineru4", "state": "running",
            "worker_ip": "10.0.0.8", "port": 40005,
        }], "pagination": {"totalPage": 1}})

    original_client = httpx.Client
    monkeypatch.setattr(
        "pdf_trans.web.routes.settings.httpx.Client",
        lambda: original_client(transport=httpx.MockTransport(handle)),
    )
    result = web_client.post("/settings/mineru/probe", data={"gpustack_model": "mineru4"}).json()
    assert result["url"] == "http://10.0.0.8:40005"
    assert seen == [("http://gpu.local/v2/model-instances?state=running&page=1&perPage=100", "Bearer admin-secret")]
    assert web_client.app.state.mineru_settings.get().url == "http://127.0.0.1:7100"


def test_discovery_resolves_route_target_when_instance_name_differs():
    def handle(request):
        if request.url.path.endswith("model-instances"):
            items = [{"id": 2, "model_id": 42, "name": "model-internal", "state": "running", "worker_advertise_address": "10.0.0.10", "ports": [40100]}]
        else:
            items = [{"route_name": "mineru4", "model_id": 42, "state": "active"}]
        return httpx.Response(200, json={"items": items, "pagination": {"totalPage": 1}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert discover_mineru_endpoint("http://gpu.local/v1", "mineru4", "key", client) == "http://10.0.0.10:40100"


def test_probe_failure_does_not_change_manual_settings(web_client, monkeypatch):
    web_client.app.state.mineru_settings.save(
        url="http://manual.local:7100", gpustack_model="mineru4", api_key=""
    )
    monkeypatch.delenv("TRANSLATION_API_KEY", raising=False)
    result = web_client.post("/settings/mineru/probe", data={"gpustack_model": "mineru4"}).json()
    assert result["ok"] is False
    assert web_client.app.state.mineru_settings.get().url == "http://manual.local:7100"


def test_health_check_uses_saved_key_and_does_not_expose_upstream_body(web_client, monkeypatch):
    web_client.app.state.mineru_settings.save(
        url="http://mineru.local:7100", gpustack_model="mineru4", api_key="private-key"
    )
    seen = []

    def handle(request):
        seen.append((str(request.url), request.headers.get("authorization")))
        return httpx.Response(401, text="private upstream detail")

    original_client = httpx.Client
    monkeypatch.setattr(
        "pdf_trans.web.routes.settings.httpx.Client",
        lambda: original_client(transport=httpx.MockTransport(handle)),
    )
    result = web_client.post("/settings/mineru/test", data={"url": "http://mineru.local:7100"}).json()
    assert result["ok"] is False
    assert "private upstream detail" not in result["message"]
    assert seen == [("http://mineru.local:7100/v1/health", "Bearer private-key")]
