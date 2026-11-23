# Resumen y alcance

## Objetivo

Construir, después de validar la documentación, un sistema local de inventario para Android y Windows. Android puede operar offline dentro de sus cuotas; Windows es el host central y sus escrituras requieren la API/PostgreSQL. Los clientes se comunican por Wi-Fi/LAN con la PC Windows principal. No se contrata hosting ni se requiere internet público para las operaciones del negocio.

## Usuarios y propósito

- **Administrador:** administra catálogo, recetas, precios, cuotas/dispositivos, costos, gastos, ajustes y conflictos; se autentica en Windows.
- **Operador:** captura entradas, ventas y devoluciones. En Android se selecciona de una lista sincronizada; la selección offline es declarativa, no autenticación personal.

## Áreas principales

### Inventario

Tabla de productos con precio, costo promedio ponderado, margen de contribución antes de gastos, existencia y ventas. La ganancia después de gastos se muestra para el negocio. Debe permitir alternar entre:

- acumulados desde el inicio del año calendario actual hasta la fecha;
- datos del mes seleccionado.

La vista también requiere acciones de captura para que las cifras se mantengan actualizadas: entradas/recepciones, ventas, devoluciones aceptadas y ajustes autorizados.

### Estadísticas

Gráficas de barras separadas por producto. El usuario elige comparar periodos mensuales o trimestrales y selecciona la métrica: unidades netas vendidas o importe de ventas. Mostrar los periodos sin ventas como cero y permitir elegir el año.

## Alcance inicial

- Catálogo de productos y componentes/materiales.
- Registro trazable de entradas, ventas, devoluciones y ajustes.
- Existencias actuales y unidades netas vendidas por periodo.
- Costo de ventas por promedio ponderado.
- Precio de venta, ingresos, gastos configurables e impuestos porcentuales.
- Resumen de inventario anual o mensual y estadísticas mensuales/trimestrales por producto.
- Cliente Flutter/Dart para Android y Windows.
- Backend local Python/Django REST Framework y PostgreSQL en la PC Windows principal; Android usa SQLite cifrado para operación offline y Windows mantiene una caché SQLite cifrada de solo lectura.
- Operación offline por cuotas Android y stock central no asignado en Windows; sincronización idempotente por Wi-Fi/LAN cuando las aplicaciones están activas.
- Respaldo cifrado diario a la cuenta de Google Drive existente, programado a las 22:00 UTC-06:00 mientras la aplicación Windows está abierta; si se pierde la ejecución, se procesa al siguiente inicio de la aplicación. Drive conserva 30 días y las copias locales pendientes se conservan hasta verificar la carga.

## Fuera del alcance confirmado

Fuera de alcance: ERP, lectores de códigos de barras, múltiples almacenes, compras a proveedores como módulo independiente, facturación fiscal y contabilidad formal. El servicio no se expone a internet; la sincronización es únicamente LAN.

## Restricciones y principios

- GitHub se utilizará para alojar código, documentación y colaboración; no como base de datos operativa.
- No convertir cifras iniciales discrepantes en existencias oficiales sin conciliación.
- Mantener movimientos que expliquen el saldo; no sobrescribir existencias sin dejar registro.
- La ganancia reportada debe distinguirse de utilidad contable/fiscal; el factor del 84% es una métrica interna.

## Validación antes de la implementación

El pseudocódigo aprobado en `09-pseudocodigo.md` es la especificación funcional de referencia para reglas y algoritmos. Los documentos `00`–`08` ya están armonizados con ella. Antes del primer despliegue se configurarán fecha efectiva de arranque, unidades/conversiones de nuevos artículos y limpieza local de respaldos ya verificados. La restauración se probará mensualmente y después de cambios importantes de instalación o esquema. La conciliación física del inventario inicial sigue siendo requisito operativo; no bloquea la implementación del sistema.
