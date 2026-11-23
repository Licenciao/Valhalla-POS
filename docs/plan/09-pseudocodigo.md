# Pseudocódigo del sistema offline-first

**Estado: aprobado como especificación funcional. Tras completar y aprobar P-03, el responsable autorizó comenzar P-04/P-05; este documento sigue siendo especificación, no código ejecutable.**

Este documento consolida las decisiones elegidas en la conversación del 7 de noviembre de 2026 y aprobadas antes de armonizar el resto de `docs/plan/`. Los documentos `00`–`08` ya se actualizaron para que no contradigan esta especificación.

## 1. Arquitectura y límites

- La PC Windows principal es la autoridad central y ejecuta Django REST Framework y PostgreSQL localmente.
- Flutter/Dart implementa los clientes Android y Windows. Android usa SQLite cifrado para catálogo sincronizado, cuotas locales y outbox. Windows mantiene SQLite cifrado solo como caché de lectura; PostgreSQL sigue siendo la autoridad y las escrituras Windows requieren API disponible, sin outbox offline. Python implementa servidor y lógica de dominio; el cliente no queda escrito en Python.
- La API de la PC solo está disponible mientras la aplicación Windows está abierta. La aplicación Windows inicia el servidor, actualiza/valida su caché SQLite local de solo lectura desde PostgreSQL y después presenta su interfaz.
- Si API/PostgreSQL no está disponible, Windows permite lecturas desde una caché íntegra marcada claramente como potencialmente obsoleta; si no hay caché válida, muestra error. Ninguna escritura se confirma ni se encola desde Windows sin respuesta satisfactoria de la API.
- La ruta base del contrato es `/api/v1`: cambios aditivos compatibles permanecen en v1; cambios incompatibles requieren una ruta mayor nueva. El servidor informa explícitamente incompatibilidad y no descarta eventos pendientes del cliente.
- `GET /health` verifica que la API y PostgreSQL estén disponibles; responde `503 SERVICE_UNAVAILABLE` si la base no responde. El `instance_id` es el UUID estable de una fila singleton en PostgreSQL y permanece igual en reinicios y restauraciones.
- El contrato se documenta en OpenAPI; los listados paginados usan cursor cuando aplique. Toda escritura Windows distinta de login/logout lleva `Idempotency-Key` UUID: clave y payload repetidos devuelven el resultado persistido, y la misma clave con payload distinto se rechaza. Los eventos Android usan UUID de evento. Una respuesta de error contiene `code`, `message`, `request_id`, `retryable` y `details`. Ningún error confirma escritura parcial. Se acepta la versión actual y anterior de Android durante 12 meses desde cada publicación; después se exige actualización para sincronizar, con aviso en la app y outbox intacta si el cliente resulta incompatible.
- Android sincroniza al abrir o volver a primer plano cuando tiene Wi-Fi hacia la PC. No se ejecuta sincronización en segundo plano. Abrir Windows no puede forzar a un teléfono con la aplicación cerrada a transmitir su cola.
- Solo se permite la red local Wi-Fi/LAN; no se publican puertos en Internet. El router reserva una IP para la PC. La API HTTPS usa TCP 8443, permitido solo en perfil privado/LAN; PostgreSQL permanece local. HTTPS local usa certificado autofirmado cuya huella se fija al emparejar el dispositivo mediante QR de un solo uso.
- Cada Android genera una clave exclusiva ECDSA P-256/SHA-256 en Android Keystore; la clave privada no se exporta y el servidor conserva la clave pública en SPKI DER/Base64URL sin padding; `key_id` es SHA-256 de esos bytes en Base64URL sin padding. Cada solicitud firma versión de protocolo, instancia, `key_id`, método, ruta de segmentos ASCII/UUID y query vacía, SHA-256 de los bytes exactos del cuerpo, timestamp y nonce. Los endpoints Android firmados no usan query; si necesitan cursor, lo llevan en la ruta. Los campos se codifican UTF-8 en orden fijo separados por LF; la firma ECDSA DER se transporta Base64URL sin padding. Timestamp tolerado ±5 minutos; nonce aleatorio de 128 bits se consume atómicamente y se retiene 15 minutos para rechazar replay. El QR dura 5 minutos, es de un solo uso y contiene endpoint LAN, ID de instancia, huella TLS y token aleatorio de 256 bits; Windows conserva solo SHA-256 del token y lo consume atómicamente. El administrador inicia el alta en Windows y el primer escaneo válido completa el emparejamiento. La credencial no expira automáticamente; revocación/reemplazo requieren decisión administrativa. Un nuevo emparejamiento genera una clave nueva; solo se conserva identidad/secuencia si el administrador confirma que es el mismo equipo. Un equipo perdido recibe identidad nueva. La huella TLS del servidor es independiente y su renovación requiere nuevo emparejamiento QR. Si se pierde la respuesta de alta, mismo `pairing_request_id`, QR y clave pública reproducen el resultado persistido; una combinación distinta falla y, si el QR venció sin que el alta ocurriera, se genera uno nuevo.
- La clave SQLite Android es aleatoria, se genera localmente durante instalación/alta, es no exportable, no se respalda y se protege en Android Keystore; si se pierde, la outbox puede ser irrecuperable y el dispositivo requiere reemparejamiento. La rotación se realiza por acción administrativa ante reemplazo o compromiso. La caché SQLite Windows usa DPAPI del usuario de la aplicación; si no puede descifrarse, se muestra error y solo se reconstruye desde PostgreSQL disponible. La clave privada TLS se guarda en el almacén de certificados Windows con ACL restringida a la aplicación. La clave/contraseña de respaldos se custodia en un gestor externo por el administrador y un segundo custodio, que se designará antes de activar respaldos; se debe comprobar su recuperación en una restauración aislada.
- PostgreSQL no acepta conexiones desde otros dispositivos: únicamente la API local se comunica con la base central.
- La PC realiza un respaldo cifrado diario a las 22:00 en UTC-06:00, mientras la aplicación Windows está abierta. Si la hora se pierde porque la app no estaba abierta, ejecuta el respaldo pendiente al siguiente inicio de la aplicación. Drive conserva 30 días. Si Drive no está disponible, se conserva la copia cifrada local y se reintenta en la siguiente ejecución diaria; no se elimina la copia local antes de verificar la carga. Tras verificar, se conserva el último respaldo local válido y las siete copias diarias anteriores. Se alerta si el espacio libre es menor a dos veces el tamaño del último respaldo cifrado válido, sin borrar copias previas ni promover una incompleta. Si aún no hay respaldo válido, se estima desde el tamaño actual de PostgreSQL y se exige espacio libre de al menos dos veces la estimación; si no alcanza, se alerta y no se borran copias. La restauración se prueba mensualmente en un entorno aislado y también después de cambios importantes de instalación o esquema. La clave/contraseña de cifrado se conserva en un gestor externo, bajo custodia del administrador y un segundo custodio que se designará antes de activar respaldos.
- Si la PC está apagada o la aplicación Windows está cerrada, los Android pueden seguir operando con sus datos y cuotas locales, pero no pueden sincronizar entre sí ni con la PC.

