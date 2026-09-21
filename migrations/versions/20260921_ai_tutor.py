"""Referência da migração do Tutor de IA.

O Portal Python não usa Alembic/Flask-Migrate. A migração efetiva é executada
por app.ai.migration.ensure_ai_schema(), seguindo SchemaMigration existente.
"""

revision = 'ai-tutor-v1'
down_revision = None

UPGRADE_SQL = """
CREATE TABLE ai_call_logs (
    id INTEGER PRIMARY KEY,
    created_at TIMESTAMP NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    feature VARCHAR(60) NOT NULL,
    model VARCHAR(120) NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    error TEXT,
    metadata JSONB
);
"""
