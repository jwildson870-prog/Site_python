# Update 5.1 — Configurações

## Correções realizadas

- Implementado `GET/POST /admin/settings`.
- Persistência das configurações no `PortalSetting`.
- Aplicação imediata do limite de upload no Flask.
- Validação do limite entre 1 e 100 MB.
- Cadastro público respeita `public_registration`.
- Google OAuth respeita `google_oauth_enabled` e continua exigindo as credenciais do ambiente.
- Nome da instituição passa a ser disponibilizado globalmente aos templates.
- Notificações gerais e categorias de atividades, materiais, prazos e avisos respeitam suas configurações.
- Alertas gerais e categorias de inatividade, pendências, baixo desempenho, queda de desempenho e prazos respeitam suas configurações.
- Alertas já existentes de categorias desativadas deixam de aparecer no painel.
- Removida a interface/rota de ranking de alunos, pois ela conflitava com o escopo definido para a fase 5.9.

## Validação realizada

- `tests/test_phase51_static.py`: 5/5
- `tests/test_phase512_static.py`: 10/10
- `tests/test_security_static.py`: 7/7
- Compilação Python (`compileall`): OK

A suíte funcional que depende de Flask/SQLAlchemy não foi executada neste ambiente porque as dependências da aplicação não estão instaladas no ambiente de análise. Os testes foram mantidos/fortalecidos no projeto para execução no ambiente do Portal.