## 2. Parámetros de negocio acordados

```text
MONEDA = MXN
ZONA_NEGOCIO = UTC-06:00 fijo todo el año
TASA_IMPUESTO_INCLUIDO = 0.16
FACTOR_IMPUESTO_INCLUIDO = 16 / 116
FACTOR_GANANCIA_POSTERIOR = 0.84
FACTOR_DEDUCCION_METRICA = 0.16
DECIMALES_CANTIDAD = 3 para unidades fraccionables
DECIMALES_PIEZAS = 0
REDONDEO_MONETARIO = mitad hacia arriba, a 2 decimales por operación contabilizada
```

Los precios capturados ya incluyen el impuesto. El impuesto desglosado es `importe_final × 16 / 116`. La métrica interna de ganancia usa ventas con impuesto incluido; **no** resta nuevamente el impuesto desglosado. No se presenta como cálculo fiscal.

La ganancia después del factor es `ganancia_antes_del_factor × 0.84`. En los reportes se muestran separadamente el importe de venta, impuesto desglosado, costo directo, gastos generales y ambas métricas de ganancia.

## 3. Modelo lógico mínimo

```text
Producto:
    id, nombre, activo, unidad_venta, precio_vigente,
    receta_version, creado_en, actualizado_en

Componente:
    id, nombre, unidad_base, precision, activo,
    existencia_central_no_asignada, costo_promedio, costo_conocido

Receta:
    producto_id, version, componente_id, cantidad_por_unidad
    Inmutable después del primer movimiento que la use.

Dispositivo:
    id, identidad_criptografica, referencia_credencial/estado,
    huella_tls_servidor_fijada, estado (activo/revocado),
    siguiente_secuencia_esperada, emparejado_en, revocado_en

CuotaDispositivo:
    dispositivo_id, componente_id, cantidad_disponible
    No se transfiere entre dispositivos.

MovimientoInventario:
    id, componente_id, tipo, cantidad, costo_unitario?,
    origen, referencia, actor, fecha_operacion, recibido_en,
    estado (provisional/confirmado/conflicto)

OperacionOffline:
    idempotency_key, dispositivo_id, secuencia, actor_seleccionado,
    tipo, payload, fecha_local, creado_en_local,
    estado (pendiente/confirmada/conflicto/rechazada), respuesta_servidor?

Venta:
    id, producto_id, receta_version, cantidad, precio_snapshot,
    tipo_descuento?, descuento_snapshot, importe_pagado,
    impuesto_incluido, costo_por_componente, costo_total?,
    fecha_solicitud, fecha_surtido?, recibido_en?,
    dispositivo_id?, actor_seleccionado, estado

Devolucion:
    id, venta_id, cantidad, importe_reembolsado, impuesto_revertido,
    componentes_recibidos, fecha_local, recibido_en?, dispositivo_id, estado

Gasto:
    id, nombre, importe, categoria, recurrencia?, fecha_inicio,
    fecha_fin?, fecha_ocurrencia, estado, auditoria

EventoAuditoria:
    actor, dispositivo?, accion, entidad, valores_anteriores?,
    valores_nuevos?, motivo?, fecha_local, recibido_en?
```

Toda operación conserva su historial. Nunca se borran físicamente ventas, entradas, gastos confirmados ni eventos sincronizados.

## 4. Catálogo, recetas y disponibilidad

### Recetas iniciales

```text
VALHALLA_750:
    Ensamble:             0.750 L
    Botella_750:          1 pieza
    Corcho_sintetico:     1 pieza

VALHALLA_MINI_375:
    1_Dest:               0.375 L
    Botella_375:          1 pieza
    Corcho_madera:        1 pieza
```

Precios vigentes iniciales: Valhalla 750 MXN $500 y Mini 375 MXN $260, ambos finales con impuesto incluido. El operador puede cambiar el precio únicamente para la partida en curso; ese cambio no actualiza el precio vigente.

Etiquetas y termoadheribles no forman parte de estas recetas ni se descuentan por botella. Sus cantidades se ajustan manualmente como inventario auxiliar; sus compras se registran aparte como gastos generales. No tienen saldo inicial cargado.

Ensamble corresponde al líquido de 750 ml y 1 Dest. al líquido de 375 ml. El costo de líquido inicial reportado es MXN $80/L: el líquido de una Mini cuesta MXN $30 por 0.375 L; el valor previo de MXN $60 no se usa.

### Crear o editar productos

```text
CREAR_PRODUCTO(producto):
    exigir actor == ADMIN
    validar metadatos del producto
    guardar producto inactivo, sin receta ni precio inicial
    escribir auditoría
```

Las versiones iniciales de receta y precio se agregan en operaciones ADMIN separadas. El producto permanece inactivo hasta completar ambas y se activa automáticamente al guardar la segunda. Cambios posteriores de versiones no desactivan el producto.

