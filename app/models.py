from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db

class User(UserMixin, db.Model):
    __tablename__='users'
    id=db.Column(db.Integer,primary_key=True); name=db.Column(db.String(120),nullable=False); email=db.Column(db.String(255),unique=True,nullable=False,index=True); password_hash=db.Column(db.String(255)); role=db.Column(db.String(20),nullable=False,default='student'); google_sub=db.Column(db.String(255),unique=True); created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False)
    def set_password(self,p): self.password_hash=generate_password_hash(p)
    def check_password(self,p): return bool(self.password_hash) and check_password_hash(self.password_hash,p)
    @property
    def is_admin(self): return self.role=='admin'
class Series(db.Model):
    __tablename__='series'
    id=db.Column(db.Integer,primary_key=True); name=db.Column(db.String(80),unique=True,nullable=False); subjects=db.relationship('Subject',backref='series',cascade='all, delete-orphan'); contents=db.relationship('Content',backref='series',cascade='all, delete-orphan')
class Subject(db.Model):
    __tablename__='subjects'
    id=db.Column(db.Integer,primary_key=True); name=db.Column(db.String(120),nullable=False); series_id=db.Column(db.Integer,db.ForeignKey('series.id'),nullable=False); contents=db.relationship('Content',backref='subject',cascade='all, delete-orphan'); __table_args__=(db.UniqueConstraint('name','series_id',name='uq_subject_series'),)
class Content(db.Model):
    __tablename__='contents'
    id=db.Column(db.Integer,primary_key=True); title=db.Column(db.String(200),nullable=False); description=db.Column(db.Text); kind=db.Column(db.String(30),nullable=False); body=db.Column(db.Text); external_url=db.Column(db.String(1000)); file_name=db.Column(db.String(255)); series_id=db.Column(db.Integer,db.ForeignKey('series.id'),nullable=False); subject_id=db.Column(db.Integer,db.ForeignKey('subjects.id'),nullable=False); created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False); updated_at=db.Column(db.DateTime,default=datetime.utcnow,onupdate=datetime.utcnow,nullable=False)


class StudyProgress(db.Model):
    __tablename__ = 'study_progress'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content_id = db.Column(db.Integer, db.ForeignKey('contents.id'), nullable=False)
    completed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    user = db.relationship('User', backref=db.backref('study_progress', cascade='all, delete-orphan'))
    content = db.relationship('Content', backref=db.backref('study_progress', cascade='all, delete-orphan'))
    __table_args__ = (db.UniqueConstraint('user_id', 'content_id', name='uq_study_progress_user_content'),)
