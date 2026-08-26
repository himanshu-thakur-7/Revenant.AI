from agents.telegram.bot import (
    _extract_research_query,
    _keyword_intent,
    _research_only_intent,
)


def test_research_only_intent_does_not_become_campaign() -> None:
    msg = "just do research about companies in healthtech"

    assert _research_only_intent(msg) is True
    assert _keyword_intent(msg) == "research_companies"


def test_market_map_routes_to_research_only() -> None:
    assert _keyword_intent("market map payments startups") == "research_companies"


def test_campaign_hunt_still_routes_to_full_campaign() -> None:
    assert _keyword_intent("find me fintech customers") == "run_campaign"


def test_research_query_extracts_vertical() -> None:
    assert _extract_research_query("research companies in climate tech") == "climate tech"
    assert _extract_research_query("show me companies around creator economy") == "creator economy"
