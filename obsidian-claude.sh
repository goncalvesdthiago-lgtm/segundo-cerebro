#!/bin/bash
# Abre o Obsidian no cofre ~/.claude/obsidian com a Visualização em gráfico na tela.
# O layout de referência fica em obsidian/.obsidian/workspace-grafo.json; o Obsidian
# sobrescreve workspace.json ao fechar, então o layout é restaurado com o app fechado.
VAULT="$HOME/.claude/obsidian"
WS="$VAULT/.obsidian/workspace.json"
TPL="$VAULT/.obsidian/workspace-grafo.json"
URI="obsidian://open?vault=obsidian"

running() { pgrep -x Obsidian >/dev/null; }

mostra_grafo() {   # a aba ativa do cofre é o grafo?
  python3 - "$WS" <<'PY'
import json, sys
try:
    w = json.load(open(sys.argv[1]))
    def leaf(n, i):
        if n.get("id") == i: return n
        for c in n.get("children", []):
            r = leaf(c, i)
            if r: return r
    l = leaf(w["main"], w["active"])
    sys.exit(0 if l and l["state"]["type"] == "graph" else 1)
except Exception:
    sys.exit(1)
PY
}

if running && ! mostra_grafo; then
  osascript -e 'tell application "Obsidian" to quit'
  for _ in $(seq 1 20); do running || break; sleep 0.5; done
fi
running || cp "$TPL" "$WS"
open "$URI"
