# Borrador de contrato API

**Estado: el responsable aprobó rutas, métodos, permisos y payloads propuestos de las filas 1–40, ciclo CSRF/sesión, revocación global, respuestas de salud/dispositivos, mapa de errores, cursor opaco con vigencia de 24 horas, `Idempotency-Key` UUID en escrituras Windows, activación automática del producto tras completar receta/precio iniciales y periodos `YYYY-MM`, `YYYY-Q1..Q4` o `YYYY` sin rangos arbitrarios. También aprobó explícitamente el diseño técnico consolidado y el plan de pruebas; P-04/P-05 están autorizados para codificación.**

Este borrador convierte los flujos aprobados en candidatos de recursos HTTP. El contrato final se publicará en OpenAPI y será aprobado por el responsable antes de iniciar la implementación. Los segmentos `{id}` serán UUID; `{cursor}` será un cursor opaco ASCII/Base64URL y el valor `inicio` solicitará la primera página.

## Convenciones aprobadas

- Base `/api/v1`; HTTPS local sobre TCP 8443, solo LAN/perfil privado.
- El cliente Windows usa Django Auth mediante cookie de sesión y CSRF. Android usa su clave ECDSA exclusiva; sus rutas firmadas no aceptan query string.
- La paginación usa cursor en el segmento de ruta. Las escrituras son atómicas; cualquier error rechaza la operación completa.
- Los errores usan el sobre común `code`, `message`, `request_id`, `retryable` y `details`, con el mapeo HTTP de este documento.
- Cursores opacos ligados a recurso, filtros e identidad autenticada vencen a las 24 horas; el cliente reinicia con `inicio` si recibe `CURSOR_EXPIRED`.
- Los payloads y respuestas propuestos de importación/conflicto/restauración se aprobaron; los casos no definidos en este borrador se concretan en OpenAPI sin cambiar estas reglas.

## Recursos, rutas y permisos aprobados

