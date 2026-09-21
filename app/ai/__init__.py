"""Infraestrutura central da integração Anthropic do Portal Python."""
import logging
import os
import time
from typing import Any

import bleach

from ..extensions import db
from ..settings import get_bool, get_int
from .models import AICallLog

logger = logging.getLogger(__name__)

TUTOR_MODEL = 'claude-haiku-4-5-20251001'
SONNET_MODEL = 'claude-sonnet-4-5-20250929'
TUTOR_TIMEOUT_SECONDS = 15
LONG_TIMEOUT_SECONDS = 60
MAX_CONTEXT_CHARS = 48000  # aproximadamente 12k tokens em texto comum

FEATURE_DEFAULTS = {
    'ai_tutor_enabled': 'false',
    'ai_feedback_enabled': 'false',
    'ai_question_gen_enabled': 'false',
    'ai_class_summary_enabled': 'false',
    'ai_project_precorrect_enabled': 'false',
    'ai_code_review_enabled': 'false',
    'ai_tutor_rate_limit': '10',
}


def _api_key():
    key = os.getenv('ANTHROPIC_API_KEY', '').strip()
    if not key:
        logger.warning('Integração Anthropic indisponível: ANTHROPIC_API_KEY não configurada.')
        return None
    return key


def feature_enabled(feature):
    return get_bool(feature, FEATURE_DEFAULTS.get(feature, 'false') == 'true')


def tutor_available():
    return feature_enabled('ai_tutor_enabled') and bool(_api_key())


def sanitize_output(text):
    return bleach.clean(text or '', tags=[], attributes={}, strip=True)


def _log_call(user_id, feature, model, started, success, error=None, input_tokens=0, output_tokens=0, metadata=None):
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    try:
        db.session.add(AICallLog(
            user_id=user_id,
            feature=feature,
            model=model,
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
            latency_ms=latency_ms,
            success=bool(success),
            error=(str(error)[:4000] if error else None),
            call_metadata=metadata,
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('Falha ao gravar AICallLog.')


def log_non_api_event(user_id, feature, model, error, metadata=None):
    """Registra bloqueios antes da chamada para manter observabilidade."""
    started = time.monotonic()
    _log_call(user_id, feature, model, started, False, error=error, metadata=metadata)


def _client(timeout):
    from anthropic import Anthropic
    return Anthropic(api_key=_api_key(), timeout=timeout, max_retries=0)


def call_anthropic(*, user_id, feature, model, system, messages, timeout=15, metadata=None):
    """Faz uma única operação centralizada com retry de 429/5xx.

    Nenhuma exceção da SDK atravessa este limite: a rota recebe sempre
    {ok, text, error}.
    """
    started = time.monotonic()
    key = _api_key()
    if not key:
        error = 'Anthropic indisponível.'
        _log_call(user_id, feature, model, started, False, error=error, metadata=metadata)
        return {'ok': False, 'text': '', 'error': error}

    try:
        from anthropic import APIStatusError
    except Exception:  # pragma: no cover - compatibilidade com SDKs da faixa suportada
        APIStatusError = Exception

    last_error = None
    for attempt in range(2):
        try:
            response = _client(timeout).messages.create(
                model=model,
                max_tokens=1200,
                system=system,
                messages=messages,
            )
            raw_text = ''.join(getattr(block, 'text', '') for block in getattr(response, 'content', []) if getattr(block, 'type', '') == 'text')
            text = sanitize_output(raw_text)
            usage = getattr(response, 'usage', None)
            _log_call(
                user_id, feature, model, started, True,
                input_tokens=getattr(usage, 'input_tokens', 0),
                output_tokens=getattr(usage, 'output_tokens', 0),
                metadata=metadata,
            )
            return {'ok': True, 'text': text, 'error': None}
        except APIStatusError as exc:
            status = getattr(exc, 'status_code', None)
            last_error = exc
            if status not in (429, 500, 502, 503, 504) or attempt == 1:
                break
        except Exception as exc:
            last_error = exc
            break

    safe_error = 'Tutor indisponível no momento, tente novamente em instantes.'
    logger.warning('Falha na chamada Anthropic (%s/%s): %s', feature, model, last_error)
    _log_call(user_id, feature, model, started, False, error=str(last_error), metadata=metadata)
    return {'ok': False, 'text': '', 'error': safe_error}


def tutor_rate_limit():
    return max(1, get_int('ai_tutor_rate_limit', 10))
