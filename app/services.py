import os
from .extensions import db
from .models import User, Series, Subject, Content

CANONICAL_SERIES = {
    '1º ano': ['1º ano', '1ª Série', '1º Série'],
    '2º ano': ['2º ano', '2ª Série', '2º Série'],
    '3º ano': ['3º ano', '3ª Série', '3º Série'],
    '4º ano': ['4º ano', '4ª Série', '4º Série'],
}
INITIAL_SUBJECTS = {
    '1º ano': ['Python Básico'],
    '2º ano': ['Programação com Python'],
    '3º ano': ['Estruturas de Dados'],
    '4º ano': ['Projetos Python'],
}
TOPICS = {
    ('1º ano', 'Python Básico'): ['Variáveis e tipos de dados', 'Entrada e saída de dados', 'Condicionais if, elif e else', 'Laços for e while'],
    ('2º ano', 'Programação com Python'): ['Funções', 'Listas e dicionários', 'Tratamento de erros', 'Módulos e bibliotecas'],
    ('3º ano', 'Estruturas de Dados'): ['Listas e tuplas', 'Dicionários e conjuntos', 'Listas de dicionários', 'Programação orientada a objetos'],
    ('4º ano', 'Projetos Python'): ['APIs e requisições', 'Flask e aplicações web', 'Banco de dados', 'Deploy e boas práticas'],
}

def ensure_admin():
    email = os.getenv('ADMIN_EMAIL', '').strip().lower()
    password = os.getenv('ADMIN_PASSWORD', '')
    name = os.getenv('ADMIN_NAME', 'Professor Python').strip() or 'Professor Python'
    if not email or not password:
        return None, False

    # Reaproveita o usuário do e-mail configurado, evitando conflitos de UNIQUE.
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
                        description=f'Conteúdo introdutório de {title}.',
                        kind='explanation',
                        body=f'<p><strong>{title}</strong></p><p>Material inicial de demonstração do Portal Python. O professor pode editar este conteúdo pelo painel do professor.</p>',
                        series_id=s.id, subject_id=sub.id
                    ))
    db.session.commit()


def migrate_legacy_python_subjects():
    """Converte matérias herdadas do antigo portal para o currículo Python.

    Os registros de conteúdos/atividades/projetos são preservados: apenas a matéria
    de origem é ajustada para que formulários e filtros não continuem exibindo
    nomes de Química no Portal Python.
    """
    legacy_words = ('quím', 'quimic', 'laboratório', 'laboratorio', 'átomo', 'atomo', 'tabela periódica', 'tabela periodica')
    for series_name, target_name in ((name, subjects[0]) for name, subjects in INITIAL_SUBJECTS.items()):
        series = Series.query.filter_by(name=series_name).first()
        if not series:
            continue
        target = Subject.query.filter_by(name=target_name, series_id=series.id).first()
        if not target:
            continue
        legacy = [s for s in Subject.query.filter_by(series_id=series.id).all()
                  if s.id != target.id and any(word in (s.name or '').lower() for word in legacy_words)]
        for old in legacy:
            for item in list(old.contents): item.subject_id = target.id
            for item in list(old.activities): item.subject_id = target.id
            for item in list(old.experiments): item.subject_id = target.id
            db.session.delete(old)
    db.session.commit()
