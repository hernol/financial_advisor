# Spec: CEDEARs

Documento de diseño. Escrito el 2026-08-31, antes de escribir código.

> **Estado: diseñado, sin implementar.** La investigación de fuentes está hecha
> y medida; lo que sigue son decisiones tomadas, no opciones abiertas.

**Objetivo**: poder cargar posiciones de CEDEARs con P&L en la cartera, y poder
analizar un CEDEAR aunque no se tenga. Sin romper la regla dura del proyecto:
nunca simular, estimar ni inventar un dato de mercado.

---

## 1. El problema

Un CEDEAR cotiza en pesos. `fa/config.py` ya dice por qué eso no entra hoy:

> La aplicación valúa todo en una moneda. Mezclarlas sin una tabla de conversión
> produce un total calladamente equivocado, que es peor que rechazar la posición.

Y `build_portfolio` lo cumple: cualquier cosa que no cotice en USD queda fuera
del total, con el motivo escrito. Hoy un CEDEAR se cargaría y quedaría afuera.

`config.py` también anticipa el costo de arreglarlo: hace falta «una tabla FX y
una decisión sobre qué tipo de cambio aplica a una operación histórica». Este
documento resuelve las dos cosas — y resulta que la primera casi no hace falta.

## 2. Lo que se midió

Todo lo que sigue son mediciones del 2026-08-31, no supuestos.

### Fuentes vivas

| Fuente | Qué da | Estado |
|---|---|---|
| yfinance `.BA` | Precio y 250 ruedas del CEDEAR, en ARS | ✅ ya es el proveedor primario |
| Comafi (depositario) | 312 CEDEARs con ratio, ticker local, ticker del subyacente e ISIN | ✅ HTML parseable |
| data912 | Precios BYMA en vivo, más especies C (cable) y D (MEP) | ✅ JSON sin clave |
| dolarapi | CCL publicado | ✅ 1.597,5 |
| BYMADATA | — | ❌ 404 en los endpoints probados |

### El tipo de cambio triangula

Tres métodos independientes, el mismo día, coinciden dentro del 0,5%:

| Método | Valor |
|---|---|
| Implícito por subyacente, 6 papeles | 1.596,7 – 1.604,8 (promedio 1.600,6) |
| Mediana de especies C líquidas, 64 papeles | 1.597,8 |
| CCL publicado (dolarapi) | 1.597,5 |

La especie C **no sirve por papel**: de los 311 con ticker simple (queda
afuera `HHPD LI`, que lleva un espacio) sólo 164 tienen C listada y
apenas 64 tuvieron precio y volumen ese día, con dispersión de 1.546,8 (ORLY) a
1.772,5 (BIOX) — puntas viejas de papeles ilíquidos. Sirve como número único de
mercado, tomando mediana, y nada más.

### La serie en pesos está contaminada

CCL implícito, hace un año contra hoy: AAPL +16,5%, MSFT +16,2%, TSLA +16,7%,
KO +13,5%. Un SMA200 sobre `AAPL.BA` es mitad gráfico de devaluación. **Los
indicadores van sobre el subyacente, nunca sobre la serie en ARS.**

### Se puede derivar de historia

Sobre 250 ruedas, 243 tienen las dos patas (BYMA y Nueva York). Las 7 que
faltan son feriados cruzados. Con 4 papeles sobre 124 días en común, la
dispersión del CCL implícito entre ellos tiene mediana 0,70% y peor caso 4,37%.

## 3. La idea central: valuar sin tipo de cambio

Un CEDEAR **es** contractualmente una fracción fija de la acción. Con ratio
`a:b` (a CEDEARs = b acciones):

```
valor_usd = cantidad × (b / a) × precio_usd_del_subyacente
```

No interviene ningún tipo de cambio. El ratio es una constante contractual
publicada por el depositario, no una estimación de mercado. El precio del
subyacente es un dato real en USD que el proyecto ya trae.

Conviene registrar por qué se descartó la alternativa obvia. Convertir el precio
en pesos con el CCL implícito **del mismo papel** es circular:

```
P_ars × a / CCL_implícito  ≡  (b/a) × P_usd
```

