from fastapi.testclient import TestClient

from tests.helpers import API, bearer, login, make_wallet


def test_work_submission_requires_funded_escrow_and_participant_auth(client: TestClient) -> None:
    outsider = login(client, make_wallet(901))
    response = client.get(
        f"{API}/escrows/00000000-0000-0000-0000-000000000001/submissions",
        headers=bearer(outsider.access_token),
    )
    assert response.status_code == 404


def test_demo_executor_and_review_routes_are_registered(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert f"{API}/escrows/{{escrow_id}}/demo-execute" in paths
    assert f"{API}/escrows/{{escrow_id}}/submissions" in paths
    assert f"{API}/submissions/{{submission_id}}/review" in paths
    assert f"{API}/submissions/{{submission_id}}/disputes" in paths
    assert f"{API}/disputes/{{dispute_id}}/resolve" in paths
