# Flujos y pantallas

## Navegación principal

Dos áreas de primer nivel, disponibles en Flutter para Android y Windows:

1. **Inventario:** tabla/lista y captura de operaciones.
2. **Estadísticas:** gráficas por producto.

El diseño debe adaptarse a pantalla táctil Android y a ventanas/resoluciones de Windows. En Android, mostrar claramente el modo offline, cuota disponible y estado de sincronización. En Windows, indicar la disponibilidad de la API y cuándo los datos de lectura de la caché local se actualizaron; si API/PostgreSQL no está disponible, permitir lecturas desde una caché íntegra con aviso visible de obsolescencia y bloquear escrituras. Si no hay caché válida, mostrar error, no datos ficticios.

## Inventario

Columnas informativas propuestas:

- Producto y unidad.
- Existencia actual.
- Precio de venta.
- Costo promedio ponderado.
- Unidades netas vendidas en el periodo elegido.
- Importe de venta, costo directo y margen de contribución antes de gastos; ganancia general en el resumen del negocio.

Controles:

- Selector de vista: **Mes** o **Acumulado anual**.
- Selector de mes/año.
- Indicador visible del periodo, origen de datos, hora de última sincronización y conflictos.
- Acciones autorizadas: registrar entrada, venta, devolución y ajuste.

### Flujo de venta

1. Elegir un producto; cada producto es una venta independiente.
2. Ingresar cantidad, precio de partida y descuento; validar receta, costos conocidos y disponibilidad.
3. **Android:** validar cuota local. Si no alcanza, guardar solicitud pendiente solo en ese dispositivo; no reserva stock, no se sincroniza y no se reporta. Si alcanza, surtir todos los componentes en una transacción local y crear evento UUID/secuencia en outbox; mostrarlo como pendiente hasta el ACK.
4. **Windows:** enviar la venta a la API para validar y aplicar contra el stock central no asignado. Confirmarla solo tras respuesta satisfactoria de la transacción central; si la API/PostgreSQL no está disponible, informar el error y no confirmar ni encolar la venta.
5. En Android, explicar errores/conflictos, conservar el evento original y pausar la cola en el primer conflicto.

### Flujo de entrada

1. Seleccionar componente, cantidad, unidad, costo del comprobante y referencia opcional.
2. Mostrar el efecto estimado en existencias y costo promedio.
3. **Android:** confirmar localmente y encolar el evento; al sincronizar, la entrada aumenta el stock central no asignado y nunca amplía una cuota Android automáticamente.
4. **Windows:** enviar la entrada a la API y mostrarla como confirmada solo después de la respuesta satisfactoria de la transacción central. Si la API/PostgreSQL no está disponible, informar el error y no confirmar ni encolar la entrada.

### Flujo de devolución

1. Encontrar venta original y partidas.
2. Exigir venta surtida; en Android offline, exigir que sea del dispositivo actual; validar unidades aún retornables.
3. Confirmar recepción completa de todos los componentes controlados de la receta congelada.
4. Calcular reembolso proporcional sin exceder el pago original.
5. **Android:** registrar localmente, reingresar componentes a sus costos originales, actualizar cuota y encolar el evento hasta ACK.
6. **Windows:** enviar la devolución a la API y mostrarla como confirmada solo después de la respuesta satisfactoria de la transacción central. Si la API/PostgreSQL no está disponible, informar el error y no confirmar ni encolar la devolución.

## Estadísticas

- Selector de año.
- Selector de agrupación: mensual o trimestral.
- Selector de métrica: unidades netas o importe neto.
- Una gráfica por producto, con periodos en orden cronológico e incluyendo ceros.
- Etiquetas de eje con unidad o moneda.
- Ventas por fecha de surtido y devoluciones por fecha real; cortes mensuales/trimestrales con límite final excluyente.
- Estado vacío cuando aún no hay productos o ventas.
- Indicar fallas de carga y permitir reintentar sin presentar información desactualizada como actual.

## Estados que todos los agentes deben contemplar

- Cargando.
- Sin datos.
- Error de almacenamiento local/servidor LAN.
- Sin sincronizar, sincronización en curso, conflicto, dispositivo revocado.
- Sesión no autenticada o permiso insuficiente.
- Guardado confirmado.
- Validación rechazada.
- Conflicto o duplicado de operación.

La app Windows principal solo ofrece sincronización mientras está abierta; abrir Windows no fuerza una transmisión desde un Android cerrado. Los wireframes y textos deben indicar que las operaciones locales pueden no estar aceptadas por el servidor todavía.