```text
EDITAR_RECETA(producto, receta_nueva):
    exigir actor == ADMIN
    si la receta vigente ya tiene movimientos:
        rechazar edición de esa versión
        crear nueva versión solo mediante flujo explícito de producto/receta nueva
    si todavía no tiene movimientos:
        validar unidades y componentes
        sustituir receta
    auditar cambio
```

La unidad base y conversión de un componente quedan fijas desde su primer movimiento. Se podrán crear componentes adicionales desde Windows, pero no vender un producto nuevo hasta que precio, receta, costos y componentes estén configurados. No se mantiene existencia de producto terminado: la disponibilidad se deriva de sus componentes.

### Disponibilidad calculada

```text
DISPONIBLE(producto, fuente):
    receta = receta_vigente(producto)
    limite = infinito
    para cada (componente, cantidad_por_unidad) en receta:
        stock = saldo_asignado_a_dispositivo(componente)
               si fuente es un dispositivo Android
               si no, saldo_central_no_asignado(componente)
        limite = min(limite, unidades_completas(stock / cantidad_por_unidad))
    devolver limite
```

Se respetan las precisiones de unidad: piezas enteras; unidades fraccionables hasta tres decimales. Ninguna cantidad se convierte implícitamente entre unidades.

## 5. Inventario, costos y surtido

### Promedio móvil y ajustes

```text
REGISTRAR_ENTRADA(componente, cantidad, costo_unitario, actor):
    exigir cantidad > 0 y precisión válida
    exigir costo_unitario >= 0 y comprobante/referencia
    en una transacción:
        saldo = saldo_actual(componente)
        exigir entrada central; Android offline no aumenta su cuota local
        cantidad_para_deficit = min(cantidad, max(0, -saldo))
        asignar cantidad_para_deficit al costo pendiente de ventas afectadas,
            por orden de venta y al costo_unitario de esta entrada
        reducir el déficit y actualizar sus reportes históricos
        resto = cantidad - cantidad_para_deficit
        si resto > 0:
            saldo_positivo_previo = max(0, saldo)
            si saldo_positivo_previo > 0 y promedio previo no existe:
                rechazar entrada
                requerir valoración del saldo previo por administrador
            si saldo_positivo_previo == 0:
                promedio_nuevo = costo_unitario
            si saldo_positivo_previo > 0:
                promedio_nuevo = NUEVO_PROMEDIO(
                    saldo_positivo_previo, promedio_previo, resto, costo_unitario)
            agregar resto al stock central no asignado
            guardar promedio_nuevo
        registrar movimiento y auditoría
    confirmar transacción
```

```text
NUEVO_PROMEDIO(cantidad_actual, promedio_actual, cantidad_entrada, costo_entrada):
    si cantidad_actual == 0:
        devolver costo_entrada
    exigir promedio_actual existe
    devolver (cantidad_actual * promedio_actual
              + cantidad_entrada * costo_entrada)
             / (cantidad_actual + cantidad_entrada)
```

Un ajuste positivo exige costo unitario y recalcula promedio. Un ajuste negativo usa el promedio vigente. Solo el administrador puede ajustar existencias.

Si existe cantidad pero no costo promedio, el costo manual es opcional cuando ya hay promedio. Si falta promedio, solo un administrador puede ingresar costo unitario por componente para ese surtido; dicho costo queda auditado y no cambia el promedio. Android offline no puede surtir con costos faltantes: debe esperar a que el administrador configure/sincronice el costo.

Antes de confirmar cualquier ingreso positivo (entrada, devolución o ajuste) a un componente con saldo positivo sin promedio, el administrador debe valorar el saldo previo. Hasta entonces se conserva el movimiento como no confirmado y no se aumenta la disponibilidad.

```text
VALORAR_EXISTENCIA_SIN_COSTO(componente, costo_unitario, motivo, actor):
    exigir actor == ADMIN, motivo y cantidad_actual > 0
    exigir costo_unitario >= 0
    en transacción:
        registrar valoración del saldo completo aún sin costo promedio
        establecer costo_promedio = costo_unitario
        marcar costo_conocido = verdadero
        conservar los costos manuales históricos por venta sin reescribirlos
        auditar cantidad, costo, actor y motivo
    confirmar
```

```text
AJUSTAR_INVENTARIO(componente, delta, motivo, costo_unitario?, actor):
    exigir actor == ADMIN, motivo y ajuste conectado a Windows
    exigir delta != 0 y precisión válida
    en transacción:
        si delta < 0:
            exigir stock central no asignado >= abs(delta)
            exigir costo_promedio conocido
            reducir stock central no asignado
        si delta > 0:
            exigir costo_unitario >= 0
            si saldo positivo previo existe y costo_promedio falta:
                no confirmar; requerir VALORAR_EXISTENCIA_SIN_COSTO
            recalcular promedio móvil e incrementar stock central no asignado
        registrar movimiento y auditoría
    confirmar
```

### Asignar cuotas

```text
ASIGNAR_CUOTA(dispositivo, cantidades_por_componente, actor):
    exigir actor == ADMIN y dispositivo activo
    en transacción:
        para cada componente:
            exigir cantidad >= 0
            exigir cantidad <= stock central no asignado
            si cantidad > 0:
                exigir costo promedio conocido
        restar cantidades del saldo central no asignado
        sumar cantidades a la cuota del dispositivo
        publicar nueva versión de cuota al dispositivo
        auditar asignación
    confirmar
```

Las entradas nuevas se quedan en stock central no asignado hasta asignación manual. No hay transferencias de cuotas entre dispositivos. Windows vende del saldo central no asignado mediante la API; Android vende solo de su propia cuota. La cuota y el costo conocido de la última sincronización permiten a Android operar offline.

### Solicitar y surtir una venta

Cada producto vendido es una venta independiente; no hay ticket común. Una solicitud puede quedar pendiente, no reserva stock y conserva precio/descuento al crearse. No se permiten surtidos parciales.

