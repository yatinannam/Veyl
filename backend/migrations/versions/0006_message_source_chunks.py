"""Remember every chunk an answer was built from, not only the ones it cited.

A model can use a source without citing it. To hide an answer once any of its sources
becomes unreadable, the full set of supplied chunks is stored on the assistant message.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-16
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ADD COLUMN source_chunk_ids uuid[] NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE messages DROP COLUMN source_chunk_ids")
