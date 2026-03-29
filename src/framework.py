"""
Framework de Integración Reutilizable
=======================================
Implementa patrones de resiliencia para integraciones HTTP salientes:
- Reintento con backoff exponencial y jitter
- Circuit breaker
- Timeout por petición
- Soporte de clave de idempotencia
- Configuración centralizada
- Logging estructurado unificado
- Propagación de trazas con OpenTelemetry

Filosofía de diseño: toda llamada saliente de un servicio pasa por este
framework. Los patrones de resiliencia se aplican una vez aquí, no dispersos
en cada punto de integración.
"""

import sys
import time
import random
import logging
import uuid
import json
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from urllib import request, error as urllib_error

# ---------------------------------------------------------------------------
# Configuración Centralizada
# ---------------------------------------------------------------------------
# Todos los parámetros ajustables viven en un solo lugar. En producción se
# cargarían desde variables de entorno o un servicio de configuración
# (propiedades de MuleSoft, AWS Parameter Store). Centralizarlos evita
# números mágicos dispersos y facilita el ajuste bajo condiciones de
# producción sin cambios de código.

@dataclass
class IntegrationConfig:
    # Número máximo de reintentos antes de rendirse.
    # Configurado en 3 basado en la ventana de fallo transitorio p99 observada:
    # la mayoría de errores transitorios se resuelven en 2 reintentos;
    # un 4to agrega latencia con retornos decrecientes.
    max_retries: int = 3

    # Tiempo de espera base en segundos para el primer reintento.
    # Elegido por encima de las duraciones típicas de GC del downstream (~200ms)
    # mientras mantiene la latencia percibida por el usuario aceptable.
    base_backoff_seconds: float = 0.5

    # Cap máximo de backoff. Sin cap, el crecimiento exponencial puede
    # producir esperas de minutos, inapropiado para flujos síncronos.
    max_backoff_seconds: float = 10.0

    # Factor de jitter (0.0–1.0). Agregar aleatoriedad al backoff evita el
    # "thundering herd": cuando múltiples clientes fallan simultáneamente
    # y reintentan en el mismo intervalo, crean picos de carga sincronizados
    # en el backend en recuperación. El jitter distribuye los reintentos.
    jitter_factor: float = 0.3

    # Timeout por petición en segundos. Fuerza fallo rápido en lugar de
    # bloquear un hilo indefinidamente. Debe ser siempre menor que el
    # timeout del servicio llamador.
    timeout_seconds: float = 5.0

    # Circuit breaker: cuántos fallos consecutivos antes de abrir el circuito.
    # Tras este umbral, las llamadas se rechazan inmediatamente sin tocar el
    # sistema downstream — protegiéndolo de sobrecarga durante la recuperación.
    circuit_breaker_failure_threshold: int = 5

    # Cuánto tiempo (segundos) permanece abierto el circuito antes de permitir
    # una llamada de prueba (estado semi-abierto). Suficientemente largo para
    # que el downstream se recupere, pero corto para retomar operación normal.
    circuit_breaker_recovery_seconds: float = 30.0

    # Nombre del servicio inyectado en cada entrada de log y span de traza.
    # Permite filtrar en dashboards de logging centralizado y trazas.
    service_name: str = "framework-integracion"


# Configuración global por defecto — puede sobreescribirse por instancia.
DEFAULT_CONFIG = IntegrationConfig()


# ---------------------------------------------------------------------------
# Logging Estructurado
# ---------------------------------------------------------------------------
# Todas las entradas de log son JSON estructurado con un esquema fijo. Esto
# es crítico para plataformas de agregación de logs (Splunk, ELK) — los logs
# en texto libre no pueden analizarse o consultarse de forma confiable a escala.
# Cada entrada incluye el nombre del servicio, trace ID y clave de idempotencia
# para correlación en sistemas distribuidos.

