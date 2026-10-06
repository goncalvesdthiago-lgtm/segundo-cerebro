#!/usr/bin/env python3
"""Segundo cérebro: vault Obsidian em ~/.claude/obsidian, gerado a partir do que o Claude sabe.

Fontes (somente leitura): tudo o que o Codex fez (sessões, workspace, skills em ~/.claude/codex), memória (projects/-Users-thiago-dias/memory), projetos/, skills/,
commands/, agents/, bin/, scripts/, plists do launchd e os projetos de EXTRA_PROJECTS.
Saída: notas .md com wikilinks, hubs MOC, grafo (.obsidian/graph.json) e índice em JSON.

Uso:
  segundo-cerebro.py atualizar   # regenera notas + reindexa (rotina do launchd)
  segundo-cerebro.py reindexar   # só refaz o índice e o relatório de saúde
  segundo-cerebro.py buscar termo [termo ...]
  segundo-cerebro.py anotar --ia <claude|codex|gemini> --tipo <tipo> --nome <slug> --descricao "..." < texto
                                 # caminho único de escrita das três IAs
  segundo-cerebro.py regra       # reaplica o bloco de regra nos arquivos de instruções das IAs
  segundo-cerebro.py verificar   # links quebrados e notas órfãs

Notas geradas levam `gerado: true` no frontmatter e são recriadas a cada execução; notas
escritas à mão (sem essa marca) nunca são alteradas nem apagadas, só indexadas.
"""

import datetime
import json
import plistlib
import re
import sys
import unicodedata
from pathlib import Path

HOME = Path.home()
CLAUDE = HOME / ".claude"
VAULT = CLAUDE / "obsidian"
MEMORY = CLAUDE / "projects" / "-Users-thiago-dias" / "memory"
EXTRA_PROJECTS: dict[str, Path] = {}     # projetos fora de ~/.claude, ex.: {"MeuProjeto": HOME / "MeuProjeto"}
INDEX_DIR = VAULT / "00_Meta" / "_index"
DOCUMENTACOES = CLAUDE / "Documentações"
CODEX_HOME = CLAUDE / "codex"                          # CODEX_HOME do bin/codex.sh
CODEX_SESSIONS = [CODEX_HOME / "sessions", HOME / ".codex" / "sessions"]
CODEX_FOLDER = "03_Resources/Codex"
GEMINI_HOME = HOME / ".gemini"                          # Antigravity CLI (agy)
GEMINI_FOLDER = "03_Resources/Gemini"
IAS = ("claude", "codex", "gemini")                    # as IAs que leem e alimentam o cérebro
# Arquivo de instruções de cada IA: todos carregam o mesmo bloco de regra (ver REGRA_IAS).
INSTRUCOES = {"Claude": CLAUDE / "CLAUDE.md", "Codex": CODEX_HOME / "AGENTS.md", "Gemini": GEMINI_HOME / "GEMINI.md"}

SECRET = re.compile(
    r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{12,}"
    r"|(?i:(?:access_token|token|secret|password|senha|api[_-]?key)\s*[=:]\s*)[^\s\"',;]{16,})")
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.S)

FOLDERS = {
    "user": "02_Areas/Usuario", "feedback": "02_Areas/Regras", "project": "01_Projects/Memoria",
    "reference": "03_Resources/Referencias",
}
MOC_OF = {"user": "MOC_Usuario", "feedback": "MOC_Regras", "project": "MOC_Projetos",
          "reference": "MOC_Referencias"}

# (nome, pasta, palavras-chave, descrição). Notas de entidade ligam tudo que as menciona.
ENTIDADES = [
    ("IA_Claude", "03_Resources/IAs", r"\bclaude\b|anthropic|opus|sonnet|haiku", "Claude Code, o assistente principal que mantém este cérebro."),
    ("IA_Codex", "03_Resources/IAs", r"\bcodex\b|gpt-5", "Codex CLI (OpenAI), usado só quando o usuário pede, via bin/codex.sh."),
    ("IA_Gemini", "03_Resources/IAs", r"gemini|antigravity", "Gemini / Antigravity CLI: tokens lidos de ~/.gemini/antigravity-cli."),
    ("IA_Humboldt", "03_Resources/IAs", r"humboldt", "Pesquisa autônoma em ciclos (/humboldt:autodeepsearch), só quando pedida."),
    ("Sistema_RTK", "03_Resources/Sistema", r"\brtk\b", "Rust Token Killer: proxy que reduz tokens de comandos de dev."),
    ("Sistema_Cloudflare_R2", "03_Resources/Sistema", r"\br2\b|cloudflare", "Mídia (imagens, vídeos, vozes) vive no R2, nunca no Mac."),
    ("Sistema_GitHub", "03_Resources/Sistema", r"github", "Repositórios e backups."),
    ("Sistema_Launchd", "03_Resources/Sistema", r"launchd|launchctl|plist", "Agendador do macOS que roda as rotinas."),
    ("Sistema_Instagram", "03_Resources/Sistema", r"instagram|\breels?\b|carrossel", "Instagram e seus formatos de post."),
    ("Sistema_TikTok", "03_Resources/Sistema", r"tiktok", "TikTok (rascunhos via API)."),
    ("Sistema_Remotion", "03_Resources/Sistema", r"remotion|heygen|seedance|kling", "Ferramentas de vídeo com IA."),
]
PROJETO_CHAVES = {     # pasta do projeto -> palavras que ligam uma nota a ele
    "meu-projeto": r"meu-projeto|outra palavra-chave",
}


