# Requisitos y glosario

## Requisitos funcionales

### Catálogo e inventario

- **RF-01:** Crear, consultar, modificar y desactivar productos sin borrar su historial.
- **RF-02:** Registrar entradas de inventario con cantidad, costo unitario, fecha y referencia.
- **RF-03:** Registrar cada producto como una venta independiente con cantidad, precio y fecha. Una venta pendiente de surtir es local al dispositivo, no reserva existencias ni se incluye en métricas; al surtirse consume su receta y queda reportable.
- **RF-04:** Registrar devoluciones vinculadas a ventas surtidas, con reembolso proporcional y recepción completa de los componentes controlados. Reingresan a sus costos originales; devolución y venta se reportan en sus respectivos periodos. Offline, solo puede realizarse desde el dispositivo de origen.
- **RF-05:** Registrar ajustes con motivo y autorización; conservar el saldo anterior y el movimiento que lo modificó.
- **RF-06:** Consultar existencia, unidades netas, precio, costos, ingresos y margen de contribución por producto; mostrar gastos generales y ganancia después del factor a nivel negocio.
- **RF-07:** En la tabla, alternar entre el mes seleccionado y el acumulado del año calendario seleccionado.

### Costos, gastos e impuestos

- **RF-08:** Calcular costo promedio ponderado móvil al confirmar cada entrada, conforme a `02-reglas-de-negocio-y-metricas.md`.
- **RF-09:** Registrar gastos generales únicos o recurrentes mensuales/anuales, sin prorrateo ni asignación a producto. Generar ocurrencias sin duplicados y auditar cambios.
- **RF-10:** Usar precios finales con impuesto incluido; desglosar el impuesto incluido como `importe_final × 16 / 116`.
- **RF-11:** Mostrar ventas, costos, gastos e impuesto separadamente. Ganancia antes del factor = ventas netas (con impuesto incluido) menos costo de ventas y gastos generales; ganancia posterior = 84% de esa métrica. No es cálculo fiscal.

### Estadísticas

- **RF-12:** Mostrar gráficas de barras separadas por producto.
- **RF-13:** Permitir agrupar por mes o trimestre, elegir el año y seleccionar unidades netas o importe de ventas.
- **RF-14:** Incluir periodos sin ventas con valor cero y devolver resultados consistentes con los registros de ventas y devoluciones.

### Acceso y sincronización

- **RF-15:** Android funciona offline con SQLite cifrado y cuotas por dispositivo. Windows principal aloja Django REST Framework y PostgreSQL locales; el cliente Windows mantiene una caché SQLite cifrada de solo lectura, pero todas sus escrituras requieren API/PostgreSQL. No se requiere internet público para operar.
- **RF-16:** Android sincroniza por Wi-Fi/LAN solo con app en primer plano y servidor Windows activo. Outbox con UUID/secuencia se conserva hasta ACK; un conflicto pausa la cola y se muestra explícitamente.
- **RF-17:** Administrador autenticado en Windows gestiona datos maestros y conflictos; los dispositivos Android se emparejan por QR de un solo uso y credencial propia. La selección offline de operador en Android es declarativa, no autenticación personal.

## Glosario

| Término | Definición de trabajo |
|---|---|
| Producto | Artículo del catálogo, vendible o insumo. Valhalla y Valhalla Mini son artículos vendibles sin saldo propio de producto terminado; el surtido consume líquido y envase. |
| Existencia actual | Cantidad derivada de movimientos confirmados; una devolución válida reingresa todos los componentes controlados. Los saldos iniciales provisionales se muestran por separado. |
| Entrada | Movimiento que aumenta una existencia y puede actualizar el costo promedio. |
| Venta bruta | Importe de partidas surtidas con los precios capturados, que incluyen impuesto. |
| Venta neta | Importe final de ventas surtidas menos reembolsos de devoluciones, por fecha efectiva de cada movimiento; los importes incluyen impuesto. |
| Unidades netas vendidas | Unidades surtidas menos unidades devueltas, por sus fechas efectivas de surtido/devolución. |
| Costo promedio ponderado | Costo unitario calculado con promedio móvil al confirmar entradas. |
| Costo de ventas | Costo aplicado a ventas surtidas menos el costo original revertido por devoluciones aceptadas. |
| Gasto configurable | Gasto general del negocio, único o recurrente mensual/anual, sin prorrateo ni asignación a producto. |
| Ganancia antes del factor | Ventas netas menos costo de ventas y gastos generales. |
| Ganancia después de impuestos | Métrica interna equivalente al 84% de la ganancia después de costo de ventas y gastos; no representa por sí misma un cálculo fiscal. |
| Sincronización | Envío idempotente por Wi-Fi/LAN entre Android y la PC Windows principal; no requiere internet. |

## Requisitos no funcionales

- Flutter/Dart en Android (mínimo Android 12/API 31) y Windows (última versión GA de Windows 11 aún soportada al desplegar; PC con edición Home); el backend en la PC usa Python, Django REST Framework y PostgreSQL. Android conserva datos locales/outbox en SQLite cifrado; Windows usa SQLite cifrado como caché de lectura, nunca como outbox ni autoridad de escritura.
- TLS en la LAN con certificado local fijado por huella compartida vía QR; la PC usa IP reservada y no expone API a internet.
- Respaldo cifrado diario a Drive a las 22:00 UTC-06:00 mientras la aplicación Windows esté abierta; una ejecución perdida queda pendiente al siguiente inicio de la aplicación. Retención remota de 30 días; si Drive no está disponible, se conserva la copia cifrada local y se reintenta en la siguiente ejecución diaria. Las copias locales no se eliminan antes de verificar la carga; su limpieza posterior se configura. Restauración aislada mensual y después de cambios importantes de instalación o esquema. La contraseña se conserva en gestor externo.
- Ventas Android se limitan a cuota por componente; Windows vende del stock central no asignado. No se transfieren cuotas entre teléfonos.
- Todo movimiento es auditable, monetario decimal y transaccional. Sin ACK no se marca sincronizado; pendientes de venta no reservan stock ni se sincronizan/reportan hasta surtir.
- Revalidar antes del despliegue la versión GA de Windows 11 con soporte vigente; como referencia al 9 de noviembre de 2026, la versión actual es 26H2. Volumen esperado y política de limpieza local de respaldos verificados se configuran antes del despliegue.
