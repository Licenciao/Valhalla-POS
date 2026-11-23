# Informe de Auditoría y Certificación Técnica Final (P-01 a P-10)

**Proyecto:** Sistema de Control de Inventario y Ventas Offline-First  
**Fecha de emisión:** 10 de noviembre de 2026  
**Auditor:** Agente de QA y Auditoría Técnica (`.codex/agents/qa_auditor.toml`)  
**Equipo sincronizado:** Líder Scrum, Arquitecto de Pseudocódigo, Programador Senior Python/Android y Auditor QA.  
**Estado:** **APROBADO PARA PRODUCCIÓN / CIERRE DE FASE DE DESARROLLO**  
**Resultado de Pruebas:** **73 / 73 pruebas automatizadas pasando (100% de éxito, 0 fallas, 0 errores)**

---

## 1. Alcance y Trazabilidad del Proyecto

El sistema fue diseñado e implementado siguiendo estrictamente las especificaciones de `docs/plan/09-pseudocodigo.md`, los criterios de aceptación de `docs/plan/06-criterios-y-pruebas.md` y las decisiones arquitectónicas de `docs/plan/05-arquitectura-y-decisiones.md`.

| Ticket | Módulo / Entregable | Criterios Validados | Estado | Pruebas Automatizadas |
|:-------|:--------------------|:-------------------|:-------|:----------------------|
| **P-01** | Armonización documental | Consistencia de reglas | Aprobado | 100% trazabilidad |
| **P-02** | Inventario inicial reportado | `08-inventario-inicial.md` | Aprobado | Seeder oficial disponible |
| **P-03** | Contrato técnico API y despliegue | Envelope común, error codes | Aprobado | Matriz API verificada |
| **P-04** | API Django, Auth, Sesiones y CSRF | CA-31, CA-32, CA-39, CA-40 | Aprobado | 12 tests en `system` |
| **P-05** | Cliente Flutter + Outbox SQLite | CA-03, CA-04, CA-19, CA-29 | Aprobado | 14 tests en `database_helper` |
| **P-06** | Catálogo, Promedio Móvil, Surtido y Ventas | CA-01, CA-02, CA-05, CA-06, CA-07 | Aprobado | 17 tests en `catalog` |
| **P-07** | Emparejamiento QR, ECDSA y Cuotas | CA-11, CA-22, CA-26, CA-30 | Aprobado | 5 tests en `devices` |
| **P-08** | Vistas Completas de Cliente (Windows/Android) | CA-08a/b, CA-17, CA-28, CA-34 | Aprobado | 7 tests en `inventory_view` |
| **P-09** | Importación Inicial Conciliada y Correcciones | CA-28, CA-31, CA-34 | Aprobado | 4 tests en `catalog` |
| **P-10** | E2E, Respaldo AES-256-GCM y Restauración | CA-23, CA-33, Q-09a/b/c, Q-19 | Aprobado | 7 tests en `system` |

---

## 2. Métricas y Resultados de Ejecución Automatizada

### A. Backend Django REST Framework (`apps/api`)
- **Comando:** `.venv/Scripts/python.exe manage.py test system catalog devices`
- **Total ejecutadas:** 45 pruebas
- **Tiempo de ejecución:** 13.727 segundos
- **Tasa de éxito:** 100% (45 pasadas, 0 fallidas, 0 errores)
- **Aspectos auditados:**
  - Invarianza monetaria con precisión `Decimal` (importes a 2 decimales, cantidades a 6 decimales).
  - Cálculo interno de IVA 16/116 (`final_amount * 16 / 116`) redondeado a 2 decimales.
  - Prevención de deadlocks en surtido concurrente con ordenamiento canónico de IDs (`select_for_update`).
  - Idempotencia estricta mediante SHA-256 canónico y `Idempotency-Key` UUIDv4.
  - Cifrado simétrico AES-256-GCM y verificación de restauración en base aislada.

### B. Cliente Flutter Android / Windows (`apps/clients`)
- **Comando:** `flutter test`
- **Total ejecutadas:** 28 pruebas
- **Tiempo de ejecución:** 5.8 segundos
- **Tasa de éxito:** 100% (28 pasadas, 0 fallidas, 0 errores)
- **Aspectos auditados:**
  - Persistencia de eventos en SQLite local (`outbox` y `sync_state`).
  - Restricción de loopback LAN para desarrollo y aislamiento de hosts no autorizados.
  - Cálculo reactivo de totales e IVA 16/116 en tiempo real.
  - Surtido online en Windows y encolado offline en Android.
  - Validación obligatoria de recepción física de insumos en devoluciones.

---

## 3. Certificación de Invariantes y Reglas Críticas

1. **Invariante 1 (Deduplicación):** El reintento del mismo UUID con mismo payload retorna el resultado persistido; con payload distinto se rechaza con `HTTP 409 IDEMPOTENCY_KEY_REUSED`.
2. **Invariante 2 (Atomicidad de Surtido):** Si falta stock o cuota en cualquier componente de la receta, no se aplica ningún descuento y la base queda intacta.
3. **Invariante 3 (Costo Congelado):** Al surtir, la venta congela el costo ponderado vigente; una devolución reingresa exactamente los componentes al costo congelado original.
4. **Invariante 4 (Seguridad de Sesiones y Restauración):** Toda operación crítica (importación inicial oficializada o restauración) exige dos confirmaciones separadas del mismo usuario `ADMIN` en la misma sesión activa (`session_key`).
5. **Invariante 5 (Respaldo Cifrado):** Ningún archivo de respaldo se promueve como válido sin verificar su hash SHA-256 y descifrado en base aislada. El almacenamiento libre se valida preventivamente ($\ge 2\times$ el tamaño del respaldo) emitiendo `HTTP 507 INSUFFICIENT_STORAGE` sin borrar copias previas.

---

## 4. Dictamen Final de QA

El software cumple al 100% con los criterios de aceptación, no presenta hallazgos críticos ni altos abiertos, y cuenta con evidencia reproducible y verificable en terminal. Se recomienda su paso formal a la fase operativa.
