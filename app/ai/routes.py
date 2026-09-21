"""Rotas isoladas do Tutor de IA."""
from datetime import timedelta

from flask import Blueprint, abort, jsonify, request, session, render_template
from flask_login import current_user, login_required
from sqlalchemy import and_, or_

from ..extensions import db
from ..models import Content, ActivityAttempt, QuestionBank
from ..settings import get_bool
from ..timeutils import utcnow
from . import TUTOR_MODEL, feature_enabled, feature_available, log_non_api_event, tutor_available, tutor_rate_limit
from .models import AICallLog
from .tutor import ask_tutor
from .feedback import generate_feedback, _wrong_answers
from .question_gen import generate_questions
from .class_summary import generate_class_summary
from flask_wtf.csrf import validate_csrf

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



def _require_admin():
    if not current_user.is_authenticated:
        abort(401)
    if not current_user.is_admin:
        abort(403)


def _question_gen_count_today(user_id):
    from datetime import datetime, timezone
    start = datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    return AICallLog.query.filter(
        AICallLog.user_id == user_id,
        AICallLog.feature == 'ai_question_gen',
        AICallLog.created_at >= start,
    ).count()


def _question_gen_limit():
    return max(1, get_int('ai_question_gen_rate_limit', 20))


@ai_bp.get('/questions')
@login_required
def question_generator():
    _require_admin()
    if not feature_enabled('ai_question_gen_enabled') or not tutor_available():
        abort(404)
    contents = Content.query.filter(
        Content.archived_at.is_(None),
        Content.body.isnot(None),
        Content.body != '',
    ).order_by(Content.id.desc()).all()
    return render_template('admin/ai_question_generator.html', contents=contents, limit=_question_gen_limit())


@ai_bp.post('/questions/generate/<int:content_id>')
@login_required
def question_generate(content_id):
    _require_admin()
    if not feature_enabled('ai_question_gen_enabled') or not tutor_available():
        abort(404)
    content = Content.query.filter(Content.id == content_id, Content.archived_at.is_(None)).first_or_404()
    if not (content.body or '').strip():
        return _json_error('Este conteúdo não possui texto suficiente para gerar questões.')
    payload = request.get_json(silent=True) or {}
    try:
        validate_csrf(str(payload.get('csrf_token', '')))
    except Exception:
        return _json_error('Sessão de segurança expirada. Atualize a página e tente novamente.', 400)
    try:
        count = int(payload.get('count', 3))
    except (TypeError, ValueError):
        count = 3
    if count < 1 or count > 10:
        return _json_error('Escolha entre 1 e 10 questões por geração.')

    limit = _question_gen_limit()
    used = _question_gen_count_today(current_user.id)
    if used >= limit:
        log_non_api_event(current_user.id, 'ai_question_gen', TUTOR_MODEL, 'rate_limit', {'content_id': content.id, 'limit': limit, 'period': 'day'})
        return _json_error('Você atingiu o limite diário de gerações de questões.', 429)

    result = generate_questions(user_id=current_user.id, content=content, count=count)
    if not result.get('ok'):
        return _json_error(result.get('error') or 'Não foi possível gerar as questões.', 503)
    return jsonify({
        'ok': True,
        'questions': result['questions'],
        'content': {'id': content.id, 'title': content.title, 'series_id': content.series_id, 'subject_id': content.subject_id},
        'badge': '🤖 Rascunho gerado por IA — revise antes de salvar',
        'remaining_generations': max(0, limit - used - 1),
    })


@ai_bp.post('/questions/save')
@login_required
def question_save():
    _require_admin()
    if not feature_enabled('ai_question_gen_enabled'):
        abort(404)
    payload = request.get_json(silent=True) or {}
    try:
        validate_csrf(str(payload.get('csrf_token', '')))
    except Exception:
        return _json_error('Sessão de segurança expirada. Atualize a página e tente novamente.', 400)
    content_id = payload.get('content_id')
    content = Content.query.filter(Content.id == content_id, Content.archived_at.is_(None)).first_or_404()
    item = payload.get('question')
    if not isinstance(item, dict):
        return _json_error('Rascunho de questão inválido.')
    question = str(item.get('question', '')).strip()[:1000]
    options = item.get('options', [])
    try:
        correct_index = int(item.get('correct_index', 0))
    except (TypeError, ValueError):
        correct_index = -1
    if not question or not isinstance(options, list) or not (2 <= len(options) <= 4) or any(not str(x).strip() for x in options):
        return _json_error('Preencha o enunciado e entre 2 e 4 alternativas.')
    options = [str(x).strip()[:500] for x in options]
    if correct_index < 0 or correct_index >= len(options):
        return _json_error('Selecione uma resposta correta válida.')
    difficulty = str(item.get('difficulty', 'medio')).strip().lower()
    if difficulty not in {'facil', 'medio', 'dificil'}:
        difficulty = 'medio'
    category = str(item.get('category', 'Geral')).strip()[:100] or 'Geral'
    tags = str(item.get('tags', '')).strip()[:1000] or None
    code = str(item.get('code', '')).strip()[:8000] or None
    saved = QuestionBank(
        question=question,
        correct=options[correct_index],
        difficulty=difficulty,
        category=category,
        tags=tags,
        code=code,
        series_id=content.series_id,
        subject_id=content.subject_id,
    )
    saved.set_options(options)
    db.session.add(saved)
    db.session.commit()
    return jsonify({'ok': True, 'id': saved.id, 'message': 'Questão salva no banco com confirmação do professor.'})


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


@ai_bp.post('/class-summary')
@login_required
def class_summary():
    """Gera resumo somente a partir das métricas já calculadas pela página administrativa."""
    _require_admin()
    if not feature_available('ai_class_summary_enabled'):
        abort(404)

    payload = request.get_json(silent=True) or {}
    metrics = payload.get('metrics')
    if not isinstance(metrics, dict):
        return _json_error('Métricas inválidas para o resumo.', 400)

    # Limite de campos/linhas para evitar que a rota vire um canal de envio de dados arbitrários.
    if len(metrics) > 20:
        return _json_error('Conjunto de métricas inválido.', 400)

    result = generate_class_summary(user_id=current_user.id, metrics=metrics)
    if not result.get('ok'):
        return _json_error('Resumo indisponível no momento, tente novamente em instantes.', 503)

    return jsonify({
        'ok': True,
        'text': result['text'],
        'badge': '🤖 Gerado por IA — pode conter erros',
        'timestamp': utcnow().isoformat(),
    })
