# Backlog y fichas de trabajo

El pseudocódigo y el diseño técnico consolidado fueron aprobados. P-03 está completado; P-04/P-05 están en implementación en `apps/api` y `apps/clients`, con QA y validaciones nativas pendientes.

| ID | Responsable principal | Entregable | Dependencias | Estado |
|---|---|---|---|---|
| P-01 | Líder + responsable del negocio | Armonizar `00`–`08`, pseudocódigo aprobado y backlog en una especificación consistente | Ninguna | Completado |
| P-02 | Responsable del negocio + líder | Configurar fecha de arranque, conciliar saldos físicos y costos de botellas/corchos, confirmar unidades de nuevos componentes y plan de recuperación | P-01 | Pendiente |
| P-03 | Líder + programador | Definir versiones mínimas, despliegue Windows, contrato/versionado de API y empaquetado sin servicios externos | P-01 | Completado; diseño y QA aprobados |
| P-04 | Programador | Backend local Python/Django REST Framework, PostgreSQL, migraciones, autenticación de administrador, auditoría y API transaccional | P-03 | En progreso: primer slice implementado; 12 pruebas SQLite pasan; falta validar contra PostgreSQL real |
| P-05 | Programador | Cliente Flutter Android/Windows; SQLite cifrado con outbox UUID/secuencia en Android y caché read-only en Windows; almacenamiento seguro de credenciales y vistas de sincronización | P-03 | En progreso: shell health/login Windows y health Android; analyze/tests pasan; builds nativos bloqueados por toolchains; acceso remoto pendiente de P-07 |
| P-06 | Programador | Catálogo/versiones de receta, movimientos, costo promedio, ventas, devoluciones, gastos, reportes y reglas de stock/cuotas | P-04, P-05 | Pendiente |
| P-07 | Programador | Emparejamiento QR/TLS local, sync idempotente, ACK, conflictos, revocación y resolución administrativa | P-04, P-05, P-06 | Pendiente |
| P-08 | Programador | Flujos Android/Windows, pendientes de surtir, inventario, ventas/devoluciones, gastos y estadísticas; interfaz de vista previa/resumen/confirmación de importación | P-05, P-06, P-07; flujo de importación espera P-09 | Pendiente |
| P-09 | Programador + responsable del negocio | Operación de dominio para importar/corregir saldos conciliados y gastos de apertura; ID idempotente, doble confirmación administrativa, reversión/reingreso auditables | P-02, P-06 | Pendiente |
| P-10 | Programador + líder | Criterios de aceptación, pruebas offline/conflicto, empaquetado Windows, respaldo cifrado y restauración aislada | P-04 a P-09 | Pendiente |

Las decisiones D1–D12 y la zona fija UTC-06:00 fueron respondidas el 7 de noviembre de 2026. Las reglas operativas están cerradas en `09-pseudocodigo.md`; los valores que dependen de la instalación se configuran antes del despliegue, no se inventan como reglas de negocio.

## Plantilla de ficha de implementación

Duplicar por tarea y completar antes de asignar:

```md
### ID y título
- Responsable:
- Objetivo:
- Documentos de referencia:
- Prerrequisitos:
- Incluye:
- No incluye:
- Reglas de negocio aplicables:
- Datos/API afectados:
- Criterios de aceptación:
- Casos límite y errores:
- Verificación:
- Decisiones que requieren aprobación:
```

## Instrucciones para los agentes

- **Líder:** mantener decisiones, pseudocódigo, modelo y criterios consistentes; no aprobar supuestos de negocio en nombre del usuario.
- **Programador:** usar Flutter/Dart, Python/Django REST Framework y PostgreSQL conforme a la ficha; preservar transacciones, idempotencia y operación LAN/offline.
- **Ambos:** ante conflicto entre una ficha y una regla aprobada, detener el cambio afectado y actualizar la documentación antes de continuar.

### P-03 — Contrato técnico y despliegue Windows

