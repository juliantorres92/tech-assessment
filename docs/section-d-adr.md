# Sección D – Registro de Decisión Técnica

---

## ADR-001: Plataforma de Integración Centralizada vs. Integraciones Descentralizadas por Equipo

**Fecha:** 2026-03-28
**Estado:** Aceptado
**Decisores:** Líder Técnico, Equipo de Arquitectura

---

### Contexto

Sura opera un Canal Digital Directo multi-país donde toda petición entrante y saliente fluye a través de una capa de middleware centralizada (MuleSoft Anypoint Platform) que media entre Salesforce (sistema de registro central de seguros) y sistemas externos, socios y canales digitales.

A medida que la organización escala en países y líneas de producto, los equipos enfrentan una presión creciente por entregar integraciones más rápido. Surgen dos modelos en competencia:

- **Opción A:** Mantener y fortalecer la plataforma de integración centralizada de MuleSoft, gestionada por un equipo dedicado de integración.
- **Opción B:** Permitir que los equipos de producto sean dueños y construyan sus propias integraciones directamente, reduciendo la dependencia de un equipo central.

Esta decisión tiene impacto directo en velocidad de entrega, gobierno, resiliencia operativa y mantenibilidad a largo plazo.

---

### Opciones Consideradas

#### Opción A — Plataforma de Integración Centralizada (API-Led de MuleSoft)

Todas las integraciones se construyen, despliegan y operan a través de MuleSoft Anypoint Platform siguiendo una arquitectura API-led de tres capas:
- **Experience APIs:** Adaptadores específicos por canal (móvil, web, portales de corredores)
- **Process APIs:** Orquestación y lógica de negocio
- **System APIs:** Conectores a Salesforce, sistemas core de seguros y proveedores externos

Un equipo dedicado de integración es dueño de la plataforma, hace cumplir los estándares y publica assets reutilizables en Anypoint Exchange.

**Ventajas:**
- Punto único de cumplimiento para seguridad, rate limiting y observabilidad
- Assets de API reutilizables reducen la duplicación entre países
- Logging, trazas y manejo de errores consistentes en todas las integraciones
- Cumplimiento y auditabilidad más sencillos (requisitos regulatorios del sector asegurador)
- Patrones de resiliencia (reintento, circuit breaker, idempotencia) aplicados una vez, heredados en todas partes

**Desventajas:**
- El equipo central se convierte en cuello de botella para los equipos de producto de alta velocidad
- Requiere fuerte gobierno de diseño de API y disciplina de versionamiento
- Mayor inversión inicial en experiencia en la plataforma y herramientas

---

#### Opción B — Integraciones Descentralizadas por Equipo

Cada equipo de producto construye y opera sus propias integraciones usando la tecnología de su elección. Los equipos son responsables de su propia confiabilidad, seguridad y observabilidad.

**Ventajas:**
- Los equipos se mueven más rápido sin esperar a un equipo central
- Flexibilidad tecnológica por contexto de equipo
- Menor dependencia organizacional

**Desventajas:**
- Observabilidad fragmentada — sin vista unificada del estado del sistema
- Brechas de seguridad y cumplimiento entre equipos de diferente madurez
- Duplicación de lógica de integración entre países y canales
- Patrones de resiliencia implementados de forma inconsistente o inexistente
- Alto costo operativo: N equipos operando N stacks de integración
- En una industria regulada (seguros), la propiedad descentralizada incrementa significativamente la complejidad de auditoría

---

### Decisión

**Recomendamos la Opción A — Plataforma de Integración Centralizada**, con ajustes tácticos específicos para reducir cuellos de botella.

Justificación:
1. **El contexto regulatorio exige gobierno.** Las operaciones de seguros en múltiples países requieren pistas de auditoría consistentes, controles de soberanía de datos y cumplimiento de seguridad. Una plataforma centralizada provee un único perímetro de cumplimiento.
2. **La resiliencia a escala requiere consistencia.** Patrones como circuit breakers, idempotencia y reintento con backoff exponencial deben aplicarse de manera uniforme. La propiedad descentralizada produce resiliencia inconsistente — algunos equipos la implementan, otros no.
3. **La reutilización reduce el costo total.** Las System APIs de Salesforce y las Process APIs del core de seguros, construidas una vez, pueden reutilizarse en todos los países y canales. La descentralización las reconstruye para cada equipo.
4. **El cuello de botella es resoluble sin descentralizar.** El dolor real es la velocidad de los equipos, no el modelo centralizado en sí. La solución es un **modelo de contribución abierta interna**: los equipos de producto contribuyen a la plataforma de integración mediante patrones de autoservicio gobernados, plantillas publicadas y conectores reutilizables — mientras el equipo central se enfoca en confiabilidad de la plataforma y gobierno, no en atender tickets de cada equipo.

---

### Consecuencias

**Positivas:**
- Observabilidad unificada en todos los flujos de integración
- Postura de resiliencia y seguridad consistente
- Reducción de duplicación de conectores de Salesforce y sistemas core
- Reportes de cumplimiento más sencillos entre países

**Negativas / Mitigaciones:**
- El equipo central debe actuar como equipo de soporte, no de control — proveer plantillas, aceleradores y patrones de autoservicio a los equipos de producto
- El gobierno de la plataforma debe ser liviano — los procesos de revisión pesados anulan el propósito
- Requiere inversión en expertise de MuleSoft y librería de assets en Anypoint Exchange

---
---

