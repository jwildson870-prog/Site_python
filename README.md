# Portal Python 🐍

Plataforma educacional feita em **Python + Flask** para aprendizado de Python. A estrutura original foi mantida como base, mas a identidade, textos, conteúdos iniciais e organização foram convertidos para um portal totalmente focado em programação Python.

## O que existe

- Login e cadastro de alunos
- Área separada de professor e aluno
- Trilha com 4 níveis:
  1. **Nível 1 — Fundamentos**
  2. **Nível 2 — Estruturas**
  3. **Nível 3 — Programação**
  4. **Nível 4 — Projetos**
- Conteúdos sobre sintaxe, variáveis, condicionais, laços, listas, dicionários, funções, módulos, POO, JSON/CSV, tratamento de erros e projetos
- Publicação de explicações, arquivos, PDFs, slides, vídeos e links
- Upload de materiais
- Banco SQLite local ou PostgreSQL em produção
- Google OAuth opcional
- Proteção CSRF e controle de acesso no servidor

## Rodar localmente

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Copie `.env.example` para `.env` e execute:

```bash
python app.py
```

Abra `http://127.0.0.1:5000`.

## Professor / administrador

Credenciais padrão:

- E-mail: `professor@portalpython.com`
- Senha: `Python@2026`
- Nome: `Professor Python`

Altere essas variáveis no `.env` antes de publicar o projeto.

## Estrutura

```text
Portal_Python/
├── app.py
├── wsgi.py
├── requirements.txt
├── app/
│   ├── models.py
│   ├── services.py
│   ├── auth/
│   ├── admin/
│   ├── student/
│   ├── templates/
│   └── static/
├── uploads/
└── tests/
```

## Publicação

Para Render:

```bash
pip install -r requirements.txt
gunicorn --bind 0.0.0.0:$PORT app:app
```

Use PostgreSQL e armazenamento persistente para materiais em produção.
