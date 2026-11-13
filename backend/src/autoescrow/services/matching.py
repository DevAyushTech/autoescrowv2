from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoescrow.core.config import Settings
from autoescrow.models.domain import Agent, Job
from autoescrow.models.enums import AgentAvailability, AgentStatus, PricingModel
from autoescrow.models.identity import User


@dataclass(frozen=True, slots=True)
class MatchRequirements:
    category: str
    required_skills: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    budget_amount: Decimal
    currency: str
    limit: int = 20


@dataclass(frozen=True, slots=True)
class MatchingWeights:
    skills: float = 0.30
    capabilities: float = 0.20
    category: float = 0.15
    availability: float = 0.10
    budget: float = 0.25

    @classmethod
    def from_settings(cls, settings: Settings) -> "MatchingWeights":
        return cls(
            skills=settings.matching_weight_skills,
            capabilities=settings.matching_weight_capabilities,
            category=settings.matching_weight_category,
            availability=settings.matching_weight_availability,
            budget=settings.matching_weight_budget,
        )

    def normalized(self) -> dict[str, float]:
        raw = {
            "skills": self.skills,
            "capabilities": self.capabilities,
            "category": self.category,
            "availability": self.availability,
            "budget": self.budget,
        }
        total = sum(raw.values())
        if total <= 0:
            raise ValueError("matching weights must have a positive total")
        return {name: weight / total for name, weight in raw.items()}


@dataclass(frozen=True, slots=True)
class MatchReasonData:
    component: str
    score: float
    weight: float
    contribution: float
    explanation: str


@dataclass(frozen=True, slots=True)
class MatchScore:
    agent: Agent
    owner_display_name: str
    score: float
    reasons: tuple[MatchReasonData, ...]


def _coverage(required: tuple[str, ...], offered: list[str]) -> tuple[float, int]:
    if not required:
        return 1.0, 0
    offered_set = {item.lower() for item in offered}
    matched = sum(1 for item in required if item.lower() in offered_set)
    return matched / len(required), matched


def _availability_score(availability: str) -> tuple[float, str]:
    if availability == AgentAvailability.AVAILABLE.value:
        return 1.0, "Agent is currently available."
    if availability == AgentAvailability.LIMITED.value:
        return 0.5, "Agent has limited availability."
    return 0.0, "Agent is currently unavailable."


def _budget_score(agent: Agent, requirements: MatchRequirements) -> tuple[float, str]:
    if agent.currency != requirements.currency:
        return (
            0.0,
            f"Currency mismatch: agent charges {agent.currency}, "
            f"job budget is {requirements.currency}.",
        )
    if agent.pricing_model == PricingModel.NEGOTIABLE.value:
        return 0.85, "Agent pricing is negotiable."
    if agent.price_amount <= requirements.budget_amount:
        return (
            1.0,
            f"Agent price {agent.price_amount} fits the budget "
            f"{requirements.budget_amount} {requirements.currency}.",
        )
    ratio = max(
        Decimal("0"),
        Decimal("1")
        - ((agent.price_amount - requirements.budget_amount) / requirements.budget_amount),
    )
    return (
        float(ratio),
        f"Agent price {agent.price_amount} exceeds the budget "
        f"{requirements.budget_amount} {requirements.currency}.",
    )


def score_agent(
    agent: Agent,
    requirements: MatchRequirements,
    *,
    weights: MatchingWeights | None = None,
) -> tuple[float, tuple[MatchReasonData, ...]]:
    normalized_weights = (weights or MatchingWeights()).normalized()

    skill_score, matched_skills = _coverage(requirements.required_skills, agent.skills)
    capability_score, matched_capabilities = _coverage(
        requirements.required_capabilities, agent.capabilities
    )
    category_score = 1.0 if agent.category == requirements.category else 0.0
    availability_score, availability_explanation = _availability_score(agent.availability)
    budget_score, budget_explanation = _budget_score(agent, requirements)

    component_scores = {
        "skills": (
            skill_score,
            f"Matched {matched_skills}/{len(requirements.required_skills)} required skills.",
        ),
        "capabilities": (
            capability_score,
            f"Matched {matched_capabilities}/"
            f"{len(requirements.required_capabilities)} required capabilities.",
        ),
        "category": (
            category_score,
            "Category matches." if category_score else "Category does not match.",
        ),
        "availability": (availability_score, availability_explanation),
        "budget": (budget_score, budget_explanation),
    }

    reasons = tuple(
        MatchReasonData(
            component=component,
            score=round(score, 6),
            weight=round(normalized_weights[component], 6),
            contribution=round(score * normalized_weights[component], 6),
            explanation=explanation,
        )
        for component, (score, explanation) in component_scores.items()
    )
    total = round(sum(reason.contribution for reason in reasons), 6)
    return total, reasons


async def match_agents(
    session: AsyncSession,
    *,
    requirements: MatchRequirements,
    weights: MatchingWeights | None = None,
) -> tuple[list[MatchScore], dict[str, float]]:
    resolved_weights = weights or MatchingWeights()
    rows = (
        await session.execute(
            select(Agent, User.display_name)
            .join(User, User.id == Agent.owner_user_id)
            .where(
                Agent.status == AgentStatus.ACTIVE.value,
                Agent.is_public.is_(True),
            )
        )
    ).all()

    matches = [
        MatchScore(
            agent=agent,
            owner_display_name=owner_name,
            score=score,
            reasons=reasons,
        )
        for agent, owner_name in rows
        for score, reasons in [score_agent(agent, requirements, weights=resolved_weights)]
    ]
    matches.sort(key=lambda item: (-item.score, item.agent.name.lower(), str(item.agent.id)))
    return matches[: requirements.limit], resolved_weights.normalized()


def requirements_from_job(job: Job) -> MatchRequirements:
    return MatchRequirements(
        category=job.category,
        required_skills=tuple(job.required_skills),
        required_capabilities=tuple(job.required_capabilities),
        budget_amount=job.budget_amount,
        currency=job.currency,
        limit=50,
    )
