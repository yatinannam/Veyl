from uuid import UUID

import pytest
from sqlalchemy import Engine

from app.authz import Role
from tests.api import API, client_for, csrf, login
from tests.conftest import DatabaseUrls
from tests.factories import add_chunks, create_document, create_tenant_with_admin, create_user
from tests.fakes import FakeEmbedder, RecordingLLM

pytestmark = pytest.mark.integration

EMBEDDER = FakeEmbedder()


def _doc(
    owner_engine: Engine, tenant_id: UUID, uploader: UUID, text: str, title: str, **acl: object
) -> UUID:
    document_id = create_document(owner_engine, tenant_id, uploader, title=title, **acl)  # type: ignore[arg-type]
    add_chunks(owner_engine, document_id, [text], EMBEDDER.embed_documents([text]))
    return document_id


def test_query_answers_with_verified_citations_and_starts_a_conversation(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    t = create_tenant_with_admin(owner_engine)
    _, email = create_user(owner_engine, t.tenant_id)
    travel = _doc(
        owner_engine,
        t.tenant_id,
        t.admin_id,
        "Employees may book economy flights through the portal.",
        "Travel policy",
        allowed_roles=[Role.MEMBER],
    )
    llm = RecordingLLM("Economy flights only [S1].")
    with client_for(database_urls.app, embedder=EMBEDDER, llm=llm) as client:
        login(client, t.slug, email)
        r = client.post(
            f"{API}/query",
            json={"question": "Which flights may employees book?"},
            headers=csrf(client),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        conversation = client.get(f"{API}/conversations/{body['conversation_id']}").json()
        listed = client.get(f"{API}/conversations").json()
    assert body["answer"] == "Economy flights only [S1]."
    assert [(c["label"], c["document_id"], c["document_title"]) for c in body["citations"]] == [
        ("S1", str(travel), "Travel policy")
    ]
    assert body["citations"][0]["snippet"].startswith("Employees may book")
    assert body["citations"][0]["classification"] == "internal"
    assert body["trace_id"]
    assert [m["role"] for m in conversation["messages"]] == ["user", "assistant"]
    assert conversation["messages"][1]["cited_document_ids"] == [str(travel)]
    assert [c["id"] for c in listed] == [body["conversation_id"]]


def test_follow_up_questions_carry_history(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    t = create_tenant_with_admin(owner_engine)
    _doc(
        owner_engine, t.tenant_id, t.admin_id, "Remote work requires manager approval.", "Handbook"
    )
    llm = RecordingLLM("Approval is needed [S1].")
    with client_for(database_urls.app, embedder=EMBEDDER, llm=llm) as client:
        login(client, t.slug, t.admin_email)
        first = client.post(
            f"{API}/query", json={"question": "Is remote work allowed?"}, headers=csrf(client)
        ).json()
        client.post(
            f"{API}/query",
            json={
                "question": "Who approves remote work?",
                "conversation_id": first["conversation_id"],
            },
            headers=csrf(client),
        )
    second_prompt = llm.calls[1]
    assert [m.role for m in second_prompt] == ["system", "user", "assistant", "user"]
    assert second_prompt[1].content == "Is remote work allowed?"
    # Old markers are stripped: [S1] meant a source of the earlier turn, not this one.
    assert second_prompt[2].content == "Approval is needed."


def test_conversations_can_be_deleted(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    t = create_tenant_with_admin(owner_engine)
    _doc(
        owner_engine, t.tenant_id, t.admin_id, "Parking permits come from facilities.", "Facilities"
    )
    with client_for(database_urls.app, embedder=EMBEDDER) as client:
        login(client, t.slug, t.admin_email)
        conversation = client.post(
            f"{API}/query", json={"question": "parking permits"}, headers=csrf(client)
        ).json()["conversation_id"]
        assert (
            client.delete(f"{API}/conversations/{conversation}", headers=csrf(client)).status_code
            == 204
        )
        assert client.get(f"{API}/conversations/{conversation}").status_code == 404


@pytest.mark.parametrize("question", ["", "   ", "x" * 2001])
def test_question_length_is_validated(
    owner_engine: Engine, database_urls: DatabaseUrls, question: str
) -> None:
    t = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as client:
        login(client, t.slug, t.admin_email)
        assert (
            client.post(
                f"{API}/query", json={"question": question}, headers=csrf(client)
            ).status_code
            == 422
        )


def test_query_requires_csrf(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    t = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as client:
        login(client, t.slug, t.admin_email)
        assert client.post(f"{API}/query", json={"question": "hello"}).status_code == 403


def test_stored_answers_keep_their_citation_labels(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    t = create_tenant_with_admin(owner_engine)
    _doc(owner_engine, t.tenant_id, t.admin_id, "Employees may book economy flights.", "Flights")
    _doc(
        owner_engine, t.tenant_id, t.admin_id, "Employees may book standard hotel rooms.", "Hotels"
    )
    llm = RecordingLLM("Hotels are standard [S2]. Flights are economy [S1].")
    with client_for(database_urls.app, embedder=EMBEDDER, llm=llm) as client:
        login(client, t.slug, t.admin_email)
        body = client.post(
            f"{API}/query", json={"question": "What may employees book?"}, headers=csrf(client)
        ).json()
        stored = client.get(f"{API}/conversations/{body['conversation_id']}").json()["messages"][1]
    # Labels are renumbered in order of first appearance so they can be stored and replayed.
    assert body["answer"] == "Hotels are standard [S1]. Flights are economy [S2]."
    assert [c["label"] for c in body["citations"]] == ["S1", "S2"]
    assert stored["content"] == body["answer"]
    assert stored["citations"] == body["citations"]