```text
CREAR_SOLICITUD_VENTA(producto, cantidad, precio, descuento, actor, dispositivo):
    exigir producto activo y cantidad positiva con precisión válida
    precio_snapshot = precio ingresado o vigente al crear la solicitud
    exigir precio_snapshot >= 0
    validar descuento:
        solo un tipo: importe o porcentaje
        importe entre 0 y precio bruto, o porcentaje entre 0% y 100%
    importe_bruto = cantidad * precio_snapshot
    importe_descuento = calcular_descuento(importe_bruto, descuento)
    importe_final = importe_bruto - importe_descuento
    guardar solicitud pendiente solo en la base local del dispositivo
    no incluirla en reportes ni sincronizarla a otros dispositivos
    no reservar ni descontar stock
    auditar actor/dispositivo
```

```text
SURTIR_VENTA(venta_pendiente, fuente, actor):
    receta = receta_version_guardada_en_venta
    consumos = cantidad_venta * cantidad_por_unidad_de_cada_componente

    en una transacción local del dispositivo Android (offline) o central mediante la API (Windows):
        verificar que venta sigue pendiente
        para cada componente:
            verificar saldo de la fuente >= consumo requerido
            si no existe costo conocido:
                si offline: bloquear y solicitar sincronización con administrador
                si central: exigir costo manual del administrador para ese componente
        si algún saldo es insuficiente:
            rechazar este intento sin movimientos
            dejar la solicitud pendiente y sin reserva
            permitir reintento explícito o cancelación auditada
        para cada componente:
            descontar consumo
            guardar costo snapshot por cantidad consumida
        cogs = suma(consumo * costo_snapshot de cada componente)
        impuesto_incluido = redondear(importe_final * 16 / 116, 2, mitad_hacia_arriba)
        importe_pagado = redondear(importe_final, 2, mitad_hacia_arriba)
        confirmar venta como surtida
        guardar fecha_local_de_surtido y, al sincronizar, recibido_en
        guardar movimiento/costo/actor
        si es Android:
            consumir la cuota local en la misma transacción
            añadir evento UUID + secuencia creciente al outbox
        auditar
    confirmar
```

Los operadores pueden cambiar el precio y aplicar descuento por partida; el cambio vale solo para esa partida y no altera el precio vigente del catálogo. La falta de stock rechaza el intento de surtido, no borra el pedido pendiente.

### Cancelar o corregir una venta

```text
CANCELAR_VENTA_PENDIENTE(venta, motivo, actor):
    exigir venta pendiente y motivo
    marcar cancelada; conservarla en historial
    mantener registro solo en la base local de ese dispositivo
    no mover stock ni afectar métricas
    auditar
```

```text
CORREGIR_VENTA_SURTIDA(venta_incorrecta, venta_correcta, motivo, actor):
    exigir actor == ADMIN y motivo
    en transacción:
        registrar reverso auditable de importes, impuesto y costos originales
        reponer los componentes de la receta original a sus costos unitarios originales
        recalcular promedios móviles de componentes
        crear una venta nueva con datos correctos
        auditar ambos registros y su relación
    confirmar
```

## 6. Devoluciones

La devolución se registra en su fecha real y ajusta ventas netas/costo neto en ese periodo; no reescribe reportes anteriores. Solo puede devolver unidades de una venta ya surtida y no devueltas previamente. Cada unidad devuelta debe traer completos todos los componentes controlados de la receta congelada de esa venta. No hay devolución parcial de componentes.

```text
REGISTRAR_DEVOLUCION(venta, cantidad, dispositivo, actor):
    exigir venta surtida y devolución no superior a cantidad aún retornable
    si offline:
        exigir dispositivo == dispositivo_que_realizo_la_venta
    verificar recepción física de todos los componentes de la receta
    si falta cualquier componente:
        rechazar devolución sin reembolso ni movimiento
    en transacción:
        si algún componente a reingresar tiene saldo positivo sin promedio:
            no confirmar devolución ni reembolso
            conservar solicitud de devolución pendiente
            requerir valoración del saldo previo por administrador
            terminar
        factor_devuelto = cantidad / cantidad_original_vendida
        importe_reembolso = redondear(
            importe_pagado_original * factor_devuelto, 2, mitad_hacia_arriba)
        impuesto_revertido = redondear(
            impuesto_original * factor_devuelto, 2, mitad_hacia_arriba)
        si es la devolución final de esa venta:
            usar saldo no reembolsado/restante de impuesto para evitar exceder el original
        reingresar cada componente a su costo unitario original de venta
        recalcular promedio móvil de cada componente
        guardar devolución enlazada a la venta
        actualizar unidades/importe/costo netos en el periodo de devolución
        si offline: aumentar la cuota del mismo dispositivo y añadir evento al outbox
        auditar
    confirmar
```

## 7. Sincronización

```text
INICIAR_WINDOWS():
    iniciar PostgreSQL local si hace falta
    iniciar API Django en la IP LAN reservada
    verificar certificado/servicio y autenticación
    si API y PostgreSQL están disponibles:
        actualizar/validar atómicamente caché SQLite Windows de solo lectura
    si API o PostgreSQL no están disponibles:
        si la caché es íntegra:
            abrir vistas de solo lectura y marcar datos como posiblemente obsoletos
        si no:
            mostrar error y no presentar datos inventados
    generar ocurrencias de gastos recurrentes vencidas (idempotente)
    ejecutar respaldo vencido si el diario no se completó
    mostrar estado de servidor y última sincronización
```