## ADR-002: Arquitectura Event-Driven vs. Solicitud-Respuesta Síncrona para Flujos Críticos

**Fecha:** 2026-03-28
**Estado:** Aceptado
**Decisores:** Líder Técnico, Equipo de Arquitectura

---

### Contexto

El Canal Digital Directo maneja múltiples tipos de flujos con características distintas:

- **Generación de cotización:** El usuario solicita una cotización de seguro en tiempo real — espera una respuesta en menos de 3 segundos
- **Emisión de póliza:** Dispara procesos downstream en Salesforce, pasarelas de pago y generación de documentos
- **Notificación de siniestro:** Inicia un flujo de trabajo multi-paso entre equipos internos y ajustadores externos
- **Sincronización de datos entre países:** Los datos de pólizas y clientes deben mantenerse consistentes entre instancias de Salesforce por país

La pregunta es: ¿qué flujos deben ser **síncronos (solicitud-respuesta)** y cuáles **event-driven (mensajería asíncrona)**?

---

### Opciones Consideradas

#### Opción A — Solicitud-Respuesta Síncrona para Todos los Flujos

Todas las operaciones se manejan mediante llamadas HTTP síncronas a través de MuleSoft. El llamador espera una respuesta completa antes de continuar.

**Ventajas:**
- Modelo de programación simple — más fácil de razonar y depurar
- Retroalimentación de error inmediata al llamador
- No requiere infraestructura adicional de mensajería

**Desventajas:**
- Acoplamiento fuerte entre sistemas — si el downstream falla, todo el flujo falla
- No puede manejar picos de alto volumen con gracia — la carga se propaga directamente a los backends
- Las operaciones de larga duración (siniestros, generación de documentos) bloquean el hilo del llamador
- Fallos en cascada: una respuesta lenta de Salesforce degrada todo el canal

---

#### Opción B — Event-Driven para Flujos de Larga Duración y Alto Volumen, Síncrono para Respuestas en Tiempo Real

Un modelo híbrido donde el patrón de comunicación se selecciona según las características del flujo:

| Tipo de Flujo | Patrón | Justificación |
|---|---|---|
| Generación de cotización | Síncrono | El usuario espera la respuesta; debe ser < 3s |
| Emisión de póliza | Event-driven (asíncrono) | Flujo de trabajo multi-paso; desacopla el canal del procesamiento backend |
| Notificación de siniestro | Event-driven (asíncrono) | Inicia un flujo de trabajo de larga duración multi-equipo |
| Sincronización de datos entre países | Event-driven (asíncrono) | La consistencia eventual es aceptable; el volumen puede ser alto |
| Autenticación / sesión | Síncrono | Sensible a la seguridad; requiere validación inmediata |

**Ventajas:**
- Desacopla productores de consumidores — el canal permanece responsivo incluso si Salesforce es lento
- Absorbe picos de tráfico mediante buffering en la cola de mensajes
- Habilita patrones de reintento y cola de mensajes fallidos para eventos con error
- Los flujos de trabajo de larga duración no bloquean los hilos orientados al usuario

**Desventajas:**
- Introduce consistencia eventual — los datos se sincronizan con un pequeño retraso, no en tiempo real; requiere idempotencia y deduplicación
- Modelo operativo más complejo — requiere monitoreo de profundidad de cola y consumer lag
- Más difícil seguir el flujo completo sin IDs de correlación apropiados y trazas distribuidas

---

### Decisión

**Recomendamos la Opción B — Modelo híbrido: event-driven para flujos de larga duración y alto volumen, síncrono para respuestas orientadas al usuario en tiempo real.**

Justificación:
1. **La experiencia del usuario dicta los flujos síncronos.** La generación de cotizaciones y la autenticación no pueden ser asíncronas — los usuarios esperan retroalimentación inmediata. Forzarlos por una cola agrega latencia sin beneficio.
2. **La resiliencia del backend requiere desacoplamiento.** La emisión de pólizas y la notificación de siniestros disparan flujos de trabajo downstream complejos en Salesforce y sistemas externos. Una cadena síncrona significa que cualquier downstream lento o fallido degrada todo el canal. Un enfoque event-driven aísla los fallos al servicio consumidor.
3. **Las colas absorben los picos de tráfico, no los backends.** En un canal digital multi-país, el tráfico pico (lanzamientos de campañas, períodos de renovación) puede saturar los backends síncronos. Las colas de mensajes actúan como amortiguadores, suavizando la carga sin necesidad de escalar los backends más allá de lo necesario.
4. **La idempotencia hace seguro el async.** Cada evento lleva una clave de idempotencia, habilitando replay seguro sin procesamiento duplicado — un requisito ya presente en el framework de integración (Sección B).

---

### Consecuencias

**Positivas:**
- El canal permanece responsivo ante degradación del backend
- Los flujos de trabajo de larga duración son confiables y reintentables
- Los picos de tráfico no se propagan en cascada hacia fallos del backend
- Cada tipo de flujo usa el patrón de comunicación más adecuado a sus características

**Negativas / Mitigaciones:**
- La complejidad operativa aumenta — mitigada por observabilidad centralizada (Sección A) con dashboards de profundidad de cola y consumer lag
- La consistencia eventual requiere disciplina de idempotencia — aplicada a nivel del framework de integración
- El trazado end-to-end requiere correlation IDs propagados en límites síncronos y asíncronos — aplicado mediante propagación de trazas de OpenTelemetry (Sección B)
