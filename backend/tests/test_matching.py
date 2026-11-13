from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from autoescrow.core.config import Settings
from autoescrow.infrastructure.database import Database
from autoescrow.models.domain import Agent
from autoescrow.services.matching import (
    MatchingWeights,
    MatchRequirements,
    match_agents,
    score_agent,
)
from tests.domain_helpers import create_agent, create_job
from tests.helpers import API, login, make_wallet


def _agent(
    *,
    name: str = "Scoring Agent",
    category: str = "writing",
    skills: list[str] | None = None,
    capabilities: list[str] | None = None,
    price: str = "100.00",
    currency: str = "USD",
    pricing_model: str = "fixed",
    availability: str = "available",
) -> Agent:
    return Agent(
        owner_user_id=uuid4(),
        name=name,
        description="Scoring fixture.",
        category=category,
        skills=skills or ["technical-writing", "editing"],
        capabilities=capabilities or ["copywriting", "seo"],
        supported_task_types=["blog-post"],
        pricing_model=pricing_model,
        price_amount=Decimal(price),
        currency=currency,
        availability=availability,
        status="active",
        is_public=True,
    )


def _requirements(**overrides: object) -> MatchRequirements:
    values: dict[str, object] = {
        "category": "writing",
        "required_skills": ("technical-writing", "editing"),
        "required_capabilities": ("copywriting",),
        "budget_amount": Decimal("500.00"),
        "currency": "USD",
        "limit": 20,
    }
    values.update(overrides)
    return MatchRequirements(**values)  # type: ignore[arg-type]


def _reasons(agent: Agent, requirements: MatchRequirements) -> dict[str, object]:
    _, reasons = score_agent(agent, requirements)
    return {reason.component: reason for reason in reasons}


def test_matching_weights_are_normalized_and_must_have_positive_total() -> None:
    default = MatchingWeights()
    normalized = default.normalized()
    assert set(normalized) == {
        "skills",
        "capabilities",
        "category",
        "availability",
        "budget",
    }
    assert sum(normalized.values()) == pytest.approx(1.0)

    skewed = MatchingWeights(
        skills=2.0, capabilities=1.0, category=1.0, availability=0.0, budget=0.0
    )
    skewed_normalized = skewed.normalized()
    assert skewed_normalized["skills"] == pytest.approx(0.5)
    assert skewed_normalized["capabilities"] == pytest.approx(0.25)
    assert skewed_normalized["category"] == pytest.approx(0.25)
    assert skewed_normalized["availability"] == 0.0
    assert skewed_normalized["budget"] == 0.0

    with pytest.raises(ValueError, match="positive total"):
        MatchingWeights(
            skills=0.0,
            capabilities=0.0,
            category=0.0,
            availability=0.0,
            budget=0.0,
        ).normalized()


def test_settings_reject_all_zero_matching_weights() -> None:
    with pytest.raises(ValidationError, match="at least one matching weight"):
        Settings(
            database_url="postgresql+asyncpg://user:pass@localhost/db",
            redis_url="redis://localhost/0",
            jwt_secret="x" * 32,
            matching_weight_skills=0.0,
            matching_weight_capabilities=0.0,
            matching_weight_category=0.0,
            matching_weight_availability=0.0,
            matching_weight_budget=0.0,
        )


def test_perfect_match_scores_one_and_explains_every_component() -> None:
    agent = _agent()
    requirements = _requirements()

    score, reasons = score_agent(agent, requirements)

    assert score == pytest.approx(1.0)
    assert [reason.component for reason in reasons] == [
        "skills",
        "capabilities",
        "category",
        "availability",
        "budget",
    ]
    assert sum(reason.contribution for reason in reasons) == pytest.approx(score)
    assert all(reason.weight > 0 for reason in reasons)
    assert "Matched 2/2 required skills" in reasons[0].explanation
    assert "Matched 1/1 required capabilities" in reasons[1].explanation
    assert reasons[2].explanation == "Category matches."
    assert reasons[3].explanation == "Agent is currently available."
    assert "fits the budget" in reasons[4].explanation