```text
SINCRONIZAR_ANDROID(dispositivo):
    ejecutar solo con app en primer plano y Wi-Fi disponible
    verificar TLS por huella fijada, identidad y estado activo del dispositivo
    enviar eventos outbox ordenados por secuencia, sin borrarlos
    para cada evento:
        servidor verifica UUID/idempotencia y siguiente secuencia
        servidor aplica reglas de rol, receta, cuota, costos y estados
        si ese UUID ya tiene resultado terminal (confirmado o rechazado):
            devolver el resultado y ACK previamente persistidos, sin repetir efectos
        si evento válido:
            aplicar operación y movimientos, avanzar secuencia y guardar resultado ACK en una transacción
            si tipo == venta:
                descontar cuota del dispositivo
            si tipo == devolución:
                aumentar cuota del dispositivo con componentes recibidos
            si tipo == entrada:
                aumentar stock central no asignado, sin aumentar cuota
            registrar recibido_en y responder ACK
            cliente marca evento confirmado y solo entonces lo retira del outbox
        si evento inválido/conflictivo:
            conservar evento en servidor y cliente como conflicto
            pausar la cola de ese dispositivo
            notificar a administrador; no aplicar eventos posteriores aún
        si administrador resuelve conflicto con decision == RECHAZAR:
            conservar evento y payload como rechazados/no oficiales
            registrar decision y motivo en auditoría
            consumir la secuencia del evento sin aplicar movimientos
            responder ACK terminal de rechazo con siguiente secuencia esperada
            cliente conserva evento en historial y lo retira de la cola activa solo al recibir ACK
            continuar con el siguiente evento solo después de recibir ese ACK
    descargar cambios de catálogo, precios, cuotas, costos, usuarios habilitados
    actualizar SQLite local atómicamente
    mostrar conteo de confirmados y conflictos
```

Los datos maestros y costos se editan únicamente en Windows por el administrador. Cada operación offline guarda el costo y la versión de receta vistos al surtir; el servidor no sustituye silenciosamente esos snapshots. La fecha usada para ventas e inventario es la hora local del surtido en UTC-06:00 fijo; se conserva aparte la hora de recepción de la PC. No hay umbral automático de reloj desfasado.

Un evento de dispositivo revocado se rechaza inicialmente. El administrador puede aceptar un evento individual con auditoría. Si su aceptación deja negativo algún componente, registrar el déficit como costo pendiente; la siguiente entrada de ese mismo componente cubre primero el déficit por tramos a su costo de entrada y actualiza los reportes históricos de esas ventas. El remanente de la entrada, si existe, pasa al stock disponible y al promedio móvil. Si el administrador rechaza el evento, se conserva como no oficial y la discrepancia física se resuelve con conteo y ajuste auditado. El rechazo consume la secuencia del evento y genera un ACK terminal con resultado rechazado; el cliente conserva el payload/auditoría, lo retira solo de la cola activa tras recibir el ACK y puede continuar con la secuencia siguiente.

```text
RESOLVER_CONFLICTO(evento, decision, motivo, actor):
    exigir actor == ADMIN y motivo
    si evento ya fue resuelto:
        si UUID y decision coinciden con la resolución persistida:
            devolver el ACK terminal persistido sin repetir efectos
        si no:
            rechazar sin modificar la resolución terminal existente
    exigir evento en estado conflicto y conservar su payload original
    si decision == ACEPTAR:
        validar evento y dispositivo
        si dispositivo revocado:
            exigir aprobación explícita para ese evento
            permitir saldo negativo solo para los componentes que faltan
            registrar cantidades/costos pendientes por componente
        si dispositivo activo:
            exigir secuencia, receta, permisos, costo y cuota válidos
        aplicar evento sin duplicar operaciones previas
        registrar aceptación, actor, motivo y referencia al conflicto
    si decision == RECHAZAR:
        conservar el evento como no oficial/rechazado
        registrar actor y motivo
        consumir la secuencia del evento sin aplicar movimientos
    en una misma transacción, persistir decisión, auditoría, secuencia consumida y ACK terminal
    responder ACK terminal de resolución con siguiente secuencia esperada
    cliente conserva payload/historial y retira el evento de la cola activa solo al recibir ACK
    reanudar la cola solo después de ACK de resolución
```

La resolución no elimina la operación original ni su payload. Un rechazo no registra venta oficial; cualquier diferencia física posterior se documenta con conteo y ajuste auditado.

## 8. Entradas, gastos y auditoría

- Operador puede registrar entradas con cantidad y costo de comprobante, incluso offline. Al sincronizar, la entrada aumenta stock central no asignado, nunca la cuota Android automáticamente.
- Ajustes y gastos solo se registran en Windows con conexión; ajustes y plantillas recurrentes de gasto son solo de administrador. Operador puede registrar gastos únicos.
- Etiquetas/termoadherible se ajustan manualmente, no afectan recetas ni ventas. Compra de esos artículos y ajuste de sus cantidades son operaciones distintas y auditadas.
- Las compras de etiquetas, termoadherible y costos adicionales de producción se registran como gastos generales en la fecha de compra. No se prorratean al costo de cada botella. Uniformes también son gastos generales.
- No se asignan gastos generales a productos ni se calcula ganancia neta por producto. Por producto se muestra venta neta, costo directo y margen de contribución antes de gastos; la ganancia después de gastos se muestra solo para el negocio.
- D8-O3 permite gasto único o recurrente mensual/anual. Ocurrencia completa en la fecha programada, sin prorrateo. El inicio es la primera ocurrencia; si el día no existe, se usa el último día del mes. Al iniciar Windows se generan todas las ocurrencias vencidas con fecha programada y clave única para evitar duplicados.
- Editar una plantilla recurrente actualiza sus ocurrencias confirmadas y reportes, conservando el historial de valores. Una corrección puntual también queda auditada.
- En la fecha de arranque configurable, crear como gastos de apertura los importes reportados para etiquetas ($3,250), etiqueta banda ($375), tarjetas ($840), termoadheribles ($46 y $40) y uniformes ($1,649, $1,649, $150 y $540 de bordados). Total reportado de apertura: $8,539 MXN. No crear saldo inicial para etiquetas/termoadheribles.

```text
REGISTRAR_GASTO(nombre, importe, categoria, fecha, recurrencia?, actor):
    exigir actor en {ADMIN, OPERADOR} y importe >= 0
    exigir categoría = gasto general del negocio
    si es recurrente:
        exigir actor == ADMIN
        exigir frecuencia mensual o anual y fecha_inicio
        guardar plantilla con fecha_fin opcional
        crear ocurrencia completa en fecha_inicio
    si es único:
        crear ocurrencia en fecha
    guardar MXN, fecha, origen y auditoría
```