Devuelve exactamente el precio del subyacente. Sólo aportaría algo distinto un
CCL *de mercado* aplicado al precio en pesos, y lo que agregaría es el premio o
descuento de ese papel — del orden del 0,5%, a cambio de una dependencia de FX y
una fuente más que se puede caer. No vale la pena todavía.

## 4. Componentes

### `fa/data/cedears.json` — la tabla, versionada

Una entrada por CEDEAR:

```json
{
  "local": "AAPL",
  "yahoo": "AAPL.BA",
  "underlying": "AAPL",
  "cedears": 20,
  "shares": 1,
  "name": "APPLE INC",
  "isin_subyacente": "US0378331005",
  "supported": true,
  "reason": ""
}
```

Los dos lados del ratio se guardan **por separado**, nunca como un solo float.
Es lo que hace que `SID 1:8` funcione: un CEDEAR son ocho acciones, y un
`ratio: 8` ambiguo erraría por 64×. Son 14 los CEDEARs con el ratio invertido
(`1:2`, `1:3`, `1:4`, `1:8`).

Versionada en git a propósito: la app nunca depende de que Comafi esté en pie,
los canjes de ratio quedan en el historial, y los tests son deterministas.

Va **dentro del paquete**, no en `data/`. `data/*` está gitignoreado —es la base
SQLite— y además es un volumen Docker montado en `/app/data`, así que una tabla
ahí no se commitearía y en producción quedaría tapada por el volumen. El
Dockerfile copia `fa/`, y por ahí viaja.

### `scripts/update_cedears.py` — regenera la tabla

Baja Comafi, parsea la tabla de programas, traduce símbolos y **valida cada
subyacente contra el proveedor**, guardando la moneda que devolvió. Lo que no
resuelve, o no cotiza en USD, queda `supported: false` con el motivo escrito.

Que valide es la parte que importa: la tabla se publica sabiendo cuáles andan,
en vez de descubrirlo en producción mientras valúa la cartera.

**Corregido al implementar.** La primera corrida marcó 33 de 312 como
inutilizables, y la lista estaba mal: BK, MMC, WBA, ERJ y X son papeles vivos y
líquidos de NYSE que no devolvieron datos en la misma corrida donde AAPL
contestó normal. El validador consulta yfinance solo, mientras que la aplicación
consulta una cadena de tres, así que un silencio acá no dice nada sobre si Alpha
Vantage o Finnhub podían darlo.

Por eso el veredicto se parte en dos. `supported: false` sólo por causas
**estructurales**: el símbolo no se pudo traducir, o se lo vio cotizando en otra
moneda. Las dos son propiedades del instrumento. Un proveedor que simplemente no
contestó deja la entrada usable con `verified: false`, y el runtime se niega
honestamente en el momento en que de verdad no puede conseguir el precio. Eso
llevó la lista de no soportados de 33 a **7** — los listados de Frankfurt en
euros, que es lo que siempre debió ser.

Imprime un diff contra el JSON actual antes de escribir, así un cambio de ratio
se ve y se revisa antes de commitearlo. No corre solo ni desde la app.

### `fa/cedears.py` — el resolver

Carga el JSON una vez y expone:

```python
@dataclass(frozen=True)
class Cedear:
    local: str          # "AAPL"
    yahoo: str          # "AAPL.BA"
    underlying: str     # "AAPL"
    cedears: int        # a
    shares: int         # b
    name: str
    supported: bool
    reason: str

    @property
    def shares_per_cedear(self) -> float:
        return self.shares / self.cedears

def resolve(ticker: str) -> Cedear | None: ...
```

Sólo reconoce el sufijo `.BA`. Devuelve `None` para cualquier otra cosa, que es
lo que deja intacto el camino de una acción común.

### Reglas de traducción de símbolos

Comafi escribe el subyacente en notación Bloomberg. Medido sobre los 30 casos
donde el ticker local difiere del subyacente:

