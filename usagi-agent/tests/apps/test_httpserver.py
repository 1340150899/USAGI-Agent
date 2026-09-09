from pathlib import Path
import sys

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[3] / "apps" / "httpserver"))
from usagi_httpserver.api import create_app
from examples.structured_agent.run import build_server


def test_http_no_longer_exposes_approval_control_endpoints(tmp_path):
    server = build_server(tmp_path / "runtime.db", requires_approval=True)
    owner = "owner-token-with-at-least-24-characters"
    other = "other-token-with-at-least-24-characters"
    app = create_app(server, credentials={owner: "owner", other: "other"},
                     scenario_key="example.research_writer")
    headers = {"Authorization": f"Bearer {owner}"}
    with TestClient(app) as client:
        assert client.get("/v1/tools").status_code == 401
        response = client.post("/v1/sessions", headers=headers,
                               json={"query": "research", "request_idempotency_key": "one"})
        assert response.status_code == 200, response.text
        run = response.json()
        assert run["outcome"]["kind"] == "suspended"
        path = f"/v1/runs/{run['run_id']}/approvals"
        assert client.get(path, headers={"Authorization": f"Bearer {other}"}).status_code == 404
        assert client.get(path, headers=headers).status_code == 404