```text
GENERAR_GASTOS_RECURRENTES(fecha_hoy):
    para cada plantilla activa:
        mientras próxima_fecha <= fecha_hoy y
                (fecha_fin no existe o próxima_fecha <= fecha_fin):
            fecha_ocurrencia = fecha programada
            si fecha programada no existe:
                usar último día de ese mes
            crear gasto con clave única (plantilla_id, fecha_ocurrencia)
            avanzar al siguiente mes o año según frecuencia
    nunca duplicar una ocurrencia ya generada
```

Al editar una plantilla, actualizar sus ocurrencias confirmadas y reportes con auditoría de valores anteriores/nuevos. El gasto de apertura se registra una sola vez con una clave fija de importación. Si se corrige un gasto de apertura, conservar el original, registrar reversión/reingreso enlazados en el periodo original y recalcular los reportes de gastos afectados con auditoría antes/después.

## 9. Inventario inicial y ajustes

```text
CARGAR_SALDO_INICIAL_PROVISIONAL(datos):
    guardar cantidades como provisionales y claramente etiquetadas
    excluirlas de disponibilidad, costo oficial y reportes oficiales
    no permitir surtir contra saldo provisional
    registrar import_id único/idempotente y auditoría de importación

CONCILIAR_SALDO_INICIAL(conteo_fisico, costos_verificados, actor):
    exigir actor == ADMIN autenticado
    exigir dos confirmaciones explícitas separadas de la misma cuenta y sesión autenticadas
    confirmar físicamente litros, envases y corchos
    envases reportados se consideran vacíos
    verificar/capturar costos físicos de botellas y corchos
    líquido puede usar costo reportado de $80/L
    convertir provisional a movimientos iniciales oficiales auditados
    desde entonces hacerlo disponible central no asignado
    guardar ambas marcas de confirmación y un import_id único/idempotente

CORREGIR_IMPORTACION(import_id, cantidades_corregidas, gastos_corregidos, actor):
    exigir actor == ADMIN autenticado y dos confirmaciones explícitas separadas de la misma cuenta y sesión
    mostrar preview con cantidades, unidades, costos de referencia,
        diferencias, advertencias, gastos y estado provisional/oficial
    exigir conciliación física para oficializar la corrección de cantidades
    si import_id ya se corrigió con los mismos datos:
        devolver resultado persistido sin duplicar movimientos
    si import_id ya se corrigió con datos distintos:
        rechazar y requerir una nueva operación auditada
    para cada componente cuya cantidad cambió:
        delta = cantidad_corregida - cantidad_original_importada
        registrar movimiento compensatorio por delta con fecha actual
        preservar el costo promedio vigente y todos los costos snapshot de ventas
        si saldo actual queda negativo:
            marcar déficit provisional con costo pendiente y auditarlo
    cubrir déficits provisionales de cada componente con entradas posteriores
        antes de aumentar stock disponible, a costo de entrada por tramo
    para cada gasto de apertura corregido:
        conservar el gasto original
        registrar reversión/reingreso en su periodo original y enlazarlos
        recalcular solo reportes afectados por gastos, con valores antes/después auditados
    confirmar en una transacción e informar el resultado

```

Datos de referencia provisionales: 40 L de Ensamble, 20 L de 1 Dest., 72 botellas vacías de 750 ml, 12 botellas vacías de 375 ml, 72 corchos sintéticos y 12 corchos de madera. Los costos de botellas/corchos se recuestan físicamente antes de oficializar. La receta y costo de Mini no incluyen $60 de líquido: 0.375 L a $80/L = $30.

Los reportes transaccionales empiezan en la fecha de puesta en marcha. Los saldos iniciales no reconstruyen ventas ni gastos históricos.

## 10. Cálculos y reportes

```text
CALCULAR_OPERACION(venta):
    importe_bruto = cantidad * precio_snapshot
    importe_neto_linea = importe_bruto - descuento_snapshot
    importe_pagado = redondear(importe_neto_linea, 2, mitad_hacia_arriba)
    impuesto_incluido = redondear(importe_pagado * 16 / 116, 2, mitad_hacia_arriba)
    si algún componente tiene costo pendiente:
        costo_directo = PENDIENTE (no sustituir por cero)
    si todos los costos son conocidos:
        costo_directo = suma(consumos_receta * costo_snapshot)
    devolver importes y costo
```

```text
REPORTE(periodo):
    aceptar solo periodo mensual YYYY-MM, trimestral YYYY-Q1..Q4 o anual YYYY
    no aceptar rangos arbitrarios
    seleccionar ventas surtidas por fecha_surtido en UTC-06:00
    seleccionar devoluciones por fecha_real en UTC-06:00, aunque la venta original
        pertenezca a otro periodo
    ventas_netas = suma(importes_pagados del periodo) - suma(reembolsos del periodo)
    unidades_netas = unidades_surtidas del periodo - unidades_devuelta_en_el_periodo
    impuesto_desglosado = suma(impuestos de ventas del periodo)
        - suma(impuestos revertidos en devoluciones del periodo)
    si hay costo pendiente en cualquier venta del periodo:
        mostrar costos conocidos por separado y marcar costo total/ganancia como PENDIENTES
        no tratar costos desconocidos como cero
    gastos_generales = gastos ocurridos dentro del periodo
    si no hay costo pendiente:
        costo_ventas = suma(costos_directos) - suma(costos_revertidos)
        margen_producto = ventas_netas_producto - costo_directo_producto
        ganancia_antes_del_factor = ventas_netas - costo_ventas - gastos_generales
        ganancia_despues_del_factor = ganancia_antes_del_factor * 0.84
    devolver ventas, impuesto, costo, gastos y métricas por separado
```

Existencia actual se informa como saldo presente, independiente del filtro histórico. Tablas y gráficas usan el mismo origen y filtros; ventas pendientes/canceladas no se cuentan. Períodos mensuales/trimestrales vacíos se devuelven en cero.

