# decomp-mcp-server — guía para agentes

Servidor MCP que permite a Claude Code trabajar en scratches de [decomp.me](https://decomp.me) controlando el navegador desde dentro, mediante una extensión de Firefox.

## Por qué una extensión y no la API

La API de decomp.me está detrás de Cloudflare y bloquea peticiones automatizadas. En lugar de pelear con eso, la extensión corre dentro de Firefox con la sesión del usuario ya autenticada y manipula el DOM directamente.

## Arquitectura

```
Claude Code (cliente MCP)
    │  stdio
    ▼
src/decomp_mcp/server.py   ← servidor MCP, expone las herramientas
    │  WebSocket ws://127.0.0.1:9400
    ▼
bridge_server.py            ← relay local, ejecutar una vez
    │  WebSocket
    ▼
extension/background.js     ← extensión Firefox, conexión WS persistente
    │  browser.tabs.sendMessage
    ▼
extension/content.js        ← manipulación DOM en la página del scratch
    │
    ▼
decomp.me                   ← editor CodeMirror, compilador, diff
```

## Herramientas MCP disponibles

### Bridge (control del navegador)

| Herramienta | Descripción |
|-------------|-------------|
| `bridge_get_scratch` | Lee scratch actual: fuente, contexto, compilador, score |
| `bridge_set_source` | Escribe código C en el editor |
| `bridge_set_context` | Escribe las cabeceras de contexto (typedefs, structs) |
| `bridge_compile` | Hace clic en Compile y espera el diff y el score |
| `bridge_get_diff` | Lee el diff de ensamblador actual |
| `bridge_get_compiler_opts` | Lee compilador, preset y flags actuales |
| `bridge_set_compiler_opts` | Cambia compilador, preset y/o flags |

### Coordinación (agentes paralelos)

| Herramienta | Descripción |
|-------------|-------------|
| `decomp_claim_function` | Reserva una función para trabajo exclusivo |
| `decomp_release_function` | Libera la reserva sin marcarla completa |
| `decomp_list_claims` | Lista reservas activas con tiempos restantes |
| `decomp_complete_function` | Marca función como terminada (persiste en disco) |
| `decomp_list_completed` | Lista funciones completadas con scores |

### Patrones

| Herramienta | Descripción |
|-------------|-------------|
| `decomp_search_patterns` | Busca patrones ASM→C por plataforma, compilador, tags o fragmento |
| `decomp_save_pattern` | Guarda un patrón exitoso en la base de datos |

## Configuración en Claude Code

Editar `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "decomp": {
      "command": "/ruta/al/repo/.venv/bin/python",
      "args": ["-m", "decomp_mcp.server"]
    }
  }
}
```

Sustituye `/ruta/al/repo/` por la ruta absoluta donde hayas clonado el repo.

## Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `DECOMP_BRIDGE_URL` | `ws://127.0.0.1:9400` | Dirección del relay WebSocket |
| `DECOMP_CLAIMS_FILE` | `/tmp/decomp_claims.json` | Claims activos (efímero, /tmp está bien) |
| `DECOMP_COMPLETED_FILE` | `~/.local/share/decomp-mcp/completed.json` | Historial persistente de funciones completadas |
| `DECOMP_CLAIM_TIMEOUT` | `3600` | Segundos hasta que una claim expirada se libera |
| `DECOMP_PATTERNS_DB` | `patterns/patterns.db` | Base de datos SQLite de patrones |

## Arranque

```bash
# 1. Instalar extensión en Firefox: about:debugging → Cargar complemento temporal → extension/manifest.json
# 2. Abrir un scratch en Firefox: https://decomp.me/scratch/SLUG
# 3. Arrancar el relay:
uv run python bridge_server.py
# 4. Lanzar Claude Code con el MCP configurado
```

## Estado

- [x] Servidor MCP funcionando con Claude Code
- [x] Extensión Firefox + relay bridge operativos
- [x] Coordinación de agentes paralelos
- [x] Base de datos de patrones SQLite
- [ ] Watcher automático de scratches (polling del feed)
- [ ] Soporte Chrome / Manifest V3
