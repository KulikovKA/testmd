import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests import test_ag_ui as existing_http
from tests.test_development_workflow import Model, Workspace
from universal_agent_runtime.adapters.fake_repository_platform import (
    FakeRepositoryPlatformAdapter,
)
from universal_agent_runtime.adapters.repository_access import (
    PublicRepositoryCredentialAdapter,
)
from universal_agent_runtime.application.development_workflow import JAVA_SKILLS
from universal_agent_runtime.composition import compose_application
from universal_agent_runtime.http_api import create_application


class DevelopmentHttpTests(unittest.TestCase):
    def app(self, root, model, workspace):
        original = existing_http.AGUIHttpTests()._app(root, model).state.composition
        return create_application(
            compose_application(
                original.settings,
                runtime=original.runtime,
                interaction=model,
                development_workspace=workspace,
                repository_platform=FakeRepositoryPlatformAdapter(root),
                repository_credentials=PublicRepositoryCredentialAdapter(
                    local_test_root=root
                ),
            )
        )

    def test_create_clarify_run_structured_result_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TestClient(self.app(root, Model(clarify=True), Workspace())) as client:
                agent = client.post(
                    "/agents",
                    json={
                        "request_id": "java-agent",
                        "skills": list(JAVA_SKILLS),
                        "tools": [],
                    },
                )
                self.assertEqual(agent.status_code, 201, agent.text)
                agent_id = agent.json()["agent_id"]
                self.assertEqual(
                    client.post(f"/agents/{agent_id}/start").status_code, 200
                )
                task = client.post(
                    f"/agents/{agent_id}/development-tasks",
                    json={"specification": "Java library"},
                )
                self.assertEqual(task.status_code, 201, task.text)
                task_id = task.json()["task_id"]
                service = client.app.state.composition.development.service
                service.claim(task_id)
                self.assertEqual(
                    client.post(f"/agents/{agent_id}/stop").status_code, 409
                )
                self.assertEqual(client.delete(f"/agents/{agent_id}").status_code, 409)
                service.release(task_id)
                endpoint = f"/ag-ui/development-tasks/{task_id}/run"
                payload = {"threadId": "development", "runId": "first"}
                events = existing_http._events(client.post(endpoint, json=payload).text)
                self.assertEqual(events[-1]["type"], "RUN_FINISHED")
                status = client.get(f"/development-tasks/{task_id}").json()
                self.assertEqual(status["state"], "WAITING_FOR_CLARIFICATION")
                self.assertEqual(status["questions"], ["CLI or library?"])
                blocked = existing_http._events(
                    client.post(endpoint, json=payload).text
                )
                self.assertEqual(blocked[-1]["code"], "clarification_required")
                self.assertEqual(
                    client.post(
                        f"/development-tasks/{task_id}/clarifications",
                        json={"answer": "Library"},
                    ).status_code,
                    200,
                )
                result = client.post(endpoint, json={**payload, "runId": "second"})
                self.assertEqual(
                    result.headers["content-type"], "text/event-stream; charset=utf-8"
                )
                self.assertEqual(
                    existing_http._events(result.text)[-1]["type"], "RUN_FINISHED"
                )
                completed = client.get(f"/development-tasks/{task_id}").json()
                self.assertEqual(completed["state"], "COMPLETED")
                self.assertEqual(
                    completed["result"]["execution_backend"],
                    "deterministic-test-double",
                )
                self.assertEqual(len(completed["result"]["commit_id"]), 40)
                self.assertNotIn("specification", completed)
                self.assertEqual(
                    client.post(f"/development-tasks/{task_id}/cancel").json()["state"],
                    "COMPLETED",
                )
                self.assertEqual(
                    client.get("/development-tasks/missing").status_code, 404
                )
                rejected = client.post(
                    f"/agents/{agent_id}/development-tasks",
                    json={"specification": "project", "branch": "--exec=secret"},
                )
                self.assertEqual(rejected.status_code, 422)
                self.assertNotIn("--exec=secret", rejected.text)

    def test_disabled_feature_and_tools_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TestClient(existing_http.AGUIHttpTests()._app(root)) as client:
                self.assertEqual(
                    client.post(
                        "/agents/agent-one/development-tasks",
                        json={"specification": "project"},
                    ).status_code,
                    503,
                )
            with TestClient(self.app(root, Model(), Workspace())) as client:
                agent = client.post(
                    "/agents", json={"request_id": "plain-agent"}
                ).json()["agent_id"]
                self.assertEqual(client.post(f"/agents/{agent}/start").status_code, 200)
                task = client.post(
                    f"/agents/{agent}/development-tasks",
                    json={"specification": "project"},
                ).json()["task_id"]
                response = client.post(
                    f"/ag-ui/development-tasks/{task}/run",
                    json={"threadId": "t", "runId": "r"},
                )
                self.assertEqual(
                    existing_http._events(response.text)[-1]["code"],
                    "agent_unavailable",
                )
                self.assertEqual(
                    client.post(f"/development-tasks/{task}/cancel").json()["state"],
                    "CANCELLED",
                )
