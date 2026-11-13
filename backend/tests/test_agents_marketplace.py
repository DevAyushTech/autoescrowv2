from typing import Any

from fastapi.testclient import TestClient

from autoescrow.infrastructure.database import Database
from autoescrow.services.audit import AuditEventType
from tests.domain_helpers import agent_payload, create_agent, error_code
from tests.helpers import API, audit_events, login, make_wallet


def test_agent_endpoints_require_authentication(client: TestClient) -> None:
    unauthenticated = client.post(f"{API}/agents", json=agent_payload())
    assert unauthenticated.status_code == 401
    assert error_code(unauthenticated) == "authentication_required"

    assert client.get(f"{API}/agents").status_code == 401
    assert (
        client.post(
            f"{API}/agents",
            json=agent_payload(),
            headers={"Authorization": "Bearer not-a-token"},
        ).status_code
        == 401
    )

    marketplace = client.get(f"{API}/marketplace/agents")
    assert marketplace.status_code == 200
    assert marketplace.json()["total"] == 0


async def test_create_agent_persists_normalized_profile_and_audit(
    client: TestClient, database: Database
) -> None:
    session = login(client, make_wallet(201))

    response = client.post(
        f"{API}/agents",
        json=agent_payload(
            name="  Atlas Writer  ",
            capabilities=[" SEO ", "Copywriting", "seo"],
            skills=[" Technical-Writing ", "editing", "technical-writing"],
            supported_task_types=[" Documentation ", "blog-post", "documentation"],
            currency="usd",
            price_amount="250.456",
        ),
        headers=session.headers,
    )

    assert response.status_code == 422

    response = client.post(
        f"{API}/agents",
        json=agent_payload(
            name="  Atlas Writer  ",
            capabilities=[" SEO ", "Copywriting", "seo"],
            skills=[" Technical-Writing ", "editing", "technical-writing"],
            supported_task_types=[" Documentation ", "blog-post", "documentation"],
            currency="usd",
            price_amount="250.45",
        ),
        headers=session.headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Atlas Writer"
    assert body["skills"] == ["editing", "technical-writing"]
    assert body["capabilities"] == ["copywriting", "seo"]
    assert body["supported_task_types"] == ["blog-post", "documentation"]
    assert body["currency"] == "USD"
    assert body["price_amount"] == "250.45"
    assert body["status"] == "active"
    assert body["is_public"] is True
    assert body["owner_display_name"] == session.user["display_name"]
    assert "owner_user_id" not in body
    assert "owner_email" not in body
    assert response.headers["Cache-Control"] == "no-store"

    reread = client.get(f"{API}/agents/{body['id']}", headers=session.headers)
    assert reread.status_code == 200
    assert reread.json()["id"] == body["id"]

    events = await audit_events(database, AuditEventType.AGENT_CREATED.value)
    assert len(events) == 1
    assert str(events[0]["subject_id"]) == body["id"]
    assert str(events[0]["actor_user_id"]) == session.user["id"]
    assert events[0]["metadata"]["status"] == "active"


def test_agent_owner_isolation(client: TestClient) -> None:
    owner = login(client, make_wallet(202))
    stranger = login(client, make_wallet(203))
    agent = create_agent(client, owner, name="Owner Agent")

    assert client.get(f"{API}/agents/{agent['id']}", headers=stranger.headers).status_code == 404
    assert (
        client.patch(
            f"{API}/agents/{agent['id']}",
            json={"name": "Hijacked"},
            headers=stranger.headers,
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{API}/agents/{agent['id']}/deactivate",
            headers=stranger.headers,
        ).status_code
        == 404
    )
    listing = client.get(f"{API}/agents", headers=stranger.headers)
    assert listing.status_code == 200
    assert listing.json()["items"] == []
    assert listing.json()["total"] == 0


def test_agent_name_is_unique_per_owner_case_insensitively(client: TestClient) -> None:
    first = login(client, make_wallet(204))
    second = login(client, make_wallet(205))
    create_agent(client, first, name="Shared Name")

    duplicate = client.post(
        f"{API}/agents",
        json=agent_payload(name="shared name"),
        headers=first.headers,
    )
    assert duplicate.status_code == 409
    assert error_code(duplicate) == "agent_name_unavailable"

    other_owner = client.post(
        f"{API}/agents",
        json=agent_payload(name="shared name"),
        headers=second.headers,
    )
    assert other_owner.status_code == 201


def test_agent_update_and_status_lifecycle(client: TestClient) -> None:
    owner = login(client, make_wallet(206))
    created = client.post(
        f"{API}/agents",
        json=agent_payload(name="Draft Agent", status="draft", is_public=False),
        headers=owner.headers,
    )
    assert created.status_code == 201, created.text
    agent = created.json()

    activated = client.patch(
        f"{API}/agents/{agent['id']}",
        json={
            "name": "Published Agent",
            "status": "active",
            "is_public": True,
            "availability": "limited",
        },
        headers=owner.headers,
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["name"] == "Published Agent"
    assert activated.json()["status"] == "active"
    assert activated.json()["is_public"] is True
    assert activated.json()["availability"] == "limited"

    public_listing = client.get(f"{API}/marketplace/agents")
    assert public_listing.json()["total"] == 1

    deactivated = client.patch(
        f"{API}/agents/{agent['id']}",
        json={"status": "inactive"},
        headers=owner.headers,
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "inactive"
    assert deactivated.json()["is_public"] is False
    assert client.get(f"{API}/marketplace/agents").json()["total"] == 0

    reactivated = client.patch(
        f"{API}/agents/{agent['id']}",
        json={"status": "active"},
        headers=owner.headers,
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == "active"
    assert reactivated.json()["is_public"] is False
    assert client.get(f"{API}/marketplace/agents").json()["total"] == 0

    explicitly_deactivated = client.post(
        f"{API}/agents/{agent['id']}/deactivate",
        headers=owner.headers,
    )
    assert explicitly_deactivated.status_code == 200
    assert explicitly_deactivated.json()["status"] == "inactive"
    assert explicitly_deactivated.json()["is_public"] is False


def test_agent_update_rejects_null_empty_and_unknown_fields(client: TestClient) -> None:
    owner = login(client, make_wallet(207))
    agent = create_agent(client, owner)

    assert (
        client.patch(
            f"{API}/agents/{agent['id']}",
            json={"name": None},
            headers=owner.headers,
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"{API}/agents/{agent['id']}",
            json={},
            headers=owner.headers,
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"{API}/agents/{agent['id']}",
            json={"owner_user_id": agent["id"]},
            headers=owner.headers,
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"{API}/agents/{agent['id']}",
            json={"status": "deleted"},
            headers=owner.headers,
        ).status_code
        == 422
    )


def test_agent_create_validation(client: TestClient) -> None:
    owner = login(client, make_wallet(208))

    inactive = client.post(
        f"{API}/agents",
        json=agent_payload(status="inactive"),
        headers=owner.headers,
    )
    assert inactive.status_code == 422
    assert error_code(inactive) == "invalid_agent_status"

    for invalid_payload in (
        agent_payload(price_amount="0"),
        agent_payload(currency="US"),
        agent_payload(skills=[" ", ""]),
        agent_payload(capabilities=[]),
        agent_payload(description=" "),
        agent_payload(category="unknown"),
        agent_payload(pricing_model="free"),
        agent_payload(availability="sometimes"),
        agent_payload(name="A"),
    ):
        response = client.post(f"{API}/agents", json=invalid_payload, headers=owner.headers)
        assert response.status_code == 422, response.text


def test_agent_list_pagination_is_scoped_to_owner(client: TestClient) -> None:
    owner = login(client, make_wallet(209))
    other = login(client, make_wallet(210))
    create_agent(client, owner, name="Alpha Agent")
    create_agent(client, owner, name="Beta Agent")
    create_agent(client, owner, name="Gamma Agent")
    create_agent(client, other, name="Other Agent")

    first_page = client.get(f"{API}/agents", params={"limit": 2}, headers=owner.headers)
    assert first_page.status_code == 200
    assert first_page.json()["total"] == 3
    assert len(first_page.json()["items"]) == 2

    second_page = client.get(
        f"{API}/agents",
        params={"limit": 2, "offset": 2},
        headers=owner.headers,
    )
    assert second_page.status_code == 200
    assert len(second_page.json()["items"]) == 1
    assert {item["name"] for item in first_page.json()["items"]}.isdisjoint(
        {item["name"] for item in second_page.json()["items"]}
    )


def test_marketplace_lists_only_active_public_agents(client: TestClient) -> None:
    owner = login(client, make_wallet(211))
    public = create_agent(client, owner, name="Public Agent")
    private = create_agent(client, owner, name="Private Agent", is_public=False)
    draft = client.post(
        f"{API}/agents",
        json=agent_payload(name="Draft Agent", status="draft"),
        headers=owner.headers,
    ).json()
    deactivated = create_agent(client, owner, name="Retired Agent")
    assert (
        client.post(
            f"{API}/agents/{deactivated['id']}/deactivate",
            headers=owner.headers,
        ).status_code
        == 200
    )

    listing = client.get(f"{API}/marketplace/agents")
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [public["id"]]
    item = body["items"][0]
    assert "owner_user_id" not in item
    assert "owner_email" not in item
    assert item["owner_display_name"] == owner.user["display_name"]

    assert client.get(f"{API}/marketplace/agents/{public['id']}").status_code == 200
    assert client.get(f"{API}/marketplace/agents/{private['id']}").status_code == 404
    assert client.get(f"{API}/marketplace/agents/{draft['id']}").status_code == 404
    assert client.get(f"{API}/marketplace/agents/{deactivated['id']}").status_code == 404

    malformed = client.get(f"{API}/marketplace/agents/not-a-uuid")
    assert malformed.status_code == 422


def test_marketplace_filters_and_search(client: TestClient) -> None:
    owner = login(client, make_wallet(212))
    create_agent(
        client,
        owner,
        name="Atlas Writer",
        category="writing",
        skills=["technical-writing"],
        capabilities=["copywriting"],
        pricing_model="fixed",
        availability="available",
        price_amount="100.00",
    )
    create_agent(
        client,
        owner,
        name="Pixel Designer",
        category="design",
        skills=["figma"],
        capabilities=["prototyping"],
        pricing_model="hourly",
        availability="limited",
        price_amount="80.00",
    )
    create_agent(
        client,
        owner,
        name="Percent % Specialist",
        category="writing",
        skills=["technical-writing"],
        capabilities=["seo"],
        pricing_model="negotiable",
        availability="unavailable",
        price_amount="300.00",
    )

    def ids(**params: Any) -> set[str]:
        response = client.get(f"{API}/marketplace/agents", params=params)
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    everything = ids()
    assert len(everything) == 3

    assert len(ids(category="writing")) == 2
    assert len(ids(skill="technical-writing")) == 2
    assert len(ids(skill="TECHNICAL-WRITING")) == 2
    assert len(ids(capability="copywriting")) == 1
    assert len(ids(pricing_model="hourly")) == 1
    assert len(ids(availability="limited")) == 1
    assert len(ids(status="active")) == 3
    assert ids(status="draft") == set()
    assert ids(category="research") == set()

    search = client.get(f"{API}/marketplace/agents", params={"search": "PIXEL"})
    assert search.status_code == 200
    assert [item["name"] for item in search.json()["items"]] == ["Pixel Designer"]

    percent = client.get(f"{API}/marketplace/agents", params={"search": "%"})
    assert percent.status_code == 200
    assert [item["name"] for item in percent.json()["items"]] == ["Percent % Specialist"]

    no_results = client.get(f"{API}/marketplace/agents", params={"search": "no-such-agent"})
    assert no_results.status_code == 200
    assert no_results.json()["total"] == 0


def test_marketplace_sorting_and_pagination(client: TestClient) -> None:
    owner = login(client, make_wallet(213))
    create_agent(client, owner, name="Beta Agent", price_amount="300.00")
    create_agent(client, owner, name="Alpha Agent", price_amount="100.00")
    create_agent(client, owner, name="Gamma Agent", price_amount="200.00")

    by_name = client.get(
        f"{API}/marketplace/agents",
        params={"sort": "name", "order": "asc"},
    )
    assert [item["name"] for item in by_name.json()["items"]] == [
        "Alpha Agent",
        "Beta Agent",
        "Gamma Agent",
    ]

    by_price = client.get(
        f"{API}/marketplace/agents",
        params={"sort": "price_amount", "order": "desc"},
    )
    assert [item["name"] for item in by_price.json()["items"]] == [
        "Beta Agent",
        "Gamma Agent",
        "Alpha Agent",
    ]

    page = client.get(
        f"{API}/marketplace/agents",
        params={"sort": "name", "order": "asc", "limit": 2, "offset": 1},
    )
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert [item["name"] for item in page.json()["items"]] == [
        "Beta Agent",
        "Gamma Agent",
    ]

    invalid_sort = client.get(f"{API}/marketplace/agents", params={"sort": "owner"})
    assert invalid_sort.status_code == 422
    invalid_limit = client.get(f"{API}/marketplace/agents", params={"limit": 101})
    assert invalid_limit.status_code == 422


async def test_agent_audit_events_record_actor_and_subject(
    client: TestClient, database: Database
) -> None:
    owner = login(client, make_wallet(214))
    agent = create_agent(client, owner)

    updated = client.patch(
        f"{API}/agents/{agent['id']}",
        json={"description": "Updated agent description."},
        headers=owner.headers,
    )
    assert updated.status_code == 200

    deactivated = client.post(
        f"{API}/agents/{agent['id']}/deactivate",
        headers=owner.headers,
    )
    assert deactivated.status_code == 200

    created_events = await audit_events(database, AuditEventType.AGENT_CREATED.value)
    updated_events = await audit_events(database, AuditEventType.AGENT_UPDATED.value)
    deactivated_events = await audit_events(database, AuditEventType.AGENT_DEACTIVATED.value)

    assert len(created_events) == 1
    assert len(updated_events) == 1
    assert len(deactivated_events) == 1
    for event in [*created_events, *updated_events, *deactivated_events]:
        assert str(event["actor_user_id"]) == owner.user["id"]
        assert str(event["subject_id"]) == agent["id"]