def test_individual_matching_components_are_explainable() -> None:
    requirements = _requirements(required_capabilities=("copywriting", "translation"))
    reasons = _reasons(_agent(), requirements)

    assert reasons["skills"].score == 1.0
    assert reasons["capabilities"].score == 0.5
    assert "Matched 1/2" in reasons["capabilities"].explanation
    assert reasons["category"].score == 1.0

    mismatched = _reasons(_agent(category="design"), requirements)
    assert mismatched["category"].score == 0.0
    assert mismatched["category"].explanation == "Category does not match."

    limited = _reasons(_agent(availability="limited"), requirements)
    assert limited["availability"].score == 0.5
    assert limited["availability"].explanation == "Agent has limited availability."

    unavailable = _reasons(_agent(availability="unavailable"), requirements)
    assert unavailable["availability"].score == 0.0
    assert unavailable["availability"].explanation == "Agent is currently unavailable."


def test_budget_component_handles_price_currency_and_negotiation() -> None:
    requirements = _requirements(budget_amount=Decimal("100.00"))

    within_budget = _reasons(_agent(price="100.00"), requirements)["budget"]
    assert within_budget.score == 1.0
    assert "fits the budget" in within_budget.explanation

    above_budget = _reasons(_agent(price="150.00"), requirements)["budget"]
    assert above_budget.score == pytest.approx(0.5)
    assert "exceeds the budget" in above_budget.explanation

    far_above_budget = _reasons(_agent(price="300.00"), requirements)["budget"]
    assert far_above_budget.score == 0.0

    currency_mismatch = _reasons(_agent(currency="EUR"), requirements)["budget"]
    assert currency_mismatch.score == 0.0
    assert "Currency mismatch" in currency_mismatch.explanation

    negotiable = _reasons(_agent(price="1000.00", pricing_model="negotiable"), requirements)[
        "budget"
    ]
    assert negotiable.score == pytest.approx(0.85)
    assert negotiable.explanation == "Agent pricing is negotiable."


def test_score_is_deterministic_and_weight_sensitive(test_settings: Settings) -> None:
    agent = _agent(
        category="design",
        availability="unavailable",
        price="900.00",
    )
    requirements = _requirements()

    first_score, first_reasons = score_agent(agent, requirements)
    second_score, second_reasons = score_agent(agent, requirements)
    assert first_score == second_score
    assert first_reasons == second_reasons

    skills_only_settings = test_settings.model_copy(
        update={
            "matching_weight_skills": 1.0,
            "matching_weight_capabilities": 0.0,
            "matching_weight_category": 0.0,
            "matching_weight_availability": 0.0,
            "matching_weight_budget": 0.0,
        }
    )
    skills_only = MatchingWeights.from_settings(skills_only_settings)
    custom_score, custom_reasons = score_agent(
        agent,
        requirements,
        weights=skills_only,
    )
    assert custom_score == pytest.approx(1.0)
    assert custom_reasons[0].weight == pytest.approx(1.0)
    assert sum(reason.weight for reason in custom_reasons) == pytest.approx(1.0)


def test_matching_api_requires_authentication_and_returns_ranked_agents(
    client: TestClient,
) -> None:
    owner = login(client, make_wallet(401))
    perfect = create_agent(client, owner, name="Perfect Agent")
    create_agent(
        client,
        owner,
        name="Partial Agent",
        skills=["technical-writing", "unrelated"],
        capabilities=["copywriting"],
        availability="limited",
    )
    create_agent(client, owner, name="Private Agent", is_public=False)
    client.post(
        f"{API}/agents",
        json={
            "name": "Draft Agent",
            "description": "Draft fixture.",
            "category": "writing",
            "capabilities": ["copywriting"],
            "skills": ["technical-writing"],
            "supported_task_types": ["blog-post"],
            "pricing_model": "fixed",
            "price_amount": "100.00",
            "currency": "USD",
            "status": "draft",
        },
        headers=owner.headers,
    )

    assert (
        client.post(
            f"{API}/matching/agents",
            json={
                "category": "writing",
                "required_skills": ["technical-writing", "editing"],
                "required_capabilities": ["copywriting"],
                "budget_amount": "500.00",
                "currency": "USD",
            },
        ).status_code
        == 401
    )

    payload = {
        "category": "writing",
        "required_skills": ["technical-writing", "editing"],
        "required_capabilities": ["copywriting"],
        "budget_amount": "500.00",
        "currency": "USD",
        "limit": 10,
    }
    first = client.post(f"{API}/matching/agents", json=payload, headers=owner.headers)
    assert first.status_code == 200, first.text
    first_body = first.json()

    second = client.post(f"{API}/matching/agents", json=payload, headers=owner.headers)
    assert second.status_code == 200
    second_body = second.json()

    assert [item["agent"]["id"] for item in first_body["matches"]] == [
        item["agent"]["id"] for item in second_body["matches"]
    ]
    assert [item["agent"]["name"] for item in first_body["matches"]] == [
        "Perfect Agent",
        "Partial Agent",
    ]
    scores = [item["score"] for item in first_body["matches"]]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == pytest.approx(1.0)
    assert scores[1] == pytest.approx(0.8)
    assert set(first_body["weights"]) == {
        "skills",
        "capabilities",
        "category",
        "availability",
        "budget",
    }
    assert sum(first_body["weights"].values()) == pytest.approx(1.0)
    assert first_body["matches"][0]["agent"]["id"] == perfect["id"]
    assert "owner_user_id" not in first_body["matches"][0]["agent"]
    assert all(
        item["agent"]["name"] not in {"Private Agent", "Draft Agent"}
        for item in first_body["matches"]
    )


