from .timeutils import utcnow
import json
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db

class SystemSetting(db.Model):
    __tablename__ = 'system_settings'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255))
    role = db.Column(db.String(20), nullable=False, default='student')
    google_sub = db.Column(db.String(255), unique=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    # Incrementado a cada troca de senha. Guardado também na sessão de login
    # (session['sv']); se não baterem, a sessão é considerada antiga e é
    # encerrada. É assim que uma troca de senha derruba sessões antigas
    # sem precisar de uma tabela de dispositivos.
    session_version = db.Column(db.Integer, nullable=False, default=0)
    # Infraestrutura para verificação de e-mail (item 8). Fica desligada por
    # padrão (ninguém é bloqueado por não confirmar); contas Google já
    # chegam confirmadas, pois o Google já validou o e-mail.
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    def set_password(self, p): self.password_hash = generate_password_hash(p)
    def check_password(self, p): return bool(self.password_hash) and check_password_hash(self.password_hash, p)
    @property
    def is_admin(self): return self.role == 'admin'
    activity_attempts = db.relationship('ActivityAttempt', back_populates='user', cascade='all, delete-orphan')
    favorites = db.relationship('Favorite', backref='user', cascade='all, delete-orphan')
    progress = db.relationship('Progress', backref='user', cascade='all, delete-orphan')
    notifications = db.relationship('Notification', backref='user', cascade='all, delete-orphan')

class Series(db.Model):
    __tablename__ = 'series'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    subjects = db.relationship('Subject', backref='series', cascade='all, delete-orphan')
    contents = db.relationship('Content', backref='series', cascade='all, delete-orphan')
    activities = db.relationship('Activity', backref='series', cascade='all, delete-orphan')
    experiments = db.relationship('Experiment', backref='series', cascade='all, delete-orphan')
    question_bank = db.relationship('QuestionBank', backref='series', cascade='all, delete-orphan')

class Subject(db.Model):
    __tablename__ = 'subjects'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    series_id = db.Column(db.Integer, db.ForeignKey('series.id'), nullable=False)
    contents = db.relationship('Content', backref='subject', cascade='all, delete-orphan')
    activities = db.relationship('Activity', backref='subject', cascade='all, delete-orphan')
    experiments = db.relationship('Experiment', backref='subject', cascade='all, delete-orphan')
    question_bank = db.relationship('QuestionBank', backref='subject', cascade='all, delete-orphan')
    __table_args__ = (db.UniqueConstraint('name', 'series_id', name='uq_subject_series'),)

class Content(db.Model):
    __tablename__ = 'contents'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    kind = db.Column(db.String(30), nullable=False)
    body = db.Column(db.Text)
    external_url = db.Column(db.String(1000))
    file_name = db.Column(db.String(255))
    preview_manifest = db.Column(db.Text)  # JSON com imagens geradas para PPTX
    series_id = db.Column(db.Integer, db.ForeignKey('series.id'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='published', index=True)
    scheduled_at = db.Column(db.DateTime, nullable=True, index=True)
    archived_at = db.Column(db.DateTime, nullable=True, index=True)
    favorites = db.relationship('Favorite', backref='content', cascade='all, delete-orphan')
    progress = db.relationship('Progress', backref='content', cascade='all, delete-orphan')

class Activity(db.Model):
    __tablename__ = 'activities'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    series_id = db.Column(db.Integer, db.ForeignKey('series.id'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False)
    questions_json = db.Column(db.Text, nullable=False, default='[]')
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    archived_at = db.Column(db.DateTime, nullable=True, index=True)
    due_at = db.Column(db.DateTime, nullable=True, index=True)
    difficulty = db.Column(db.String(20), nullable=False, default='medio', index=True)
    max_attempts = db.Column(db.Integer, nullable=False, default=0)  # 0 = ilimitado
    review_enabled = db.Column(db.Boolean, nullable=False, default=True)
    shuffle_questions = db.Column(db.Boolean, nullable=False, default=False)
    shuffle_options = db.Column(db.Boolean, nullable=False, default=False)
    attempts = db.relationship('ActivityAttempt', back_populates='activity', cascade='all, delete-orphan')
    def get_questions(self):
        try: return json.loads(self.questions_json or '[]')
        except (TypeError, ValueError): return []
    def set_questions(self, questions): self.questions_json = json.dumps(questions, ensure_ascii=False)


class ContentHistory(db.Model):
    __tablename__ = 'content_history'
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(20), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=False, index=True)
    action = db.Column(db.String(40), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    snapshot_json = db.Column(db.Text, nullable=False, default='{}')
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    user = db.relationship('User')


class QuestionBank(db.Model):
    __tablename__ = 'question_bank'
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.String(1000), nullable=False)
    options_json = db.Column(db.Text, nullable=False, default='[]')
    correct = db.Column(db.String(500), nullable=False)
    code = db.Column(db.Text, nullable=True)
    difficulty = db.Column(db.String(20), nullable=False, default='medio', index=True)
    category = db.Column(db.String(120), nullable=True, index=True)
    tags = db.Column(db.String(500), nullable=True)
    series_id = db.Column(db.Integer, db.ForeignKey('series.id'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    def get_options(self):
        try: return json.loads(self.options_json or '[]')
        except (TypeError, ValueError): return []
    def set_options(self, options):
        self.options_json = json.dumps(options, ensure_ascii=False)

class ActivityAttempt(db.Model):
    __tablename__ = 'activity_attempts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    activity_id = db.Column(db.Integer, db.ForeignKey('activities.id'), nullable=False)
    answers_json = db.Column(db.Text, nullable=False, default='{}')
    score = db.Column(db.Float, nullable=False, default=0)
    total = db.Column(db.Integer, nullable=False, default=0)
    question_order_json = db.Column(db.Text, nullable=False, default='[]')
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    user = db.relationship('User', back_populates='activity_attempts')
    activity = db.relationship('Activity', back_populates='attempts')
    def get_answers(self):
        try: return json.loads(self.answers_json or '{}')
        except (TypeError, ValueError): return {}

class Experiment(db.Model):
    __tablename__ = 'experiments'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    objective = db.Column(db.Text)
    materials = db.Column(db.Text)
    steps = db.Column(db.Text)
    safety = db.Column(db.Text)
    conclusion = db.Column(db.Text)
    series_id = db.Column(db.Integer, db.ForeignKey('series.id'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

class Favorite(db.Model):
    __tablename__ = 'favorites'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content_id = db.Column(db.Integer, db.ForeignKey('contents.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'content_id', name='uq_favorite'),)

class Progress(db.Model):
    __tablename__ = 'progress'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content_id = db.Column(db.Integer, db.ForeignKey('contents.id'), nullable=False)
    completed_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'content_id', name='uq_progress'),)

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    link = db.Column(db.String(500))
    read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class Alert(db.Model):
    __tablename__ = 'alerts'
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(40), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    activity_id = db.Column(db.Integer, db.ForeignKey('activities.id'), nullable=True, index=True)
    message = db.Column(db.String(500), nullable=False)
    link = db.Column(db.String(500))
    priority = db.Column(db.String(12), nullable=False, default='medium')
    resolved = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    resolved_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User', foreign_keys=[user_id])
    activity = db.relationship('Activity', foreign_keys=[activity_id])


class PasswordResetToken(db.Model):
    """Token de uso único para o fluxo "Esqueci minha senha".

    Guardamos apenas o hash SHA-256 do token, nunca o valor bruto: o valor
    bruto só existe no link enviado por e-mail. Assim, mesmo um vazamento
    do banco não permite redefinir senhas de usuários.
    """
    __tablename__ = 'password_reset_tokens'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship('User')
    @property
    def is_valid(self):
        return self.used_at is None and self.expires_at > utcnow()


class EmailVerificationToken(db.Model):
    """Token de uso único para confirmar o e-mail cadastrado.

    Infraestrutura preparada (item 8): a conta funciona normalmente mesmo
    sem confirmar o e-mail, então isso nunca bloqueia o acesso — apenas
    permite exibir um aviso e, futuramente, exigir a confirmação se o
    Portal Python decidir habilitar isso.
    """
    __tablename__ = 'email_verification_tokens'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship('User')
    @property
    def is_valid(self):
        return self.used_at is None and self.expires_at > utcnow()


class LoginAttempt(db.Model):
    """Registro leve de tentativas malsucedidas, usado só para limitar taxa
    (login, cadastro, recuperação de senha). Guardado no banco (não em
    memória do processo) para funcionar corretamente com vários workers
    do gunicorn. Linhas antigas podem ser removidas periodicamente; a
    tabela é pequena porque só tentativas malsucedidas são gravadas.
    """
    __tablename__ = 'login_attempts'
    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(20), nullable=False, index=True)  # 'login' | 'register' | 'forgot_password'
    ip = db.Column(db.String(64), nullable=True, index=True)
    email = db.Column(db.String(255), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)


class UserSession(db.Model):
    """Uma sessão de login (um dispositivo/navegador). Guardamos só o hash
    do token de sessão — o valor bruto vive apenas no cookie do navegador
    (chave 'st' na sessão do Flask), nunca em texto puro no banco. Isso dá
    suporte à tela "sessões ativas" (item 10): listar, encerrar uma sessão
    específica ou encerrar todas as outras.
    """
    __tablename__ = 'user_sessions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    user_agent = db.Column(db.String(255), nullable=True)
    ip = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_seen_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    revoked_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship('User')
    @property
    def is_active(self):
        return self.revoked_at is None
