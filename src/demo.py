"""
Servicio Demo — Prueba de Confiabilidad
=========================================
Un servicio pequeño que usa el framework IntegrationClient para llamar a un
upstream simulado inestable (Salesforce System API). Demuestra el comportamiento
de los patrones de resiliencia bajo condiciones de fallo.

Cómo ejecutar:
    python3 src/demo.py

No se requieren dependencias externas. El upstream se simula en el mismo proceso.
"""

import random
import threading
import time
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from framework import IntegrationClient, IntegrationConfig, TraceContext, CircuitBreakerOpenError

# ---------------------------------------------------------------------------
# Colores ANSI para output visual
# ---------------------------------------------------------------------------
class Color:
    VERDE    = "\033[92m"
    AMARILLO = "\033[93m"
    ROJO     = "\033[91m"
    AZUL     = "\033[94m"
    CYAN     = "\033[96m"
    GRIS     = "\033[90m"
    BOLD     = "\033[1m"
    RESET    = "\033[0m"

def ok(msg):      print(f"{Color.VERDE}  ✔ {msg}{Color.RESET}")
def warn(msg):    print(f"{Color.AMARILLO}  ⚠ {msg}{Color.RESET}")
def error(msg):   print(f"{Color.ROJO}  ✘ {msg}{Color.RESET}")
def info(msg):    print(f"{Color.AZUL}  ℹ {msg}{Color.RESET}")
def dim(msg):     print(f"{Color.GRIS}    {msg}{Color.RESET}")
def titulo(msg):  print(f"\n{Color.BOLD}{Color.CYAN}{msg}{Color.RESET}")
def separador():  print(f"{Color.GRIS}{'─' * 70}{Color.RESET}")

# ---------------------------------------------------------------------------
# Servidor Upstream Inestable Simulado
# ---------------------------------------------------------------------------
# Simula una Salesforce System API que se comporta de forma poco confiable:
#   - 30% de las peticiones tienen éxito (HTTP 200)
#   - 60% fallan transitoriamente (HTTP 503 Servicio No Disponible)
#   - 10% se demoran (sleep > timeout del cliente)

UPSTREAM_PORT = 9999
FAILURE_RATE = 0.6
TIMEOUT_RATE = 0.1

class FlakySalesforceHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        roll = random.random()
        if roll < TIMEOUT_RATE:
            dim("upstream → timeout simulado (8s)")
            time.sleep(8)
            self._respond(200, {"estado": "ok", "nota": "demasiado tarde"})
        elif roll < TIMEOUT_RATE + FAILURE_RATE:
            dim("upstream → 503 Servicio No Disponible")
            self._respond(503, {"error": "Servicio temporalmente no disponible"})
        else:
            dim("upstream → 200 OK")
            self._respond(200, {"id_poliza": "SF-POL-001", "estado": "emitida"})

    def do_POST(self):
        self.do_GET()

    def _respond(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(payload))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