El período mensual es desde el primer día del mes hasta el primer día del siguiente, con límite final excluyente; el trimestral usa los límites del trimestre calendario y el anual el año calendario. El acumulado anual del año actual va del 1 de enero hasta la fecha de consulta; un año anterior incluye el año calendario completo. La API solo acepta `YYYY-MM`, `YYYY-Q1` a `YYYY-Q4` o `YYYY`, sin rangos arbitrarios. Las gráficas muestran 12 meses o 4 trimestres y permiten elegir unidades netas o importe neto, nunca ambas escalas en el mismo eje.

## 11. Usuarios y autorización

- Administrador: gestiona catálogo, recetas, precios vigentes, dispositivos/cuotas, costos manuales faltantes, ajustes y plantillas recurrentes de gastos; ejecuta importación/corrección y restauración; resuelve conflictos.
- Operador: registra entradas, ventas, devoluciones y gastos únicos; puede aplicar descuento por partida hasta los límites aprobados.
- El administrador inicia sesión con Django Auth en Windows mediante cookie `Secure`, `HttpOnly`, `SameSite=Strict` y CSRF sobre HTTPS local. Expira tras 30 minutos de inactividad o 8 horas máximo; DPAPI del usuario Windows protege la cookie almacenada y la contraseña no se persiste. El operador no se autentica con contraseña offline; se selecciona de la lista sincronizada en un dispositivo emparejado.
- El primer ADMIN se crea localmente mediante `createsuperuser`; Django lo identifica por `is_superuser`. El rol OPERADOR corresponde a pertenecer al grupo Django `OPERADOR`. No hay rutas de gestión de usuarios en el contrato aprobado; el aprovisionamiento inicial se hace localmente por comandos Django.
- Emparejamiento Android mediante QR de un solo uso válido 5 minutos. La app genera localmente una clave ECDSA P-256/SHA-256 exclusiva en Android Keystore y fija independientemente la huella TLS del servidor. La clave no expira automáticamente; la rotación por reemplazo o compromiso requiere acción administrativa y nuevo emparejamiento. Las solicitudes firman timestamp y nonce; el servidor limita el timestamp a ±5 minutos y consume nonce aleatorio de 128 bits durante 15 minutos. La renovación de la huella requiere nuevo QR. La clave aleatoria de SQLite Android también se genera localmente, es no exportable, no se respalda y se protege en Android Keystore; si se pierde, la outbox puede ser irrecuperable y el dispositivo requiere reemparejamiento.
- Offline no se solicita contraseña por operación: el dispositivo emparejado identifica la caja y el operador selecciona su nombre de la lista sincronizada. La identidad individual offline es declarativa, no autenticación personal.
- Revocar dispositivo bloquea sincronización. Una cuota puede reasignarse inmediatamente por decisión del administrador; eventos no recibidos quedan rechazados salvo aceptación individual auditada. Esta decisión puede causar diferencia física y obliga a conciliación.

```text
EMPAREJAR_DISPOSITIVO(QR, dispositivo, actor):
    exigir actor == ADMIN y alta iniciada previamente en Windows
    verificar certificado TLS local por huella del QR
    si existe alta completada para pairing_request_id:
        si QR y clave pública coinciden con la solicitud ya persistida:
            devolver resultado persistido sin crear otra asociación
        rechazar combinación distinta
    exigir QR vigente, no usado y de un solo uso
    crear clave exclusiva en Android Keystore
    enviar pairing_request_id, token QR y clave pública por HTTPS fijado
    en una transacción, validar/consumir QR, registrar dispositivo,
        clave pública, key_id y resultado de emparejamiento
    proteger la clave privada en Android Keystore
    si la respuesta se pierde, conservar pairing_request_id y clave para reintentar
    sincronizar roster de operadores y catálogo
    asignar cuota solo mediante operación explícita del administrador
    auditar emparejamiento
```

```text
REVOCAR_DISPOSITIVO(dispositivo, actor):
    exigir actor == ADMIN
    marcar dispositivo como revocado y bloquear nuevas conexiones
    liberar/reasignar cuota restante de inmediato según decisión administrativa
    marcar operaciones recibidas pero aún no aplicadas como conflicto
    no admitir automáticamente operaciones aún no recibidas
    permitir resolución individual auditada por administrador
    auditar revocación y movimientos de cuota
```

Si el dispositivo se pierde antes de sincronizar, su cola offline puede ser irrecuperable. La cuota podrá reasignarse según la regla aprobada, pero las operaciones que nunca llegaron a Windows no pueden revisarse ni convertirse en ventas oficiales; cualquier discrepancia física requiere conteo y ajuste. Esta pérdida de trazabilidad es un riesgo aceptado de permitir operación offline y revocación inmediata.

El cliente offline selecciona el operador de una lista sincronizada; la selección identifica al actor para auditoría, pero no equivale a autenticar individualmente a esa persona.

## 12. Respaldo

```text
RESPALDAR():
    mientras la aplicación Windows esté abierta, a las 22:00 UTC-06:00:
        crear dump consistente de PostgreSQL
        cifrar con clave/contraseña custodiada en gestor externo
        guardar copia cifrada local
        validar que el dump y el cifrado terminaron íntegramente
        si Drive está disponible:
            subir copia a carpeta de Drive
            comparar tamaño y SHA-256 del objeto subido contra el archivo local
            si Drive no expone el hash:
                descargar objeto a temporal y calcular SHA-256
            solo si tamaño y hash coinciden:
                marcar carga verificada
                borrar copias de Drive con más de 30 días
        si Drive no está disponible:
            conservar copia cifrada local
            dejar carga pendiente para reintentar en la siguiente ejecución diaria
        si dump o cifrado falla:
            no promover archivo incompleto como respaldo válido
            conservar cualquier copia local válida previa
            dejar estado pendiente/error visible para reintentar en la siguiente ejecución diaria
        si carga o verificación falla:
            conservar copia cifrada local
            dejar estado pendiente/error visible para reintentar en la siguiente ejecución diaria
    si la aplicación no estaba abierta a la hora programada:
        ejecutar el respaldo pendiente al siguiente inicio de la aplicación
    después de verificar carga en Drive:
        conservar la copia local válida más reciente y las 7 copias diarias anteriores
        limpiar solo copias locales más antiguas según esa política
    si espacio libre es menor a 2 veces el tamaño del último respaldo válido:
        mostrar alerta y no borrar copia previa ni promover respaldo incompleto
    si aún no existe respaldo válido:
        estimar tamaño desde el tamaño actual de PostgreSQL
        exigir espacio libre >= 2 * estimación
        si no alcanza:
            alertar y no borrar copias ni promover respaldo incompleto
    probar restauración mensualmente en entorno aislado
    repetir prueba tras cambios importantes de instalación o esquema
```

