import os
from pathlib import Path
from flask import Flask,render_template,redirect,url_for,request
from sqlalchemy import inspect, text
from flask_login import LoginManager,current_user
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

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        response.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin-allow-popups')
        response.headers.setdefault('Content-Security-Policy', "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline' https:; script-src 'self' 'unsafe-inline' https:; font-src 'self' data: https:; frame-src 'self' https:; connect-src 'self' https:; object-src 'none'; base-uri 'self'; form-action 'self' https://accounts.google.com")
        if request.is_secure or os.getenv('RENDER','').lower() == 'true':
            response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        return response
    from .models import User
    @login.user_loader
    def load_user(uid): return db.session.get(User,int(uid))
    from .auth.routes import auth_bp
    from .student.routes import student_bp
    from .admin.routes import admin_bp
    app.register_blueprint(auth_bp); app.register_blueprint(student_bp); app.register_blueprint(admin_bp)
    @app.get('/')
    def home():
        if current_user.is_authenticated:
            return redirect(url_for('admin.dashboard' if current_user.is_admin else 'student.dashboard'))
        return render_template('index.html')
    @app.errorhandler(403)
    def forbidden(e): return render_template('error.html',message='Acesso negado.'),403
    @app.errorhandler(404)
    def not_found(e): return render_template('error.html',message='Página não encontrada.'),404
    @app.errorhandler(413)
    def too_large(e): return render_template('error.html',message='Arquivo muito grande. Limite: 25 MB.'),413

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
    with app.app_context():
        db.create_all()
        # Migração leve e retrocompatível para instalações existentes: adiciona
        # o prazo das atividades sem apagar nem recriar tabelas do Neon.
        inspector = inspect(db.engine)
        if 'contents' in inspector.get_table_names() and 'preview_manifest' not in {c['name'] for c in inspector.get_columns('contents')}:
            with db.engine.begin() as conn:
                if db.engine.dialect.name == 'postgresql':
                    conn.execute(text('ALTER TABLE contents ADD COLUMN IF NOT EXISTS preview_manifest TEXT'))
                elif db.engine.dialect.name == 'sqlite':
                    conn.execute(text('ALTER TABLE contents ADD COLUMN preview_manifest TEXT'))
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
        # O banco de questões é aditivo e não altera tabelas existentes.
        db.create_all()
        from .services import ensure_admin,seed_initial_content,migrate_legacy_python_subjects
        ensure_admin(); seed_initial_content(); migrate_legacy_python_subjects()
    return app