| # | Método y ruta propuesta | Autenticación / permiso | Propósito |
|---|---|---|---|
| 1 | `GET /api/v1/health` | LAN; sin datos de negocio | Estado mínimo del servicio para la aplicación Windows. |
| 2 | `GET /api/v1/auth/csrf` | Cookie CSRF | Obtener token CSRF antes de iniciar sesión. |
| 3 | `POST /api/v1/auth/session` | CSRF; credenciales Django | Iniciar sesión y crear cookie de sesión. |
| 4 | `GET /api/v1/auth/session` | Cookie de sesión | Consultar identidad y rol autenticados. |
| 5 | `DELETE /api/v1/auth/session` | Sesión + CSRF | Cerrar e invalidar la sesión. |
| 6 | `GET /api/v1/products/pages/{cursor}` | Sesión; ADMIN/OPERADOR | Consultar productos y versiones vigentes. |
| 7 | `POST /api/v1/products` | Sesión; ADMIN | Crear producto y datos iniciales acordados. |
| 8 | `GET /api/v1/components/pages/{cursor}` | Sesión; ADMIN/OPERADOR | Consultar componentes, unidades y costos disponibles. |
| 9 | `POST /api/v1/components` | Sesión; ADMIN | Crear componente con unidad/precisión. |
| 10 | `POST /api/v1/products/{product_id}/recipe-versions` | Sesión; ADMIN | Crear versión de receta sin reescribir ventas históricas. |
| 11 | `POST /api/v1/products/{product_id}/price-versions` | Sesión; ADMIN | Crear versión de precio vigente. |
| 11a | `GET /api/v1/products/{product_id}/contador` | Sesión; ADMIN/OPERADOR | Consultar desglose de costos de receta activa, impuestos personalizables y costo parcial de producción (CONTADOR). |
| 11b | `POST /api/v1/products/{product_id}/contador/taxes` | Sesión + CSRF + `Idempotency-Key`; ADMIN | Crear impuesto personalizable por producto (máximo 3). |
| 11c | `PATCH` / `DELETE /api/v1/products/{product_id}/contador/taxes/{tax_id}` | Sesión + CSRF + `Idempotency-Key`; ADMIN | Actualizar o eliminar impuesto personalizable del producto (recompacta posiciones al eliminar). |
| 12 | `GET /api/v1/inventory/stock/pages/{cursor}` | Sesión; ADMIN/OPERADOR | Consultar saldos, disponibilidad y estados provisionales. |
| 13 | `GET /api/v1/inventory/movements/pages/{cursor}` | Sesión; ADMIN/OPERADOR | Consultar movimientos con cursor. |
| 14 | `POST /api/v1/inventory/receipts` | Sesión; ADMIN/OPERADOR | Registrar entrada central con cantidad, costo y referencia. |
| 15 | `POST /api/v1/inventory/adjustments` | Sesión; ADMIN | Ajustar stock central con motivo y auditoría. |
| 16 | `POST /api/v1/inventory/valuations` | Sesión; ADMIN | Valorar saldo existente que carece de costo promedio. |
| 17 | `POST /api/v1/sales` | Sesión; ADMIN/OPERADOR | Surtir venta desde Windows, con precio/descuento snapshot. |
| 18 | `POST /api/v1/sales/{sale_id}/corrections` | Sesión; ADMIN | Revertir/auditar venta incorrecta y crear la correcta. |
| 19 | `POST /api/v1/sales/{sale_id}/returns` | Sesión; ADMIN/OPERADOR | Registrar devolución completa de componentes. |
| 19a | `GET /api/v1/sales/pages/{cursor}` | Sesión; ADMIN/OPERADOR | Consultar historial de ventas con cursor. |
| 20 | `POST /api/v1/expenses` | Sesión; ADMIN/OPERADOR | Registrar gasto único. |
| 20a | `GET /api/v1/expenses/pages/{cursor}` | Sesión; ADMIN/OPERADOR | Consultar gastos con cursor. |
| 21 | `POST /api/v1/expense-templates` | Sesión; ADMIN | Crear plantilla recurrente. |
| 22 | `PUT /api/v1/expense-templates/{template_id}` | Sesión; ADMIN | Actualizar plantilla y ocurrencias auditables. |
| 22a | `GET /api/v1/expense-templates/pages/{cursor}` | Sesión; ADMIN | Consultar plantillas recurrentes. |
| 23 | `GET /api/v1/reports/{period}` | Sesión; ADMIN/OPERADOR | Consultar informe del periodo indicado. |
| 24 | `POST /api/v1/initial-imports` | Sesión; ADMIN | Crear importación provisional idempotente. |
| 24a | `GET /api/v1/initial-imports/pages/{cursor}` | Sesión; ADMIN | Consultar importaciones y su estado. |
| 25 | `GET /api/v1/initial-imports/{import_id}/preview` | Sesión; ADMIN | Consultar diferencias, advertencias, costos y estados. |
| 26 | `POST /api/v1/initial-imports/{import_id}/confirmations` | Sesión + CSRF; ADMIN | Registrar confirmación explícita; dos confirmaciones separadas en la misma sesión para oficializar. |
| 27 | `POST /api/v1/initial-imports/{import_id}/corrections` | Sesión + CSRF; ADMIN | Corregir importación de forma idempotente y auditada. |
| 28 | `GET /api/v1/devices/pages/{cursor}` | Sesión; ADMIN | Consultar dispositivos y estado. |
| 29 | `POST /api/v1/pairing-requests` | Sesión + CSRF; ADMIN | Iniciar alta y emitir QR de un solo uso. |
| 30 | `POST /api/v1/pairing-requests/{pairing_request_id}/complete` | QR/token de un uso + TLS fijado | Completar alta Android con clave pública. |
| 31 | `POST /api/v1/devices/{device_id}/revocations` | Sesión + CSRF; ADMIN | Revocar dispositivo conservando auditoría y outbox remota pendiente. |
| 32 | `PUT /api/v1/devices/{device_id}/quota` | Sesión + CSRF; ADMIN | Asignar cuota dentro del stock central disponible. |
| 33 | `POST /api/v1/devices/{device_id}/event-batches` | Firma ECDSA Android | Enviar eventos ordenados; devolver ACK individual terminal por evento. |
| 34 | `GET /api/v1/devices/{device_id}/sync-pages/{cursor}` | Firma ECDSA Android | Descargar cambios de catálogo, recetas, precios, costos, cuotas y usuarios habilitados. |
| 35 | `GET /api/v1/conflicts/pages/{cursor}` | Sesión; ADMIN | Consultar conflictos pendientes. |
| 36 | `POST /api/v1/conflicts/{event_id}/resolutions` | Sesión + CSRF; ADMIN | Resolver conflicto con decisión y motivo; persistir resolución y ACK atómicamente. |
| 37 | `GET /api/v1/backups/status` | Sesión; ADMIN | Consultar última ejecución, estado local/Drive y errores. |
| 38 | `POST /api/v1/restorations` | Sesión + CSRF; ADMIN | Crear solicitud de restauración pendiente, con advertencia de pérdida y sin cambiar datos. |
| 39 | `POST /api/v1/restorations/{restoration_id}/confirmations` | Sesión + CSRF; ADMIN | Registrar dos confirmaciones explícitas separadas de la misma cuenta ADMIN y sesión; ejecutar la restauración controlada solo después de la segunda. |
| 40 | `POST /api/v1/auth/sessions/revocations` | Sesión + CSRF; ADMIN | Invalidar todas las sesiones activas, incluida la sesión solicitante; la solicitud se completa y la app vuelve al login. |

