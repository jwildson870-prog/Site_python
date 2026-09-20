import os
from pathlib import Path
from datetime import timedelta
import hashlib
from flask import Flask,render_template,redirect,url_for,request,session
from sqlalchemy import inspect, text
from flask_login import LoginManager,current_user,logout_user
from flask_wtf import CSRFProtect
import bleach
from .extensions import db

def create_app(test_config=None):
    app=Flask(__name__,instance_relative_config=True)
    local_uploads=Path(app.root_path).parent/'uploads'
    configured_uploads=os.getenv('UPLOAD_FOLDER','').strip()
    upload_folder=Path(configured_uploads) if configured_uploads else local_uploads
    upload_folder.mkdir(parents=True,exist_ok=True)
    Path(app.instance_path).mkdir(parents=True,exist_ok=True)
    dburl=os.getenv('DATABASE_URL','sqlite:///portal_python.db')
    if dburl.startswith('postgres://'): dburl='postgresql+psycopg2://'+dburl[11:]
    elif dburl.startswith('postgresql://'): dburl='postgresql+psycopg2://'+dburl[13:]
    is_production = os.getenv('FLASK_ENV', '').lower() == 'production' or os.getenv('RENDER') == 'true'
    secret_key = os.getenv('SECRET_KEY', '').strip()
    if not secret_key and is_production and not test_config:
        raise RuntimeError('SECRET_KEY deve ser configurada no ambiente de produção.')
    if not secret_key:
        secret_key = 'test-only-secret-key'
    app.config.update(
        SECRET_KEY=secret_key,
        SQLALCHEMY_DATABASE_URI=dburl,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # Reutiliza conexões do PostgreSQL/Neon para reduzir a latência das páginas.
        **({'SQLALCHEMY_ENGINE_OPTIONS': {'pool_pre_ping': True, 'pool_recycle': 300, 'pool_size': 5, 'max_overflow': 5, 'pool_timeout': 10}} if dburl.startswith('postgresql+') else {}),
        UPLOAD_FOLDER=str(upload_folder),
        MAX_CONTENT_LENGTH=25*1024*1024,
        MAX_FORM_MEMORY_SIZE=2*1024*1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', 'true' if is_production else 'false').lower() == 'true',
        SESSION_REFRESH_EACH_REQUEST=True,
        WTF_CSRF_TIME_LIMIT=3600,
        WTF_CSRF_SSL_STRICT=is_production,
        MAX_FORM_PARTS=200,
        TRUSTED_HOSTS=[h.strip() for h in os.getenv('TRUSTED_HOSTS','').split(',') if h.strip()] or None,
    )
    if test_config: app.config.update(test_config)
    db.init_app(app); login=LoginManager(app); login.login_view='auth.login'; login.session_protection='strong'; CSRFProtect(app)

    @app.template_filter('sanitize_html')
    def sanitize_html(value):
        allowed_tags = {'p','br','strong','em','b','i','u','ul','ol','li','h2','h3','h4','blockquote','code','pre','a'}
        allowed_attrs = {'a': ['href','title','target','rel']}
        return bleach.clean(value or '', tags=allowed_tags, attributes=allowed_attrs, protocols={'http','https','mailto'}, strip=True)

    @app.template_filter('highlight')
    def highlight(value, query):
        """Marca visualmente (com <mark>) as ocorrências de `query` dentro de
        `value`, sem HTML injection: o texto original é sempre escapado antes
        de qualquer marcação ser inserida."""
        from markupsafe import Markup, escape
        import re
        text = value or ''
        query = (query or '').strip()
        if not query:
            return Markup(escape(text))
        try:
            pattern = re.compile(re.escape(query), re.IGNORECASE)
        except re.error:
            return Markup(escape(text))
        pieces = []
        last_end = 0
        for match in pattern.finditer(text):
            pieces.append(escape(text[last_end:match.start()]))
            pieces.append(Markup('<mark class="search-hit">') + escape(match.group(0)) + Markup('</mark>'))
            last_end = match.end()
        pieces.append(escape(text[last_end:]))
        return Markup('').join(pieces)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        response.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin-allow-popups')
        response.headers.setdefault('Content-Security-Policy', "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline' https:; script-src 'self' 'unsafe-inline' https:; font-src 'self' data: https:; frame-src 'self' https:; connect-src 'self' https:; object-src 'none'; base-uri 'self'; form-action 'self' https://accounts.google.com")
        # Arquivos estáticos são imutáveis do ponto de vista da página e já usam
        # query strings de versão quando necessário; cache longo acelera visitas seguintes.
        if request.path.startswith('/static/'):
            response.headers.setdefault('Cache-Control', 'public, max-age=604800, stale-while-revalidate=86400')
        if request.is_secure or os.getenv('RENDER','').lower() == 'true':
            response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        return response
    from .models import User
    @login.user_loader
    def load_user(uid): return db.session.get(User,int(uid))

    @app.before_request
    def _enforce_session_version():
        # Uma troca/redefinição de senha incrementa User.session_version.
        # Se a sessão do navegador guarda uma versão antiga, ela é encerrada
        # aqui — é assim que uma redefinição de senha derruba sessões
        # antigas (item 7/9 do pedido) sem exigir uma tabela de dispositivos.
        if not current_user.is_authenticated:
            return
        if session.get('sv') != current_user.session_version:
            logout_user(); session.clear(); return
        # Também valida contra a tabela de sessões (item 9/10): se esta
        # sessão específica foi encerrada pelo usuário na tela "Sessões
        # ativas" (ou por qualquer outro motivo), derruba aqui também.
        raw = session.get('st')
        if raw:
            from .models import UserSession
            from .timeutils import utcnow
            token_hash = hashlib.sha256(raw.encode()).hexdigest()
            row = UserSession.query.filter_by(token_hash=token_hash, user_id=current_user.id).first()
            if not row or row.revoked_at is not None:
                logout_user(); session.clear()
            elif utcnow() - row.last_seen_at > timedelta(minutes=5):
                row.last_seen_at = utcnow(); db.session.commit()
    from .auth.routes import auth_bp
    from .student.routes import student_bp
    from .admin.routes import admin_bp
    app.register_blueprint(auth_bp); app.register_blueprint(student_bp); app.register_blueprint(admin_bp)
    @app.get('/')
    def home():
        if current_user.is_authenticated:
            return redirect(url_for('admin.dashboard' if current_user.is_admin else 'student.dashboard'))
        return render_template('index.html', registration_enabled=get_bool('public_registration', True))
    @app.errorhandler(403)
    def forbidden(e): return render_template('error.html',message='Acesso negado.'),403
    @app.errorhandler(404)
    def not_found(e): return render_template('error.html',message='Página não encontrada.'),404
    @app.errorhandler(413)
    def too_large(e): return render_template('error.html',message=f"Arquivo muito grande. Limite: {app.config.get('MAX_CONTENT_LENGTH', 25*1024*1024)//(1024*1024)} MB."),413

    @app.errorhandler(400)
    def bad_request(e): return render_template('error.html', message='Solicitação inválida.'), 400

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        app.logger.exception('Erro interno não tratado')
        return render_template('error.html', message='Ocorreu um erro interno. Tente novamente.'), 500

    @app.cli.command('create-admin')
    def create_admin():
        from .services import ensure_admin; u,_=ensure_admin(); print('Administrador configurado.' if u else 'Defina ADMIN_EMAIL e ADMIN_PASSWORD.')
    @app.cli.command('seed')
    def seed():
        from .services import seed_initial_content; seed_initial_content(); print('Conteúdo inicial confirmado.')

    @app.cli.command('seed-question-bank')
    def seed_question_bank():
        from .question_seed_service import seed_generated_question_bank
        added = seed_generated_question_bank()
        print(f'Banco expandido: {added} questão(ões) nova(s).')
    with app.app_context():
        db.create_all()
        from .settings import ensure_default_settings, get_int, get_bool
        ensure_default_settings()
        # O limite configurável continua respeitando o limite seguro do Flask.
        app.config['MAX_CONTENT_LENGTH'] = max(1, min(get_int('upload_limit_mb', 25), 100)) * 1024 * 1024
        # Migração leve e retrocompatível para instalações existentes: adiciona
        # o prazo das atividades sem apagar nem recriar tabelas do Neon.
        inspector = inspect(db.engine)
        if 'contents' in inspector.get_table_names() and 'preview_manifest' not in {c['name'] for c in inspector.get_columns('contents')}:
            with db.engine.begin() as conn:
                if db.engine.dialect.name == 'postgresql':
                    conn.execute(text('ALTER TABLE contents ADD COLUMN IF NOT EXISTS preview_manifest TEXT'))
                elif db.engine.dialect.name == 'sqlite':
                    conn.execute(text('ALTER TABLE contents ADD COLUMN preview_manifest TEXT'))
        if 'contents' in inspector.get_table_names() and 'archived_at' not in {c['name'] for c in inspector.get_columns('contents')}:
            with db.engine.begin() as conn:
                if db.engine.dialect.name == 'postgresql':
                    conn.execute(text('ALTER TABLE contents ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP'))
                elif db.engine.dialect.name == 'sqlite':
                    conn.execute(text('ALTER TABLE contents ADD COLUMN archived_at DATETIME'))
        if 'activities' in inspector.get_table_names() and 'archived_at' not in {c['name'] for c in inspector.get_columns('activities')}:
            with db.engine.begin() as conn:
                if db.engine.dialect.name == 'postgresql':
                    conn.execute(text('ALTER TABLE activities ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP'))
                elif db.engine.dialect.name == 'sqlite':
                    conn.execute(text('ALTER TABLE activities ADD COLUMN archived_at DATETIME'))
        if 'activities' in inspector.get_table_names() and 'due_at' not in {c['name'] for c in inspector.get_columns('activities')}:
            with db.engine.begin() as conn:
                if db.engine.dialect.name == 'postgresql':
                    conn.execute(text('ALTER TABLE activities ADD COLUMN IF NOT EXISTS due_at TIMESTAMP'))
                elif db.engine.dialect.name == 'sqlite':
                    conn.execute(text('ALTER TABLE activities ADD COLUMN due_at DATETIME'))
        inspector = inspect(db.engine)
        if 'activities' in inspector.get_table_names() and 'difficulty' not in {c['name'] for c in inspector.get_columns('activities')}:
            with db.engine.begin() as conn:
                if db.engine.dialect.name == 'postgresql':
                    conn.execute(text("ALTER TABLE activities ADD COLUMN IF NOT EXISTS difficulty VARCHAR(20) DEFAULT 'medio'"))
                elif db.engine.dialect.name == 'sqlite':
                    conn.execute(text("ALTER TABLE activities ADD COLUMN difficulty VARCHAR(20) DEFAULT 'medio'"))
        inspector = inspect(db.engine)
        if 'activities' in inspector.get_table_names():
            activity_cols = {c['name'] for c in inspector.get_columns('activities')}
            additions = {
                'max_attempts': "INTEGER DEFAULT 0",
                'allow_review': "BOOLEAN DEFAULT TRUE",
                'shuffle_questions': "BOOLEAN DEFAULT FALSE",
                'shuffle_options': "BOOLEAN DEFAULT FALSE",
            }
            with db.engine.begin() as conn:
                for col, definition in additions.items():
                    if col not in activity_cols:
                        definition_sql = definition
                        if db.engine.dialect.name == 'postgresql':
                            conn.execute(text(f'ALTER TABLE activities ADD COLUMN IF NOT EXISTS {col} {definition_sql}'))
                        else:
                            conn.execute(text(f'ALTER TABLE activities ADD COLUMN {col} {definition_sql}'))
        inspector = inspect(db.engine)
        if 'activity_attempts' in inspector.get_table_names() and 'presented_questions_json' not in {c['name'] for c in inspector.get_columns('activity_attempts')}:
            with db.engine.begin() as conn:
                if db.engine.dialect.name == 'postgresql':
                    conn.execute(text("ALTER TABLE activity_attempts ADD COLUMN IF NOT EXISTS presented_questions_json TEXT DEFAULT '[]'"))
                else:
                    conn.execute(text("ALTER TABLE activity_attempts ADD COLUMN presented_questions_json TEXT DEFAULT '[]'"))
        # Migração leve para instalações existentes: adiciona as colunas de
        # autenticação (item 3 do ITEM 3) sem apagar ou recriar tabelas.
        inspector = inspect(db.engine)
        if 'users' in inspector.get_table_names():
            existing_cols = {c['name'] for c in inspector.get_columns('users')}
            with db.engine.begin() as conn:
                if 'session_version' not in existing_cols:
                    if db.engine.dialect.name == 'postgresql':
                        conn.execute(text('ALTER TABLE users ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 0'))
                    elif db.engine.dialect.name == 'sqlite':
                        conn.execute(text('ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0'))
                if 'email_verified' not in existing_cols:
                    if db.engine.dialect.name == 'postgresql':
                        conn.execute(text('ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT false'))
                    elif db.engine.dialect.name == 'sqlite':
                        conn.execute(text('ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT 0'))
        # O banco de questões é aditivo e não altera tabelas existentes.
        # As tabelas novas (password_reset_tokens, email_verification_tokens,
        # login_attempts) são criadas automaticamente pelo db.create_all()
        # abaixo, sem afetar as tabelas já existentes.
        db.create_all()
        inspector = inspect(db.engine)
        if 'question_bank' in inspector.get_table_names() and 'code' not in {c['name'] for c in inspector.get_columns('question_bank')}:
            with db.engine.begin() as conn:
                if db.engine.dialect.name == 'postgresql':
                    conn.execute(text('ALTER TABLE question_bank ADD COLUMN IF NOT EXISTS code TEXT'))
                elif db.engine.dialect.name == 'sqlite':
                    conn.execute(text('ALTER TABLE question_bank ADD COLUMN code TEXT'))
        inspector = inspect(db.engine)
        if 'question_bank' in inspector.get_table_names():
            qb_cols = {c['name'] for c in inspector.get_columns('question_bank')}
            with db.engine.begin() as conn:
                if 'category' not in qb_cols:
                    if db.engine.dialect.name == 'postgresql':
                        conn.execute(text("ALTER TABLE question_bank ADD COLUMN IF NOT EXISTS category VARCHAR(100) DEFAULT 'Geral'"))
                    elif db.engine.dialect.name == 'sqlite':
                        conn.execute(text("ALTER TABLE question_bank ADD COLUMN category VARCHAR(100) DEFAULT 'Geral'"))
                if 'tags' not in qb_cols:
                    if db.engine.dialect.name == 'postgresql':
                        conn.execute(text('ALTER TABLE question_bank ADD COLUMN IF NOT EXISTS tags TEXT'))
                    elif db.engine.dialect.name == 'sqlite':
                        conn.execute(text('ALTER TABLE question_bank ADD COLUMN tags TEXT'))
                # Corrige valores antigos/legados para que a dificuldade tenha
                # sempre um dos três valores oficiais do Portal.
                conn.execute(text("UPDATE question_bank SET difficulty = 'medio' WHERE difficulty IS NULL OR lower(trim(difficulty)) NOT IN ('facil','medio','dificil')"))
                conn.execute(text("UPDATE question_bank SET difficulty = lower(trim(difficulty)) WHERE difficulty IS NOT NULL"))
                conn.execute(text("UPDATE question_bank SET category = 'Geral' WHERE category IS NULL OR trim(category) = ''"))
        from .services import ensure_admin,seed_initial_content,migrate_legacy_python_subjects
        ensure_admin(); seed_initial_content(); migrate_legacy_python_subjects()
        from .question_seed_service import seed_generated_question_bank
        seed_generated_question_bank()
    return app
