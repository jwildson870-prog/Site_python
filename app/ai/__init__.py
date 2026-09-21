"""Infraestrutura central da integração Gemini do Portal Python."""
import logging
import os
import time
from typing import Any

import bleach

from ..extensions import db
from ..settings import get_bool, get_int
from .models import AICallLog

logger = logging.getLogger(__name__)

TUTOR_MODEL = 'gemini-3-flash-preview'
SONNET_MODEL = TUTOR_MODEL
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
    'ai_question_gen_rate_limit': '20',
}


def _api_key():
    key = os.getenv('GEMINI_API_KEY', '').strip()
    if not key:
        logger.warning('Integração Gemini indisponível: GEMINI_API_KEY não configurada.')
        return None
    return key


def feature_enabled(feature):
    return get_bool(feature, FEATURE_DEFAULTS.get(feature, 'false') == 'true')


def tutor_available():
    return feature_enabled('ai_tutor_enabled') and bool(_api_key())


def feature_available(feature):
    """Retorna True somente quando a feature está ligada e a chave do Gemini existe."""
    return feature_enabled(feature) and bool(_api_key())


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
    from google import genai
    return genai.Client(api_key=_api_key(), http_options={"timeout": int(timeout * 1000)})


def call_gemini(*, user_id, feature, model, system, messages, timeout=15, metadata=None, max_tokens=1200):
    """Faz uma única operação centralizada com Gemini, com retry de erros transitórios.

    Nenhuma exceção da SDK atravessa este limite: a rota recebe sempre
    {ok, text, error}.
    """
    started = time.monotonic()
    key = _api_key()
    if not key:
        error = 'Gemini indisponível.'
        _log_call(user_id, feature, model, started, False, error=error, metadata=metadata)
        return {'ok': False, 'text': '', 'error': error}

    # O Gemini recebe a instrução de sistema separadamente e o histórico
    # no formato de texto para preservar o contrato existente das features.
    prompt_parts = []
    for item in messages or []:
        role = item.get('role', 'user')
        content = str(item.get('content', '')).strip()
        if content:
            label = 'Aluno' if role == 'user' else 'Tutor'
            prompt_parts.append(f'{label}:\n{content}')
    prompt = '\n\n'.join(prompt_parts)

    last_error = None
    for attempt in range(2):
        try:
            response = _client(timeout).models.generate_content(
                model=model,
                contents=prompt,
                config={
                    'system_instruction': system,
                    'max_output_tokens': max_tokens,
                },
            )
            raw_text = getattr(response, 'text', '') or ''
            text = sanitize_output(raw_text)
            usage = getattr(response, 'usage_metadata', None)
            _log_call(
                user_id, feature, model, started, True,
                input_tokens=getattr(usage, 'prompt_token_count', 0),
                output_tokens=getattr(usage, 'candidates_token_count', 0),
                metadata=metadata,
            )
            return {'ok': True, 'text': text, 'error': None}
        except Exception as exc:
            last_error = exc
            status = getattr(exc, 'code', None) or getattr(exc, 'status_code', None)
            if status not in (429, 500, 502, 503, 504) or attempt == 1:
                break
            time.sleep(0.5)

    safe_error = 'IA indisponível no momento, tente novamente em instantes.'
    logger.warning('Falha na chamada Gemini (%s/%s): %s', feature, model, last_error)
    _log_call(user_id, feature, model, started, False, error=str(last_error), metadata=metadata)
    return {'ok': False, 'text': '', 'error': safe_error}

def tutor_rate_limit():
    return max(1, get_int('ai_tutor_rate_limit', 10))