- **Estado:** completado; el responsable aprobó explícitamente el diseño técnico consolidado y el plan QA, y autorizó iniciar P-04/P-05.
- **Responsable:** líder, quien dirige el ticket y coordina las asignaciones.
- **Colaboradores:** developer_pseudocodigo traza las secciones aprobadas hacia tickets sin añadir reglas; programador prepara el contrato técnico y la estrategia de despliegue; qa_auditor define criterios y pruebas antes de cualquier implementación.
- **Objetivo:** completar y someter a aprobación del responsable del negocio el diseño técnico requerido antes de abrir P-04 o P-05 para codificación.
- **Documentos de referencia:** `05-arquitectura-y-decisiones.md`, `06-criterios-y-pruebas.md`, `09-pseudocodigo.md` y esta ficha.
- **Plan coordinado:** `10-plan-de-implementacion-y-gates.md`; contiene el análisis de flujos QA, la trazabilidad de secciones, los gates y las decisiones pendientes.
- **Prerrequisitos:** P-01 completado.
- **Decisiones confirmadas por el responsable del negocio:** Android 12 (API 31) como mínimo; última versión GA de Windows 11 con soporte vigente al desplegar y PC con edición Home (referencia actual: 26H2 al 9 de noviembre de 2026, revalidar antes del despliegue); `/api/v1` con cambios aditivos compatibles y nueva ruta mayor para cambios incompatibles; contrato OpenAPI, paginación por cursor en listados cuando aplique, errores comunes `code/message/request_id/retryable/details`, sin escrituras parciales; versión actual y anterior de Android aceptadas durante 12 meses desde cada publicación, luego actualización obligatoria para sincronizar, con aviso en app y conservación de outbox al bloquear incompatibilidad; autenticación administrativa en Windows con Django Auth, credencial firmada por dispositivo en Android y operador offline declarativo; MSI construido con WiX Toolset, datos PostgreSQL fuera del directorio reemplazable, firewall limitado al proceso/API en perfil privado y LAN, PostgreSQL local; actualizaciones en ventana planificada con aviso previo, escrituras detenidas durante migración, respaldo verificado, migraciones hacia adelante/reanudables y sin downgrade destructivo automático; restaurar backup exige autorización administrativa y aviso de pérdida de operaciones posteriores; caché SQLite cifrada de solo lectura en Windows, sin outbox Windows; lecturas desde caché íntegra y rotulada como potencialmente obsoleta si falla API/PostgreSQL, error si la caché es inválida y escrituras siempre vía API; clave ECDSA P-256/SHA-256 exclusiva por Android en Keystore, QR de un solo uso válido 5 minutos, firmas con timestamp ±5 minutos y nonce aleatorio 128-bit consumido atómicamente/retenido 15 minutos, sin expiración automática de clave; revocación/reemplazo por decisión administrativa, nuevo QR/clave, identidad/secuencia solo se conservan al confirmar mismo equipo, equipo perdido obtiene identidad nueva y renovar huella TLS requiere nuevo emparejamiento; aceptar un conflicto aplica operación, consume secuencia y guarda ACK atómicamente; rechazarlo conserva evento no oficial/auditable, consume secuencia y emite ACK terminal, tras el cual continúa la cola; respaldo diario a las 22:00 UTC-06:00 mientras la app esté abierta, pendiente al siguiente inicio si se pierde, retry diario ante fallo de dump/cifrado/carga/verificación de Drive, retención Drive 30 días, validación de dump íntegro y comparación de tamaño/SHA-256 (descargar y calcular si Drive no expone hash), conservar localmente el último respaldo válido y las 7 copias diarias anteriores, alertar bajo 2× tamaño de último respaldo válido sin borrar copia previa; restauración válida en PostgreSQL aislado con comprobación de esquema/integridad y cotejo de manifiesto; pruebas mensuales y tras cambios importantes de instalación/esquema. RPO/RTO quedan pendientes y no garantizados. La PC estará disponible diariamente varias veces, aunque el respaldo programado depende de que la aplicación esté abierta a las 22:00.
- **Seguridad, permisos y custodia confirmados:** ADMIN gestiona catálogo, recetas, precios, dispositivos/cuotas, costos manuales, ajustes, importaciones/correcciones, restauraciones, conflictos y plantillas recurrentes; OPERADOR registra entradas, ventas, devoluciones y gastos únicos. Windows usa Django Auth, cookie `Secure`/`HttpOnly`/`SameSite=Strict` con CSRF, expiración de 30 min de inactividad/8 h máxima y almacenamiento DPAPI sin persistir contraseña. La clave SQLite Android se genera localmente, es no exportable/no respaldada y se protege en Android Keystore; pérdida puede volver irrecuperable la outbox y exige reemparejar; rotación administrativa por reemplazo o compromiso. La caché SQLite Windows usa DPAPI del usuario de la aplicación; TLS usa almacén de certificados Windows con ACL; el secreto de respaldo va en gestor externo bajo custodia del administrador y un segundo custodio, que se designará antes de activar respaldos y verificará restauración aislada.
- **Rendimiento confirmado:** PC de referencia de al menos 4 núcleos, 16 GB RAM y SSD; hasta 3 Android; backlog máximo de 5,000 eventos por dispositivo; p95 de solicitudes API interactivas ≤2 s con 3 dispositivos en uso por LAN; sincronización individual de 1,000 eventos por dispositivo ≤60 s.
- **Contrato API confirmado:** las escrituras Windows usan `Idempotency-Key` UUID; producto se crea inactivo, se completa en pasos separados con versiones de receta y precio y se activa automáticamente al tener ambas. Están aprobados rutas, métodos, permisos y payloads propuestos de sesión/dispositivos/sync, catálogo/inventario, ventas/gastos/reportes e importación/conflicto/restauración; errores HTTP/código/`retryable`, respuestas de salud/listado de dispositivos, ciclo CSRF, revocación global de todas las sesiones ADMIN (incluida la solicitante), cursor ligado a recurso/filtros/identidad y vigencia de 24 horas. Los periodos son mes `YYYY-MM`, trimestre `YYYY-Q1..Q4` o año `YYYY`, sin rango arbitrario. El diseño y las pruebas QA recibieron aprobación explícita; P-04/P-05 están autorizados.
- **Incluye:** contrato inicial `/api/v1`; límites y responsabilidades de cliente, API y PostgreSQL; operación del servidor mientras Windows está abierto; instalación, actualización, ubicación/protección de datos y recuperación; papel de la caché SQLite de Windows; horario y reintentos de respaldo; dependencias y secuencia de tickets; estrategia de pruebas y evidencia QA.
- **No incluye:** código de aplicación, implementación de migraciones, creación de saldos oficiales o decisiones no aprobadas por el responsable del negocio.
- **Criterios de aceptación:** (1) los límites y flujos concuerdan con la arquitectura y el pseudocódigo; (2) las versiones mínimas y el versionado de API quedan documentados; (3) MSI/WiX, alcance de firewall y estrategia de actualización/migración/recuperación protegen los datos PostgreSQL; (4) la caché SQLite Windows no puede confirmar escrituras ni almacenar outbox, y su fallback sigue la regla aprobada; (5) aceptación y rechazo de conflictos convergen con aplicación/consumo de secuencia/ACK atómicos y sin duplicados; (6) el respaldo valida dump, cifrado, tamaño y SHA-256 antes de marcar carga verificada, y refleja horario, retry, retención y restauración aprobados; (7) cada criterio de aceptación tiene una prueba verificable y resultado esperado; (8) riesgos y decisiones pendientes se presentan explícitamente; (9) el responsable del negocio aprueba el diseño y los casos de prueba antes de autorizar código.
- **Gate de calidad:** QA revisa cada cambio incremental y no recomienda su integración si una prueba falla o quedan hallazgos críticos/altos abiertos. No se afirmará seguridad absoluta; cada aprobación se sustentará en evidencia verificable y alcance declarado.
- **Reportes QA:** por cada sección numerada implementada de `09-pseudocodigo.md`, QA entregará un PDF en `docs/qa/`, versionado en el repositorio. Cada informe identificará sección y alcance, criterios, pruebas y resultados reproducibles, hallazgos abiertos/cerrados y justificación de la aprobación. No se generan informes de implementación antes de que exista una sección implementada y validada.
- **Pendientes operativos:** designar al segundo custodio antes de activar respaldos, verificar recuperación aislada y resolver metas RPO/RTO (no garantizadas mientras estén pendientes). Django Auth con cookie de sesión y CSRF, rutas/métodos/permisos y payloads de la matriz, respuestas salud/dispositivos, mapa de errores, cursor opaco en ruta con vigencia de 24 horas, revocación global de sesiones, puerto TCP 8443, permisos por rol, regla de espacio para el primer respaldo, rendimiento, doble confirmación ADMIN en misma sesión para restaurar, compatibilidad actual/anterior de Android por 12 meses y versionado de API por ruta mayor están acordados. El responsable aprobó explícitamente el diseño técnico y QA; P-04/P-05 están autorizados.
