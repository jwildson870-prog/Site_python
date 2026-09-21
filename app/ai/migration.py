"""Migrações compatíveis com o mecanismo SchemaMigration já usado pelo Portal."""
from sqlalchemy import inspect, text

from ..extensions import db
from ..models import SchemaMigration

AI_TUTOR_VERSION = 'ai-tutor-v1'
AI_FEEDBACK_VERSION = 'ai-feedback-v1'


def ensure_ai_schema():
    """Garante as estruturas da IA sem depender de Flask-Migrate/Alembic.

    A tabela de logs e a coluna de feedback são verificadas separadamente.
    Isso é importante para instalações que já receberam o Item 1: nesse caso
    ``ai_call_logs`` já existe, mas ``activity_attempts.ai_feedback`` ainda
    pode não existir.
    """
    inspector = inspect(db.engine)

    # A tabela nova do Item 1 é criada sem alterar tabelas existentes.
    if 'ai_call_logs' not in inspector.get_table_names():
        from .models import AICallLog  # noqa: F401
        db.create_all()
        inspector = inspect(db.engine)

    if db.session.get(SchemaMigration, AI_TUTOR_VERSION) is None:
        db.session.add(SchemaMigration(version=AI_TUTOR_VERSION))
        db.session.commit()

    # O Item 2 adicionou uma coluna a uma tabela existente. create_all() não
    # adiciona colunas em tabelas que já existem, então fazemos a alteração
    # explicitamente e de forma compatível com PostgreSQL/SQLite.
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
