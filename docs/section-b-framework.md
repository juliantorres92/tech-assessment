# Sección B – Framework de Integración Reutilizable

---

## Visión General

El framework de integración es un módulo Python reutilizable ([`src/framework.py`](../src/framework.py)) que provee un punto de entrada único para todas las llamadas HTTP salientes de un servicio. Toda integración pasa por este cliente — los patrones de resiliencia se aplican una sola vez aquí, sin duplicarlos en cada punto de integración.

---

## Componentes

### 1. Configuración Centralizada (`IntegrationConfig`)

Todos los parámetros ajustables se declaran en un único dataclass. En producción, estos valores se cargan desde variables de entorno o un servicio de configuración (propiedades de MuleSoft, AWS Parameter Store, etc.).

```python
config = IntegrationConfig(
    max_retries=3,
    base_backoff_seconds=0.5,
    max_backoff_seconds=10.0,
    jitter_factor=0.3,
    timeout_seconds=5.0,
    circuit_breaker_failure_threshold=5,
    circuit_breaker_recovery_seconds=30.0,
    service_name="api-emision-polizas"
)
```

**Decisión de diseño:** Centralizar la configuración evita números mágicos dispersos en el código y permite ajuste en runtime sin cambios de código.

---

### 2. Reintento con Backoff Exponencial y Jitter

**Fórmula:** `espera = min(cap, base × 2^intento) + jitter_aleatorio`

| Intento | Demora base | Con cap | + Jitter (30%) | Espera aprox. |
|---|---|---|---|---|
| 0 (1er reintento) | 0.5s | 0.5s | ±0.15s | ~0.5–0.65s |
| 1 (2do reintento) | 1.0s | 1.0s | ±0.30s | ~1.0–1.30s |
| 2 (3er reintento) | 2.0s | 2.0s | ±0.60s | ~2.0–2.60s |

**Por qué backoff exponencial:** Las demoras progresivas le dan al sistema downstream tiempo creciente para recuperarse entre intentos. Un intervalo de reintento fijo puede sobrecargar un backend en recuperación parcial.

**Por qué jitter:** Cuando múltiples clientes fallan simultáneamente (p. ej., una interrupción breve de red), todos reintentan en el mismo intervalo si no hay jitter — creando picos de carga sincronizados que impiden al backend en recuperación estabilizarse. El jitter distribuye los reintentos en el tiempo, suavizando la carga.

**Por qué 3 reintentos:** Basado en las ventanas de fallo transitorio observadas — la mayoría de errores transitorios se resuelven en 2 reintentos. Un 4to reintento agrega latencia sin beneficio proporcional y puede comprometer el SLO del servicio llamador.

---

### 3. Circuit Breaker

Máquina de tres estados que protege los sistemas downstream de sobrecarga durante ventanas de fallo:

```
CERRADO ──(umbral de fallos)──> ABIERTO ──(ventana de recuperación)──> SEMI-ABIERTO
   ^                                                                          |
   └────────────────────(llamada de prueba exitosa)────────────────────────────┘
```

| Estado | Comportamiento |
|---|---|
| **Cerrado** | Operación normal. Los fallos se cuentan. |
| **Abierto** | Todas las llamadas se rechazan inmediatamente (fallo rápido). El sistema downstream descansa. |
| **Semi-abierto** | Se permite una llamada de prueba. Éxito → Cerrado. Fallo → Abierto. |

**Por qué el circuit breaker es importante:** Sin él, las tormentas de reintentos de múltiples clientes golpean un sistema downstream en fallo — impidiéndole recuperarse. El circuit breaker rompe este ciclo deteniendo las llamadas completamente durante la ventana de recuperación.

**Configuración:** 5 fallos → Abierto; 30s de ventana de recuperación antes de la llamada de prueba Semi-abierto.

---

### 4. Timeout por Petición

Cada llamada HTTP está acotada por un timeout configurable (por defecto: 5 segundos). Esto evita que un sistema downstream lento bloquee indefinidamente un hilo — crítico en flujos síncronos orientados al usuario, donde el presupuesto total de tiempo de respuesta está acotado por el SLA del servicio llamador.

**Decisión de diseño:** El timeout siempre se configura menor que el timeout del servicio llamador, para garantizar que el framework pueda manejar el fallo con gracia antes de que venza el plazo del llamador.

