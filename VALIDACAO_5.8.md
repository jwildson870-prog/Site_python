# Validação final — Fase 5.8

Esta versão recebeu uma última camada de robustez antes do envio.

## Melhorias aplicadas

- Reforço dos testes da fase 5.8 com cenários de integração por rota.
- Teste real da proteção contra alteração incompatível de série/matéria.
- Teste de alteração compatível de trilha.
- Teste de bloqueio de etapa por pré-requisito na tela do aluno.
- Teste de conteúdo arquivado não aparecer como etapa visível.
- Teste direto da regra de ordenação e pré-requisito.
- Teste das regras `access` e `complete`.
- Teste adicional para projetos: uma nova entrega não permanece concluída só porque uma entrega anterior já foi revisada.
- Simplificação do cálculo do desbloqueio da trilha para usar uma única fonte de verdade.
- Remoção do parâmetro de fixture inexistente no teste da 5.8.

## Verificações executadas

- Compilação Python (`compileall`): OK.
- Parse AST dos arquivos principais da 5.8: OK.

## Observação sobre pytest

A suíte foi reforçada, mas a execução de `pytest` neste ambiente de empacotamento não foi possível porque as dependências do projeto (incluindo Flask) não estão disponíveis e o ambiente não possui acesso à internet para instalá-las.

No ambiente do Portal, executar:

```bash
python -m pytest -q tests/test_phase58.py
python -m pytest -q
```

O primeiro valida especificamente a 5.8; o segundo verifica regressões nas fases anteriores.
