# Segundo cérebro

Um cofre do Obsidian gerado sozinho a partir do que eu ensino para a IA, para que Claude, Codex e Gemini consultem a mesma memória antes de me perguntar qualquer coisa.

Eu uso três IAs e cada uma sabia um pedaço do meu contexto. Aqui tudo que vale para outras sessões (uma regra, uma decisão, um erro que não quero repetir) vira uma nota, e um script liga essas notas aos meus projetos, skills, comandos e rotinas. O resultado é um grafo: a IA abre o assunto e segue as ligações até achar o que precisa, em vez de ler tudo.

Este repositório tem só o gerador. O cofre e a memória são pessoais e não estão aqui.

## Como funciona

A fonte de verdade é a pasta de memória do Claude Code (`~/.claude/projects/<projeto>/memory/`), um arquivo por fato. O script lê essa pasta e mais o que existe em `~/.claude` (projetos, skills, comandos, agentes, scripts, rotinas do launchd) e nas pastas do Codex e do Gemini, e escreve as notas em `~/.claude/obsidian`:

| Pasta | O que tem |
| --- | --- |
| `00_Meta` | ponto de entrada, hubs por assunto (MOC), índice em JSON e relatório de saúde |
| `01_Projects` | uma nota por projeto e as memórias de projeto |
| `02_Areas` | quem é o usuário, regras de trabalho e rotinas agendadas |
| `03_Resources` | IAs, skills, comandos, agentes, scripts e referências |

Cada nota tem uma descrição de uma linha no topo, então a IA decide se vale abrir sem ler o corpo. Toda nota gerada leva `gerado: true` e é recriada a cada rodada; nota escrita à mão não é tocada.

## Uso

Só precisa de Python 3.10 ou mais novo, sem dependências.

```bash
python3 segundo-cerebro.py atualizar          # regenera as notas e o índice
python3 segundo-cerebro.py buscar <termos>    # o que a IA roda antes de pedir contexto
python3 segundo-cerebro.py verificar          # links quebrados e notas órfãs
python3 segundo-cerebro.py regra              # põe a mesma regra nas instruções das três IAs
```

Para registrar algo, as três IAs usam o mesmo comando:

```bash
python3 segundo-cerebro.py anotar --ia <claude|codex|gemini> \
  --tipo <user|feedback|project|reference> --nome <slug> --descricao "<uma linha>" <<'TXT'
<o fato>
TXT
```

O comando recusa texto que pareça credencial, grava a memória e regenera o cofre.

## Arquivos

| Arquivo | Para que serve |
| --- | --- |
| `segundo-cerebro.py` | o gerador, a busca e o comando de anotar |
| `obsidian-claude.sh` | abre o Obsidian no cofre, direto na visualização em gráfico |
| `comandos/obsidian_claude.md` | comando `/obsidian_claude` do Claude Code que chama o script acima |
| `launchd/com.claude.segundo-cerebro.plist.exemplo` | rotina do macOS: roda de hora em hora e quando a memória muda |

## Para adaptar ao seu uso

O script foi feito para a minha máquina, então os caminhos e os nomes estão no topo do arquivo e precisam ser trocados:

1. `MEMORY`: a pasta de memória do seu projeto no Claude Code.
2. `EXTRA_PROJECTS`: projetos que ficam fora de `~/.claude`.
3. `ENTIDADES` e `PROJETO_CHAVES`: as ferramentas e os projetos que viram nós centrais do grafo, com as palavras que ligam uma nota a eles.
4. No plist de exemplo, troque `SEU_USUARIO` pelo seu usuário, salve sem o `.exemplo` em `~/Library/LaunchAgents/` e carregue com `launchctl load`.

Depois abra `~/.claude/obsidian` no Obsidian com "Abrir pasta como cofre".
