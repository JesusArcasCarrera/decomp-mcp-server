# NDS ARM9 (nds_arm9)

## Compilador

**Preset:** Pokémon Mystery Dungeon: Explorers of Sky
**Compiler:** `mwcc_30_137` (MW 2.0sp2p2)
**Flags base:** `-O4,s -enum min -proc arm946e -gccext,on -fp soft -lang c99 -char signed -inline on,noauto -Cpp_exceptions off -gccinc -interworking -gccdep -MD -g`

Otros compiladores comunes según el juego:
- `mwcc_30_139` — MW 2.0sp2p4
- `mwcc_40_1051` — MW 1.6sp1

---

## Tipos básicos

```c
typedef signed char s8;
typedef unsigned char u8;
typedef signed short s16;
typedef unsigned short u16;
typedef signed int s32;
typedef unsigned int u32;
typedef signed long long s64;
typedef unsigned long long u64;
typedef float f32;
typedef double f64;
```

---

## Trucos y patrones conocidos

### switch → if/else para conditional moves

El compilador `mwcc_30_137` con `-O4,s` **no** convierte `switch` a instrucciones condicionales ARM (`moveq`, `movne`, `bxeq`). Genera branches en su lugar.

Si el target usa `moveq`/`movne`/`bxeq`, usar `if/else` en vez de `switch`:

```c
// ❌ genera branches
switch (val) {
case -256: return 1;
case 256:  return 2;
default:   return 0;
}

// ✅ genera moveq/movne
if (val == -256) return 1;
if (val == 256)  return 2;
return 0;
```

### Registro r1 vs r0 para valores intermedios

Cuando el target carga en `r1` en vez de `r0`, el compilador está preservando el puntero en `r0`. Esto ocurre cuando `arg0` sigue siendo válido después de la carga. Usar el tipo correcto (struct en vez de `void*`) para que el compilador gestione bien los registros.

### ARM interworking

El flag `-interworking` genera `bx lr` en vez de `mov pc, lr` para retornos. Necesario para código que puede ser llamado desde Thumb.

### Soft float

`-fp soft` usa emulación software para floats. No usar FPU instructions en el ensamblado objetivo si está activo.

### Valores negativos en comparaciones

`mvn r0, #0xff` equivale a `r0 = ~0xff = -256 (0xFFFFFF00)`. El compilador lo usa para comparar con -256 en vez de `mov r0, #0xFFFFFF00` (que no cabe en un immediate ARM de 8 bits).

### ASR vs LSR en shifts

Después de `AND` con una máscara, el compilador elige `lsr` (logical shift right) en lugar de `asr` (arithmetic shift right). Para forzar `asr`, hacer un cast a `s32` **antes** del AND:

```c
// ❌ genera lsr (logical shift)
u32 val = read_half_word(...);
result = (val & 0xF8) >> 3;

// ✅ genera asr (arithmetic shift)
u32 val = read_half_word(...);
result = (s32)((s32)val & 0xF8) >> 3;
```

### Indexación de half-words en arrays u8

Para leer `ldrh [base, offset]` sin factor de escala extra, declarar arrays como `u8` pero hacer pointer cast a `(unsigned short*)`:

```c
extern u8 data[];  // indexado con offset en bytes

// ✅ genera ldrh [r0, r3] sin lsl
s32 val = *(unsigned short*)(&data[offset]);

// ❌ genera ldr [r0, r3, lsl #2] con escala extra
s32 val = ((s32*)data)[offset];
```

---

## Struct offsets

Los offsets de struct se leen directamente del assembly:
- `ldrsh r1, [r0, #0x10]` → campo `s16` en offset `0x10`
- `ldr r0, [r0, #0x4]` → campo `u32/ptr` en offset `0x4`
- `strb r1, [r0, #0x8]` → campo `u8` en offset `0x8`

Para rellenar padding hasta un offset: `u8 unkXX[tamaño];`

---

## Patrones de retorno

| Assembly | C equivalente |
|----------|--------------|
| `bx lr` | `return;` / `return val;` |
| `bxeq lr` | `if (cond) return val;` |
| `mov r0, #0 / bx lr` | `return 0;` |
| `moveq r0, #1 / bxeq lr` | `if (cond) return 1;` |
