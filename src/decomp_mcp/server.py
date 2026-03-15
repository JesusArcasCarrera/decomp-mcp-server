"""MCP server for decomp.me decompilation coordination and browser bridge."""

import asyncio
import fcntl
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import (
    Tool,
    TextContent,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("decomp-mcp")

# Claims file: which agent is working on which function.
# Ephemeral by design — losing it on reboot is fine, claims auto-expire anyway.
DECOMP_CLAIMS_FILE = os.environ.get("DECOMP_CLAIMS_FILE", "/tmp/decomp_claims.json")

# Completed functions file: permanent record that persists across reboots.
# Stored in the XDG data directory so it survives /tmp clears.
_DEFAULT_COMPLETED = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "decomp-mcp",
    "completed.json",
)
DECOMP_COMPLETED_FILE = os.environ.get("DECOMP_COMPLETED_FILE", _DEFAULT_COMPLETED)

# Seconds before an agent's claim is considered stale and auto-released.
DECOMP_CLAIM_TIMEOUT = int(os.environ.get("DECOMP_CLAIM_TIMEOUT", "3600"))  # 1 hour

# Add patterns dir to path for local pattern database
_patterns_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'patterns')
if os.path.isdir(_patterns_dir):
    sys.path.insert(0, _patterns_dir)

try:
    from db import search_patterns, add_pattern
    PATTERNS_AVAILABLE = True
except ImportError:
    PATTERNS_AVAILABLE = False
    logger.info("Patterns module not available - decomp_search_patterns and decomp_save_pattern tools will be disabled")

