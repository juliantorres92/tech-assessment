# Sección C – Servicio Demo y Prueba de Confiabilidad

---

## Visión General

El servicio demo simula una **Process API de Emisión de Pólizas** que llama a una **Salesforce System API** usando el framework de integración de la Sección B. El sistema upstream es intencionalmente inestable — simula las condiciones de fallo que una integración de seguros real enfrenta en producción.

El demo no requiere dependencias externas y se ejecuta con un solo comando.

---

## Cómo Ejecutarlo

**Requisitos:** Python 3.8+

```bash
# Desde la raíz del repositorio
python src/demo.py
```

Sin paquetes adicionales. El servidor upstream inestable corre en el mismo proceso en el puerto 9999.

---

## Qué Demuestra el Demo

### Arquitectura

```
Servicio Demo (API de Emisión de Pólizas)
    │
    ├── IntegrationClient (framework.py)
    │       ├── Verificación de idempotencia
    │       ├── Verificación del circuit breaker
    │       ├── Llamada HTTP con timeout (5s)
    │       ├── Reintento + backoff exponencial + jitter
    │       └── Logging estructurado + propagación de traza
    │
    └──> Upstream Inestable (Salesforce System API simulada :9999)
              ├── 30% éxito (HTTP 200)
              ├── 60% fallo transitorio (HTTP 503)
              └── 10% timeout (respuesta después de 8s > timeout cliente 5s)
```

---

## Escenarios de Fallo Demostrados

### Escenario 1 — Reintento bajo Fallos Transitorios

El upstream retorna HTTP 503 de forma aleatoria. El framework reintenta hasta 3 veces con backoff exponencial + jitter antes de rendirse.

**Ejemplo de salida en consola:**
```json
{"nivel": "advertencia", "evento": "llamada_fallida_reintentando",
 "url": "http://localhost:9999/v1/polizas",
 "intento": 0, "error": "HTTP 503 de ...", "espera_segundos": 0.52, "trace_id": "4bf92f..."}

{"nivel": "advertencia", "evento": "llamada_fallida_reintentando",
 "url": "http://localhost:9999/v1/polizas",
 "intento": 1, "error": "HTTP 503 de ...", "espera_segundos": 1.18, "trace_id": "4bf92f..."}

{"nivel": "info", "evento": "llamada_exitosa",
 "url": "http://localhost:9999/v1/polizas", "intento": 2, "trace_id": "4bf92f..."}
```

**Cómo la resiliencia mitiga el problema:** El cliente reintenta automáticamente. El servicio llamador recibe una respuesta exitosa sin conocer los fallos subyacentes. El backoff progresivo le da tiempo al upstream para recuperarse entre intentos.

---

### Escenario 2 — Timeout por Petición

El upstream duerme por 8 segundos. El cliente aplica un timeout de 5 segundos, falla rápido y reintenta.

**Cómo la resiliencia mitiga el problema:** Sin timeout, el hilo del cliente se bloquearía por 8+ segundos — violando el SLA del servicio llamador (p. ej., un usuario web esperando una cotización). El timeout fuerza un fallo rápido, permitiendo que el reintento potencialmente alcance un worker sano del upstream.

---

### Escenario 3 — Circuit Breaker se Abre

Tras 3 fallos consecutivos (umbral del demo), el circuito se abre. Las llamadas posteriores se rechazan inmediatamente sin tocar el upstream.

**Ejemplo de salida en consola:**
```json
{"nivel": "advertencia", "evento": "circuit_breaker_abierto",
 "circuit": "salesforce-system-api",
 "cantidad_fallos": 3, "segundos_recuperacion": 10.0}

{"nivel": "advertencia", "evento": "circuit_breaker_rechazado",
 "url": "http://localhost:9999/v1/polizas",
 "circuit": "salesforce-system-api", "trace_id": "7c3a1f..."}
```

**Cómo la resiliencia mitiga el problema:** El circuit breaker deja de golpear el upstream que ya está fallando, dándole espacio para recuperarse. El llamador recibe un `CircuitBreakerOpenError` rápido y predecible — en lugar de esperar múltiples timeouts y reintentos.

---

### Escenario 4 — Acierto en Caché de Idempotencia

Una petición con una clave de idempotencia previamente exitosa es reenviada. El framework retorna la respuesta cacheada sin realizar ninguna llamada de red.

**Ejemplo de salida en consola:**
```json
{"nivel": "info", "evento": "cache_idempotencia_hit",
 "clave_idempotencia": "demo-poliza-001", "trace_id": "4bf92f..."}
```

**Cómo la resiliencia mitiga el problema:** En producción, un cliente móvil podría reintentar una solicitud de emisión de póliza tras una caída de red. Sin idempotencia, Salesforce crearía una póliza duplicada. La clave de idempotencia garantiza que la operación se ejecute exactamente una vez — incluso a través de múltiples reintentos.

---

## Salida de Observabilidad

Cada evento produce una entrada de log JSON estructurado visible en consola. En producción, estas entradas fluyen a la plataforma centralizada de agregación de logs. El campo `trace_id` vincula todas las entradas de log de una misma cadena de petición — a través de reintentos, servicios y límites síncronos/asíncronos.

---

## Equivalentes en Producción

| Componente del Demo | Equivalente en Producción |
|---|---|
| Servidor HTTP inestable en proceso | Salesforce System API bajo degradación |
| Almacén de idempotencia en memoria | Caché distribuido Redis con TTL |
| Contexto de traza con `print()` | OpenTelemetry SDK → Collector → Jaeger/Datadog |
| Proceso Python único | Worker de MuleSoft CloudHub con múltiples réplicas |
| `CircuitBreakerOpenError` | Error handler de MuleSoft retornando HTTP 503 + header Retry-After |
