"""Infraestrutura central da integração Gemini do Portal Python."""
import logging
import os
import time
from typing import Any

import bleach
import requests

from ..extensions import db
from ..settings import get_bool, get_int
from .models import AICallLog

logger = logging.getLogger(__name__)

# Stable model suitable for production; the preview endpoint is avoided to reduce model-availability surprises.
TUTOR_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.8-flash').strip() or 'gemini-3.8-flash'
SONNET_MODEL = TUTOR_MODEL
TUTOR_TIMEOUT_SECONDS = 20
LONG_TIMEOUT_SECONDS = 45
MAX_CONTEXT_CHARS = 48000  # aproximadamente 12k tokens em texto comum

FEATURE_DEFAULTS = {
    'ai_tutor_enabled': 'false',
    'ai_feedback_enabled': 'false',
    'ai_question_gen_enabled': 'true',
    'ai_class_summary_enabled': 'false',
    'ai_project_precorrect_enabled': 'false',
    'ai_code_review_enabled': 'false',
    'ai_tutor_rate_limit': '10',
    'ai_question_gen_rate_limit': '20',
}


def _api_key():
    # Gemini Developer API accepts GEMINI_API_KEY and GOOGLE_API_KEY.
    # Prefer the project-specific name used by Portal Python.
    key = (os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY') or '').strip()
    if not key:
        logger.warning('Integração Gemini indisponível: GEMINI_API_KEY/GOOGLE_API_KEY não configurada.')
        return None
    return key


def _model_name(model=None):
    value = (model or TUTOR_MODEL).strip()
    # The REST endpoint expects the bare model id, not models/<id>.
    return value.removeprefix('models/').strip() or 'gemini-3.8-flash'


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


def _extract_response_text(data):
    candidates = data.get('candidates') if isinstance(data, dict) else None
    if not isinstance(candidates, list) or not candidates:
        prompt_feedback = data.get('promptFeedback') if isinstance(data, dict) else None
        block_reason = (prompt_feedback or {}).get('blockReason') if isinstance(prompt_feedback, dict) else None
        if block_reason:
            raise RuntimeError(f'Resposta bloqueada pelo Gemini: {block_reason}')
        raise RuntimeError('O Gemini não retornou candidatos de resposta.')

    parts = ((candidates[0].get('content') or {}).get('parts') or [])
    text_parts = [str(part.get('text', '')) for part in parts if isinstance(part, dict) and part.get('text')]
    text = '\n'.join(text_parts).strip()
    if not text:
        finish_reason = candidates[0].get('finishReason')
        raise RuntimeError(f'O Gemini retornou uma resposta sem texto{f" ({finish_reason})" if finish_reason else ""}.')
    return text


def call_gemini(*, user_id, feature, model, system, messages, timeout=20, metadata=None, max_tokens=1200, response_mime_type=None):
    """Faz uma chamada Gemini via REST v1 com resposta normalizada para todas as features.

    O Portal usa uma única porta de entrada para Tutor, feedback, resumo e geração
    de questões. Assim, uma mudança no SDK não quebra cada recurso separadamente.
    A chave nunca é incluída em logs ou respostas do navegador.
    """
    started = time.monotonic()
    key = _api_key()
    model_id = _model_name(model)
    if not key:
        error = 'Gemini indisponível: configure GEMINI_API_KEY no Render.'
        _log_call(user_id, feature, model_id, started, False, error=error, metadata=metadata)
        return {'ok': False, 'text': '', 'error': error}

    prompt_parts = []
    for item in messages or []:
        role = item.get('role', 'user')
        content = str(item.get('content', '')).strip()
        if content:
            label = 'Aluno' if role == 'user' else 'Tutor'
            prompt_parts.append(f'{label}:\n{content}')
    prompt = '\n\n'.join(prompt_parts).strip()

    payload = {
        'systemInstruction': {'parts': [{'text': str(system or '')}]},
        'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
        'generationConfig': {
            'maxOutputTokens': int(max_tokens),
        },
    }
    if response_mime_type:
        payload['generationConfig']['responseMimeType'] = response_mime_type

    url = f'https://generativelanguage.googleapis.com/v1/models/{model_id}:generateContent'
    try:
        response = requests.post(
            url,
            headers={'x-goog-api-key': key, 'Content-Type': 'application/json'},
            json=payload,
            timeout=max(5, int(timeout)),
        )
        try:
            data = response.json()
        except ValueError:
            data = {}

        if not response.ok:
            error_info = data.get('error') if isinstance(data, dict) else None
            code = response.status_code
            message = error_info.get('message') if isinstance(error_info, dict) else response.text[:1000]
            exc = RuntimeError(f'Gemini HTTP {code}: {message}')
            setattr(exc, 'code', code)
            raise exc

        raw_text = _extract_response_text(data)
        text = sanitize_output(raw_text)
        if not text.strip():
            raise RuntimeError('O Gemini retornou uma resposta vazia.')

        usage = data.get('usageMetadata') if isinstance(data, dict) else {}
        _log_call(
            user_id, feature, model_id, started, True,
            input_tokens=(usage or {}).get('promptTokenCount', 0),
            output_tokens=(usage or {}).get('candidatesTokenCount', 0),
            metadata=metadata,
        )
        return {'ok': True, 'text': text, 'error': None}
    except requests.Timeout as exc:
        safe_error = 'O Gemini demorou demais para responder. Tente novamente.'
        logger.warning('Timeout Gemini (%s/%s): %s', feature, model_id, exc)
        _log_call(user_id, feature, model_id, started, False, error=str(exc), metadata=metadata)
        return {'ok': False, 'text': '', 'error': safe_error}
    except requests.RequestException as exc:
        logger.warning('Falha de rede Gemini (%s/%s): %s', feature, model_id, exc)
        _log_call(user_id, feature, model_id, started, False, error=str(exc), metadata=metadata)
        return {'ok': False, 'text': '', 'error': 'Não foi possível conectar ao Gemini. Tente novamente em instantes.'}
    except Exception as exc:
        status = getattr(exc, 'code', None) or getattr(exc, 'status_code', None)
        message = str(exc).lower()
        if status in (401, 403) or 'api key' in message or 'permission' in message:
            safe_error = 'A chave do Gemini não foi aceita. Verifique GEMINI_API_KEY no Render.'
        elif status == 400 or 'invalid argument' in message:
            safe_error = 'O Gemini rejeitou a solicitação. Verifique o modelo e tente novamente.'
        elif status == 404 or 'not found' in message or 'not_found' in message:
            safe_error = f'O modelo Gemini configurado ({model_id}) não está disponível para esta chave.'
        elif status == 429 or 'resource exhausted' in message or 'rate limit' in message:
            safe_error = 'O limite do Gemini foi atingido. Aguarde um pouco e tente novamente.'
        elif 'blocked' in message:
            safe_error = 'O Gemini bloqueou esta solicitação. Tente reformular o pedido ou o conteúdo.'
        else:
            safe_error = 'IA indisponível no momento, tente novamente em instantes.'
        logger.warning('Falha na chamada Gemini (%s/%s): %s', feature, model_id, exc)
        _log_call(user_id, feature, model_id, started, False, error=str(exc), metadata=metadata)
        return {'ok': False, 'text': '', 'error': safe_error}

def tutor_rate_limit():
    return max(1, get_int('ai_tutor_rate_limit', 10))
