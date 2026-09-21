"""Migração compatível com o mecanismo SchemaMigration já usado pelo Portal."""
from sqlalchemy import inspect, text

from ..extensions import db
from ..models import SchemaMigration

AI_TUTOR_VERSION = 'ai-tutor-v1'
AI_FEEDBACK_VERSION = 'ai-feedback-v1'


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

    inspector = inspect(db.engine)
    if 'activity_attempts' in inspector.get_table_names():
        columns = {column['name'] for column in inspector.get_columns('activity_attempts')}
        if 'ai_feedback' not in columns:
            with db.engine.begin() as conn:
                if db.engine.dialect.name == 'postgresql':
                    conn.execute(text('ALTER TABLE activity_attempts ADD COLUMN IF NOT EXISTS ai_feedback TEXT'))
                elif db.engine.dialect.name == 'sqlite':
                    conn.execute(text('ALTER TABLE activity_attempts ADD COLUMN ai_feedback TEXT'))

    if db.session.get(SchemaMigration, AI_FEEDBACK_VERSION) is None:
        db.session.add(SchemaMigration(version=AI_FEEDBACK_VERSION))
        db.session.commit()