## Payloads aprobados — sesión, dispositivos y sincronización

Estos esquemas también son propuestas. Las fechas son ISO 8601 UTC; cantidades/costos son decimales como cadenas, no flotantes. La clave pública SPKI se codifica Base64URL sin padding y `key_id` es SHA-256 de los bytes DER en Base64URL sin padding.

| Endpoint | Request propuesto | Respuesta exitosa propuesta |
|---|---|---|
| `GET /auth/csrf` | Sin body. | `200 {csrf_token}`; usar el valor en el header `X-CSRFToken`. |
| `GET /health` | Sin body. | Si API y PostgreSQL responden: `200 {status:"ok", api_version, instance_id}`; no incluye datos de negocio. Si PostgreSQL no responde: `503 SERVICE_UNAVAILABLE`. `instance_id` es el UUID singleton persistido en PostgreSQL. |
| `POST /auth/session` | `{"username","password"}` + `X-CSRFToken`. | `200 {user_id, display_name, role, expires_at}` y cookie de sesión segura. |
| `GET /auth/session` | Cookie de sesión. | `200 {user_id, display_name, role}`. |
| `DELETE /auth/session` | Cookie + `X-CSRFToken`; sin body. | `204`; sesión invalidada. |
| `POST /auth/sessions/revocations` | `{reason}` + CSRF + `Idempotency-Key`; ADMIN. | `200 {revoked_sessions, audit_id}`; invalida todas las sesiones, incluida la solicitante, y requiere volver a iniciar sesión. |
| `GET /devices/pages/{cursor}` | Sin body; ADMIN. | `{items:[{device_id, label, status, key_id, last_sequence, quota_version}], next_cursor, has_more}`; no expone clave privada ni token QR. |
| `POST /pairing-requests` | `{device_label}`; requiere sesión ADMIN. | `201 {pairing_request_id, qr_payload, expires_at}`; el QR incluye solo endpoint LAN, instancia, huella TLS y token de un uso. |
| `POST /pairing-requests/{id}/complete` | `{qr_token, public_key_spki, key_id, protocol_version}`; SPKI y `key_id` se codifican según CA-26. | `{device_id, key_id, initial_sequence, pairing_status}`; la etiqueta ADMIN queda asociada al alta; repetir exactamente ID/QR/clave reproduce resultado persistido. |
| `POST /devices/{id}/revocations` | `{reason}`. | `{device_id, status, revoked_at}`. |
| `PUT /devices/{id}/quota` | `{quota_version, components:[{component_id, quantity, unit}]}`. | `{device_id, quota_version, components:[...]}`. |
| `GET /devices/pages/{cursor}` | Sin body; ADMIN. | `{items:[{device_id, device_label, status, key_id, last_sequence, quota_version}], next_cursor, has_more}`. |
| `POST /devices/{id}/event-batches` | `{events:[{event_id, sequence, type, occurred_at, protocol_version, payload}]}`; firma ECDSA sobre bytes exactos. | `{acks:[{event_id, status, next_expected_sequence, terminal_result}]}`; respuesta por evento conforme al resultado persistido. |
| `GET /devices/{id}/sync-pages/{cursor}` | Sin body ni query; firma ECDSA. | `{changes:[...], next_cursor, has_more}` con cambios versionados de datos maestros. |

### Ciclo de sesión aprobado

- Cookie de sesión `Secure`, `HttpOnly`, `SameSite=Strict`; sesión de servidor, sin opción “recordarme”.
- Vence tras 30 minutos de inactividad o 8 horas máximo. Cada inicio de sesión exige CSRF; la app obtiene el token en el body de `GET /auth/csrf` y lo envía como `X-CSRFToken`.
- El cliente protege la cookie almacenada localmente con DPAPI del usuario de Windows; la contraseña no se persiste.

## Payloads aprobados — catálogo e inventario