def slug_title(text: str) -> str:
    text = text.replace("/", "-")
    text = re.sub(r'[\\:?*"<>|#^\[\]]', "", text).strip().strip(".")
    return re.sub(r"\s+", " ", text)[:90] or "Sem titulo"


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return re.sub(r"[^a-z0-9]+", "", "".join(c for c in text if not unicodedata.combining(c)))


def redact(text: str) -> str:
    return SECRET.sub("[CREDENCIAL-REMOVIDA]", text)


def split_front(text: str) -> tuple[dict, str]:
    """Frontmatter simples (chave: valor, uma linha por chave) e o corpo."""
    m = FRONT.match(text)
    if not m:
        return {}, text
    data = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            data[k.strip()] = v.strip().strip("\"'")
    return data, text[m.end():]


def first_line(text: str, limit: int = 160) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line and not line.startswith(("---", "<!--", "```", "import ", "#!")):
            return line[:limit]
    return ""


def excerpt(path: Path, lines: int = 30) -> str:
    try:
        body = split_front(path.read_text(errors="replace"))[1]
    except OSError:
        return ""
    keep = [l.rstrip() for l in body.splitlines() if l.strip()][:lines]
    return redact("\n".join(keep))


class Cerebro:
    def __init__(self) -> None:
        self.notes: dict[str, dict] = {}       # título -> {path, folder, body(with frontmatter), ...}
        self.alias: dict[str, str] = {}        # nome normalizado -> título
        self.mem: list[dict] = []

    # ---------- coleta ----------
    def add(self, title: str, folder: str, kind: str, body: str, description: str = "",
            tags: list[str] | None = None, source: str = "", aliases: list[str] | None = None) -> str:
        title = slug_title(title)
        base, n = title, 2
        while title in self.notes:
            title, n = f"{base} ({n})", n + 1
        self.notes[title] = {"folder": folder, "type": kind, "body": body, "description": description,
                             "tags": tags or [], "source": source, "aliases": aliases or []}
        for name in [title, *(aliases or [])]:
            self.alias.setdefault(norm(name), title)
        return title

    def collect_memory(self) -> None:
        titles = {}
        index = MEMORY / "MEMORY.md"
        if index.exists():
            for m in re.finditer(r"^- \[([^\]]+)\]\(([^)]+\.md)\)", index.read_text(), re.M):
                titles[m.group(2)] = m.group(1)
        for path in sorted(MEMORY.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            front, body = split_front(path.read_text(errors="replace"))
            raw = FRONT.match(path.read_text(errors="replace"))
            mt = re.search(r"^\s+type:\s*(\w+)", raw.group(1) if raw else "", re.M)
            meta_type = mt.group(1) if mt else ""
            meta_type = meta_type if meta_type in FOLDERS else "reference"
            title = titles.get(path.name) or front.get("name", path.stem).replace("_", " ").replace("-", " ").strip().capitalize()
            names = [path.stem, front.get("name", ""), path.stem.replace("_", "-")]
            final = self.add(title, FOLDERS[meta_type], meta_type, redact(body).strip(),
                             description=front.get("description", ""), tags=["memoria", meta_type],
                             source=str(path), aliases=[n for n in names if n])
            self.mem.append({"title": final, "type": meta_type})

    def collect_projects(self) -> None:
        roots = {p.name: p for p in sorted((CLAUDE / "projetos").iterdir()) if p.is_dir()}
        roots.update({k: v for k, v in EXTRA_PROJECTS.items() if v.is_dir()})
        for name, path in roots.items():
            doc = next((path / f for f in ("HANDOFF.md", "README.md", "ESTRUTURA_PROJETO.md", "briefing.md")
                        if (path / f).exists()), None)
            body = f"Pasta: `{path}`\n"
            if doc:
                body += f"\n## Resumo (de `{doc.name}`)\n\n{excerpt(doc)}\n"
            subs = sorted(p.name for p in path.iterdir() if p.is_dir() and not p.name.startswith((".", "__")))[:20]
            if subs:
                body += "\n## Pastas\n" + "\n".join(f"- `{s}/`" for s in subs) + "\n"
            self.add(f"Projeto_{name}", "01_Projects", "projeto", body,
                     description=first_line(excerpt(doc, 6)) if doc else f"Projeto {name}",
                     tags=["projeto"], source=str(path))

    def collect_documentacoes(self) -> None:
        """Documentações escritas para consulta e operação."""
        if not DOCUMENTACOES.is_dir():
            return
        for f in sorted(DOCUMENTACOES.rglob("*.md")):
            if f.stat().st_size > 500_000:
                continue
            rel = f.relative_to(DOCUMENTACOES)
            text = redact(f.read_text(errors="replace"))
            body = (f"Documento em `{f}`.\n\n"
                    f"## Trecho inicial\n\n{text[:6000]}{'…' if len(text) > 6000 else ''}\n")
            title = "Doc_" + rel.with_suffix("").as_posix().replace("/", "-")
            self.add(title, "03_Resources/Documentacoes", "documentacao", body,
                     description=first_line(text) or rel.name, tags=["documentacao"], source=str(f),
                     aliases=[rel.stem])

    def collect_tools(self) -> None:
        def walk(root: Path, pattern: str, prefix: str, folder: str, kind: str) -> None:
            if not root.is_dir():
                return
            for f in sorted(root.glob(pattern)):
                text = f.read_text(errors="replace")
                front, body = split_front(text)
                name = front.get("name") or (f.parent.name if f.name == "SKILL.md" else f.stem)
                desc = front.get("description") or first_line(body)
                self.add(f"{prefix}_{name}".replace(" ", "_"), folder, kind,
                         f"{desc}\n\nArquivo: `{f}`\n", description=desc[:200], tags=[kind], source=str(f))
        walk(CLAUDE / "skills", "*/SKILL.md", "Skill", "03_Resources/Skills", "skill")
        walk(CLAUDE / "skills", "*/*/SKILL.md", "Skill", "03_Resources/Skills", "skill")
        walk(CLAUDE / "commands", "*.md", "Comando", "03_Resources/Commands", "comando")
        walk(CLAUDE / "agents", "*.md", "Agente", "03_Resources/Agentes", "agente")
        for folder, label in ((CLAUDE / "bin", "Bin"), (CLAUDE / "scripts", "Script")):
            if not folder.is_dir():
                continue
            for f in sorted(folder.iterdir()):
                if f.is_file() and f.suffix in ("", ".sh", ".py"):
                    try:
                        desc = first_line(f.read_text(errors="replace").split("\n", 1)[-1].replace('"""', ""))
                    except OSError:
                        desc = ""
                    self.add(f"{label}_{f.stem}", "03_Resources/Sistema", "script",
                             f"{desc}\n\nArquivo: `{f}`\n", description=desc, tags=["script"], source=str(f))

    def collect_routines(self) -> None:
        seen = set()
        for folder in (CLAUDE / "settings", HOME / "Library" / "LaunchAgents"):
            for f in sorted(folder.glob("*.plist")) if folder.is_dir() else []:
                try:
                    plist = plistlib.loads(f.read_bytes())
                except Exception:
                    continue
                label = plist.get("Label", f.stem)
                if label in seen or not label.startswith(("com.claude", "com.user")):
                    continue
                seen.add(label)
                sched = []
                if "StartInterval" in plist:
                    sched.append(f"a cada {plist['StartInterval'] // 60} min")
                cal = plist.get("StartCalendarInterval")
                for c in ([cal] if isinstance(cal, dict) else cal or []):
                    sched.append(f"{c.get('Weekday', 'todo dia')} {c.get('Hour', '*')}:{str(c.get('Minute', 0)).zfill(2)}")
                if plist.get("WatchPaths"):
                    sched.append("ao mudar " + ", ".join(plist["WatchPaths"]))
                if plist.get("RunAtLoad") or plist.get("KeepAlive"):
                    sched.append("sempre ligado" if plist.get("KeepAlive") else "ao carregar")
                prog = " ".join(plist.get("ProgramArguments", []))
                body = (f"Label: `{label}`\n\nQuando: {'; '.join(sched) or 'sob demanda'}\n\n"
                        f"Comando: `{redact(prog)}`\n\nPlist: `{f}`\n\nStatus: `launchctl list | grep {label}`\n")
                self.add("Rotina_" + label.replace("com.", "").replace(".", "_"), "02_Areas/Rotinas", "rotina",
                         body, description=f"{label}: {'; '.join(sched) or 'sob demanda'}"[:200],
                         tags=["rotina"], source=str(f))

    def collect_codex(self) -> None:
        """Sessões, arquivos de trabalho e skills do Codex (~/.claude/codex)."""
        files = sorted({f for root in CODEX_SESSIONS if root.is_dir() for f in root.rglob("rollout-*.jsonl")})
        # Sessão não vira nota própria (eram centenas de nós vazios no grafo): as que têm pedido
        # entram todas numa nota só, Codex_Sessoes; as vazias (sem pedido) só são contadas.
        sessoes, vazias, projetos = [], 0, set()
        for f in files:
            info = {"prompt": "", "answer": "", "model": "", "tokens": 0, "cwd": "", "when": "", "ms": 0, "touched": []}
            try:
                lines = f.read_text(errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                pl = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                kind = pl.get("type")
                if ev.get("type") == "session_meta":
                    info["cwd"], info["when"] = pl.get("cwd", ""), pl.get("timestamp", "")
                elif ev.get("type") == "turn_context" and pl.get("model"):
                    info["model"] = pl["model"]
                elif kind == "user_message" and not info["prompt"]:
                    info["prompt"] = pl.get("message", "")
                elif kind == "task_complete":
                    info["answer"] = pl.get("last_agent_message") or info["answer"]
                    info["ms"] = int(pl.get("duration_ms") or 0)
                elif kind == "token_count":
                    total = ((pl.get("info") or {}).get("total_token_usage") or {}).get("total_tokens")
                    info["tokens"] = int(total) if total else info["tokens"]
                elif kind == "custom_tool_call" and "*** Begin Patch" in str(pl.get("input", "")):
                    for m in re.finditer(r"\*\*\* (Add|Update|Delete) File: ([^\\\n\"]+)", str(pl["input"])):
                        entry = f"{m.group(2)} ({m.group(1).lower()})"
                        if entry not in info["touched"]:
                            info["touched"].append(entry)
            try:
                stamp = datetime.datetime.fromisoformat(info["when"].replace("Z", "+00:00")).astimezone()
            except ValueError:
                stamp = datetime.datetime.fromtimestamp(f.stat().st_mtime)
            prompt = redact(info["prompt"]).strip()
            if not prompt:
                vazias += 1
                continue
            headline = first_line(prompt, 60).rstrip(".:")
            proj = Path(info["cwd"]).name if info["cwd"] else ""
            body = (f"## {stamp:%d/%m/%Y %H:%M} · {headline}\n\n"
                    f"- Modelo: `{info['model'] or '?'}` · Tokens: {info['tokens']:,} · Duração: {info['ms'] // 1000} s\n"
                    f"- Pasta de trabalho: `{info['cwd']}`\n- Sessão: `{f.name}`\n")
            if f"Projeto_{proj}" in self.notes:
                body += f"- Projeto: [[Projeto_{proj}]]\n"
                projetos.add(proj)
            body += (f"\n**Pedido:** {prompt[:800]}{'…' if len(prompt) > 800 else ''}\n\n"
                     f"**Resposta final:** {redact(info['answer']).strip()[:1500] or '_sem resposta final_'}\n")
            if info["touched"]:
                body += "\n**Arquivos tocados:** " + ", ".join(f"`{t}`" for t in info["touched"][:30]) + "\n"
            sessoes.append((stamp, body))
        if sessoes or vazias:
            sessoes.sort(key=lambda x: x[0], reverse=True)
            resumo = (f"{len(sessoes)} sessões do Codex com pedido, da mais recente para a mais antiga. "
                      f"Outras {vazias} sessões vazias (sem pedido) foram ignoradas.")
            self.add("Codex_Sessoes", CODEX_FOLDER, "codex_sessao",
                     resumo + "\n\n" + "\n".join(b for _, b in sessoes),
                     description=f"Registro das {len(sessoes)} sessões do Codex com pedido ({vazias} vazias ignoradas)"[:200],
                     tags=["codex", "sessao"], source=str(CODEX_SESSIONS[0]))
        work = CODEX_HOME / "workspace"
        for f in sorted(work.rglob("*")) if work.is_dir() else []:
            if f.is_file() and f.suffix in (".md", ".txt", ".log", ".py", ".json") and f.stat().st_size < 400_000:
                rel = f.relative_to(work)
                text = redact(f.read_text(errors="replace"))
                lines = [l for l in text.splitlines() if l.strip()]
                self.add(f"Codex_Arquivo_{rel.as_posix().replace('/', '-')}", CODEX_FOLDER, "codex_arquivo",
                         f"Arquivo em `{f}` (workspace do Codex, {len(lines)} linhas úteis).\n\n```\n"
                         + "\n".join(lines[:40]) + "\n```\n", description=first_line(text) or rel.name,
                         tags=["codex", "arquivo"], source=str(f))
        for f in sorted((CODEX_HOME / "skills").glob("*/*/SKILL.md")):
            front, body = split_front(f.read_text(errors="replace"))
            desc = front.get("description") or first_line(body)
            self.add(f"Codex_Skill_{front.get('name') or f.parent.name}", CODEX_FOLDER, "codex_skill",
                     f"{desc}\n\nArquivo: `{f}`\n", description=desc[:200], tags=["codex", "skill"], source=str(f))

    def collect_gemini(self) -> None:
        """Agentes e skills do Gemini/Antigravity (~/.gemini). Conversas não viram nota, como no Codex."""
        for f in sorted((GEMINI_HOME / "agents").glob("*.md")) if (GEMINI_HOME / "agents").is_dir() else []:
            front, body = split_front(f.read_text(errors="replace"))
            desc = front.get("description") or first_line(body)
            self.add(f"Gemini_Agente_{front.get('name') or f.stem}", GEMINI_FOLDER, "gemini_agente",
                     f"{desc}\n\nArquivo: `{f}`\n", description=desc[:200], tags=["gemini", "agente"], source=str(f))
        for f in sorted((GEMINI_HOME / "skills").glob("*/SKILL.md")):
            front, body = split_front(f.read_text(errors="replace"))
            desc = front.get("description") or first_line(body)
            self.add(f"Gemini_Skill_{front.get('name') or f.parent.name}", GEMINI_FOLDER, "gemini_skill",
                     f"{desc}\n\nArquivo: `{f}`\n", description=desc[:200], tags=["gemini", "skill"], source=str(f))

    def collect_instrucoes(self) -> None:
        """Uma nota por IA apontando para o arquivo de instruções dela (mesma regra nas três)."""
        for ia, f in INSTRUCOES.items():
            if not f.exists():
                continue
            ok = "segundo-cerebro.py anotar" in f.read_text(errors="replace")
            self.add(f"{ia}_Instrucoes", "03_Resources/IAs", "instrucoes",
                     f"Arquivo de instruções do {ia}: `{f}`\n\nRegra do segundo cérebro presente: "
                     f"{'sim' if ok else 'NÃO (rodar `segundo-cerebro.py regra`)'}\n\nVer [[LEIAME_IAs]] e [[IA_{ia}]].\n",
                     description=f"Instruções do {ia} ({'com' if ok else 'sem'} a regra do segundo cérebro)",
                     tags=["instrucoes", ia.lower()], source=str(f))

    def collect_entities(self) -> None:
        for name, folder, _, desc in ENTIDADES:
            self.add(name, folder, "entidade", desc + "\n", description=desc, tags=["entidade"])

    # ---------- ligações ----------
    def resolve(self, target: str) -> str | None:
        return self.alias.get(norm(target))

    def rewrite_links(self, body: str) -> str:
        def sub(m: re.Match) -> str:
            real = self.resolve(m.group(1))
            if not real:
                return m.group(0)
            return f"[[{real}]]" if norm(m.group(1)) == norm(real) else f"[[{real}|{m.group(1)}]]"
        return WIKILINK.sub(sub, body)

    def connections(self) -> dict[str, set[str]]:
        """título -> notas de entidade/projeto que o texto menciona (sem se citar)."""
        patterns = {n: re.compile(p, re.I) for n, _, p, _ in ENTIDADES}
        patterns.update({f"Projeto_{k}": re.compile(v, re.I) for k, v in PROJETO_CHAVES.items()
                         if f"Projeto_{k}" in self.notes})
        out: dict[str, set[str]] = {}
        for title, note in self.notes.items():
            if note["type"] in ("entidade", "projeto", "moc", "meta"):
                continue
            text = f"{title} {note['description']} {note['body']}"
            out[title] = {n for n, rx in patterns.items() if n != title and rx.search(text)}
        return out

    # ---------- MOCs ----------
    def build_mocs(self) -> None:
        def listing(kinds: set[str], folder_filter=None) -> str:
            rows = [(t, n) for t, n in sorted(self.notes.items()) if n["type"] in kinds]
            return "\n".join(f"- [[{t}]] — {n['description'][:110]}" for t, n in rows) or "_vazio_"
        mocs = {
            "MOC_Usuario": ("Quem é o usuário e onde ele trabalha.", {"user"}),
            "MOC_Regras": ("Preferências e regras que o usuário me deu (como trabalhar).", {"feedback"}),
            "MOC_Projetos": ("Projetos em andamento e o que se sabe deles.", {"projeto", "project"}),
            "MOC_Referencias": ("Onde achar coisas: skills de terceiros, setups, ferramentas.", {"reference"}),
            "MOC_Skills": ("Skills instaladas.", {"skill"}),
            "MOC_Commands": ("Slash commands.", {"comando"}),
            "MOC_Agentes": ("Subagentes.", {"agente"}),
            "MOC_Rotinas": ("Rotinas agendadas no launchd.", {"rotina"}),
            "MOC_Sistema": ("Scripts, executáveis e ferramentas de infra.", {"script", "entidade"}),
            "MOC_Codex": ("Tudo o que o Codex fez: sessões, arquivos de trabalho e skills.",
                          {"codex_sessao", "codex_arquivo", "codex_skill"}),
            "MOC_Gemini": ("Estrutura do Gemini/Antigravity: agentes e skills.",
                           {"gemini_agente", "gemini_skill"}),
            "MOC_Documentacoes": ("Documentações criadas para consulta, venda, operação e entrega.",
                                  {"documentacao"}),
            "MOC_IAs": ("As IAs que o usuário usa e como cada uma entra no fluxo.", set()),
        }
        for name, (desc, kinds) in mocs.items():
            if name == "MOC_IAs":
                body = "\n".join(f"- [[{n}]] — {d}" for n, f, _, d in ENTIDADES if f.endswith("IAs")) + "\n"
            else:
                body = listing(kinds) + "\n"
            related = [m for m in mocs if m != name][:4]
            self.add(name, "00_Meta/MOCs", "moc",
                     f"Hub de **{desc}**\n\n{body}\nVer também: " + ", ".join(f"[[{r}]]" for r in related) + "\n",
                     description=desc, tags=["moc", "hub"])
        self.moc_names = list(mocs)

    def build_home(self) -> None:
        count = {}
        for n in self.notes.values():
            count[n["type"]] = count.get(n["type"], 0) + 1
        stats = ", ".join(f"{k}: {v}" for k, v in sorted(count.items()))
        body = ("# Segundo cérebro\n\nConhecimento do Claude sobre o Thiago, seus projetos, regras e ferramentas, "
                "em grafo. Feito para **qualquer IA consultar**: leia [[LEIAME_IAs]] primeiro.\n\n## Hubs\n"
                + "\n".join(f"- [[{m}]]" for m in self.moc_names)
                + f"\n\n## Diário\n- [[Diario]] (notas de tarefas, escritas à mão)\n\n## Estado\n- Notas: {len(self.notes) + 2} ({stats})\n"
                f"- Atualizado em {datetime.datetime.now():%d/%m/%Y %H:%M}\n- Saúde do grafo: [[Saude_do_Cerebro]]\n")
        self.add("Inicio", "00_Meta", "meta", body, description="Ponto de entrada do vault.", tags=["meta"])
        guide = f"""Este vault é a memória de longo prazo compartilhada entre as IAs do Thiago: Claude, Codex e Gemini (Antigravity).
As três seguem **a mesma regra** para consultar e para registrar. Ninguém organiza o cofre na mão: quem organiza é o gerador.

## 1. Antes da tarefa: consultar
- `python3 ~/.claude/scripts/segundo-cerebro.py buscar <termos>` antes de pedir contexto ao usuário.
- Ou comece por [[Inicio]] e pelos hubs (`00_Meta/MOCs/MOC_*.md`). Cada nota tem `description` no frontmatter:
  dá para decidir se vale abrir sem ler o corpo.
- Índice para máquina: `00_Meta/_index/index.json` (notas, tags, links) e `00_Meta/_index/grafo.json` (nós e arestas).

## 2. Depois da tarefa: registrar (um caminho só)
Aprendeu algo que vale para outras sessões? Registre com o mesmo comando, qualquer que seja a IA:

```
python3 ~/.claude/scripts/segundo-cerebro.py anotar --ia <claude|codex|gemini> \\
  --tipo <user|feedback|project|reference> --nome <slug-curto> --descricao "<uma linha>" <<'TXT'
<o fato; em feedback e project, acrescente as linhas **Why:** e **How to apply:**>
TXT
```

- `user`: quem é o usuário · `feedback`: como ele quer que se trabalhe · `project`: trabalho em andamento,
  metas e decisões · `reference`: onde achar algo (URL, painel, ferramenta).
- Um fato por nota. Antes de criar, busque: se já existe, use o mesmo `--nome` com `--substituir`.
- Ligue a outras notas com wikilinks (colchetes duplos) usando o `name` delas.
- O comando grava na memória (`~/.claude/projects/-Users-thiago-dias/memory/`, a fonte de verdade),
  atualiza o índice e regenera o cofre. O Claude pode gravar o arquivo de memória direto, no mesmo formato.

## 3. O que nenhuma IA faz
- Criar, editar ou apagar arquivo dentro do cofre: tudo aqui é gerado, e edição some na próxima rodada.
- Gravar senha, token, chave de API ou dado de cliente (o comando recusa o que parecer credencial).
- Registrar o que só vale para a tarefa atual, ou o que o código e o git já guardam.
- Responder ao usuário em outro idioma que não o português.

## Onde está o quê
- `01_Projects/` projetos e memórias de projeto · `02_Areas/` usuário, regras e rotinas ·
  `03_Resources/` IAs, skills, comandos, agentes, scripts, referências · `00_Meta/` hubs e índice.
- A estrutura de cada IA entra no mesmo molde: instruções, skills e agentes. Sessões e conversas não viram nota.
- Atualização: `python3 ~/.claude/scripts/segundo-cerebro.py atualizar` (o launchd já faz de hora em hora,
  ver [[Rotina_claude_segundo-cerebro]]). `... regra` reaplica este bloco de regra nos arquivos de instruções das três IAs.
"""
        self.add("LEIAME_IAs", "00_Meta", "meta", guide, description="Guia para qualquer IA consultar este vault.",
                 tags=["meta"])

    # ---------- escrita ----------
    def render(self, title: str, note: dict, links: set[str]) -> str:
        body = self.rewrite_links(note["body"])
        if links:
            body += "\n\n## Conexões\n" + " · ".join(f"[[{l}]]" for l in sorted(links)) + "\n"
        moc = MOC_OF.get(note["type"])
        if moc:
            body += ("" if links else "\n\n") + f"\nHub: [[{moc}]]\n"
        fm = ["---", f"type: {note['type']}", f"tags: [{', '.join(note['tags'])}]",
              f"description: {json.dumps(note['description'][:200], ensure_ascii=False)}"]
        if note["aliases"]:
            fm.append("aliases: [" + ", ".join(json.dumps(a, ensure_ascii=False) for a in note["aliases"]) + "]")
        if note["source"]:
            fm.append(f"source: {json.dumps(note['source'], ensure_ascii=False)}")
        fm += ["gerado: true", "---", ""]
        head = "" if body.lstrip().startswith("#") else f"# {title}\n\n"
        return "\n".join(fm) + head + body.strip() + "\n"

    def write_all(self) -> tuple[int, int]:
        conns = self.connections()
        mentioned: dict[str, set[str]] = {}
        for t, ls in conns.items():
            for l in ls:
                mentioned.setdefault(l, set()).add(t)
        written, produced = 0, set()
        for title, note in self.notes.items():
            extra = set(conns.get(title, set()))
            if note["type"] in ("entidade", "projeto"):
                extra |= mentioned.get(title, set())
            if title == "IA_Codex":
                extra.add("MOC_Codex")
            if title == "IA_Gemini":
                extra.add("MOC_Gemini")
            path = VAULT / note["folder"] / f"{title}.md"
            produced.add(path.resolve())
            text = self.render(title, note, extra)
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists() or path.read_text() != text:
                path.write_text(text)
                written += 1
        removed = 0
        for path in VAULT.rglob("*.md"):
            if ".obsidian" in path.parts or path.resolve() in produced or path.name == "Saude_do_Cerebro.md":
                continue
            if split_front(path.read_text(errors="replace"))[0].get("gerado") == "true":
                path.unlink()
                removed += 1
        return written, removed


def default_groups() -> list[dict]:
    groups = [("00_Meta/MOCs", 16766720), ("01_Projects", 15548997), ("02_Areas/Usuario", 3447003),
              ("02_Areas/Regras", 15418782), ("02_Areas/Rotinas", 2067276), ("03_Resources/IAs", 10181046), ("03_Resources/Codex", 1090431),
              ("03_Resources/Skills", 1752220), ("03_Resources/Commands", 10181046),
              ("03_Resources/Agentes", 3447003), ("03_Resources/Referencias", 3066993),
              ("03_Resources/Sistema", 9807270)]
    return [{"query": f'path:"{p}"', "color": {"a": 1, "rgb": c}} for p, c in groups]


def write_config() -> None:
    cfg = VAULT / ".obsidian"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "app.json").write_text(json.dumps({"alwaysUpdateLinks": True}, indent=2))
    graph = cfg / "graph.json"
    if graph.exists():                              # respeita ajustes; só acrescenta grupos que faltam
        try:
            data = json.loads(graph.read_text())
            have = {g["query"] for g in data.get("colorGroups", [])}
            new = [g for g in default_groups() if g["query"] not in have]
            if new:
                data.setdefault("colorGroups", []).extend(new)
                graph.write_text(json.dumps(data, indent=2))
        except ValueError:
            pass
        return
    graph.write_text(json.dumps({
        "showOrphans": True, "showTags": False, "showAttachments": False, "hideUnresolved": False,
        "showArrow": False, "nodeSizeMultiplier": 1.2, "lineSizeMultiplier": 1, "textFadeMultiplier": 0,
        "centerStrength": 0.5, "repelStrength": 10, "linkStrength": 1, "linkDistance": 250,
        "colorGroups": default_groups()}, indent=2))


# ---------- índice ----------
def reindex() -> dict:
    notes, by_alias = {}, {}
    for path in sorted(VAULT.rglob("*.md")):
        if ".obsidian" in path.parts:
            continue
        text = path.read_text(errors="replace")
        front, body = split_front(text)
        aliases = re.findall(r'"([^"]+)"', front.get("aliases", "")) or []
        notes[path.stem] = {"path": str(path.relative_to(VAULT)), "type": front.get("type", ""),
                            "description": front.get("description", ""), "tags": re.findall(r"[\w-]+", front.get("tags", "")),
                            "aliases": aliases, "generated": front.get("gerado") == "true",
                            "links": sorted({m.group(1).strip() for m in WIKILINK.finditer(body)}),
                            "mtime": int(path.stat().st_mtime), "size": len(body)}
        for name in [path.stem, *aliases]:
            by_alias.setdefault(norm(name), path.stem)
    broken, back = [], {t: [] for t in notes}
    for title, n in notes.items():
        resolved = []
        for link in n["links"]:
            real = by_alias.get(norm(link))
            if real:
                resolved.append(real)
                if real != title:
                    back[real].append(title)
            else:
                broken.append((title, link))
        n["links"] = sorted(set(resolved))
    for t, b in back.items():
        notes[t]["backlinks"] = sorted(set(b))
    orphans = sorted(t for t, n in notes.items() if not n["links"] and not n["backlinks"])
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    (INDEX_DIR / "index.json").write_text(json.dumps({"updated": stamp, "notes": notes}, ensure_ascii=False, indent=1))
    (INDEX_DIR / "grafo.json").write_text(json.dumps({
        "updated": stamp, "nodes": [{"id": t, "type": n["type"], "path": n["path"]} for t, n in notes.items()],
        "edges": [{"from": t, "to": l} for t, n in notes.items() for l in n["links"]]}, ensure_ascii=False))
    edges = sum(len(n["links"]) for n in notes.values())
    report = (f"---\ntype: meta\ntags: [meta]\ndescription: \"Relatório de saúde do grafo.\"\ngerado: true\n---\n"
              f"# Saúde do cérebro\n\nAtualizado em {stamp}.\n\n- Notas: {len(notes)}\n- Ligações: {edges}\n"
              f"- Links quebrados: {len(broken)}\n- Notas órfãs: {len(orphans)}\n\n## Links quebrados\n"
              + ("\n".join(f"- {a} → `{b}`" for a, b in broken[:80]) or "_nenhum_")
              + "\n\n## Órfãs\n" + ("\n".join(f"- {o}" for o in orphans[:80]) or "_nenhuma_") + "\n")
    health = VAULT / "00_Meta" / "Saude_do_Cerebro.md"
    health.write_text(report)
    return {"notes": len(notes), "edges": edges, "broken": broken, "orphans": orphans}


def search(terms: list[str]) -> None:
    index = json.loads((INDEX_DIR / "index.json").read_text())["notes"]
    words = [norm(t) for t in terms if norm(t)]
    scored = []
    for title, n in index.items():
        text = (VAULT / n["path"]).read_text(errors="replace")
        hay = norm(text)
        head = norm(f"{title} {n['description']} {' '.join(n['aliases'])}")
        score = sum(hay.count(w) + 8 * head.count(w) for w in words)
        if score and all(w in hay or w in head for w in words):
            scored.append((score, title, n, text))
    for score, title, n, text in sorted(scored, key=lambda s: -s[0])[:10]:
        print(f"[{score:>3}] {title}  ({n['path']})")
        if n["description"]:
            print(f"      {n['description'][:140]}")
    if not scored:
        print("nada encontrado")


def update() -> None:
    VAULT.mkdir(parents=True, exist_ok=True)
    write_config()
    c = Cerebro()
    c.collect_memory()
    c.collect_projects()
    c.collect_documentacoes()
    c.collect_tools()
    c.collect_routines()
    c.collect_codex()
    c.collect_gemini()
    c.collect_instrucoes()
    c.collect_entities()
    c.build_mocs()
    c.build_home()
    written, removed = c.write_all()
    stats = reindex()
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} notas={stats['notes']} ligações={stats['edges']} "
          f"gravadas={written} removidas={removed} quebrados={len(stats['broken'])} órfãs={len(stats['orphans'])}")


