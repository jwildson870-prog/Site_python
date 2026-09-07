import os
from .extensions import db
from .models import User, Series, Subject, Content

# Os quatro níveis organizam a trilha de aprendizagem em Python.
CANONICAL_SERIES = {
    'Nível 1 — Fundamentos': ['Nível 1 — Fundamentos', '1º ano', '1ª Série', '1º Série'],
    'Nível 2 — Estruturas': ['Nível 2 — Estruturas', '2º ano', '2ª Série', '2º Série'],
    'Nível 3 — Programação': ['Nível 3 — Programação', '3º ano', '3ª Série', '3º Série'],
    'Nível 4 — Projetos': ['Nível 4 — Projetos', '4º ano', '4ª Série', '4º Série'],
}
INITIAL_SUBJECTS = {
    'Nível 1 — Fundamentos': ['Sintaxe e Fundamentos'],
    'Nível 2 — Estruturas': ['Estruturas de Dados'],
    'Nível 3 — Programação': ['Funções e POO'],
    'Nível 4 — Projetos': ['Projetos em Python'],
}
TOPICS = {
    ('Nível 1 — Fundamentos', 'Sintaxe e Fundamentos'): [
        'Variáveis e tipos de dados', 'Entrada e saída com input() e print()',
        'Operadores e expressões', 'if, elif e else', 'while e for',
    ],
    ('Nível 2 — Estruturas', 'Estruturas de Dados'): [
        'Listas', 'Tuplas', 'Dicionários', 'Conjuntos (set)', 'Listas de listas',
    ],
    ('Nível 3 — Programação', 'Funções e POO'): [
        'Funções e parâmetros', 'Escopo e retorno', 'Módulos e bibliotecas',
        'Classes e objetos', 'Herança e encapsulamento',
    ],
    ('Nível 4 — Projetos', 'Projetos em Python'): [
        'Arquivos JSON e CSV', 'Tratamento de erros com try/except',
        'Persistência de dados', 'Organização de projetos', 'Projeto final',
    ],
}

def ensure_admin():
    email = os.getenv('ADMIN_EMAIL', 'professor@portalpython.com').strip().lower()
    password = os.getenv('ADMIN_PASSWORD', 'Python@2026')
    name = os.getenv('ADMIN_NAME', 'Professor Python').strip() or 'Professor Python'
    if not email or not password:
        return None, False

    target = User.query.filter_by(email=email).first()
    admins = User.query.filter_by(role='admin').order_by(User.id).all()
    if target:
        admin = target
        for other in admins:
            if other.id != admin.id:
                other.role = 'student'
    elif admins:
        admin = admins[0]
        admin.email = email
        for other in admins[1:]:
            other.role = 'student'
    else:
        admin = User(email=email)
        db.session.add(admin)

    admin.name = name
    admin.role = 'admin'
    admin.set_password(password)
    db.session.commit()
    return admin, True

def ensure_canonical_series():
    for canonical, aliases in CANONICAL_SERIES.items():
        if Series.query.filter_by(name=canonical).first():
            continue
        legacy = next((Series.query.filter_by(name=a).first() for a in aliases if a != canonical), None)
        if legacy:
            legacy.name = canonical
        else:
            db.session.add(Series(name=canonical))
    db.session.commit()

def seed_initial_content():
    ensure_canonical_series()
    for sname, subjects in INITIAL_SUBJECTS.items():
        s = Series.query.filter_by(name=sname).first()
        for subname in subjects:
            sub = Subject.query.filter_by(name=subname, series_id=s.id).first()
            if not sub:
                sub = Subject(name=subname, series_id=s.id)
                db.session.add(sub)
                db.session.flush()
            for title in TOPICS.get((sname, subname), []):
                if not Content.query.filter_by(title=title, subject_id=sub.id).first():
                    db.session.add(Content(
                        title=title,
                        description=f'Conteúdo de Python: {title}.',
                        kind='explanation',
                        body=(
                            f'<p><strong>{title}</strong></p>'
                            f'<p>Material introdutório para aprender Python passo a passo. '
                            f'Use o painel do professor para editar, ampliar e adicionar exemplos e exercícios.</p>'
                        ),
                        series_id=s.id, subject_id=sub.id
                    ))
    db.session.commit()
