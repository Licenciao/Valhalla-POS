# Reglas de negocio y métricas

Las reglas de este documento resumen las decisiones aprobadas. `09-pseudocodigo.md` es la especificación detallada y prevalece para algoritmos, estados y casos límite.

## Periodos

- El periodo mensual comprende el primer día del mes seleccionado hasta el primer día del mes siguiente, usando la zona fija UTC-06:00.
- El acumulado anual comprende desde el 1 de enero del año seleccionado hasta la fecha de consulta; el año actual es el valor inicial.
- Un trimestre contiene tres meses calendario: T1 enero-marzo, T2 abril-junio, T3 julio-septiembre y T4 octubre-diciembre.
- En caso de consultar el acumulado anual de un año distinto al actual, el periodo se ajusta a 1 de enero hasta 31 de diciembre del año seleccionado.

## Unidades y existencias

Para componentes controlados:

`existencia = saldo inicial oficial + entradas + devoluciones completas + ajustes positivos - consumos por ventas - ajustes negativos`

- La venta normal consume componentes de receta y no mantiene stock de producto terminado. Una devolución válida reingresa todos los componentes controlados de la receta congelada a su costo original.
- Un ajuste siempre requiere motivo y usuario responsable.
- Una excepción administrativa auditada puede aceptar un evento offline revocado con saldo negativo. El déficit queda pendiente y las entradas posteriores del componente lo cubren primero por tramos.
- Valhalla 750 consume 0.750 L Ensamble, una botella 750 y un corcho sintético; Mini 375 consume 0.375 L 1 Dest., una botella 375 y un corcho de madera.
- Etiquetas y termoadheribles no son componentes de receta ni se descuentan por botella; son inventario auxiliar manual y sus compras son gastos generales.
- Las ventas pendientes no reservan stock ni se sincronizan/reportan hasta surtir. Cada producto es una venta independiente; no hay ticket conjunto.
- Unidades de piezas enteras; fraccionables hasta 3 decimales. Unidad y conversión de un artículo quedan fijas tras su primer movimiento.

## Promedio ponderado

Para una entrada al inventario de un artículo valorado:

`nuevo costo promedio = (existencia anterior × costo promedio anterior + cantidad recibida × costo unitario de entrada) / (existencia anterior + cantidad recibida)`

Se usa promedio móvil: recalcular el costo promedio al confirmar cada entrada. Cada salida conserva el costo aplicable al surtir.

Un ajuste positivo requiere costo unitario y actualiza el promedio ponderado; un ajuste negativo utiliza el promedio vigente. Una entrada, devolución o ajuste positivo no se confirma si queda saldo positivo previo sin promedio conocido, hasta valoración administrativa. Las ventas normales requieren costos conocidos; solo el déficit aprobado de un conflicto queda pendiente y se valoriza por tramos con entradas posteriores. Los saldos iniciales son provisionales hasta conteo y costeo físico.

El costo reportado inicial del líquido es $80/L. Los costos de botellas y corchos se verifican físicamente antes de oficializar saldos.

## Ingresos, costos y ganancia

Fórmulas acordadas para las métricas internas:

- `ventas netas = importes finales de ventas surtidas - reembolsos de devoluciones`
- `costo de ventas = costos directos de ventas - costos reingresados por devoluciones`
- `ganancia antes del factor = ventas netas - costo de ventas - gastos generales`
- `ganancia después del factor = ganancia antes del factor × 0.84`

Los precios vigentes iniciales son $500 para Valhalla 750 y $260 para Mini 375, con impuesto incluido. El impuesto desglosado es `importe_final × 16 / 116`; esta cifra y el factor de ganancia del 84% son métricas internas y no cálculo fiscal. El factor de ganancia no vuelve a restar el impuesto desglosado.

### Gastos configurables

- Cada gasto requiere nombre e importe.
- Se permiten gastos de una sola ocasión y recurrentes; recurrentes requieren fecha inicial y pueden tener fecha final opcional.
- Los gastos son generales del negocio, no se asignan ni prorratean por producto.
- Se permiten gastos únicos o recurrentes mensuales/anuales; se registra el importe completo en la ocurrencia.
- La primera ocurrencia es la fecha inicial; el día inexistente usa el último día del mes. No se duplican ocurrencias.
- Editar una plantilla recurrente actualiza sus ocurrencias históricas con auditoría.
- Gastos de apertura aprobados: etiquetas $3,250; banda $375; tarjetas $840; termoadheribles $46 y $40; uniformes $1,649, $1,649, $150 y bordados $540. Total $8,539 MXN.

### Impuestos

- El impuesto incluido es 16%, desglosado con `importe_final × 16 / 116`.
- Ganancia antes del factor usa ventas con impuesto incluido, menos costo de ventas y gastos generales; después del factor equivale a 84%.
- Ambas métricas son de gestión y no sustituyen declaraciones ni cálculos fiscales.


## Métricas de la tabla

Por producto y periodo seleccionado:

- Existencia actual: saldo a la fecha de consulta, independiente del filtro histórico.
- Unidades vendidas netas: ventas menos devoluciones que revierten unidades.
- Precio de venta: precio vigente del artículo.
- Costo promedio: costo vigente del componente.
- Importe de ventas netas, costo directo y margen de contribución antes de gastos por producto.
- Ganancia del negocio después de gastos generales y aplicación del factor de 84%.

- “Acumulado anual” filtra ventas y devoluciones por sus fechas efectivas del año seleccionado; la existencia siempre muestra el saldo actual.

## Gráficas

- Una gráfica independiente por producto.
- Selector de agrupación mensual (12 barras) o trimestral (4 barras) y selector de año.
- Selector de métrica: unidades netas vendidas o importe neto de ventas. No combinar cantidades y dinero en un mismo eje.
- Incluir cero para periodos sin operaciones.
- Identificar claramente moneda y unidades.
- Las ventas se agrupan por fecha de surtido y las devoluciones por fecha real; el mismo filtro y reglas aplican a tabla y gráfica.
- Los periodos mensuales/trimestrales vacíos se devuelven en cero; las gráficas ofrecen 12 meses o 4 trimestres y una sola escala por eje.
