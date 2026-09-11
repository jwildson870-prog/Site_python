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
    '1º ano': ['Química Geral'],
    '2º ano': ['Química Orgânica'],
    '3º ano': ['Físico-Química'],
    '4º ano': ['Química Aplicada'],
}
TOPICS = {
    ('1º ano', 'Química Geral'): ['Estrutura Atômica', 'Prótons, nêutrons e elétrons', 'Tabela Periódica', 'Ligações Químicas'],
    ('2º ano', 'Química Orgânica'): ['Funções Orgânicas', 'Hidrocarbonetos'],
    ('3º ano', 'Físico-Química'): ['Eletroquímica', 'Termoquímica'],
    ('4º ano', 'Química Aplicada'): ['Química Ambiental', 'Química no Cotidiano'],
}

def ensure_admin():
    email = os.getenv('ADMIN_EMAIL', 'professor@portaljm.com').strip().lower()
    password = os.getenv('ADMIN_PASSWORD', 'PortalJM@2026')
    name = os.getenv('ADMIN_NAME', 'Professor JM').strip() or 'Professor JM'
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
                        body=f'<p><strong>{title}</strong></p><p>Material inicial de demonstração do Portal JM – Química. O professor pode editar este conteúdo pelo painel do professor.</p>',
                        series_id=s.id, subject_id=sub.id
                    ))
    db.session.commit()