| Endpoint | Request propuesto | Respuesta exitosa propuesta |
|---|---|---|
| `GET /products/pages/{cursor}` | Sin body. | `{items:[{product_id, name, sku, description, active, current_price, recipe_version, bottle_stock, bottle_stock_precise, recipe_components, contador_summary:{has_active_recipe, recipe_cost_complete, cost_known, known_components_subtotal, recipe_total_cost, taxes_count, max_taxes, limit_state, taxes_cost_complete, known_taxes_subtotal, taxes_total, partial_production_cost}}], next_cursor, has_more}`. |
| `POST /products` | `{name, sku?, description?, product_type, base_unit, precision}` + CSRF. | `201 {product_id, active:false}`; no se puede vender hasta configurar precio y receta. |
| `GET /components/pages/{cursor}` | Sin body. | `{items:[{component_id, name, base_unit, precision, average_cost?, cost_known}], next_cursor, has_more}`. |
| `POST /components` | `{name, base_unit, precision}` + CSRF. | `201 {component_id, name, base_unit, precision}`. |
| `POST /products/{id}/recipe-versions` | `{effective_at, components:[{component_id, quantity, unit}]}` + CSRF. | `201 {recipe_version_id, product_id, effective_at, components:[...], product_active}`; activa automáticamente si ya existe precio inicial. |
| `POST /products/{id}/price-versions` | `{amount, currency:"MXN", tax_included:true, effective_at}` + CSRF. | `201 {price_version_id, product_id, amount, effective_at, product_active}`; activa automáticamente si ya existe receta inicial. |
| `GET /products/{id}/contador` | Sin body; ADMIN/OPERADOR. | `200 {product_id, name, sku, base_unit, active, current_price, has_active_recipe, recipe_version_id, recipe_version_number, recipe_effective_from, recipe_cost_complete, cost_known, known_components_subtotal, recipe_total_cost, missing_cost_components_count, missing_cost_components:[...], recipe_components:[...], taxes:[{tax_id, product_id, name, tax_type, rate_or_amount, edit_value, display_value, position, amount_known, calculated_amount, share_of_partial_percent, created_by, created_at, updated_at}], taxes_count, max_taxes:3, can_add_tax, limit_state, taxes_cost_complete, known_taxes_subtotal, taxes_total, partial_production_cost, recipe_share_of_partial_percent, cost_basis_note, currency:"MXN"}`. |
| `POST /products/{id}/contador/taxes` | `{name, tax_type:"PERCENT"|"FIXED", rate_or_amount, position?:1..3}` + CSRF + `Idempotency-Key`; ADMIN. | `201 {tax:{...}, contador:{...}}`. Al superar 3 impuestos responde `409 BUSINESS_CONFLICT` con `details:{reason:"LIMIT_REACHED_3_OF_3", product_id, max_taxes:3}`. |
| `PATCH /products/{id}/contador/taxes/{tax_id}` | `{name?, tax_type?, rate_or_amount?, position?:1..3}` (al menos 1 campo) + CSRF + `Idempotency-Key`; ADMIN. | `200 {tax:{...}, contador:{...}}`; validación cruzada `(tax_type, rate_or_amount)` y lookup estricto `(tax_id, product_id)` (`404` anti-IDOR). |
| `DELETE /products/{id}/contador/taxes/{tax_id}` | Body vacío (o `{}`) + CSRF + `Idempotency-Key`; ADMIN. | `200 {deleted_tax_id, contador:{...}}`; elimina el impuesto, recompacta `position` (`1..N`) y cachea el `200` para reintentos idempotentes. |
| `GET /inventory/stock/pages/{cursor}` | Sin body. | `{items:[{component_id, central_unassigned, provisional, pending_cost, unit, average_cost?}], next_cursor, has_more}`. |
| `GET /inventory/movements/pages/{cursor}` | Sin body. | `{items:[{movement_id, component_id, movement_type, quantity_delta, unit_cost?, occurred_at, received_at, reference, actor_id}], next_cursor, has_more}`. |
| `POST /inventory/receipts` | `{occurred_at, reference, lines:[{component_id, quantity, unit, unit_cost}]}` + CSRF. | `201 {receipt_id, movement_ids, resulting_balances}`. |
| `POST /inventory/adjustments` | `{component_id, quantity_delta, unit, reason, unit_cost?}` + CSRF. | `201 {movement_id, resulting_balance, average_cost?}`. |
| `POST /inventory/valuations` | `{component_id, unit_cost, reason}` + CSRF. | `201 {component_id, valued_quantity, average_cost, audit_id}`. |

