# decomp-mcp-server

> **English** | [Español](#español)

---

## English

An MCP (Model Context Protocol) server that lets Claude Code automatically work on [decomp.me](https://decomp.me) decompilation scratches — reading code, compiling, analyzing assembly diffs, and iterating until it reaches a 100% match.

### Why a browser extension?

The natural approach would be to call the decomp.me REST API directly. However, the API is behind Cloudflare, which blocks automated requests regardless of cookies or user-agent spoofing. Rather than fighting that battle, this project takes a different route: **a Firefox extension that controls the browser from the inside**.

Claude talks to a local relay server (`bridge_server.py`) over WebSocket. The relay forwards commands to the Firefox extension, which runs inside the browser with a real authenticated session and manipulates the decomp.me page directly via DOM. No Cloudflare, no API tokens needed.

> **Note:** The extension currently only works in **Firefox** (Manifest V2). Chrome support would require a Manifest V3 port.

### Architecture

```
Claude Code (MCP client)
    │  stdio (Model Context Protocol)
    ▼
server.py  ──  MCP server exposing tools to Claude
    │  WebSocket  ws://127.0.0.1:9400
    ▼
bridge_server.py  ──  local relay daemon (run once, keep running)
    │  WebSocket
    ▼
Firefox extension (background.js)
    │  browser.tabs.sendMessage
    ▼
content.js  ──  DOM manipulation on the open decomp.me scratch page
    │
    ▼
decomp.me  ──  CodeMirror editor, compiler, diff view
```

### Features

- **Read scratch** — source code, context headers, compiler settings, current score
- **Write & compile** — set source/context in the CodeMirror editor, click Compile, wait for the diff
- **Compiler options** — change preset, compiler version, and flags from Claude
- **Parallel agent coordination** — file-locked claim system so multiple Claude instances don't duplicate work
- **Patterns database** — SQLite store of known ASM→C translations, searchable by platform/compiler/tags

### Installation

**Prerequisites:** Python 3.10+, [uv](https://github.com/astral-sh/uv) (recommended), Firefox

```bash
git clone https://github.com/JesusArcasCarrera/decomp-mcp-server
cd decomp-mcp-server

# Create virtual environment and install
uv venv
uv pip install -e .
```

### Setup

#### 1. Install the Firefox extension

1. Open Firefox → `about:debugging` → **This Firefox** → **Load Temporary Add-on**
2. Select `extension/manifest.json`

The extension shows a small dot in the bottom-right corner of decomp.me scratch pages:
- **Grey** = bridge not connected
- **Green** = bridge connected and ready

#### 2. Start the bridge relay

Keep this running in a terminal while you work:

```bash
uv run python bridge_server.py
```

#### 3. Configure Claude Code (or Claude Desktop)

Add the MCP server to your Claude config file.

**Claude Code** (`~/.claude/settings.json`):
```json
{
  "mcpServers": {
    "decomp": {
      "command": "/path/to/your/.venv/bin/python",
      "args": ["-m", "decomp_mcp.server"]
    }
  }
}
```

**Claude Desktop — macOS** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "decomp": {
      "command": "/path/to/your/.venv/bin/python",
      "args": ["-m", "decomp_mcp.server"]
    }
  }
}
```

To find the path to your Python interpreter:
```bash
cd decomp-mcp-server
source .venv/bin/activate
which python
```

#### 4. Open a scratch page in Firefox

Navigate to any `https://decomp.me/scratch/SLUG` page. The dot turns green when the bridge is ready.

### Available MCP Tools

#### Browser Bridge Tools

| Tool | Description |
|------|-------------|
| `bridge_get_scratch` | Read the current scratch: source, context, compiler settings, score |
| `bridge_set_source` | Write C source code into the CodeMirror editor |
| `bridge_set_context` | Write context headers (typedefs, structs) into the editor |
| `bridge_compile` | Click Compile and wait for the diff and score |
| `bridge_get_diff` | Read the current assembly diff (target vs. current) |
| `bridge_get_compiler_opts` | Read current compiler preset, version, and flags |
| `bridge_set_compiler_opts` | Change compiler preset, version, and/or flags |

#### Coordination Tools (parallel agents)

| Tool | Description |
|------|-------------|
| `decomp_claim_function` | Reserve a function so other agents skip it |
| `decomp_release_function` | Release a claimed function |
| `decomp_list_claims` | Show all active claims |
| `decomp_complete_function` | Mark a function as done (persists across sessions) |
| `decomp_list_completed` | List all completed functions with scores |

#### Patterns Database

| Tool | Description |
|------|-------------|
| `decomp_search_patterns` | Search stored ASM→C patterns by platform, compiler, tags, or assembly fragment |
| `decomp_save_pattern` | Save a successful pattern for future reference |

### Typical Workflow

```
1.  Open a scratch page in Firefox
2.  Start bridge_server.py
3.  Ask Claude: "Decompile the open scratch to 100%"
4.  Claude:
      → decomp_claim_function("fn_80393C14")
      → bridge_get_scratch()          — reads source + target asm
      → decomp_search_patterns(...)   — checks known patterns
      → bridge_set_source(new_code)
      → bridge_compile()              — get diff + score
      → [iterate until 100%]
      → decomp_complete_function(...)
      → decomp_save_pattern(...)      — stores the solution
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DECOMP_BRIDGE_URL` | `ws://127.0.0.1:9400` | Address of the bridge relay server |
| `DECOMP_CLAIMS_FILE` | `/tmp/decomp_claims.json` | Active agent claims — ephemeral, fine in /tmp |
| `DECOMP_COMPLETED_FILE` | `~/.local/share/decomp-mcp/completed.json` | Persistent record of completed functions (survives reboots) |
| `DECOMP_CLAIM_TIMEOUT` | `3600` | Seconds before a stale claim is auto-released |
| `DECOMP_PATTERNS_DB` | `patterns/patterns.db` | Path to the SQLite patterns database |

### Project Structure

```
decomp-mcp-server/
├── src/decomp_mcp/
│   ├── server.py          # MCP server — exposes tools to Claude
│   └── bridge.py          # WebSocket client to bridge_server
├── bridge_server.py       # Local relay daemon
├── extension/
│   ├── manifest.json      # Firefox extension manifest (MV2)
│   ├── background.js      # Persistent WS connection + tab tracking
│   └── content.js         # DOM manipulation on decomp.me pages
├── patterns/
│   ├── db.py              # SQLite interface for patterns
│   └── schema.sql         # Database schema
├── docs/                  # Architecture and platform notes
└── pyproject.toml
```

### Status

- [x] MCP server installed and working with Claude Code
- [x] Firefox extension + bridge relay operational
- [x] Parallel agent coordination (claim/release/complete)
- [x] Patterns database (SQLite)
- [ ] Automatic scratch watcher (feed polling)
- [ ] Chrome / Manifest V3 support

### Resources

- [decomp.me](https://decomp.me) — collaborative game decompilation platform
- [Model Context Protocol](https://modelcontextprotocol.io) — MCP documentation
- [decomp.me source](https://github.com/decompme/decomp.me)

### License

GPL v3 — see [LICENSE](LICENSE)

---

## Español

Servidor MCP que permite a Claude Code trabajar automáticamente en scratches de [decomp.me](https://decomp.me) — leyendo código, compilando, analizando diffs de ensamblador e iterando hasta alcanzar el 100% de match.

### ¿Por qué una extensión de navegador?

La forma obvia sería llamar directamente a la API REST de decomp.me. Sin embargo, la API está protegida por Cloudflare, que bloquea las peticiones automatizadas independientemente de las cookies o el user-agent. En lugar de pelear con eso, este proyecto toma otro camino: **una extensión de Firefox que controla el navegador desde dentro**.

Claude habla con un servidor relay local (`bridge_server.py`) via WebSocket. El relay reenvía los comandos a la extensión de Firefox, que corre dentro del navegador con una sesión autenticada real y manipula la página de decomp.me directamente vía DOM. Sin Cloudflare, sin tokens de API.

> **Nota:** La extensión actualmente solo funciona en **Firefox** (Manifest V2). El soporte para Chrome requeriría portarla a Manifest V3.

### Arquitectura

```
Claude Code (cliente MCP)
    │  stdio (Model Context Protocol)
    ▼
server.py  ──  servidor MCP que expone herramientas a Claude
    │  WebSocket  ws://127.0.0.1:9400
    ▼
bridge_server.py  ──  relay local (ejecutar una vez, dejar corriendo)
    │  WebSocket
    ▼
Extensión Firefox (background.js)
    │  browser.tabs.sendMessage
    ▼
content.js  ──  manipulación del DOM en la página del scratch abierto
    │
    ▼
decomp.me  ──  editor CodeMirror, compilador, vista de diff
```

### Instalación

**Requisitos:** Python 3.10+, [uv](https://github.com/astral-sh/uv), Firefox

```bash
git clone https://github.com/JesusArcasCarrera/decomp-mcp-server
cd decomp-mcp-server

uv venv
uv pip install -e .
```

### Configuración

#### 1. Instalar la extensión en Firefox

1. Abrir Firefox → `about:debugging` → **Este Firefox** → **Cargar complemento temporal**
2. Seleccionar `extension/manifest.json`

La extensión muestra un punto en la esquina inferior derecha de los scratches de decomp.me:
- **Gris** = bridge no conectado
- **Verde** = bridge conectado y listo

#### 2. Arrancar el relay

Dejar corriendo en una terminal mientras se trabaja:

```bash
uv run python bridge_server.py
```

#### 3. Configurar Claude Code

Editar `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "decomp": {
      "command": "/ruta/a/tu/.venv/bin/python",
      "args": ["-m", "decomp_mcp.server"]
    }
  }
}
```

Para encontrar la ruta al intérprete de Python:
```bash
source .venv/bin/activate
which python
```

#### 4. Abrir un scratch en Firefox

Navegar a `https://decomp.me/scratch/SLUG`. El punto se pone verde cuando el bridge está listo.

### Herramientas MCP disponibles

#### Bridge (control del navegador)

| Herramienta | Descripción |
|-------------|-------------|
| `bridge_get_scratch` | Lee el scratch actual: fuente, contexto, opciones del compilador, score |
| `bridge_set_source` | Escribe código C en el editor CodeMirror |
| `bridge_set_context` | Escribe las cabeceras de contexto (typedefs, structs) |
| `bridge_compile` | Hace clic en Compile y espera el diff y el score |
| `bridge_get_diff` | Lee el diff de ensamblador actual (target vs. generado) |
| `bridge_get_compiler_opts` | Lee el compilador, preset y flags actuales |
| `bridge_set_compiler_opts` | Cambia compilador, preset y/o flags |

#### Coordinación (agentes paralelos)

| Herramienta | Descripción |
|-------------|-------------|
| `decomp_claim_function` | Reserva una función para que otros agentes no la dupliquen |
| `decomp_release_function` | Libera una función reservada |
| `decomp_list_claims` | Muestra todas las reservas activas |
| `decomp_complete_function` | Marca una función como terminada (persiste entre sesiones) |
| `decomp_list_completed` | Lista funciones terminadas con sus scores |

#### Base de datos de patrones

| Herramienta | Descripción |
|-------------|-------------|
| `decomp_search_patterns` | Busca patrones ASM→C por plataforma, compilador, tags o fragmento de ensamblador |
| `decomp_save_pattern` | Guarda un patrón exitoso para uso futuro |

### Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `DECOMP_BRIDGE_URL` | `ws://127.0.0.1:9400` | Dirección del relay |
| `DECOMP_CLAIMS_FILE` | `/tmp/decomp_claims.json` | Reservas activas (efímero, /tmp está bien) |
| `DECOMP_COMPLETED_FILE` | `~/.local/share/decomp-mcp/completed.json` | Registro persistente de funciones completadas (sobrevive reinicios) |
| `DECOMP_CLAIM_TIMEOUT` | `3600` | Segundos hasta que una claim expirada se libera automáticamente |
| `DECOMP_PATTERNS_DB` | `patterns/patterns.db` | Ruta a la base de datos SQLite de patrones |

### Estado

- [x] Servidor MCP instalado y funcionando con Claude Code
- [x] Extensión Firefox + relay bridge operativos
- [x] Coordinación de agentes paralelos (claim/release/complete)
- [x] Base de datos de patrones (SQLite)
- [ ] Watcher automático de scratches (polling del feed)
- [ ] Soporte para Chrome / Manifest V3
