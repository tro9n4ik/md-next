"""${message}

Идентификатор миграции: ${up_revision}
Предыдущая миграция: ${down_revision | comma,n}
Дата создания: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# Идентификаторы миграции, используемые Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Обновляет схему базы данных."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Восстанавливает предыдущую схему базы данных."""
    ${downgrades if downgrades else "pass"}