def test_matching_ties_break_by_name_then_identifier(client: TestClient) -> None:
    owner = login(client, make_wallet(402))
    other = login(client, make_wallet(403))
    third = login(client, make_wallet(407))
    create_agent(client, owner, name="Zeta Agent")
    create_agent(client, owner, name="Alpha Agent")
    same_name_first = create_agent(client, other, name="Same Agent")
    same_name_second = create_agent(client, third, name="Same Agent", description="Another twin.")

    payload = {
        "category": "writing",
        "required_skills": ["technical-writing"],
        "required_capabilities": ["copywriting"],
        "budget_amount": "500.00",
        "currency": "USD",
        "limit": 10,
    }
    response = client.post(f"{API}/matching/agents", json=payload, headers=owner.headers)
    assert response.status_code == 200, response.text
    names = [item["agent"]["name"] for item in response.json()["matches"]]
    assert names[0] == "Alpha Agent"
    assert names[1] == "Same Agent"
    assert names[2] == "Same Agent"
    assert names[3] == "Zeta Agent"

    same_name_ids = [item["agent"]["id"] for item in response.json()["matches"][1:3]]
    assert same_name_ids == sorted(
        [same_name_first["id"], same_name_second["id"]],
        key=str,
    )


async def test_matching_service_is_deterministic(client: TestClient, database: Database) -> None:
    owner = login(client, make_wallet(406))
    create_agent(client, owner, name="Zeta Service Agent")
    create_agent(client, owner, name="Alpha Service Agent")

    requirements = _requirements()
    async with database.session() as session:
        first, _ = await match_agents(session, requirements=requirements)
        second, _ = await match_agents(session, requirements=requirements)
    assert [item.agent.id for item in first] == [item.agent.id for item in second]
    assert [item.agent.name for item in first] == [
        "Alpha Service Agent",
        "Zeta Service Agent",
    ]


def test_matching_a_job_requires_ownership(client: TestClient) -> None:
    owner = login(client, make_wallet(404))
    stranger = login(client, make_wallet(405))
    create_agent(client, owner, name="Job Matcher")
    job = create_job(client, owner, title="Matchable job")

    denied = client.post(f"{API}/matching/jobs/{job['id']}", headers=stranger.headers)
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "job_not_found"

    allowed = client.post(
        f"{API}/matching/jobs/{job['id']}",
        params={"limit": 1},
        headers=owner.headers,
    )
    assert allowed.status_code == 200, allowed.text
    assert len(allowed.json()["matches"]) == 1
    assert allowed.json()["matches"][0]["agent"]["name"] == "Job Matcher"
    assert (
        "Matched 1/1 required skills" in allowed.json()["matches"][0]["reasons"][0]["explanation"]
    )

    unauthenticated = client.post(f"{API}/matching/jobs/{job['id']}")
    assert unauthenticated.status_code == 401

    missing = client.post(
        f"{API}/matching/jobs/00000000-0000-4000-8000-000000000000",
        headers=owner.headers,
    )
    assert missing.status_code == 404