REGRA_INI, REGRA_FIM = "<!-- segundo-cerebro:regra -->", "<!-- /segundo-cerebro:regra -->"
REGRA_IAS = f"""{REGRA_INI}
## Segundo cérebro (regra única para Claude, Codex e Gemini)

Base de conhecimento em grafo (Obsidian) em `~/.claude/obsidian`. Guia completo: `~/.claude/obsidian/00_Meta/LEIAME_IAs.md`.

1. **Antes de pedir contexto ao usuário**, consulte: `python3 ~/.claude/scripts/segundo-cerebro.py buscar <termos>`.
2. **Para registrar** algo que vale para outras sessões (regra do usuário, decisão, erro a não repetir, referência), use sempre:
   `python3 ~/.claude/scripts/segundo-cerebro.py anotar --ia <claude|codex|gemini> --tipo <user|feedback|project|reference> --nome <slug> --descricao "<uma linha>"` com o texto no stdin. Um fato por nota; se já existe, mesmo `--nome` com `--substituir`.
3. **Nunca** crie, edite ou apague arquivo dentro do cofre (ele é gerado), nunca grave credenciais nem dado de cliente, e não registre o que só vale para a tarefa atual.
{REGRA_FIM}
"""


def aplicar_regra() -> None:
    """Põe o mesmo bloco de regra no arquivo de instruções de cada IA (cria o arquivo se não existir)."""
    bloco = re.compile(re.escape(REGRA_INI) + r".*?" + re.escape(REGRA_FIM) + r"\n?", re.S)
    alvos = list(INSTRUCOES.items())
    if (HOME / ".codex").is_dir():                     # Codex chamado sem o bin/codex.sh usa o home padrão
        alvos.append(("Codex (home padrão)", HOME / ".codex" / "AGENTS.md"))
    for ia, f in alvos:
        texto = f.read_text() if f.exists() else ""
        novo = bloco.sub(REGRA_IAS, texto) if bloco.search(texto) else (texto.rstrip() + "\n\n" + REGRA_IAS).lstrip()
        if novo != texto:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(novo)
        print(f"{ia}: {f} ({'atualizado' if novo != texto else 'já estava em dia'})")


