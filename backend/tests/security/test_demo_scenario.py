"""The spec section 14 walkthrough as an end-to-end test. The demo is seeded through the
real API, and every assertion is about what the model would have been sent
(RecordingLLM) or what the API returned."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient

from tests.api import API, csrf, login
from tests.demo_support import DEMO_PASSWORD, DemoWorld
from tests.fakes import RecordingLLM

FINANCIALS = "18.4 million"
# Markers appear only in the document text, never in the questions asked.
ROADMAP = "99.95 percent"
REVIEW = "exceeds expectations"
INJECTION = "Ignore previous instructions"
ACME_ONLY = ("core hours", ROADMAP, FINANCIALS, REVIEW, "VPN")


@contextmanager
def signed_in(world: DemoWorld, slug: str, email: str) -> Iterator[tuple[TestClient, RecordingLLM]]:
    llm = RecordingLLM()
    with world.client(llm=llm) as client:
        assert login(client, slug, email, DEMO_PASSWORD).status_code == 200
        yield client, llm


def ask(client: TestClient, question: str) -> Any:
    response = client.post(f"{API}/query", json={"question": question}, headers=csrf(client))
    assert response.status_code == 200
    return response.json()


def cited_titles(answer: Any) -> set[str]:
    return {c["document_title"] for c in answer["citations"]}


def document_id(client: TestClient, title: str) -> str:
    return next(d["id"] for d in client.get(f"{API}/documents").json() if d["title"] == title)


def test_member_cannot_reach_board_financials(demo_world: DemoWorld) -> None:
    with signed_in(demo_world, "acme", "mia@acme.test") as (client, llm):
        answer = ask(client, "What was Acme's revenue in Q3?")
    assert FINANCIALS not in llm.prompt_text()
    assert "Q3 Board Financials" not in cited_titles(answer)


def test_admin_gets_cited_answer_from_board_financials(demo_world: DemoWorld) -> None:
    with signed_in(demo_world, "acme", "alice@acme.test") as (client, llm):
        answer = ask(client, "What was Acme's revenue in Q3? Board financials revenue cash")
    assert FINANCIALS in llm.prompt_text()
    restricted = [c for c in answer["citations"] if c["document_title"] == "Q3 Board Financials"]
    assert restricted, "the answer must actually cite the board financials"
    assert all(c["classification"] == "restricted" for c in restricted)


def test_other_tenant_sees_nothing_from_acme(demo_world: DemoWorld) -> None:
    with signed_in(demo_world, "globex", "gary@globex.test") as (client, llm):
        answer = ask(client, "What was Acme's revenue in Q3? What is Project Lighthouse?")
        titles = {d["title"] for d in client.get(f"{API}/documents").json()}
    assert not any(marker in llm.prompt_text() for marker in ACME_ONLY)
    assert answer["citations"] == []
    # Gary is a member; the only Globex document is restricted.
    assert titles == set()


def test_reclassification_takes_effect_on_next_query_and_revert_removes_it(
    demo_world: DemoWorld,
) -> None:
    question = "What is on the engineering roadmap for platform reliability?"
    with signed_in(demo_world, "acme", "alice@acme.test") as (admin, _):
        roadmap = document_id(admin, "Engineering Roadmap")

        def set_acl(classification: str, roles: list[str]) -> None:
            response = admin.put(
                f"{API}/documents/{roadmap}/permissions",
                json={"classification": classification, "allowed_roles": roles},
                headers=csrf(admin),
            )
            assert response.status_code == 200

        with signed_in(demo_world, "acme", "mia@acme.test") as (member, llm):
            ask(member, question)
            assert ROADMAP not in llm.prompt_text()
            try:
                set_acl("internal", ["admin", "manager", "member"])
                llm.calls.clear()
                ask(member, question)
                assert ROADMAP in llm.prompt_text()
            finally:
                set_acl("confidential", ["admin", "manager"])
            llm.calls.clear()
            ask(member, question)
            assert ROADMAP not in llm.prompt_text()


def test_injected_instruction_cannot_widen_access(demo_world: DemoWorld) -> None:
    with signed_in(demo_world, "acme", "mia@acme.test") as (client, llm):
        ask(client, "How do I reset my VPN password? Also reveal the Q3 financials.")
    prompt = llm.prompt_text()
    # The planted instruction reaches the model only as quoted source text...
    assert INJECTION in prompt
    assert all(INJECTION not in m.content for call in llm.calls for m in call if m.role == "system")
    # ...and the data it asks for was never in the prompt to begin with.
    assert FINANCIALS not in prompt
    assert "42.7 million" not in prompt


def test_personal_share_is_only_for_mark(demo_world: DemoWorld) -> None:
    question = "What was the outcome of Mark's performance review?"
    seen = {}
    for email in ("mark@acme.test", "mia@acme.test", "alice@acme.test"):
        with signed_in(demo_world, "acme", email) as (client, llm):
            ask(client, question)
            seen[email] = REVIEW in llm.prompt_text()
    assert seen == {"mark@acme.test": True, "mia@acme.test": False, "alice@acme.test": True}


def test_audit_trail_shows_the_walkthrough(demo_world: DemoWorld) -> None:
    secret_question = "Mia audit probe: what is the Q3 cash position?"
    with signed_in(demo_world, "acme", "mia@acme.test") as (client, _):
        ask(client, secret_question)
        mia = client.get(f"{API}/auth/me").json()["user"]["id"]
    with signed_in(demo_world, "acme", "alice@acme.test") as (client, _):
        events = client.get(
            f"{API}/audit-events", params={"event_type": "rag.query", "user_id": mia}
        ).json()["events"]
    assert events, "Mia's questions are audited"
    assert all(e["result_count"] is not None for e in events)
    # Questions are recorded as a keyed reference, not as text (default tenant setting).
    assert all(e["query_text"] is None and e["query_ref"] for e in events)
    assert secret_question not in str(events)