Una restauración se considera válida únicamente si la copia se descifra y restaura en una instancia PostgreSQL aislada, se verifica esquema e integridad y se cotejan conteos, saldos, eventos y auditoría con el manifiesto del respaldo. Los objetivos RPO/RTO quedan pendientes y no se garantizan.

```text
SOLICITAR_RESTAURACION(respaldo_id, actor):
    exigir actor == ADMIN autenticado
    exigir respaldo verificado
    mostrar advertencia de pérdida de operaciones posteriores al respaldo
    registrar solicitud pendiente y auditoría sin modificar la base operativa

CONFIRMAR_RESTAURACION(solicitud_id, confirmacion, actor, sesion):
    exigir actor == ADMIN autenticado y misma cuenta/sesión de la solicitud
    exigir confirmaciones explícitas separadas
    registrar cada confirmación y auditoría
    solo tras la segunda confirmación iniciar restauración controlada
```

La clave/contraseña de cifrado se mantiene en un gestor externo, bajo custodia del administrador y un segundo custodio designado. La recuperación de ambos custodios se valida mediante restauración aislada; si se pierde la clave, las copias cifradas no se pueden restaurar.

## 13. Invariantes de aceptación

1. Repetir un UUID nunca duplica una venta, entrada, devolución, ajuste o gasto.
2. No se confirma venta normal si falta cualquier componente de receta, cuota/stock o costo requerido.
3. Las ventas offline consumen solo cuota de su propio dispositivo; Windows consume solo stock central no asignado.
4. Una venta pendiente no reserva ni modifica saldos/reportes; el surtido es completo por producto o no ocurre.
5. Una devolución ordinaria reingresa exactamente todos los componentes controlados, solo desde el dispositivo de la venta si es offline.
6. Saldos iniciales provisionales no se usan para vender.
7. El costo snapshot de una venta nunca se reemplaza silenciosamente. Solo los déficits excepcionalmente aprobados por administrador se costean por tramos con entradas posteriores.
8. Toda corrección deja usuario, motivo, fecha y referencia a los registros anteriores.
9. Ganancia por producto es margen antes de gastos generales; ganancia del negocio resta gastos generales y aplica el factor 84%.
10. Ningún cliente requiere internet público ni servicio de pago para operar; sincronización requiere Wi-Fi local y PC con la aplicación Windows abierta.

## 14. Casos de prueba de referencia

- Una entrada de 5 unidades a $16 sobre 10 unidades a promedio $10 da promedio $12.
- Una solicitud de venta no surtida permanece local en su dispositivo y no se comparte con Windows ni otros Android.
- Valhalla 750 consume 0.750 L Ensamble, 1 botella 750 y 1 corcho sintético.
- Mini 375 consume 0.375 L 1 Dest., 1 botella 375 y 1 corcho de madera; líquido cuesta $30 con $80/L.
- Si falta un componente, no se descuenta ningún otro y la venta queda pendiente sin reserva.
- Si se reintenta un evento con el mismo UUID, la respuesta es idempotente y no duplica el efecto.
- Una devolución superior a lo vendido o sin componentes completos se rechaza sin reembolso/movimiento.
- Una devolución válida revierte importe/costo en su periodo y reingresa componentes a costos originales.
- La compra nueva queda sin asignar hasta que administrador otorgue cuota a un Android.
- Un dispositivo no puede vender más de su cuota; la cuota de otros dispositivos no se utiliza.
- Un evento de dispositivo revocado queda en conflicto; solo el administrador decide aceptación/rechazo auditados.
- Si la aceptación excepcional crea déficit, las entradas posteriores del componente cubren primero ese déficit y costean ventas pendientes por tramos.
- Si un componente tiene saldo positivo sin costo promedio, una entrada, devolución o ajuste positivo no se confirma hasta que el administrador valore el saldo previo.
- Si un periodo contiene costos pendientes, el reporte no presenta costo ni ganancia como definitivos ni sustituye costos desconocidos por cero.
- El impuesto incluido de un importe final de $116.00 es $16.00; el factor de ganancia posterior se calcula por separado sobre ganancia antes del factor.
- Redondeos de cobro, reembolso e impuesto se hacen a dos decimales por operación, mitad hacia arriba.
- Un gasto recurrente mensual configurado para el día 31 ocurre el último día de los meses más cortos; una reejecución no lo duplica.
- Al cancelar una venta pendiente, queda historial pero no hay movimientos ni impacto en reportes.
- Restaurar respaldo cifrado con la contraseña recuperada reproduce saldos, eventos y auditoría.

## 15. Validación previa a la implementación

El pseudocódigo está aprobado como especificación, pero la programación aún no ha comenzado ni queda autorizada por esta aprobación documental. Antes del primer despliegue se configuran fecha efectiva de arranque, unidades/conversiones y costos verificados de nuevos componentes, y la versión GA de Windows 11 aún soportada en la fecha de despliegue (PC con edición Home). La referencia vigente al 9 de noviembre de 2026 es Windows 11 26H2; se debe revalidar el ciclo de soporte antes de desplegar. El API TCP 8443 solo en perfil privado/LAN, el objetivo de hasta 3 Android y 5,000 eventos offline por dispositivo, p95 interactivo ≤2 segundos, lote individual de 1,000 eventos ≤60 segundos sobre PC mínima de 4 núcleos/16 GB/SSD, el respaldo diario a las 22:00 UTC-06:00, la ejecución pendiente al siguiente inicio, retención Drive de 30 días, retención local aprobada y restauración aislada con cotejo de manifiesto ya están acordados.
