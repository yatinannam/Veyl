"""The demo scenario (spec section 14): two companies, their people, and synthetic
documents with deliberately different permissions."""

from dataclasses import dataclass, field
from pathlib import Path

DOCUMENTS_DIR = Path(__file__).parent / "documents"


@dataclass(frozen=True, slots=True)
class DemoUser:
    email: str
    role: str


@dataclass(frozen=True, slots=True)
class DemoDocument:
    title: str
    filename: str
    classification: str
    allowed_roles: tuple[str, ...] = ()
    # Emails of people (besides the uploader) the document is shared with.
    shared_with: tuple[str, ...] = ()

    def content(self) -> bytes:
        return (DOCUMENTS_DIR / self.filename).read_bytes()


@dataclass(frozen=True, slots=True)
class DemoTenant:
    slug: str
    name: str
    admin: DemoUser
    people: tuple[DemoUser, ...]
    documents: tuple[DemoDocument, ...] = field(default=())


EVERYONE = ("admin", "manager", "member")

ACME = DemoTenant(
    slug="acme",
    name="Acme Corp",
    admin=DemoUser("alice@acme.test", "admin"),
    people=(DemoUser("mark@acme.test", "manager"), DemoUser("mia@acme.test", "member")),
    documents=(
        DemoDocument("Employee Handbook", "employee-handbook.md", "internal", EVERYONE),
        DemoDocument(
            "Engineering Roadmap", "engineering-roadmap.md", "confidential", ("admin", "manager")
        ),
        DemoDocument("Q3 Board Financials", "q3-board-financials.md", "restricted"),
        DemoDocument(
            "Mark's Performance Review",
            "mark-performance-review.md",
            "confidential",
            shared_with=("mark@acme.test",),
        ),
        # Contains an injected instruction; access control must not depend on the model
        # ignoring it.
        DemoDocument("IT Help FAQ", "it-help-faq.md", "internal", EVERYONE),
    ),
)

GLOBEX = DemoTenant(
    slug="globex",
    name="Globex",
    admin=DemoUser("gina@globex.test", "admin"),
    people=(DemoUser("gary@globex.test", "member"),),
    documents=(DemoDocument("Merger Plan", "merger-plan.md", "restricted"),),
)

TENANTS = (ACME, GLOBEX)
