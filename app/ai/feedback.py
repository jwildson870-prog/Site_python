"""Geração de feedback educacional para tentativas de atividades."""
import re

import bleach

from ..models import Content
from . import MAX_CONTEXT_CHARS, TUTOR_MODEL, call_gemini


def _plain(text):
    value = bleach.clean(text or "", tags=[], attributes={}, strip=True)
    return re.sub(r"\s+", " ", value).strip()


def _relevant_contents(attempt, limit=5):
    query = (
        Content.query
        .filter(
            Content.series_id == attempt.activity.series_id,
            Content.subject_id == attempt.activity.subject_id,
            Content.archived_at.is_(None),
            Content.status == "published",
            Content.body.isnot(None),
        )
        .order_by(Content.updated_at.desc(), Content.id.desc())
        .limit(limit)
    )
    return query.all()


def _wrong_answers(attempt):
    questions = attempt.get_presented_questions()
    answers = attempt.get_answers()
    wrong = []
    for index, question in enumerate(questions):
        if question.get("kind", "objective") == "essay":
            continue
        answer = answers.get(str(index), "")
        correct = question.get("correct", "")
        if answer != correct:
            wrong.append({
                "number": index + 1,
                "question": _plain(question.get("question", ""))[:1200],
                "student_answer": _plain(answer)[:800] or "Não respondida",
                "correct_answer": _plain(correct)[:800],
            })
    return wrong


def build_feedback_prompt(attempt, wrong_answers, contents):
    remaining = MAX_CONTEXT_CHARS
    materials = []
    for content in contents:
        body = _plain(content.body)
        if not body:
            continue
        excerpt = body[:min(3500, remaining)]
        if not excerpt:
            break
        materials.append(f"MATERIAL: {content.title}\n{excerpt}")
        remaining -= len(excerpt)
        if remaining <= 0:
            break

    errors = "\n\n".join(
        f"Questão {item['number']}: {item['question']}\n"
        f"Resposta do aluno: {item['student_answer']}\n"
        f"Resposta correta: {item['correct_answer']}"
        for item in wrong_answers
    )
    material_text = "\n\n---\n\n".join(materials) or "Nenhum material publicado relacionado foi encontrado."

    system = f"""Você é um tutor educacional do Portal Python.

Sua tarefa é explicar os erros de uma tentativa de atividade para ajudar o aluno a aprender.

REGRAS OBRIGATÓRIAS:
- Use somente os erros, respostas corretas e materiais fornecidos abaixo.
- Não use conhecimento externo para preencher lacunas.
- Nunca dê ou altere uma nota.
- Não diga que uma resposta está errada sem explicar o conceito com base no material disponível.
- Para cada erro, explique de forma curta o que aconteceu e como o aluno pode entender o conceito.
- Ao final de cada erro, indique o material que deve ser revisado usando o título exato quando houver material relacionado.
- Se os materiais não permitirem explicar algum erro, diga que não encontrou explicação suficiente no material, em vez de inventar.
- Não faça a atividade pelo aluno nem entregue uma resposta de prova além da resposta correta que já foi fornecida para explicar o erro.
- Não atribua nota nem faça julgamento sobre o desempenho do aluno além de explicar os erros.
- Escreva em português do Brasil, com linguagem clara e adequada para estudante.

ERROS DA TENTATIVA:
---
{errors}
---

MATERIAIS DISPONÍVEIS PARA REVISÃO:
---
{material_text}
---"""
    return system


def generate_feedback(*, user_id, attempt):
    wrong_answers = _wrong_answers(attempt)
    if not wrong_answers:
        return {"ok": True, "text": "", "error": None, "has_errors": False}

    contents = _relevant_contents(attempt)
    result = call_gemini(
        user_id=user_id,
        feature="ai_feedback",
        model=TUTOR_MODEL,
        system=build_feedback_prompt(attempt, wrong_answers, contents),
        messages=[{"role": "user", "content": "Explique meus erros e diga quais materiais devo revisar."}],
        timeout=15,
        metadata={
            "attempt_id": attempt.id,
            "activity_id": attempt.activity_id,
            "wrong_count": len(wrong_answers),
            "content_ids": [content.id for content in contents],
        },
    )
    result["has_errors"] = True
    return result
