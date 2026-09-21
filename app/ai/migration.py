"""Migração compatível com o mecanismo SchemaMigration já usado pelo Portal."""
from sqlalchemy import inspect, text

from ..extensions import db
from ..models import SchemaMigration

AI_TUTOR_VERSION = 'ai-tutor-v1'


def ensure_ai_schema():
    """Cria a tabela de logs sem depender de Flask-Migrate/Alembic."""
    inspector = inspect(db.engine)
    if 'ai_call_logs' in inspector.get_table_names():
        if db.session.get(SchemaMigration, AI_TUTOR_VERSION) is None:
            db.session.add(SchemaMigration(version=AI_TUTOR_VERSION))
            db.session.commit()
        return

    # O modelo é importado antes desta função ser chamada; create_all é
    # seguro para uma tabela nova e não altera tabelas existentes.
    from .models import AICallLog  # noqa: F401
    db.create_all()
    if db.session.get(SchemaMigration, AI_TUTOR_VERSION) is None:
        db.session.add(SchemaMigration(version=AI_TUTOR_VERSION))
        db.session.commit()
