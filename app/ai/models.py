"""Modelos isolados da camada de IA do Portal Python."""
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

from ..extensions import db
from ..timeutils import utcnow


class AICallLog(db.Model):
    __tablename__ = 'ai_call_logs'

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    feature = db.Column(db.String(60), nullable=False, index=True)
    model = db.Column(db.String(120), nullable=False)
    input_tokens = db.Column(db.Integer, nullable=False, default=0)
    output_tokens = db.Column(db.Integer, nullable=False, default=0)
    latency_ms = db.Column(db.Integer, nullable=False, default=0)
    success = db.Column(db.Boolean, nullable=False, default=False, index=True)
    error = db.Column(db.Text, nullable=True)
    # 'metadata' is reserved by SQLAlchemy's Declarative API.
    # Keep the database column name required by the IA contract, but expose it
    # through a non-reserved Python attribute.
    call_metadata = db.Column('metadata', JSON().with_variant(JSONB(), 'postgresql'), nullable=True)
