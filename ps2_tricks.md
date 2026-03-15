# Trucos de decompilación PS2 — MWCPS2 3.0.3

## Compilador y flags

- El juego (Silent Hill 3) parece compilado con **MWCPS2 3.0.3 -O0** (o -O0,s), no -O3 como se asumió inicialmente.
- Algunas funciones `sh*` de bajo nivel (ej. `shQzero`) pueden ser **ASM escrito a mano** o compilado con otro compilador/flags, ya que usan patrones que no matchean MWCPS2 en ningún nivel de optimización (`bnezl`, registros `t6`/`t7`, `sub`/`addi` trapping).

## Branches e if/else

### Dirección del branch
Con -O0, el compilador genera el branch para **saltar al else** (o saltar fuera del if-body):

```c
// if (x > 0) { A } else { B }
// Genera: blez x, else_label  (salta a B si x <= 0, fall-through a A)

// if (x <= 0) { A } else { B }
// Genera: bgtz x, else_label  (salta a B si x > 0, fall-through a A)
```

**Regla**: Si el target tiene `blez`, el código original usa `if (x > 0)`. Si tiene `bgtz`, usa `if (x <= 0)`. El fall-through es siempre el if-body.

| Target         | Código C             |
|----------------|----------------------|
| `blez reg, L`  | `if (reg > 0) { }`  |
| `bgtz reg, L`  | `if (reg <= 0) { }` |
| `bltz reg, L`  | `if (reg >= 0) { }` |
| `bgez reg, L`  | `if (reg < 0) { }`  |
| `beqz reg, L`  | `if (reg != 0) { }` |
| `bnez reg, L`  | `if (reg == 0) { }` |

### Invertir if/else para matchear
Si el compilado genera `bgtz` pero el target tiene `blez`, invierte el if/else:
```c
// MAL (genera bgtz, salta a success):
if (len <= 0) { error; } // success...

// BIEN (genera blez, salta a error):
if (len > 0) { success; } else { error; }
```

## Registros s0-s3

### Asignación de s-registers con -O0
MWCPS2 -O0 asigna s-registers basándose en el **orden de declaración** de variables. El orden exacto puede variar, pero la posición relativa importa.

**Truco**: Si s2 y s3 están swapped, intenta:
1. Cambiar el orden de declaración
2. Poner el buffer/struct **entre** las declaraciones de variables
3. Mover la variable al final o al principio

Ejemplo que funciona:
```c
int i;                          // → s1
func_001532C0_arg0_struct sp50; // buffer (sin s-register)
int var_s2;                     // → s2
int ret;                        // → s0
int var_s3 = arg1 & 0x200000;   // → s3
```

### s0 suele ser el valor de retorno
`s0` típicamente almacena la variable que se devuelve con `return`.

## Args en stack vs registers

Con **-O0**, los argumentos de función se guardan en el stack:
```
sw a0, 0x250(sp)
sw a1, 0x260(sp)
sw a2, 0x270(sp)
```
Y se recargan con `lw` cuando se necesitan. Con **-O3**, se mantienen en s-registers (s4, s5...), generando un stack frame más pequeño.

Si ves `sw a0,X(sp)` / `lw a1,X(sp)` en el target → probablemente **-O0**.
Si ves los args en s4/s5 → probablemente **-O3** o superior.

## El truco del `do { } while(0)`

Con -O0, `do { } while(0)` genera un **`nop` extra** porque el compilador emite código para evaluar la condición `while(0)` aunque siempre sea falsa.

```c
// Si falta un nop en un branch target:
if (flag != 0) {
    do {
        printf(str, arg);
    } while(0);            // ← genera nop extra
}
```

Esto es común en código de la época porque las **macros multi-statement** usan `do { } while(0)`:
```c
#define DEBUG_PRINT(fmt, ...) do { printf(fmt, __VA_ARGS__); } while(0)
```

## Loops

### `for(;;)` vs `do { } while(1)` vs `while(1)`
Pueden generar código ligeramente diferente con -O0. Probar los tres si no matchea.

### Estructura do-while vs for
```c
// do-while: el check va al final
do { body; } while (cond);
// Genera: body, bnez cond, loop_top

// for(;;) con break:
for (;;) { body; if (cond) break; }
// Puede generar código diferente en el epilogo
```

## Tipos de 128 bits (instrucción `sq`)

Para generar `sq` (store quadword) en vez de `sw`:
```c
// NO funciona con MWCPS2:
u_long128 *p;  // u_long128 suele ser typedef a unsigned int (32-bit)

// SÍ funciona:
__int128 *p = (__int128 *)addr;
*p = 0;  // genera sq
```

## Delay slots

Con -O0, la mayoría de delay slots son `nop`. Con -O3, el compilador rellena delay slots agresivamente. Si ves muchos `nop` tras branches/jumps → **-O0**.

## Printf y strings externas

Declarar strings como punteros extern:
```c
extern char* D_00357930;  // "%s: %d: "
```

Ojo: a veces se pasan con `&` (dirección del puntero):
```c
printf(&D_00357930, &D_00357940, 0x214);  // dirección del puntero
printf(D_00357930, D_00357940, 0x214);    // valor del puntero
```

Depende de cómo estén definidas en el contexto — probar ambas si no matchea.

## Señales de ASM manual

Si una función no matchea con ningún nivel de optimización, puede ser ASM escrito a mano. Señales:
- Usa `bnezl` (branch-likely, raro en compiladores)
- Registros `t6`, `t7` (MWCPS2 prefiere `v0`, `v1`, `a2`)
- `sub`/`addi` (trapping) en vez de `subu`/`addiu`
- Delay slots perfectamente rellenados con instrucciones útiles
- Patrones de optimización manual (unrolling con `sq` alineado)

## Checklist rápido

1. ¿Muchos `nop` en delay slots? → Probar **-O0**
2. ¿Args guardados en stack? → Confirma **-O0**
3. ¿Branch invertido? → Invertir **if/else**
4. ¿Registros swapped? → Cambiar **orden de declaración**
5. ¿Falta un `nop`? → Probar **`do { } while(0)`**
6. ¿Necesitas `sq`? → Usar **`__int128`**
7. ¿No matchea con nada? → Puede ser **ASM manual**
