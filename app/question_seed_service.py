"""Carga inicial do banco de questões gerado para o Portal Python."""
from .extensions import db
from .models import Series, Subject, QuestionBank
from .question_bank_seed import ALL_GENERATED_QUESTIONS, QUESTION_COUNT


def seed_generated_question_bank():
    """Insere as 2.000 questões uma única vez, sem apagar questões do professor.

    A carga é idempotente: se o banco já tiver todas as questões geradas, nada é feito.
    Se algumas forem removidas, somente as faltantes são recriadas.
    """
    existing_count = QuestionBank.query.count()
    if existing_count >= QUESTION_COUNT:
        return 0

    series1 = Series.query.filter_by(name='1º ano').first()
    series2 = Series.query.filter_by(name='2º ano').first()
    sub1 = Subject.query.filter_by(name='Python Básico', series_id=series1.id).first() if series1 else None
    sub2 = Subject.query.filter_by(name='Programação com Python', series_id=series2.id).first() if series2 else None
    if not sub1 or not sub2:
        return 0

    existing = {row.question for row in QuestionBank.query.with_entities(QuestionBank.question).all()}
    added = 0
    for item in ALL_GENERATED_QUESTIONS:
        if item['question'] in existing:
            continue
        # As duas primeiras categorias ficam em Python Básico; funções e análise em Programação com Python.
        subject = sub1 if item.get('topic') in {'Condicionais', 'Estruturas de Repetição'} else sub2
        obj = QuestionBank(
            question=item['question'],
            correct=item['correct'],
            code=item.get('code'),
            difficulty=item.get('difficulty', 'medio'),
            series_id=subject.series_id,
            subject_id=subject.id,
        )
        obj.set_options(item['options'])
        db.session.add(obj)
        existing.add(item['question'])
        added += 1
    if added:
        db.session.commit()
    return added
