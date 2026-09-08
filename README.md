# Reconciliación bancaria automática

**Compara el extracto del banco contra los libros y te dice, en segundos,
exactamente qué no cuadra.**

---

## El problema

Cada mes se repite lo mismo. Abres el extracto del banco de un lado, el
libro contable del otro, y empiezas a tachar líneas: este depósito sí, esta
comisión no la registré, este cheque todavía no salió.

Con 50 movimientos son un par de horas. Con 200, es media jornada. Y es
trabajo donde un solo renglón mal leído se arrastra hasta el cierre.

Lo peor es que **no es trabajo difícil, es trabajo repetitivo**. No requiere
tu criterio profesional; requiere paciencia. Tu criterio hace falta después,
cuando aparece la diferencia y hay que decidir qué se hace con ella.

## Qué hace esto

Toma los dos archivos, los compara, y te entrega un Excel con las
diferencias ya clasificadas por tipo. Lo que cuadra desaparece de tu vista.
Lo que no cuadra queda separado según qué tipo de problema es.

Tres detalles que marcan la diferencia respecto de una comparación con
fórmulas de Excel:

- **Entiende que el banco se demora.** Un pago que registraste el 15 y el
  banco acreditó el 17 es el mismo pago. No aparece como dos problemas.
- **No inventa parejas.** Si una comisión de $15 y un cobro de $3.200
  cayeron el mismo día, no los cruza para "cerrar" el día. Los deja como lo
  que son.
- **Te dice cuánto falta explicar.** El reporte trae un control: la
  diferencia total entre banco y libro tiene que quedar explicada al
  centavo por las partidas que lista. Si no cierra, te avisa.

## Qué recibes cada mes

Un Excel con seis pestañas:

| Pestaña | Qué contiene | Qué haces con eso |
|---|---|---|
| **Resumen** | Totales por cuenta y de qué se compone la diferencia | Lo primero que miras |
| **Coincidencias** | Todo lo que cuadró | No lo miras. Está por si acaso |
| **Discrepancias** | Mismo movimiento, distinto monto | Revisas cuál de los dos está bien |
| **Solo en banco** | Cargos que el banco hizo y no están registrados | Los das de alta |
| **Solo en libro** | Asientos que el banco todavía no refleja | Verificas si están en tránsito |
| **Filas con problemas** | Renglones que no se pudieron leer | Los corriges en el archivo |

Funciona con **varias cuentas a la vez** (corriente, ahorros, la del socio)
en un mismo reporte, cada una conciliada por separado. Y el reporte sale en
**español o inglés**, según a quién se lo entregues.

## Cómo se ve el servicio mensual

**Día 1 — tú subes dos archivos.** El extracto que descargaste del banco y
la exportación de tu software contable. Tal cual salen. No hace falta
limpiarlos: entiende `$1,450.00`, `1.450,00`, negativos entre paréntesis y
encabezados en español o inglés.

**Minutos después — recibes el reporte.** Con las diferencias ya separadas
por tipo y los totales cuadrados.

**Tú haces lo que sabes hacer.** Revisas quince renglones en vez de
doscientos, y decides qué corresponde con cada uno.

**Yo mantengo la herramienta.** Si tu banco cambia el formato del export,
si sumas una cuenta nueva, si quieres una regla distinta (otro umbral, otra
moneda, categorías propias), lo ajusto. No es un programa que compras: es
un servicio que se adapta a cómo trabajas.

### Inversión

Entre **$150 y $400 al mes** según el volumen y la cantidad de cuentas.
Incluye el mantenimiento, los ajustes y el soporte.

La cuenta a hacer es simple: si te ahorra media jornada al mes y tu hora
vale más que eso, se paga solo. Si además evita un error que llega al
cierre, se paga varias veces.

## Qué NO hace

Vale la pena ser claro, para que sepas qué esperar:

- **No decide por ti.** Marca la diferencia; el criterio de qué hacer con
  ella es tuyo.
- **No se conecta solo a tu banco.** Trabaja sobre los archivos que
  descargas. Conectar la API del banco es posible, pero es otro proyecto.
- **No adivina.** Si dos movimientos no se parecen lo suficiente, los deja
  como partidas sueltas en vez de emparejarlos por conveniencia.
- **No lee escaneos.** Trabaja con CSV y Excel, no con fotos del extracto.

---

# Documentación técnica