# Create server instance
app = Server("decomp-mcp-server")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        # ─── Browser Bridge Tools ───
        Tool(
            name="bridge_get_scratch",
            description=(
                "Read the current scratch info, source code, context, and compilation state "
                "from the decomp.me page open in the browser. Requires the decomp.me Bridge "
                "browser extension to be installed and connected."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="bridge_set_source",
            description=(
                "Set the source code in the decomp.me editor via the browser extension. "
                "This types the code directly into the CodeMirror editor on the open scratch page."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The C source code to set in the editor",
                    },
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="bridge_set_context",
            description=(
                "Set the context (typedefs, structs, declarations) in the decomp.me editor "
                "via the browser extension."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The context code (typedefs, structs, declarations)",
                    },
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="bridge_compile",
            description=(
                "Click Compile on the decomp.me page and return the result: match score, "
                "diff output, and compiler errors. Uses the browser extension."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "timeout_ms": {
                        "type": "integer",
                        "description": "Timeout in milliseconds to wait for compilation (default 30000)",
                    },
                },
            },
        ),
        Tool(
            name="bridge_get_diff",
            description=(
                "Get the current diff view (target vs current assembly) from the open "
                "decomp.me scratch page via the browser extension."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="bridge_set_compiler_opts",
            description=(
                "Set compiler options on the open decomp.me scratch via the browser extension. "
                "Can set the compiler version, the raw flags string, and/or a preset. "
                "All parameters are optional — only the provided ones are changed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "compiler": {
                        "type": "string",
                        "description": "Compiler ID (e.g. 'mwcc_40_1051', 'mwcc_30_139')",
                    },
                    "flags": {
                        "type": "string",
                        "description": "Full compiler flags string (e.g. '-O4,s -enum min -proc arm946e -lang c99')",
                    },
                    "preset": {
                        "type": "string",
                        "description": "Preset name (e.g. 'Pokémon Diamond / Pearl', 'Custom')",
                    },
                },
            },
        ),
        Tool(
            name="bridge_get_compiler_opts",
            description=(
                "Read the current compiler options (compiler version, flags, preset) "
                "from the open decomp.me scratch via the browser extension."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # ─── Coordination Tools ───
        Tool(
            name="decomp_claim_function",
            description=(
                "Claim a function to work on, preventing other parallel agents from picking it. "
                "Returns success if claimed, or failure if already claimed by another agent. "
                "Claims auto-expire after 1 hour. Always claim before starting work on a function."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "function_name": {
                        "type": "string",
                        "description": "Name of the function to claim (e.g., 'fn_80393C14')",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Optional identifier for this agent (for debugging)",
                    },
                },
                "required": ["function_name"],
            },
        ),
        Tool(
            name="decomp_release_function",
            description=(
                "Release a claimed function, allowing other agents to work on it. "
                "Call this when done working on a function (whether matched or giving up)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "function_name": {
                        "type": "string",
                        "description": "Name of the function to release",
                    },
                },
                "required": ["function_name"],
            },
        ),
        Tool(
            name="decomp_list_claims",
            description=(
                "List all currently claimed functions. Useful for seeing what other agents are working on. "
                "Stale claims (older than 1 hour) are automatically removed."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="decomp_complete_function",
            description=(
                "Mark a function as completed/attempted. Call this when done working on a function "
                "(whether 100% match or stuck at 95%+). This prevents other agents from picking it up. "
                "Also automatically releases the claim."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "function_name": {
                        "type": "string",
                        "description": "Name of the function",
                    },
                    "match_percent": {
                        "type": "number",
                        "description": "Best match percentage achieved (0-100)",
                    },
                    "scratch_slug": {
                        "type": "string",
                        "description": "The decomp.me scratch slug with the best code",
                    },
                    "committed": {
                        "type": "boolean",
                        "description": "Whether the code was committed to the repo",
                        "default": False,
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional notes (e.g., 'register diffs only', 'needs struct work')",
                    },
                },
                "required": ["function_name", "match_percent", "scratch_slug"],
            },
        ),
        Tool(
            name="decomp_list_completed",
            description=(
                "List all completed/attempted functions. Shows match percentages, scratch slugs, "
                "and whether they were committed to the repo."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "min_match": {
                        "type": "number",
                        "description": "Only show functions with at least this match % (default: 0)",
                    },
                },
            },
        ),
        # ─── Patterns Tools ───
        Tool(
            name="decomp_search_patterns",
            description=(
                "Search the local patterns database for previously successful decompilation patterns. "
                "Useful for finding known assembly-to-C translations for specific platforms and compilers. "
                "Returns matching patterns with their assembly, C code, match scores, and notes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "asm_fragment": {
                        "type": "string",
                        "description": "Assembly fragment to search for (substring match against stored asm patterns)",
                    },
                    "platform": {
                        "type": "string",
                        "description": "Filter by platform (e.g., gc_wii, n64, ps1, ps2)",
                    },
                    "compiler": {
                        "type": "string",
                        "description": "Filter by compiler (e.g., mwcc_233_163n)",
                    },
                    "tags": {
                        "type": "string",
                        "description": "Filter by tags (comma-separated, substring match)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 20)",
                        "default": 20,
                    },
                },
            },
        ),
        Tool(
            name="decomp_save_pattern",
            description=(
                "Save a successful decompilation pattern to the local patterns database. "
                "Use this after achieving a good match to record the assembly-to-C translation "
                "for future reference by yourself or other agents."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "description": "Platform (e.g., gc_wii, n64, ps1, ps2)",
                    },
                    "compiler": {
                        "type": "string",
                        "description": "Compiler used (e.g., mwcc_233_163n)",
                    },
                    "asm_pattern": {
                        "type": "string",
                        "description": "The target assembly code (or representative snippet)",
                    },
                    "c_code": {
                        "type": "string",
                        "description": "The matching C source code",
                    },
                    "match_score": {
                        "type": "number",
                        "description": "Match score achieved (0.0 = perfect match, higher = worse)",
                    },
                    "scratch_url": {
                        "type": "string",
                        "description": "URL or slug of the decomp.me scratch (optional)",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Any notes about this pattern (optional)",
                    },
                    "tags": {
                        "type": "string",
                        "description": "Comma-separated tags for categorization (optional, e.g., 'loop,switch,struct')",
                    },
                },
                "required": ["platform", "compiler", "asm_pattern", "c_code", "match_score"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    try:
        if name.startswith("bridge_"):
            return await handle_bridge_command(name, arguments)
        elif name == "decomp_claim_function":
            return await handle_claim_function(arguments)
        elif name == "decomp_release_function":
            return await handle_release_function(arguments)
        elif name == "decomp_list_claims":
            return await handle_list_claims(arguments)
        elif name == "decomp_complete_function":
            return await handle_complete_function(arguments)
        elif name == "decomp_list_completed":
            return await handle_list_completed(arguments)
        elif name == "decomp_search_patterns":
            return await handle_search_patterns(arguments)
        elif name == "decomp_save_pattern":
            return await handle_save_pattern(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        logger.error(f"Error in {name}: {e}", exc_info=True)
        return [TextContent(type="text", text=f"Error: {str(e)}")]


def _load_completed() -> dict[str, Any]:
    """Load completed functions from file."""
    completed_path = Path(DECOMP_COMPLETED_FILE)

    if not completed_path.exists():
        return {}

    try:
        with open(completed_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_completed(completed: dict[str, Any]) -> None:
    """Save completed functions to file."""
    completed_path = Path(DECOMP_COMPLETED_FILE)
    completed_path.parent.mkdir(parents=True, exist_ok=True)

    with open(completed_path, "w") as f:
        json.dump(completed, f, indent=2)


def _is_function_completed(function_name: str) -> dict[str, Any] | None:
    """Check if a function is already completed. Returns info if so, None otherwise."""
    completed = _load_completed()
    return completed.get(function_name)


def _load_claims() -> dict[str, Any]:
    """Load claims from file, removing stale entries."""
    claims_path = Path(DECOMP_CLAIMS_FILE)

    if not claims_path.exists():
        return {}

    try:
        with open(claims_path, "r") as f:
            claims = json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}

    # Remove stale claims
    now = time.time()
    active_claims = {}
    for func_name, claim_info in claims.items():
        if now - claim_info.get("timestamp", 0) < DECOMP_CLAIM_TIMEOUT:
            active_claims[func_name] = claim_info

    return active_claims


def _save_claims(claims: dict[str, Any]) -> None:
    """Save claims to file."""
    claims_path = Path(DECOMP_CLAIMS_FILE)
    claims_path.parent.mkdir(parents=True, exist_ok=True)

    with open(claims_path, "w") as f:
        json.dump(claims, f, indent=2)


async def handle_claim_function(arguments: dict[str, Any]) -> list[TextContent]:
    """Handle decomp_claim_function tool."""
    function_name = arguments["function_name"]
    agent_id = arguments.get("agent_id", "unknown")

    logger.info(f"Claiming function: {function_name} (agent: {agent_id})")

    # Check if function is already completed (before locking)
    completed_info = _is_function_completed(function_name)
    if completed_info:
        match_pct = completed_info.get("match_percent", 0)
        scratch = completed_info.get("scratch_slug", "?")
        committed = "committed" if completed_info.get("committed") else "not committed"
        return [
            TextContent(
                type="text",
                text=f"❌ **Claim Failed**\n\n`{function_name}` was already completed:\n- Match: {match_pct:.1f}%\n- Scratch: {scratch}\n- Status: {committed}\n\nPick a different function.",
            )
        ]

    claims_path = Path(DECOMP_CLAIMS_FILE)
    claims_path.parent.mkdir(parents=True, exist_ok=True)

    # Use file locking for atomic operation
    lock_path = Path(str(claims_path) + ".lock")
    lock_path.touch(exist_ok=True)

    with open(lock_path, "r") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

            claims = _load_claims()

            # Check if already claimed
            if function_name in claims:
                existing = claims[function_name]
                age_mins = (time.time() - existing["timestamp"]) / 60
                return [
                    TextContent(
                        type="text",
                        text=f"❌ **Claim Failed**\n\n`{function_name}` is already claimed by `{existing.get('agent_id', 'unknown')}` ({age_mins:.0f} minutes ago).\n\nPick a different function.",
                    )
                ]

            # Claim it
            claims[function_name] = {
                "agent_id": agent_id,
                "timestamp": time.time(),
            }
            _save_claims(claims)

            return [
                TextContent(
                    type="text",
                    text=f"✅ **Claimed:** `{function_name}`\n\nYou have 1 hour to work on this function. Call `decomp_release_function` when done.",
                )
            ]
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


async def handle_release_function(arguments: dict[str, Any]) -> list[TextContent]:
    """Handle decomp_release_function tool."""
    function_name = arguments["function_name"]

    logger.info(f"Releasing function: {function_name}")

    claims_path = Path(DECOMP_CLAIMS_FILE)
    lock_path = Path(str(claims_path) + ".lock")

    if not claims_path.exists():
        return [
            TextContent(
                type="text",
                text=f"Function `{function_name}` was not claimed.",
            )
        ]

    lock_path.touch(exist_ok=True)

    with open(lock_path, "r") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

            claims = _load_claims()

            if function_name not in claims:
                return [
                    TextContent(
                        type="text",
                        text=f"Function `{function_name}` was not claimed.",
                    )
                ]

            del claims[function_name]
            _save_claims(claims)

            return [
                TextContent(
                    type="text",
                    text=f"✅ **Released:** `{function_name}`\n\nOther agents can now work on this function.",
                )
            ]
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


async def handle_list_claims(arguments: dict[str, Any]) -> list[TextContent]:
    """Handle decomp_list_claims tool."""
    logger.info("Listing claims")

    claims = _load_claims()

    if not claims:
        return [
            TextContent(
                type="text",
                text="No functions are currently claimed.",
            )
        ]

    lines = ["# Currently Claimed Functions", ""]
    now = time.time()

    for func_name, claim_info in sorted(claims.items()):
        age_mins = (now - claim_info["timestamp"]) / 60
        remaining_mins = (DECOMP_CLAIM_TIMEOUT / 60) - age_mins
        lines.append(
            f"- **{func_name}** - claimed by `{claim_info.get('agent_id', 'unknown')}` ({age_mins:.0f}m ago, {remaining_mins:.0f}m remaining)"
        )

    lines.extend(["", f"_Claims expire after {DECOMP_CLAIM_TIMEOUT // 60} minutes._"])

    return [
        TextContent(
            type="text",
            text="\n".join(lines),
        )
    ]


async def handle_complete_function(arguments: dict[str, Any]) -> list[TextContent]:
    """Handle decomp_complete_function tool."""
    function_name = arguments["function_name"]
    match_percent = arguments["match_percent"]
    scratch_slug = arguments["scratch_slug"]
    committed = arguments.get("committed", False)
    notes = arguments.get("notes", "")

    logger.info(f"Marking function as completed: {function_name} ({match_percent}%)")

    # Load and update completed functions
    completed = _load_completed()
    completed[function_name] = {
        "match_percent": match_percent,
        "scratch_slug": scratch_slug,
        "committed": committed,
        "notes": notes,
        "timestamp": time.time(),
    }
    _save_completed(completed)

    # Also release any claim on this function
    claims_path = Path(DECOMP_CLAIMS_FILE)
    if claims_path.exists():
        lock_path = Path(str(claims_path) + ".lock")
        lock_path.touch(exist_ok=True)
        with open(lock_path, "r") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                claims = _load_claims()
                if function_name in claims:
                    del claims[function_name]
                    _save_claims(claims)
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    # Format response
    status = "✅ Committed" if committed else "📝 Recorded"
    lines = [
        f"# Function Completed",
        f"",
        f"**Function:** `{function_name}`",
        f"**Match:** {match_percent:.1f}%",
        f"**Scratch:** {scratch_slug}",
        f"**Status:** {status}",
    ]
    if notes:
        lines.append(f"**Notes:** {notes}")

    lines.extend(
        [
            f"",
            f"This function is now marked as completed and won't be picked by other agents.",
        ]
    )

    return [
        TextContent(
            type="text",
            text="\n".join(lines),
        )
    ]


async def handle_list_completed(arguments: dict[str, Any]) -> list[TextContent]:
    """Handle decomp_list_completed tool."""
    min_match = arguments.get("min_match", 0)

    logger.info(f"Listing completed functions (min_match={min_match})")

    completed = _load_completed()

    if not completed:
        return [
            TextContent(
                type="text",
                text="No functions have been completed yet.",
            )
        ]

    # Filter by min_match
    filtered = {
        name: info
        for name, info in completed.items()
        if info.get("match_percent", 0) >= min_match
    }

    if not filtered:
        return [
            TextContent(
                type="text",
                text=f"No functions with ≥{min_match}% match found.",
            )
        ]

    lines = [f"# Completed Functions ({len(filtered)} total)", ""]

    # Sort by match percent descending
    sorted_funcs = sorted(
        filtered.items(), key=lambda x: x[1].get("match_percent", 0), reverse=True
    )

    for func_name, info in sorted_funcs:
        match_pct = info.get("match_percent", 0)
        scratch = info.get("scratch_slug", "?")
        committed = "✅" if info.get("committed") else "📝"
        notes = info.get("notes", "")

        line = f"- {committed} **{func_name}** - {match_pct:.1f}% ([{scratch}](http://decomp.me/scratch/{scratch}))"
        if notes:
            line += f" - _{notes}_"
        lines.append(line)

    return [
        TextContent(
            type="text",
            text="\n".join(lines),
        )
    ]


async def handle_search_patterns(arguments: dict[str, Any]) -> list[TextContent]:
    """Handle decomp_search_patterns tool."""
    if not PATTERNS_AVAILABLE:
        return [
            TextContent(
                type="text",
                text=(
                    "Patterns module is not available. Make sure the patterns/db.py file exists "
                    "in the project root. Expected location: "
                    f"{os.path.abspath(_patterns_dir)}/db.py"
                ),
            )
        ]

    asm_fragment = arguments.get("asm_fragment")
    platform = arguments.get("platform")
    compiler = arguments.get("compiler")
    tags = arguments.get("tags")
    limit = arguments.get("limit", 20)

    logger.info(
        f"Searching patterns: asm={asm_fragment!r}, platform={platform}, "
        f"compiler={compiler}, tags={tags}, limit={limit}"
    )

    results = search_patterns(
        asm_fragment=asm_fragment,
        platform=platform,
        compiler=compiler,
        tags=tags,
        limit=limit,
    )

    if not results:
        return [
            TextContent(
                type="text",
                text="No matching patterns found.",
            )
        ]

    lines = [f"# Pattern Search Results ({len(results)} found)", ""]

    for i, row in enumerate(results, 1):
        lines.extend([
            f"## Pattern {i}",
            f"",
            f"**Platform:** {row.get('platform', '?')}",
            f"**Compiler:** {row.get('compiler', '?')}",
            f"**Match Score:** {row.get('match_score', '?')}",
        ])
        if row.get("scratch_url"):
            lines.append(f"**Scratch:** {row['scratch_url']}")
        if row.get("tags"):
            lines.append(f"**Tags:** {row['tags']}")
        if row.get("notes"):
            lines.append(f"**Notes:** {row['notes']}")
        lines.extend([
            f"",
            f"**Assembly:**",
            f"```asm",
            row.get("asm_pattern", ""),
            f"```",
            f"",
            f"**C Code:**",
            f"```c",
            row.get("c_code", ""),
            f"```",
            f"",
        ])

    return [
        TextContent(
            type="text",
            text="\n".join(lines),
        )
    ]


async def handle_save_pattern(arguments: dict[str, Any]) -> list[TextContent]:
    """Handle decomp_save_pattern tool."""
    if not PATTERNS_AVAILABLE:
        return [
            TextContent(
                type="text",
                text=(
                    "Patterns module is not available. Make sure the patterns/db.py file exists "
                    "in the project root. Expected location: "
                    f"{os.path.abspath(_patterns_dir)}/db.py"
                ),
            )
        ]

    platform = arguments["platform"]
    compiler = arguments["compiler"]
    asm_pattern = arguments["asm_pattern"]
    c_code = arguments["c_code"]
    match_score = arguments["match_score"]
    scratch_url = arguments.get("scratch_url")
    notes = arguments.get("notes")
    tags = arguments.get("tags")

    logger.info(
        f"Saving pattern: platform={platform}, compiler={compiler}, "
        f"score={match_score}, tags={tags}"
    )

    pattern_id = add_pattern(
        platform=platform,
        compiler=compiler,
        asm_pattern=asm_pattern,
        c_code=c_code,
        match_score=match_score,
        scratch_url=scratch_url,
        notes=notes,
        tags=tags,
    )

    lines = [
        f"# Pattern Saved",
        f"",
        f"**Pattern ID:** {pattern_id}",
        f"**Platform:** {platform}",
        f"**Compiler:** {compiler}",
        f"**Match Score:** {match_score}",
    ]
    if scratch_url:
        lines.append(f"**Scratch:** {scratch_url}")
    if tags:
        lines.append(f"**Tags:** {tags}")
    lines.extend([
        f"",
        f"Pattern has been saved to the local database for future reference.",
    ])

    return [
        TextContent(
            type="text",
            text="\n".join(lines),
        )
    ]


async def handle_bridge_command(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle browser bridge tool calls."""
    try:
        from decomp_mcp.bridge import (
            bridge,
            bridge_get_scratch_info, bridge_get_source,
            bridge_set_source, bridge_get_context, bridge_set_context,
            bridge_compile, bridge_get_diff, bridge_get_compiler_output,
            bridge_get_score, bridge_set_compiler_opts, bridge_get_compiler_opts,
            BridgeError,
        )
    except ImportError as e:
        return [TextContent(
            type="text",
            text=f"Bridge module not available: {e}. Install websockets: pip install websockets",
        )]

    try:
        if name == "bridge_get_scratch":
            info = await bridge_get_scratch_info()
            source = await bridge_get_source()
            context = await bridge_get_context()
            compiler_out = await bridge_get_compiler_output()
            score = await bridge_get_score()

            lines = [
                "# Current Scratch (via Browser)",
                "",
                f"**Name:** {info.get('name', '?')}",
                f"**Slug:** {info.get('slug', '?')}",
                f"**Platform:** {info.get('platform', '?')}",
                f"**Score:** {score.get('text', '?')}",
                "",
                "## Source Code",
                "```c",
                source,
                "```",
                "",
                "## Context",
                "```c",
                context if context else "(empty)",
                "```",
            ]
            if compiler_out:
                lines.extend([
                    "",
                    "## Compiler Output",
                    "```",
                    compiler_out,
                    "```",
                ])

            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "bridge_set_source":
            result = await bridge_set_source(arguments["code"])
            if result.get("error"):
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            return [TextContent(type="text", text="Source code updated in browser editor.")]

        elif name == "bridge_set_context":
            result = await bridge_set_context(arguments["code"])
            if result.get("error"):
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            return [TextContent(type="text", text="Context updated in browser editor.")]

        elif name == "bridge_compile":
            timeout_ms = arguments.get("timeout_ms", 30000)
            result = await bridge_compile(timeout=timeout_ms)
            score = result.get("score", {})
            api = result.get("api_data", {})
            compiler_out = result.get("compiler_output", "")

            lines = ["# Compilation Result (via Browser)", ""]

            if api:
                current = api.get("score", "?")
                maximum = api.get("max_score", "?")
                success = api.get("success", False)
                if isinstance(current, (int, float)) and isinstance(maximum, (int, float)) and maximum > 0:
                    pct = (1 - current / maximum) * 100 if current <= maximum else 0
                    lines.append(f"**Match:** {pct:.1f}% (score {current}/{maximum})")
                else:
                    lines.append(f"**Score:** {current}/{maximum}")
                lines.append(f"**Compiled:** {'Yes' if success else 'No (errors)'}")
            else:
                lines.append(f"**Score:** {score.get('text', '?')}")

            if compiler_out:
                lines.extend(["", "## Compiler Output", "```", compiler_out, "```"])

            diff_rows = result.get("diff", [])
            if diff_rows:
                lines.extend(["", "## Diff (Target vs Current)"])
                for row in diff_rows[:50]:  # limit output
                    target = row.get("target", "")
                    current = row.get("current", "")
                    lines.append(f"  {target:40s} | {current}")

            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "bridge_get_diff":
            result = await bridge_get_diff()
            score = result.get("score", {})
            diff_rows = result.get("diff", [])

            lines = [
                "# Current Diff",
                "",
                f"**Score:** {score.get('text', '?')}",
                "",
                "## Assembly Diff (Target | Current)",
            ]
            for row in diff_rows:
                target = row.get("target", "")
                current = row.get("current", "")
                lines.append(f"  {target:40s} | {current}")

            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "bridge_set_compiler_opts":
            result = await bridge_set_compiler_opts(
                compiler=arguments.get("compiler"),
                flags=arguments.get("flags"),
                preset=arguments.get("preset"),
            )
            changed = [k for k, v in result.get("results", {}).items() if v]
            return [TextContent(
                type="text",
                text=f"Compiler options updated: {', '.join(changed) if changed else 'nothing changed'}",
            )]

        elif name == "bridge_get_compiler_opts":
            result = await bridge_get_compiler_opts()
            lines = [
                "# Current Compiler Options",
                "",
                f"**Preset:** {result.get('preset') or '—'}",
                f"**Compiler:** {result.get('compiler') or '—'}",
                f"**Flags:** `{result.get('flags') or ''}`",
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        else:
            return [TextContent(type="text", text=f"Unknown bridge command: {name}")]

    except BridgeError as e:
        return [TextContent(type="text", text=f"Bridge error: {e}")]


async def main():
    """Run the MCP server."""
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
