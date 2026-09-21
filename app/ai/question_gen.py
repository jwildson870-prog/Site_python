"""Geração de questões em rascunho para revisão humana."""
import json
import re

import bleach

from ..models import Content
from . import TUTOR_MODEL, MAX_CONTEXT_CHARS, call_gemini


def _clean_text(value, limit):
    return bleach.clean(str(value or '').strip(), tags=[], attributes={}, strip=True)[:limit]


def _extract_json(text):
    text = (text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
        text = re.sub(r'\s*```$', '', text)
    start = text.find('{')
    end = text.rfind('}')
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def _normalize_questions(payload, limit):
    raw = payload.get('questions', []) if isinstance(payload, dict) else []
    if not isinstance(raw, list):
        return []

    result = []
    for item in raw[:limit]:
        if not isinstance(item, dict):
            continue
        question = _clean_text(item.get('question'), 1000)
        options = item.get('options', [])
        if not question or not isinstance(options, list):
            continue
        options = [_clean_text(option, 500) for option in options[:4]]
        options = [option for option in options if option]
        if len(options) < 2:
            continue
        try:
            correct_index = int(item.get('correct_index', 0))
        except (TypeError, ValueError):
            correct_index = 0
        if correct_index < 0 or correct_index >= len(options):
            correct_index = 0
        difficulty = _clean_text(item.get('difficulty'), 20).lower()
        if difficulty not in {'facil', 'medio', 'dificil'}:
            difficulty = 'medio'
        result.append({
            'question': question,
            'options': options,
            'correct_index': correct_index,
            'difficulty': difficulty,
            'category': _clean_text(item.get('category'), 100) or 'Geral',
            'tags': _clean_text(item.get('tags'), 1000),
            'code': _clean_text(item.get('code'), 8000),
        })
    return result


def generate_questions(*, user_id, content, count):
    body = (content.body or '').strip()
    if not body:
        return {'ok': False, 'text': '', 'error': 'Este conteúdo não possui texto suficiente para gerar questões.'}

    count = max(1, min(int(count), 10))
    context = body[:MAX_CONTEXT_CHARS]
    system = (
        'Você é um assistente pedagógico do Portal Python. Gere questões exclusivamente a partir do material fornecido. '
        'Não invente fatos, conceitos ou informações que não estejam no material. '
        'As questões são RASCUNHOS para revisão de um professor. Nunca diga que foram salvas. '
        'Produza questões objetivas com 2 a 4 alternativas e apenas uma resposta correta. '
        'Retorne SOMENTE JSON válido no formato: '
        '{"questions":[{"question":"...","options":["...","..."],"correct_index":0,'
        '"difficulty":"facil|medio|dificil","category":"...","tags":"...","code":""}]}.'
    )
    prompt = (
        f'Conteúdo: {content.title}\n\nMaterial:\n{context}\n\n'
        f'Gere {count} questão(ões) de múltipla escolha. Cada questão deve testar compreensão real do material.'
    )
    result = call_gemini(
        user_id=user_id,
        feature='ai_question_gen',
        model=TUTOR_MODEL,
        system=system,
        messages=[{'role': 'user', 'content': prompt}],
        timeout=20,
        response_mime_type='application/json',
        metadata={'content_id': content.id, 'requested_count': count},
        max_tokens=max(1600, count * 420),
    )
    if not result['ok']:
        return result

    try:
        payload = _extract_json(result['text'])
        questions = _normalize_questions(payload, count)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {'ok': False, 'text': '', 'error': 'Não foi possível interpretar as questões geradas. Tente novamente.'}

    if not questions:
        return {'ok': False, 'text': '', 'error': 'A IA não retornou questões utilizáveis. Tente novamente.'}
    return {'ok': True, 'text': '', 'error': None, 'questions': questions}