<details>
<summary>Instalación, uso y detalles de implementación</summary>

## Instalación

```bash
pip install -r requirements.txt      # solo lo necesario para correr
pip install -r requirements-dev.txt  # además, los tests
```

## Uso

```bash
python reconcile.py                  # usa los CSV de ejemplo de data/
```

Con archivos propios:

```bash
python reconcile.py \
  --bank extracto_agosto.csv \
  --ledger libro_agosto.csv \
  --out reports/agosto.xlsx \
  --lang en
```

Todas las opciones: `python reconcile.py --help`

| Opción | Para qué sirve | Default |
|---|---|---|
| `--lang` | Idioma del reporte (`es` / `en`) | `es` |
| `--date-window` | Días de desfase tolerados entre banco y libro | `3` |
| `--amount-tolerance` | Diferencia máxima para considerar dos montos iguales | `0.01` |
| `--discrepancy-abs` | Diferencia absoluta máxima para reportar discrepancia | `50.00` |
| `--discrepancy-pct` | Diferencia porcentual máxima para lo mismo | `5.0` |
| `--fail-on-exceptions` | Código de salida 2 si hay ítems por revisar | off |
| `--quiet` | No imprimir el reporte en consola | off |
| `--json-logs` | Logs en JSON para Cloud Logging | off |

**Códigos de salida**: `0` todo bien · `1` error de entrada o escritura ·
`2` corrió bien pero hay ítems para revisar (solo con `--fail-on-exceptions`).

## Formato de entrada

CSV o Excel. Se aceptan encabezados en inglés o español (`date`/`fecha`,
`amount`/`monto`/`importe`, `description`/`concepto`...).

| Columna | Extracto | Libro |
|---|---|---|
| `date` | obligatoria | obligatoria |
| `description` | obligatoria | obligatoria |
| `amount` | obligatoria | obligatoria |
| `account` | opcional | opcional |
| `invoice_ref` | — | opcional |

- **Montos**: se interpretan `$1,450.00`, `1.450,00`, `(89.50)` y `89.50-`.
- **`account`**: si se usa, tiene que estar en **ambos** archivos. La
  conciliación corre por separado dentro de cada cuenta, nunca cruzada.
- **Filas ilegibles**: van a la pestaña *Filas con problemas* con el número
  de fila del archivo original. No se descartan en silencio.

## Cómo empareja

1. **Coincidencias exactas**: mismo monto (± `--amount-tolerance`) dentro de
   la ventana de fechas.
2. **Discrepancias**: sobre lo que quedó suelto, empareja movimientos del
   **mismo signo** cuya diferencia cae dentro del umbral (el mayor entre
   `--discrepancy-abs` y `--discrepancy-pct`).
3. Lo que no cae en ninguna fase queda como partida suelta.

En ambas fases los candidatos se evalúan de mejor a peor (menor diferencia
de monto, luego menor desfase) y se asignan uno a uno. Eso evita cruzar una
comisión de $15 con un cobro de $3.200 solo porque cayeron el mismo día.

Los totales de control se suman con `Decimal`, no con coma flotante: la
columna *Sin explicar* del Resumen debe dar `0.00` exacto.

## Tests

```bash
python -m pytest
```

106 tests sobre parseo de montos, validación de entrada, motor de matching,
resumen, localización y CLI.

## Estructura

```
reconcile.py              CLI y pipeline (run())
reconciliation/
  loader.py               lectura, normalización y validación de entrada
  matching.py             motor de reconciliación
  report.py               resumen, salida por consola y Excel
  i18n.py                 etiquetas en español e inglés
gcp/
  main.py                 Cloud Function (trigger por archivo)
  job.py                  Cloud Run Job (programado)
  Dockerfile              imagen del job
tests/                    106 tests con pytest
data/                     CSV de ejemplo (sintéticos)
```

## Despliegue en GCP

Ver **[DEPLOY.md](DEPLOY.md)**: comandos `gcloud` para Cloud Function
(trigger por archivo) y Cloud Run Job (programado), con alertas por Pub/Sub
y estimación de costos.

`run()` en [reconcile.py](reconcile.py) concentra el pipeline y no depende
de argparse, así que la Cloud Function lo invoca directamente.

</details>

---

> Los datos reales de un cliente van en `data/private/`, que está en
> `.gitignore`. Los CSV de `data/` son sintéticos.