---

### 5. Soporte de Clave de Idempotencia

Las operaciones mutantes (POST, PUT, PATCH) aceptan una clave de idempotencia. El framework almacena las respuestas exitosas indexadas por ese valor. Las peticiones duplicadas retornan la respuesta cacheada inmediatamente sin hacer una llamada de red.

```python
respuesta = cliente.call(
    url="https://salesforce-api/polizas",
    method="POST",
    body={"account_id": "001...", "producto": "vida"},
    idempotency_key="emision-poliza-req-abc123"
)
```

**Por qué la idempotencia es esencial para el reintento:** Sin ella, una petición que expiró por timeout pero que fue procesada por el sistema downstream (p. ej., Salesforce creó la póliza) generaría un registro duplicado al reintentar. La clave de idempotencia hace que los reintentos sean seguros.

**Implementación en producción:** El almacén sería un caché distribuido (Redis) compartido entre todos los workers de MuleSoft, con un TTL que coincide con la ventana máxima esperada de reintentos.

---

### 6. Logging Estructurado Unificado

Cada entrada de log es un objeto JSON con un esquema consistente:

```json
{
  "nivel": "advertencia",
  "evento": "llamada_fallida_reintentando",
  "timestamp": "2026-03-28T19:00:00Z",
  "url": "https://salesforce-api/polizas",
  "metodo": "POST",
  "intento": 1,
  "error": "HTTP 503 de ...: Servicio no disponible",
  "espera_segundos": 1.23,
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"
}
```

**Por qué logging estructurado:** Los logs en texto libre no pueden ser analizados o consultados de forma confiable en plataformas de agregación de logs (Splunk, ELK). Un esquema JSON fijo permite filtrar por `trace_id`, `clave_idempotencia` o tipo de `evento` en millones de entradas — esencial para el diagnóstico de incidentes.

---

### 7. Propagación de Trazas con OpenTelemetry

Cada petición saliente lleva headers W3C Trace Context (`traceparent`, `tracestate`). Esto permite a la plataforma de observabilidad unir toda la cadena de llamadas — desde el canal digital a través de las Process APIs de MuleSoft hasta Salesforce y de regreso — incluso a través de límites de mensajería asíncrona.

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
tracestate:  sura=00f067aa0ba902b7
```

**En producción:** Reemplazar la clase `TraceContext` liviana con el paquete `opentelemetry-sdk`. El framework crea un span hijo para cada llamada saliente, registra estado y latencia, y exporta los spans al OpenTelemetry Collector (que alimenta Jaeger, Datadog o similar).

---

## Ejemplo de Uso

```python
from framework import IntegrationClient, IntegrationConfig, TraceContext

config = IntegrationConfig(
    max_retries=3,
    timeout_seconds=5.0,
    circuit_breaker_failure_threshold=5,
    service_name="api-emision-polizas"
)

cliente = IntegrationClient(config=config, circuit_name="salesforce-system-api")
traza = TraceContext()  # Span raíz para esta petición

respuesta = cliente.call(
    url="https://salesforce-system-api/polizas",
    method="POST",
    body={"account_id": "001ABC", "codigo_producto": "VIDA_COL"},
    idempotency_key="emision-2026-03-28-001ABC-VIDA",
    trace_context=traza
)
```

---

## Resumen de Decisiones de Diseño

| Decisión | Justificación |
|---|---|
| Único punto de entrada `IntegrationClient` | Patrones de resiliencia aplicados una vez, no duplicados por integración |
| Backoff exponencial con jitter | Evita thundering herd; da al sistema downstream tiempo progresivo de recuperación |
| Circuit breaker separado del reintento | El reintento maneja errores transitorios; el circuit breaker maneja interrupciones sostenidas — modos de fallo distintos requieren respuestas distintas |
| Idempotencia a nivel de framework | Reintentos seguros para todas las operaciones mutantes sin que el llamador lo gestione |
| Logging JSON estructurado | Logs legibles por máquina para consultas confiables en plataformas de agregación |
| Headers W3C Trace Context | Formato de propagación estándar; compatible con todas las plataformas de observabilidad principales |
| Almacén de idempotencia en memoria (demo) | Equivalente en producción: Redis con TTL; misma interfaz, respaldo distribuido |