def start_upstream():
    server = HTTPServer(("localhost", UPSTREAM_PORT), FlakySalesforceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------------------
# Configuración del demo
# ---------------------------------------------------------------------------
UPSTREAM_URL = f"http://localhost:{UPSTREAM_PORT}/v1/polizas"

demo_config = IntegrationConfig(
    max_retries=3,
    base_backoff_seconds=0.3,
    max_backoff_seconds=3.0,
    jitter_factor=0.2,
    timeout_seconds=5.0,
    circuit_breaker_failure_threshold=3,
    circuit_breaker_recovery_seconds=10.0,
    service_name="api-emision-polizas"
)

# Cliente compartido para que el circuit breaker acumule estado entre peticiones
_cliente = IntegrationClient(config=demo_config, circuit_name="salesforce-system-api")

def emitir_poliza(solicitud, clave_idempotencia, traza):
    return _cliente.call(
        url=UPSTREAM_URL,
        method="POST",
        body=solicitud,
        idempotency_key=clave_idempotencia,
        trace_context=traza,
    )


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
def run_demo():
    print(f"\n{Color.BOLD}{'═' * 70}{Color.RESET}")
    print(f"{Color.BOLD}  DEMO — Servicio de Emisión de Pólizas | Confiabilidad Bajo Fallo{Color.RESET}")
    print(f"{Color.BOLD}{'═' * 70}{Color.RESET}")
    print(f"\n{Color.GRIS}  Upstream : {int(FAILURE_RATE*100)}% fallos | {int(TIMEOUT_RATE*100)}% timeouts | {int((1-FAILURE_RATE-TIMEOUT_RATE)*100)}% éxito{Color.RESET}\n")

    # ── Escenario 1: Retry ──────────────────────────────────────────────────
    titulo("ESCENARIO 1 — Retry con Backoff y Jitter")
    separador()
    info("Enviamos 8 pólizas al upstream inestable. Cuando falla, el framework")
    info("reintenta automáticamente con esperas crecientes + jitter aleatorio.\n")

    resultados = {"exitosas": 0, "fallidas": 0, "circuito_abierto": 0}

    for i in range(1, 9):
        solicitud = {
            "id_cuenta": f"001-CUENTA-{i:03d}",
            "codigo_producto": "VIDA_COL",
            "prima": 150000 + (i * 10000),
        }
        clave_idem = f"demo-poliza-{i:03d}"
        traza = TraceContext()
        print(f"{Color.BOLD}  [{i}/8] cuenta {solicitud['id_cuenta']}  {Color.GRIS}trace: {traza.trace_id[:16]}...{Color.RESET}")

        try:
            respuesta = emitir_poliza(solicitud, clave_idem, traza)
            ok(f"EMITIDA  — id_poliza: {respuesta.get('id_poliza')} | intento registrado en log")
            resultados["exitosas"] += 1
        except CircuitBreakerOpenError:
            error("CIRCUITO ABIERTO — petición rechazada sin tocar Salesforce")
            resultados["circuito_abierto"] += 1
        except Exception as e:
            error(f"FALLIDA — reintentos agotados: {e}")
            resultados["fallidas"] += 1
        print()

    separador()
    print(f"\n  {Color.BOLD}Resumen:{Color.RESET}  "
          f"{Color.VERDE}✔ {resultados['exitosas']} exitosas{Color.RESET}  "
          f"{Color.ROJO}✘ {resultados['fallidas']} fallidas{Color.RESET}  "
          f"{Color.AMARILLO}⊘ {resultados['circuito_abierto']} circuito abierto{Color.RESET}\n")

    # ── Escenario 2: Circuit Breaker ────────────────────────────────────────
    titulo("ESCENARIO 2 — Circuit Breaker")
    separador()
    info("Forzamos el circuito enviando peticiones adicionales.")
    info("Tras 3 fallos consecutivos el circuito se ABRE y rechaza llamadas\n")
    info("inmediatamente — sin tocar Salesforce.\n")

    # Forzar fallos para abrir el circuito
    for i in range(9, 15):
        solicitud = {"id_cuenta": f"001-CUENTA-{i:03d}", "codigo_producto": "VIDA_COL", "prima": 200000}
        clave_idem = f"demo-poliza-{i:03d}"
        traza = TraceContext()
        print(f"{Color.BOLD}  [{i-8}/6] cuenta {solicitud['id_cuenta']}  {Color.GRIS}trace: {traza.trace_id[:16]}...{Color.RESET}")

        try:
            respuesta = emitir_poliza(solicitud, clave_idem, traza)
            ok(f"EMITIDA  — id_poliza: {respuesta.get('id_poliza')}")
            resultados["exitosas"] += 1
        except CircuitBreakerOpenError:
            error("CIRCUITO ABIERTO — fallo rapido, Salesforce protegido")
            resultados["circuito_abierto"] += 1
        except Exception as e:
            warn(f"FALLIDA (reintentos agotados) — acumulando fallos en circuit breaker")
            resultados["fallidas"] += 1
        print()

    estado = _cliente.circuit_breaker.state.value
    color_estado = Color.ROJO if estado == "abierto" else Color.VERDE
    separador()
    print(f"\n  Estado del circuit breaker: {color_estado}{Color.BOLD}{estado.upper()}{Color.RESET}\n")

    # ── Escenario 3: Idempotencia ───────────────────────────────────────────
    titulo("ESCENARIO 3 — Idempotencia")
    separador()
    info("Repetimos una petición ya procesada con la misma clave.")
    info("El framework retorna el resultado cacheado — Salesforce no recibe nada.\n")

    traza_idem = TraceContext()
    print(f"{Color.BOLD}  Re-enviando demo-poliza-001 (ya emitida en Escenario 1){Color.RESET}")
    print(f"{Color.GRIS}  trace: {traza_idem.trace_id[:16]}... (nuevo trace, misma clave de idempotencia){Color.RESET}")
    try:
        respuesta = emitir_poliza(
            {"id_cuenta": "001-CUENTA-001", "codigo_producto": "VIDA_COL", "prima": 160000},
            clave_idempotencia="demo-poliza-001",
            traza=traza_idem,
        )
        ok(f"RESPUESTA DESDE CACHE — {respuesta}")
        info("Salesforce no recibió ninguna llamada. Sin póliza duplicada.")
    except Exception as e:
        warn(f"Petición original también falló, nada en caché: {e}")

    # ── Resumen final ───────────────────────────────────────────────────────
    print(f"\n{Color.BOLD}{'═' * 70}{Color.RESET}")
    print(f"{Color.BOLD}  PATRONES DEMOSTRADOS{Color.RESET}")
    print(f"{Color.BOLD}{'═' * 70}{Color.RESET}\n")
    print(f"  {Color.VERDE}✔{Color.RESET}  Retry con backoff exponencial + jitter")
    print(f"  {Color.VERDE}✔{Color.RESET}  Timeout por petición (5s)")
    print(f"  {Color.VERDE}✔{Color.RESET}  Circuit breaker — aislamiento del upstream en fallo")
    print(f"  {Color.VERDE}✔{Color.RESET}  Idempotencia — sin duplicados al reintentar")
    print(f"  {Color.VERDE}✔{Color.RESET}  Logging JSON estructurado con trace ID")
    print(f"  {Color.VERDE}✔{Color.RESET}  Propagacion de traza W3C en cada llamada")
    print(f"{Color.BOLD}{'═' * 70}{Color.RESET}\n")


if __name__ == "__main__":
    server = start_upstream()
    time.sleep(0.2)
    try:
        run_demo()
    finally:
        server.shutdown()
