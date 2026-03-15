# MCP Tool Reference

Tools available in the `decomp-mcp-server`. All browser interaction requires the Firefox extension connected and a scratch page open.

---

## Browser Bridge Tools

These tools interact with the decomp.me scratch open in Firefox via the WebSocket bridge.

### `bridge_get_scratch`
Read the full current state of the open scratch.

Returns: name, slug, platform, match score, source code, context, and compiler output.

No parameters.

---

### `bridge_set_source`
Set the source code in the editor.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `code` | string | yes | C source code to write into the editor |

---

### `bridge_set_context`
Set the context panel (typedefs, structs, extern declarations).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `code` | string | yes | C declarations to write into the context editor |

---

### `bridge_compile`
Click Compile and wait for the result.

Returns: match score, compiler output, assembly diff.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `timeout_ms` | integer | no | Max wait time in ms (default: 30000) |

---

### `bridge_get_diff`
Read the current assembly diff without recompiling.

Returns: score + side-by-side target/current assembly rows.

No parameters.

---

### `bridge_get_compiler_opts`
Read the current compiler options from the page.

Returns: `preset`, `compiler` (ID string), `flags` (full flags string).

No parameters.

---

### `bridge_set_compiler_opts`
Change compiler options. All parameters are optional — only provided ones are changed.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `compiler` | string | no | Compiler ID, e.g. `mwcc_40_1051`, `mwcc_30_139` |
| `flags` | string | no | Full flags string, e.g. `-O4,s -enum min -proc arm946e -lang c99` |
| `preset` | string | no | Preset name, e.g. `Pokémon Diamond / Pearl`, `Custom` |

**Note:** After changing compiler options, always call `bridge_compile` to apply them.

---

## Coordination Tools

File-based locking for parallel agents working on different functions simultaneously.

### `decomp_claim_function`
Reserve a function for exclusive work. Fails if already claimed or already completed.

Claims auto-expire after 1 hour.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `function_name` | string | yes | e.g. `sub_0200BC54` |
| `agent_id` | string | no | Label for this agent (for debugging) |

---

### `decomp_release_function`
Release a claim without marking the function done.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `function_name` | string | yes | Function to release |

---

### `decomp_list_claims`
Show all active claims with agent IDs and ages.

No parameters.

---

### `decomp_complete_function`
Mark a function as done. Automatically releases any claim.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `function_name` | string | yes | Function name |
| `match_percent` | number | yes | Best match achieved (0–100) |
| `scratch_slug` | string | yes | Slug of the best scratch |
| `committed` | boolean | no | Whether committed to repo (default: false) |
| `notes` | string | no | e.g. `"register diffs only"`, `"needs struct work"` |

---

### `decomp_list_completed`
List all completed functions sorted by match %.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `min_match` | number | no | Filter: only show functions with ≥ this match % |

---

## Patterns Tools

SQLite database of known assembly-to-C translations. Shared across agents.

### `decomp_search_patterns`
Search the patterns database.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `asm_fragment` | string | no | Substring to match against stored assembly |
| `platform` | string | no | e.g. `nds_arm9`, `gc_wii`, `n64`, `ps2` |
| `compiler` | string | no | e.g. `mwcc_40_1051` |
| `tags` | string | no | Comma-separated, substring match |
| `limit` | integer | no | Max results (default: 20) |

---

### `decomp_save_pattern`
Save a successful pattern to the database.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `platform` | string | yes | Platform ID |
| `compiler` | string | yes | Compiler ID |
| `asm_pattern` | string | yes | Target assembly (or representative snippet) |
| `c_code` | string | yes | Matching C code |
| `match_score` | number | yes | Score (0.0 = perfect, higher = worse) |
| `scratch_url` | string | no | decomp.me scratch slug or URL |
| `notes` | string | no | Free-form notes |
| `tags` | string | no | Comma-separated tags, e.g. `loop,switch,struct` |

---

## Typical Workflow

```
1. bridge_get_scratch           → see what's open, check current score
2. bridge_get_compiler_opts     → confirm compiler and flags
3. decomp_claim_function        → reserve the function
4. decomp_search_patterns       → look for similar known patterns
5. bridge_set_context           → add typedefs / externs
6. bridge_set_source            → write C code
7. bridge_compile               → compile and check score
   ... iterate steps 5-7 ...
8. decomp_complete_function     → record result
9. decomp_save_pattern          → save successful pattern
```