def anotar(argv: list[str]) -> int:
    """Caminho único de escrita das três IAs: grava uma memória no formato padrão e regenera o cofre."""
    import argparse
    ap = argparse.ArgumentParser(prog="segundo-cerebro.py anotar")
    ap.add_argument("--ia", required=True, choices=IAS)
    ap.add_argument("--tipo", required=True, choices=sorted(FOLDERS))
    ap.add_argument("--nome", required=True, help="slug curto, ex.: deploy-so-com-script")
    ap.add_argument("--descricao", required=True, help="resumo de uma linha")
    ap.add_argument("--titulo", help="título no índice (padrão: derivado do nome)")
    ap.add_argument("--texto", help="corpo da nota (padrão: stdin)")
    ap.add_argument("--substituir", action="store_true", help="sobrescreve a nota de mesmo nome")
    a = ap.parse_args(argv)
    slug = re.sub(r"[^a-z0-9]+", "-", a.nome.lower()).strip("-")
    corpo = (a.texto if a.texto is not None else sys.stdin.read()).strip()
    desc = " ".join(a.descricao.split())
    if not slug or not corpo or not desc:
        print("erro: nome, descrição e texto são obrigatórios", file=sys.stderr)
        return 2
    if SECRET.search(corpo) or SECRET.search(desc):
        print("erro: o texto parece conter credencial; nada foi gravado", file=sys.stderr)
        return 2
    arq = MEMORY / f"{a.tipo}_{slug.replace('-', '_')}.md"
    if arq.exists() and not a.substituir:
        print(f"erro: {arq.name} já existe; leia a nota e use --substituir para atualizar", file=sys.stderr)
        return 2
    arq.write_text(f"---\nname: {slug}\ndescription: {json.dumps(desc, ensure_ascii=False)}\nmetadata:\n"
                   f"  type: {a.tipo}\n  origem: {a.ia}\n  data: {datetime.date.today()}\n---\n\n{corpo}\n")
    index = MEMORY / "MEMORY.md"
    linhas = index.read_text() if index.exists() else ""
    if f"({arq.name})" not in linhas:
        titulo = a.titulo or slug.replace("-", " ").capitalize()
        index.write_text(linhas.rstrip("\n") + f"\n- [{titulo}]({arq.name}) — {desc[:150]}\n")
    print(f"gravado: {arq}")
    update()
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "atualizar"
    if cmd == "atualizar":
        update()
    elif cmd == "reindexar":
        s = reindex()
        print(f"notas={s['notes']} ligações={s['edges']} quebrados={len(s['broken'])} órfãs={len(s['orphans'])}")
    elif cmd == "buscar":
        search(sys.argv[2:])
    elif cmd == "anotar":
        return anotar(sys.argv[2:])
    elif cmd == "regra":
        aplicar_regra()
    elif cmd == "verificar":
        s = reindex()
        for a, b in s["broken"]:
            print(f"quebrado: {a} -> {b}")
        for o in s["orphans"]:
            print(f"órfã: {o}")
        print(f"{len(s['broken'])} quebrados, {len(s['orphans'])} órfãs")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