| Regla | Ejemplo | Resultado |
|---|---|---|
| Símbolo directo | `NOKA` → `NOK` | ✅ USD |
| `/` → `-` | `BRK/B` → `BRK-B` | ✅ 504,03 USD |
| sufijo ` US` → sacar | `VIST US` → `VIST` | ✅ 71,46 USD |
| sufijo ` LI` → `.IL` | `SMSN LI` → `SMSN.IL` | ✅ 4.758 USD (GDR en USD) |
| `.` de clase → `-` | `AKO.B` → `AKO-B` | ✅ 29,19 USD |
| sufijo ` GR` → `.DE` | `BAS GR` → `BAS.DE` | ❌ cotiza en **EUR** |
| ADR dado de baja | `OGZPY`, `LUKOY`, `NLMK`, `TEF` | ❌ sin datos |

Quedan **7 no soportados** de 312, todos cotizando en Frankfurt en euros: BASF,
Bayer, Danone, Deutsche Telekom, E.ON, Mercedes-Benz y NEC. Otros 25 quedan
soportados pero sin verificar, porque el proveedor no contestó por ellos el día
de la generación — entre ellos los ADRs rusos dados de baja por sanciones, que
probablemente no anden, y BK o MMC, que casi seguro sí.

No se les inventa un mapeo. Adivinar el sufijo Yahoo de un listado extranjero
es exactamente el tipo de dato fabricado que la regla del proyecto prohíbe. Un
CEDEAR en euros además necesitaría un segundo salto de FX, que es el problema
que este diseño existe para evitar.

Los otros 282 tienen ticker local igual al subyacente y no se verificaron uno
por uno; el script los valida al generar la tabla y el conteo final sale de ahí.

### `fa/market.py` — resolución explícita

`MarketService.quote("AAPL.BA")` resuelve y devuelve **el `Quote` del subyacente
tal cual**: `ticker="AAPL"`, `currency="USD"`. No lo disfraza de CEDEAR.

La sustitución tiene que ser visible, no callada. `Holding` lleva los dos lados
—`position.ticker="AAPL.BA"` y `quote.ticker="AAPL"`— y la interfaz muestra
ambos. Un programa que contesta sobre AAPL cuando le preguntaron por AAPL.BA sin
decirlo se volvió calladamente engañoso, que es justo lo que este código evita.

Un CEDEAR con `supported: false` levanta `DataUnavailableError` con el motivo, y
cae en el camino de `excluded` que ya existe. Igual que hoy una posición en
euros: reportada, nunca sumada a ciegas.

### `fa/portfolio.py` — la valuación

`Holding` gana `shares_per_unit` (1.0 para una acción común) y `cedear`, para
poder mostrar «12 CEDEARs de AAPL = 0,6 acciones».

```
market_value = precio_usd × cantidad × shares_per_unit
```

El P&L **no** puede usar `Position.unrealized(price)`: compara contra
`buy_price`, que para un CEDEAR está en pesos. Va contra el costo congelado.

## 5. El costo congelado — migración 14

La compra se carga como se hizo: cantidad en CEDEARs, precio en ARS,
`currency='ARS'`. El sistema deriva, **de la fecha de la operación**:

```
CCL_implícito = P_ars_cierre × cedears / (shares × P_usd_cierre)
costo_usd     = (cantidad × P_ars_pagado + comisiones) / CCL_implícito
```

Las dos patas son historia real de esa fecha. No se usa el tipo de cambio de hoy
para una compra de marzo.

Se guarda congelado —`transactions.fx_rate`, `transactions.usd_price`,
`positions.cost_basis_usd`— para que el P&L de una compra vieja no se mueva solo
cada vez que salta el dólar. El ledger sigue siendo la fuente de verdad: la
posición es un rollup de las transacciones, como en el resto del proyecto.

**Si falta cualquiera de las dos patas, se niega y pide el costo en USD a mano.**
Pasa en los feriados cruzados (7 de 250 ruedas) y en compras anteriores al año de
historia disponible. Rellenar con el CCL de hoy sería el número inventado que la
regla prohíbe; pedirlo es decir que no se sabe y explicar por qué.

## 6. Los dos caminos de valuación

Hay **dos**, y los dos hay que tocar:

| Camino | Quién lo usa | Cómo valúa |
|---|---|---|
| `fa/portfolio.py::build_portfolio` | CLI, `digest`, `actions`, menú | cotización en vivo vía `MarketService` |
| `fa/api/portfolio.py::_summary` | dashboard y APK | ledger + barras guardadas |

