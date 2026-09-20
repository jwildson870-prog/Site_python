# Validação da fase 5.9 — Engajamento

## Contemplado

- Metas semanais por aluno, com limite de 1 a 20 ações.
- Contagem de materiais concluídos, atividades respondidas e projetos enviados.
- Percentual da meta semanal.
- Conquistas individuais.
- Atividades pendentes.
- Próximos prazos.
- Lembretes de prazo e da meta semanal.
- Respeito às configurações globais de notificações.
- Ranking semanal de alunos por acertos objetivos em atividades.
- Ranking disponível para aluno e professor.
- Pesquisa de alunos no ranking administrativo.
- Ranking reiniciado por semana, sem histórico acumulativo.

## Correções desta revisão

- Ranking foi restaurado e integrado à navegação.
- O teste que afirmava que o ranking deveria inexistir foi removido.
- Foram adicionados testes para pontuação, rota administrativa, pesquisa e navegação.
- Corrigida a interação entre `notification_deadlines` e o lembrete da meta: desativar lembretes de prazo não desativa o lembrete da meta semanal.
- Removidos caches `__pycache__` e arquivos `.pyc` do pacote final.

## Observação de execução

A compilação estática dos arquivos Python foi executada com sucesso. A suíte pytest completa depende das dependências do ambiente do Portal (incluindo Flask/SQLAlchemy) e deve ser executada no ambiente do projeto antes do deploy final.
