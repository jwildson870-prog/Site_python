import os
from pathlib import Path
from flask import Flask,render_template
from flask_login import LoginManager
from flask_wtf import CSRFProtect
from .extensions import db

def create_app(test_config=None):
    app=Flask(__name__,instance_relative_config=True); Path(app.instance_path).mkdir(parents=True,exist_ok=True); Path(app.root_path).parent.joinpath('uploads').mkdir(exist_ok=True)
    dburl=os.getenv('DATABASE_URL','sqlite:///portal_python.db')
    if dburl.startswith('postgres://'): dburl='postgresql+psycopg2://'+dburl[11:]
    elif dburl.startswith('postgresql://'): dburl='postgresql+psycopg2://'+dburl[13:]
    app.config.update(SECRET_KEY=os.getenv('SECRET_KEY','dev-change-me'),SQLALCHEMY_DATABASE_URI=dburl,SQLALCHEMY_TRACK_MODIFICATIONS=False,UPLOAD_FOLDER=str(Path(app.root_path).parent/'uploads'),MAX_CONTENT_LENGTH=25*1024*1024,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE','false').lower()=='true')
    if test_config: app.config.update(test_config)
    db.init_app(app); login=LoginManager(app); login.login_view='auth.login'; CSRFProtect(app)
    from .models import User
    @login.user_loader
    def load_user(uid): return db.session.get(User,int(uid))
    from .auth.routes import auth_bp
    from .student.routes import student_bp
    from .admin.routes import admin_bp
    app.register_blueprint(auth_bp); app.register_blueprint(student_bp); app.register_blueprint(admin_bp)
    @app.get('/')
    def home(): return render_template('index.html')
    @app.errorhandler(403)
    def forbidden(e): return render_template('error.html',message='Acesso negado.'),403
    @app.errorhandler(404)
    def not_found(e): return render_template('error.html',message='Página não encontrada.'),404
    @app.errorhandler(413)
    def too_large(e): return render_template('error.html',message='Arquivo muito grande. Limite: 25 MB.'),413
    @app.cli.command('create-admin')
    def create_admin():
        from .services import ensure_admin; u,_=ensure_admin(); print('Administrador configurado.' if u else 'Defina ADMIN_EMAIL e ADMIN_PASSWORD.')
    @app.cli.command('seed')
    def seed():
        from .services import seed_initial_content; seed_initial_content(); print('Conteúdo inicial confirmado.')
    with app.app_context():
        db.create_all()
        from .services import ensure_admin,seed_initial_content
        ensure_admin(); seed_initial_content()
    return app