def _build_logger(service_name: str) -> logging.Logger:
    logger = logging.getLogger(service_name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def _log(logger: logging.Logger, level: str, event: str, **kwargs):
    """Emitir una entrada de log JSON estructurado."""
    entry = {
        "nivel": level,
        "evento": event,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **kwargs,
    }
    getattr(logger, level)(json.dumps(entry))


# ---------------------------------------------------------------------------
# Propagación de Trazas con OpenTelemetry
# ---------------------------------------------------------------------------
# En producción este módulo usaría el paquete opentelemetry-sdk para crear
# spans y propagar contexto de traza mediante headers W3C Trace Context
# (traceparent / tracestate). Aquí simulamos el concepto con una
# implementación liviana para demostrar el patrón sin agregar dependencias.
#
# El principio clave: toda petición HTTP saliente lleva el trace ID y span ID
# actuales en sus headers. Esto permite a la plataforma de observabilidad unir
# la cadena completa de llamadas entre servicios — desde el canal digital
# a través de las Process APIs de MuleSoft hasta Salesforce y de regreso.

@dataclass
class TraceContext:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_span_id: Optional[str] = None

    def to_headers(self) -> Dict[str, str]:
        """
        Producir headers W3C Trace Context para inyectar en peticiones salientes.
        Formato traceparent: 00-{trace_id}-{span_id}-01
        """
        return {
            "traceparent": f"00-{self.trace_id}-{self.span_id}-01",
            "tracestate": f"sura={self.span_id}",
        }

    def child_span(self) -> "TraceContext":
        """Crear un span hijo que hereda el trace ID actual."""
        return TraceContext(
            trace_id=self.trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=self.span_id,
        )


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------
# El patrón circuit breaker evita que un servicio downstream fallando sea
# bombardeado con peticiones durante su ventana de recuperación. Sin él,
# los reintentos de múltiples clientes pueden impedir que un servicio
# degradado se recupere (tormenta de reintentos / fallo en cascada).
#
# Máquina de estados:
#   CERRADO    → operación normal; los fallos se cuentan
#   ABIERTO    → las llamadas se rechazan inmediatamente (fallo rápido)
#   SEMI-ABIERTO → se permite una llamada de prueba; éxito → CERRADO, fallo → ABIERTO

class CircuitState(Enum):
    CLOSED = "cerrado"
    OPEN = "abierto"
    HALF_OPEN = "semi-abierto"


class CircuitBreaker:
    def __init__(self, config: IntegrationConfig, name: str):
        self.config = config
        self.name = name
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.logger = _build_logger(config.service_name)

    def allow_request(self) -> bool:
        """Retorna True si la llamada debe proceder, False para fallo rápido."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            elapsed = time.monotonic() - (self.last_failure_time or 0)
            if elapsed >= self.config.circuit_breaker_recovery_seconds:
                # Ventana de recuperación transcurrida — permitir una llamada de prueba
                self.state = CircuitState.HALF_OPEN
                _log(self.logger, "info", "circuit_breaker_semi_abierto",
                     circuito=self.name, segundos_transcurridos=round(elapsed, 2))
                return True
            return False  # Aún dentro de la ventana abierta — fallo rápido

        # SEMI-ABIERTO: dejar pasar la llamada de prueba
        return True

    def record_success(self):
        """Reiniciar el circuito tras una llamada exitosa."""
        if self.state != CircuitState.CLOSED:
            _log(self.logger, "info", "circuit_breaker_cerrado",
                 circuito=self.name, estado_anterior=self.state.value)
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None

    def record_failure(self):
        """Incrementar contador de fallos; abrir el circuito si se alcanza el umbral."""
        self.failure_count += 1
        self.last_failure_time = time.monotonic()

        if self.failure_count >= self.config.circuit_breaker_failure_threshold:
            self.state = CircuitState.OPEN
            _log(self.logger, "warning", "circuit_breaker_abierto",
                 circuito=self.name,
                 cantidad_fallos=self.failure_count,
                 segundos_recuperacion=self.config.circuit_breaker_recovery_seconds)


class CircuitBreakerOpenError(Exception):
    """Lanzado cuando una llamada es rechazada por un circuit breaker abierto."""
    pass


# ---------------------------------------------------------------------------
# Almacén de Idempotencia
# ---------------------------------------------------------------------------
# La idempotencia evita efectos secundarios duplicados cuando una petición
# se reintenta. Ejemplo: una solicitud de emisión de póliza que expiró pero
# que fue procesada por Salesforce no debe crear una segunda póliza al reintentar.
#
# Cada petición mutante lleva una clave de idempotencia (provista por el llamador
# o generada por el framework). El almacén registra qué claves fueron procesadas
# y sus resultados. Las peticiones duplicadas retornan el resultado cacheado.
#
# En producción este almacén sería un caché distribuido (Redis) compartido
# entre todos los workers de MuleSoft para manejar reintentos que llegan
# a instancias diferentes.

class IdempotencyStore:
    def __init__(self):
        # En producción: Redis o caché distribuido similar con TTL
        self._store: Dict[str, Dict[str, Any]] = {}

    def is_duplicate(self, key: str) -> bool:
        return key in self._store

    def get_cached_response(self, key: str) -> Optional[Dict[str, Any]]:
        return self._store.get(key)

    def store(self, key: str, response: Dict[str, Any]):
        self._store[key] = response


# Almacén singleton compartido entre instancias del cliente de integración
_idempotency_store = IdempotencyStore()


# ---------------------------------------------------------------------------
# Cliente de Integración — Punto de Entrada Principal
# ---------------------------------------------------------------------------
# Esta es la única interfaz que usa todo el código de integración para realizar
# llamadas HTTP salientes. Compone todos los patrones de resiliencia en el
# orden correcto:
#
#   1. Verificación de idempotencia (antes de cualquier llamada de red)
#   2. Verificación del circuit breaker (fallo rápido si está abierto)
#   3. Llamada HTTP con timeout
#   4. Reintento con backoff exponencial + jitter (en fallo transitorio)
#   5. Actualización del estado del circuit breaker (éxito o fallo)
#   6. Logging estructurado + propagación de traza en cada paso

class IntegrationClient:
    def __init__(
        self,
        config: IntegrationConfig = DEFAULT_CONFIG,
        circuit_name: str = "default",
    ):
        self.config = config
        self.circuit_breaker = CircuitBreaker(config, name=circuit_name)
        self.logger = _build_logger(config.service_name)

    def call(
        self,
        url: str,
        method: str = "GET",
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        idempotency_key: Optional[str] = None,
        trace_context: Optional[TraceContext] = None,
    ) -> Dict[str, Any]:
        """
        Ejecutar una llamada HTTP saliente con cobertura completa de patrones de resiliencia.

        Args:
            url: URL del endpoint destino
            method: Método HTTP (GET, POST, PUT, PATCH)
            body: Payload de la petición (se codificará como JSON)
            headers: Headers HTTP adicionales
            idempotency_key: Clave única para deduplicación. Requerida para
                             operaciones mutantes (POST, PUT, PATCH).
                             Si es None, no se realiza verificación de idempotencia.
            trace_context: Contexto de traza actual para propagación de span.
                           Si es None, se inicia una nueva traza raíz.

        Returns:
            Respuesta JSON parseada como dict.

        Raises:
            CircuitBreakerOpenError: Si el circuito está abierto.
            Exception: Si se agotan todos los reintentos.
        """
        # Iniciar o continuar una traza distribuida
        trace = trace_context or TraceContext()
        child_span = trace.child_span()

        # Verificación de idempotencia: si ya vimos esta clave, retornar resultado cacheado.
        # Maneja el caso donde el cliente reintenta una petición que fue procesada
        # exitosamente por el downstream (p. ej., después de un timeout).
        if idempotency_key and _idempotency_store.is_duplicate(idempotency_key):
            cached = _idempotency_store.get_cached_response(idempotency_key)
            _log(self.logger, "info", "cache_idempotencia_hit",
                 clave_idempotencia=idempotency_key,
                 trace_id=child_span.trace_id)
            return cached

        # Verificación del circuit breaker: rechazar inmediatamente si el circuito está abierto
        if not self.circuit_breaker.allow_request():
            _log(self.logger, "warning", "circuit_breaker_rechazado",
                 url=url, circuito=self.circuit_breaker.name,
                 trace_id=child_span.trace_id)
            raise CircuitBreakerOpenError(
                f"Circuito '{self.circuit_breaker.name}' está ABIERTO. "
                f"Llamada a {url} rechazada."
            )

        # Bucle de reintento con backoff exponencial y jitter
        last_exception = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self._http_call(url, method, body, headers, child_span)

                # Camino de éxito: reiniciar circuito, almacenar resultado de idempotencia
                self.circuit_breaker.record_success()
                if idempotency_key:
                    _idempotency_store.store(idempotency_key, response)

                _log(self.logger, "info", "llamada_exitosa",
                     url=url, metodo=method, intento=attempt,
                     trace_id=child_span.trace_id,
                     clave_idempotencia=idempotency_key)
                return response

            except Exception as exc:
                last_exception = exc
                self.circuit_breaker.record_failure()

                if attempt < self.config.max_retries:
                    wait = self._backoff_with_jitter(attempt)
                    _log(self.logger, "warning", "llamada_fallida_reintentando",
                         url=url, metodo=method, intento=attempt,
                         error=str(exc), espera_segundos=round(wait, 3),
                         trace_id=child_span.trace_id)
                    time.sleep(wait)
                else:
                    _log(self.logger, "error", "llamada_fallida_reintentos_agotados",
                         url=url, metodo=method, intentos=attempt + 1,
                         error=str(exc), trace_id=child_span.trace_id)

        raise last_exception

    def _http_call(
        self,
        url: str,
        method: str,
        body: Optional[Dict],
        extra_headers: Optional[Dict[str, str]],
        trace: TraceContext,
    ) -> Dict[str, Any]:
        """Ejecutar la petición HTTP con timeout e inyección de headers de traza."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Inyectar contexto de traza en toda petición saliente para que el
            # servicio receptor pueda continuar la traza distribuida.
            **trace.to_headers(),
            **(extra_headers or {}),
        }

        data = json.dumps(body).encode() if body else None
        req = request.Request(url, data=data, headers=headers, method=method)

        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                return json.loads(resp.read().decode())
        except urllib_error.HTTPError as exc:
            # Errores 5xx son transitorios y reintentables; 4xx no lo son
            if exc.code >= 500:
                raise Exception(f"HTTP {exc.code} de {url}: {exc.reason}")
            raise Exception(f"HTTP {exc.code} (no reintentable) de {url}: {exc.reason}")
        except urllib_error.URLError as exc:
            raise Exception(f"Error de red llamando a {url}: {exc.reason}")

    def _backoff_with_jitter(self, attempt: int) -> float:
        """
        Calcular la duración de espera para un intento de reintento dado.

        Fórmula: min(cap, base * 2^intento) + jitter_uniforme

        El componente exponencial asegura demoras crecientes entre reintentos.
        El componente de jitter (fracción aleatoria de la demora calculada)
        evita reintentos sincronizados de múltiples clientes — el problema del
        "thundering herd" que puede impedir a un backend en recuperación estabilizarse.
        """
        exponential = self.config.base_backoff_seconds * (2 ** attempt)
        capped = min(exponential, self.config.max_backoff_seconds)
        jitter = random.uniform(0, capped * self.config.jitter_factor)
        return capped + jitter
