from datetime import datetime
import json
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255))
    role = db.Column(db.String(20), nullable=False, default='student')
    google_sub = db.Column(db.String(255), unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
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

class Subject(db.Model):
    __tablename__ = 'subjects'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    series_id = db.Column(db.Integer, db.ForeignKey('series.id'), nullable=False)
    contents = db.relationship('Content', backref='subject', cascade='all, delete-orphan')
    activities = db.relationship('Activity', backref='subject', cascade='all, delete-orphan')
    experiments = db.relationship('Experiment', backref='subject', cascade='all, delete-orphan')
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
    series_id = db.Column(db.Integer, db.ForeignKey('series.id'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    due_at = db.Column(db.DateTime, nullable=True, index=True)
    attempts = db.relationship('ActivityAttempt', back_populates='activity', cascade='all, delete-orphan')
    def get_questions(self):
        try: return json.loads(self.questions_json or '[]')
        except (TypeError, ValueError): return []
    def set_questions(self, questions): self.questions_json = json.dumps(questions, ensure_ascii=False)

class ActivityAttempt(db.Model):
    __tablename__ = 'activity_attempts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    activity_id = db.Column(db.Integer, db.ForeignKey('activities.id'), nullable=False)
    answers_json = db.Column(db.Text, nullable=False, default='{}')
    score = db.Column(db.Float, nullable=False, default=0)
    total = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

class Favorite(db.Model):
    __tablename__ = 'favorites'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content_id = db.Column(db.Integer, db.ForeignKey('contents.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'content_id', name='uq_favorite'),)

class Progress(db.Model):
    __tablename__ = 'progress'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content_id = db.Column(db.Integer, db.ForeignKey('contents.id'), nullable=False)
    completed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'content_id', name='uq_progress'),)

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    link = db.Column(db.String(500))
    read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
