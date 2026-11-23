# Planeación del sistema de inventario

Documentación aprobada para planear una aplicación de inventario offline-first para Android y Windows. Este conjunto describe alcance, reglas, datos reportados y criterios para que los agentes trabajen con una única fuente de verdad.

## Índice y orden recomendado

1. [Resumen y alcance](00-resumen-y-alcance.md)
2. [Requisitos y glosario](01-requisitos-y-glosario.md)
3. [Reglas de negocio y métricas](02-reglas-de-negocio-y-metricas.md)
4. [Modelo de datos](03-modelo-de-datos.md)
5. [Flujos y pantallas](04-flujos-y-pantallas.md)
6. [Arquitectura y decisiones](05-arquitectura-y-decisiones.md)
7. [Criterios de aceptación y pruebas](06-criterios-y-pruebas.md)
8. [Backlog y fichas de trabajo](07-backlog-y-fichas-de-trabajo.md)
9. [Inventario inicial recibido](08-inventario-inicial.md)
10. [Pseudocódigo offline-first (aprobado, aún no implementado)](09-pseudocodigo.md)
11. [Plan de implementación y gates de calidad (propuesta, requiere aprobación)](10-plan-de-implementacion-y-gates.md)
12. [Borrador de contrato API (propuesta, requiere aprobación)](11-borrador-contrato-api.md)

## Cómo usar estos documentos

- Los datos transcritos de la lista inicial son datos reportados, no datos contables ya validados. Revisar las alertas en `08-inventario-inicial.md` antes de cargarlos como saldos oficiales.
- `09-pseudocodigo.md` es la especificación funcional aprobada y la referencia para algoritmos y casos límite.
- `10-plan-de-implementacion-y-gates.md` coordina la trazabilidad Scrum, el análisis de flujo QA, los gates y las asignaciones; P-03 y su plan de pruebas están aprobados, y P-04/P-05 están autorizados.
- `11-borrador-contrato-api.md` contiene el contrato `/api/v1` aprobado, incluidos rutas, permisos, payloads, errores, CSRF, sesiones y cursor.
- `02-reglas-de-negocio-y-metricas.md` resume las fórmulas. `03-modelo-de-datos.md` describe entidades y persistencia.
- Si una decisión cambia, actualizar primero este conjunto de documentos y después las tareas de implementación afectadas.
- Flutter/Dart, Python/Django REST Framework y PostgreSQL se ejecutan sin hosting contratado: la PC Windows principal aloja API/base local y sincroniza Android por Wi-Fi/LAN.
- Google Drive existente se utiliza solo para respaldos cifrados diarios (retención 30 días); no se requiere internet para las operaciones del negocio.
- La aprobación del pseudocódigo y la actualización de estos documentos no equivalen a autorización para comenzar la implementación.
