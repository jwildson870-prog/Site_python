"""Rotas isoladas do Tutor de IA."""
from datetime import timedelta

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user, login_required
from sqlalchemy import and_, or_

from ..extensions import db
from ..models import Content, ActivityAttempt
from ..settings import get_bool
from ..timeutils import utcnow
from . import TUTOR_MODEL, feature_enabled, log_non_api_event, tutor_available, tutor_rate_limit
from .models import AICallLog
from .tutor import ask_tutor
from .feedback import generate_feedback, _wrong_answers

ai_bp = Blueprint('ai', __name__, url_prefix='/ai')


def _visible_content(content_id):
    now = utcnow()
    return Content.query.filter(
        Content.id == content_id,
        Content.archived_at.is_(None),
        or_(
            Content.status == 'published',
            and_(Content.status == 'scheduled', Content.scheduled_at.isnot(None), Content.scheduled_at <= now),
        ),
    ).first()


def _history_key(content_id):
    return f'ai_tutor_history_{content_id}'


def _recent_count(user_id):
    cutoff = utcnow() - timedelta(hours=1)
    return AICallLog.query.filter(
        AICallLog.user_id == user_id,
        AICallLog.feature == 'ai_tutor',
        AICallLog.created_at >= cutoff,
    ).count()


def _json_error(message, status=400):
    return jsonify({'ok': False, 'error': message}), status


@ai_bp.get('/tutor/status/<int:content_id>')
@login_required
def tutor_status(content_id):
    if current_user.is_admin:
        abort(403)
    if not feature_enabled('ai_tutor_enabled') or not tutor_available():
        abort(404)
    content = _visible_content(content_id)
    if content is None:
        abort(404)
    return jsonify({'ok': True, 'enabled': True, 'badge': '🤖 Gerado por IA — pode conter erros'})


@ai_bp.post('/tutor/<int:content_id>')
@login_required
def tutor(content_id):
    if current_user.is_admin:
        abort(403)
    if not feature_enabled('ai_tutor_enabled') or not tutor_available():
        abort(404)

    content = _visible_content(content_id)
    if content is None:
        abort(404)

    payload = request.get_json(silent=True) or {}
    question = str(payload.get('question', '')).strip()
    if not question:
        return _json_error('Digite uma dúvida para o tutor.')
    if len(question) > 4000:
        return _json_error('Sua dúvida é muito longa. Tente resumir a pergunta.')

    limit = tutor_rate_limit()
    count = _recent_count(current_user.id)
    if count >= limit:
        log_non_api_event(current_user.id, 'ai_tutor', TUTOR_MODEL, 'rate_limit', {'content_id': content.id, 'limit': limit})
        return _json_error('Você atingiu o limite de mensagens desta hora. Tente mais tarde.', 429)

    history = session.get(_history_key(content.id), [])
    if not isinstance(history, list):
        history = []

    result = ask_tutor(
        user_id=current_user.id,
        content=content,
        history=history,
        question=question,
    )
    if not result['ok']:
        return _json_error(result['error'], 503)

    history = history[-8:]
    history.extend([
        {'role': 'user', 'content': question},
        {'role': 'assistant', 'content': result['text']},
    ])
    session[_history_key(content.id)] = history[-10:]
    session.modified = True
    return jsonify({
        'ok': True,
        'text': result['text'],
        'badge': '🤖 Gerado por IA — pode conter erros',
    })


@ai_bp.post('/tutor/<int:content_id>/clear')
@login_required
def clear_tutor(content_id):
    if current_user.is_admin:
        abort(403)
    session.pop(_history_key(content_id), None)
    session.modified = True
    return jsonify({'ok': True})


@ai_bp.get('/feedback/<int:attempt_id>')
@login_required
def feedback(attempt_id):
    if current_user.is_admin:
        abort(403)
    if not feature_enabled('ai_feedback_enabled'):
        abort(404)

    attempt = ActivityAttempt.query.filter_by(id=attempt_id, user_id=current_user.id).first_or_404()
    wrong_answers = _wrong_answers(attempt)
    if not wrong_answers:
        return jsonify({'ok': True, 'has_errors': False, 'feedback': '', 'badge': '🤖 Gerado por IA — pode conter erros'})

    if attempt.ai_feedback:
        return jsonify({'ok': True, 'has_errors': True, 'feedback': attempt.ai_feedback, 'badge': '🤖 Gerado por IA — pode conter erros', 'cached': True})

    result = generate_feedback(user_id=current_user.id, attempt=attempt)
    if not result['ok']:
        return _json_error('Tutor indisponível no momento, tente novamente em instantes.', 503)

    attempt.ai_feedback = result['text']
    db.session.commit()
    return jsonify({'ok': True, 'has_errors': True, 'feedback': result['text'], 'badge': '🤖 Gerado por IA — pode conter erros', 'cached': False})


@ai_bp.post('/feedback/<int:attempt_id>/rating')
@login_required
def feedback_rating(attempt_id):
    if current_user.is_admin:
        abort(403)
    if not feature_enabled('ai_feedback_enabled'):
        abort(404)

    attempt = ActivityAttempt.query.filter_by(id=attempt_id, user_id=current_user.id).first_or_404()
    payload = request.get_json(silent=True) or {}
    rating = str(payload.get('rating', '')).strip().lower()
    if rating not in {'up', 'down'}:
        return _json_error('Avaliação inválida.')

    log_non_api_event(
        current_user.id,
        'ai_feedback_rating',
        TUTOR_MODEL,
        None,
        {'attempt_id': attempt.id, 'rating': rating},
    )
    return jsonify({'ok': True})