Para cualquier escritura Windows distinta de login/logout, se exige `Idempotency-Key: <UUID>`; repetir la misma clave y payload devuelve el resultado persistido, y reutilizarla con payload distinto se rechaza. Los eventos Android usan `event_id` como clave idempotente.

## Payloads aprobados — ventas, gastos e informes

| Endpoint | Request propuesto | Respuesta exitosa propuesta |
|---|---|---|
| `GET /sales/pages/{cursor}` | Sin body. | `{items:[{sale_id, product_id, quantity, unit, status, amount_paid, tax, direct_cost, cost_status, occurred_at}], next_cursor, has_more}`. |
| `POST /sales` | `{product_id, quantity, unit, price_override?, discount?:{type:"AMOUNT"|"PERCENT", value}}` + CSRF + `Idempotency-Key`. | `201 {sale_id, status:"FULFILLED", price_snapshot, discount_snapshot, amount_paid, tax, cost_snapshot, movements}`; todo stock/movimientos se confirman atómicamente. |
| `POST /sales/{id}/corrections` | `{reason, replacement:{product_id, quantity, unit, price_override?, discount?}}` + CSRF + `Idempotency-Key`. | `{reversed_sale_id, replacement_sale_id, movements, audit_id}`. |
| `POST /sales/{id}/returns` | `{quantity, occurred_at}` + CSRF + `Idempotency-Key`. | `201 {return_id, quantity, refund, tax_reversed, movements, occurred_at}`; solo con todos los componentes recibidos. |
| `GET /expenses/pages/{cursor}` | Sin body. | `{items:[{expense_id, name, amount, currency, category, occurred_at, recurrence_template_id?}], next_cursor, has_more}`. |
| `POST /expenses` | `{name, amount, currency:"MXN", category, occurred_at}` + CSRF + `Idempotency-Key`. | `201 {expense_id, amount, occurred_at}`; gasto único. |
| `GET /expense-templates/pages/{cursor}` | Sin body; ADMIN. | `{items:[{template_id, name, amount, currency, category, frequency, start_date, end_date?, status}], next_cursor, has_more}`. |
| `POST /expense-templates` | `{name, amount, currency:"MXN", category, frequency:"MONTHLY"|"YEARLY", start_date, end_date?}` + CSRF + `Idempotency-Key`. | `201 {template_id, first_occurrence_id, next_occurrence_at}`. |
| `PUT /expense-templates/{id}` | `{name, amount, category, frequency, start_date, end_date?, status}` + CSRF + `Idempotency-Key`. | `{template_id, updated_occurrences, audit_id}`; conserva historial anterior/nuevo. |
| `GET /reports/{period}` | Sin body. | `{period, net_sales, net_units, tax, direct_cost, general_expenses, margin, business_profit, pending_costs, series}`. |

## Payloads aprobados — importación, conflictos y restauración

| Endpoint | Request propuesto | Respuesta exitosa propuesta |
|---|---|---|
| `POST /initial-imports` | `{import_id, quantities:[{component_id, quantity, unit, reference_unit_cost?}], opening_expenses:[{name, amount, currency:"MXN", occurred_at}]}` + CSRF + `Idempotency-Key`. | `201 {import_id, status:"PROVISIONAL", preview_version}`. |
| `GET /initial-imports/pages/{cursor}` | Sin body; ADMIN. | `{items:[{import_id, status, created_at, correction_status}], next_cursor, has_more}`. |
| `GET /initial-imports/{id}/preview` | Sin body; ADMIN. | `{import_id, preview_version, quantities, units, reference_costs, differences, warnings, opening_expenses, provisional_status}`. |
| `POST /initial-imports/{id}/confirmations` | `{confirmation_id, preview_version, physical_count?, verified_costs?, acknowledged:true}` + CSRF + `Idempotency-Key`; dos IDs distintos en la misma sesión ADMIN. | `{import_id, confirmations_received, status, audit_ids}`; oficializa solo tras dos confirmaciones válidas y conciliación. |
| `POST /initial-imports/{id}/corrections` | `{correction_id, quantities:[{component_id, quantity, unit}], opening_expenses:[...] , confirmation_id?}` + CSRF + `Idempotency-Key`; corrección requiere doble confirmación ADMIN. | `{import_id, correction_id, status, compensating_movements, pending_deficits, expense_reversals, audit_ids}`. |
| `GET /conflicts/pages/{cursor}` | Sin body; ADMIN. | `{items:[{event_id, device_id, sequence, event_type, payload, conflict_code, status}], next_cursor, has_more}`. |
| `POST /conflicts/{event_id}/resolutions` | `{decision:"ACCEPT"|"REJECT", reason}` + CSRF + `Idempotency-Key`. | `{event_id, decision, terminal_ack, next_expected_sequence, audit_id}`. |
| `GET /backups/status` | Sin body; ADMIN. | `{last_attempt_at, last_valid_backup, local_valid_copies, drive_status, next_retry_at, error?}`. |
| `POST /restorations` | `{backup_id}` + CSRF + `Idempotency-Key`. | `202 {restoration_id, status:"PENDING_CONFIRMATION", warning, backup_created_at, restore_cutoff}`; no data changes. |
| `POST /restorations/{id}/confirmations` | `{confirmation_id, acknowledged:true}` + CSRF + `Idempotency-Key`; dos IDs distintos de la misma cuenta ADMIN y sesión. | `{restoration_id, confirmations_received, status, audit_ids}`; proceso controlado inicia solo en la segunda confirmación. |