Además `fa/api/portfolio.py:393` **rechaza con 422** cualquier transacción que no
sea USD, así que hoy una compra de CEDEARs en pesos no entra ni por la puerta.
Esa validación pasa a aceptar ARS **sólo** cuando el ticker resuelve a un CEDEAR
soportado; para cualquier otro papel sigue rechazando igual que hoy.

## 7. Análisis y alertas

No se tocan. Todo resuelve al subyacente en `MarketService` y funciona sin
enterarse: técnicos, fundamentals, ratios, alertas, informes de IA. Y funciona
*mejor* que sobre la serie en pesos, por lo medido en la sección 2.

## 8. Lo que no se hace

data912 y dolarapi sirvieron para validar el diseño y **no quedan como
dependencia de la aplicación**. Sin especies C ni D, sin CCL de mercado, sin
premio/descuento del papel, sin instrumento de primera clase en el modelo.

Si mañana entran bonos, opciones o FCI, este diseño no estorba para llegar a un
`Position.instrument` genérico. Hoy sería armar el andamiaje para un solo tipo
de instrumento nuevo.

## 9. Pruebas

TDD, con un fixture chico en vez de las 312 filas reales: los tests tienen que
ser deterministas y no tocar la red.

Casos que van sí o sí:

- La inversión `1:8` — que `SID` valúe ocho acciones por CEDEAR, no un octavo.
- Las cuatro reglas de traducción de símbolos, incluida `BRK/B`.
- Un CEDEAR no soportado: queda excluido, con el motivo, y no rompe el total.
- La valuación de una posición CEDEAR contra el precio del subyacente.
- **La negativa a derivar el costo cuando falta una pata**, que es la regla dura.
- El parser de Comafi, contra un HTML guardado como fixture.

Sin tests que peguen contra la red ni contra la base real del usuario.

## 10. Alcance

| Archivo | Cambio |
|---|---|
| `fa/cedears.py` | nuevo — resolver y modelo |
| `fa/data/cedears.json` | nuevo — tabla versionada |
| `scripts/update_cedears.py` | nuevo — regenera y valida |
| `fa/market.py` | resolución en `quote`, `context`, `fundamentals` |
| `fa/portfolio.py` | valuación vía subyacente; `Holding.shares_per_unit` y `Holding.cedear` |
| `fa/models.py` | `Position.cost_basis_usd`, `Transaction.fx_rate`, `Transaction.usd_price` |
| `fa/store/schema.py` | las columnas nuevas, para bases creadas de cero |
| `fa/store/migrations.py` | migración 14 — costo congelado |
| `fa/store/positions.py` | leer y escribir `cost_basis_usd` |
| `fa/store/transactions.py` | leer y escribir `fx_rate` y `usd_price` |
| `fa/api/portfolio.py` | aceptar ARS para un CEDEAR; valuar en `_summary` |

**No toca** `analytics.py`, `indicators/`, `alerts/`, `metrics.py` ni `ai.py`:
todo eso recibe el subyacente ya resuelto y no se entera. Que la lista termine
ahí es la razón por la que se eligió la capa de traducción y no un instrumento
de primera clase en el modelo.

Corregido después de leer el código: una versión anterior de esta spec decía que
la API no se tocaba y ponía la tabla en `data/`. Las dos cosas eran falsas.

## 11. Riesgos

- **Comafi cambia el HTML.** Rompe `update_cedears.py`, no la aplicación: la
  tabla está versionada y sigue sirviendo. Se arregla el parser cuando pase.
- **Un canje de ratio sin actualizar la tabla** valúa mal, calladamente. Es el
  riesgo real de la tabla estática. Mitigación: el script imprime diff, y vale
  correrlo cada tanto. Un chequeo que compare el ratio guardado contra el
  implícito por precios y avise cuando se despeguen queda como trabajo futuro.
- **Ratios no enteros.** No se vieron: los 312 son `a:1` o `1:b` con enteros.
  Si aparece otra forma, el parser tiene que rechazarla, no redondearla.