## Mapa aprobado de códigos de error por caso

Todos llevan `code`, `message`, `request_id`, `retryable` y `details`; no hay escrituras parciales. Los códigos HTTP, nombres y reglas de reintento fueron aprobados:

| HTTP | `code` | `retryable` | Casos |
|---|---|---:|---|
| 400 | `VALIDATION_ERROR` | false | Campo requerido, tipo/unidad/precisión/formato inválido o cursor inválido/alterado. |
| 401 | `AUTHENTICATION_REQUIRED` | false | Sesión vencida o credencial ausente/inválida. |
| 403 | `PERMISSION_DENIED` | false | Rol insuficiente o dispositivo revocado. |
| 403 | `CSRF_FAILED` | false | Token CSRF inválido/ausente. |
| 404 | `RESOURCE_NOT_FOUND` | false | Producto, dispositivo, importación o recurso inexistente. |
| 409 | `BUSINESS_CONFLICT` | false | Stock insuficiente, secuencia/conflicto o estado incompatible. |
| 409 | `IDEMPOTENCY_KEY_REUSED` | false | Misma `Idempotency-Key` con payload distinto. |
| 409 | `RESTORE_CONFIRMATION_REQUIRED` | false | Intento de ejecutar restauración sin doble confirmación; la primera confirmación válida responde éxito pendiente. |
| 422 | `COST_REQUIRED` | false | Falta costo necesario para confirmar la operación. |
| 410 | `CURSOR_EXPIRED` | false | Cursor vencido a las 24 horas; reiniciar la consulta con `inicio`. |
| 426 | `CLIENT_VERSION_UNSUPPORTED` | false | Versión incompatible; outbox se conserva. |
| 507 | `INSUFFICIENT_STORAGE` | false | Espacio insuficiente para dump/cifrado/restauración. |
| 503 | `SERVICE_UNAVAILABLE` | true | API, PostgreSQL o dependencia temporal no disponible; retry con mismo identificador idempotente. |
| 500 | `INTERNAL_ERROR` | true | Falla inesperada con transacción revertida; retry solo con mismo identificador idempotente. |

## Reglas CSRF y de sesión aprobadas

- `GET /auth/csrf` entrega el token que se envía en `X-CSRFToken`; queda ligado a la sesión.
- Rotar el token al iniciar sesión y al cambiar privilegios. Un token ausente/vencido se rechaza con `403 CSRF_FAILED`; obtener uno nuevo y reintentar con la misma clave de idempotencia cuando aplique.
- `DELETE /auth/session` invalida la sesión actual. La ruta ADMIN de revocación global invalida todas las sesiones activas, incluida la que la invoca; se completa la respuesta y la aplicación vuelve al login.
- Los cursores son opacos y quedan ligados a recurso, filtros e identidad autenticada; vencen a las 24 horas.
- El primer ADMIN se crea localmente mediante Django `createsuperuser` y se identifica por `is_superuser`; OPERADOR corresponde a pertenecer al grupo Django `OPERADOR`. No hay rutas de gestión de usuarios en este alcance; las cuentas se aprovisionan localmente con comandos Django.

P-03 está completado. P-04/P-05 están autorizados y el primer incremento/estructura (`apps/api`, `apps/clients`) quedó aprobado; no incluye lógica de inventario.
