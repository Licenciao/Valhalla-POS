# 📖 Manual de Usuario Final — Valhalla Control de Inventario y Ventas

> **Destinatario:** Propietario del Negocio, Administradores y Operadores de Venta  
> **Sistema:** Valhalla — Gestión Integral de Destilería y Punto de Venta

---

## 🔑 1. Acceso al Sistema

### En la Computadora Principal:
* Abre tu navegador web y entra a: **`http://127.0.0.1:8000/`**
* Ingresa con:
  - **Usuario:** `admin`
  - **Contraseña:** `Admin-password-123!`

### En el Celular o Tablet:
* **Opción A (Aplicación Nativa Android - Recomendada):**
  - Instala en tu teléfono celular o tablet el archivo [valhalla-movil.apk](file:///G:/IAs/valhalla-movil.apk).
  - Conéctate a la red Wi-Fi del negocio y pulsa el botón verde **"Abrir Modo Móvil / Outbox"**. Entrarás directamente al inventario y punto de venta.
* **Opción B (Navegador Web / PWA sin instalar archivos):**
  - Conéctate al Wi-Fi del negocio y abre en Chrome o Safari:
    👉 **`http://192.168.1.70:8000/`** *(o la IP local indicada por tu instalador)*.
  - Puedes agregarlo a la pantalla de inicio tocando los 3 puntos de Chrome $\rightarrow$ **"Agregar a la pantalla principal"** para usarlo como app a pantalla completa.

---

## 📦 2. Catálogo, Materias Primas y Subinventario de Botellas

En la pestaña **📦 Catálogo y Existencias**:

### A. Agregar Materia Prima / Complemento Personalizable (Litros o Piezas):
1. En la tarjeta de **Existencias Centrales de Insumos**, presiona el botón **`🌿 ➕ Agregar Materia Prima`**.
2. Ingresa el nombre del insumo (ej. *Etiqueta Holográfica*, *Líquido Roble*, *Caja Individual*).
3. Selecciona la **Unidad Estándar de Medida**:
   - **📦 Piezas (`pza`):** Para elementos unitarios (envases, corchos, etiquetas, sellos).
   - **💧 Litros (`L`):** Para insumos líquidos o destilados que admiten fracciones decimales.
4. Opcionalmente asigna una existencia inicial recibida y su costo unitario promedio.
5. Presiona **💾 Guardar Materia Prima**. Aparecerá de inmediato en la tabla de existencias con su costo y saldo activo.

### B. Dar de Alta Nuevas Botellas con Receta Dinámica:
1. En la tarjeta de **Catálogo de Productos Vendibles**, presiona el botón **`🍾 ➕ Nueva Botella / Producto`**.
2. Escribe el nombre de la botella (ej. *Valhalla Edición Roble 500ml*) y su precio final con IVA.
3. En la sección **🧪 Receta: Complementos a restar por cada botella llena**:
   - Selecciona los complementos que componen esta botella.
   - Especifica la cantidad exacta que se descontará de cada uno al llenarse (ej. *0.500 L de Líquido Roble*, *1 pza de Botella 500ml*, *1 pza de Corcho*).
   - Puedes usar el botón **+ Añadir Insumo** para agregar tantos complementos como necesite tu fórmula.
4. Presiona **✅ Crear Botella y Activar**. El producto queda disponible de inmediato para embotellado y venta.

### C. Embotellar / Producir Lote ("Agregar Botella"):
1. En la tabla de productos, haz clic en el botón verde **`🍾 + Agregar Botella`** junto al producto.
2. Ingresa el número de botellas a declarar (ej. *10 botellas*).
3. El sistema calculará en tiempo real los insumos exactos que se descontarán del stock central según su receta configurada.
4. Haz clic en **✅ Confirmar y Embotellar**. Las materias primas se consumen automáticamente y las botellas se suman a tu subinventario listas para venta.

---

## 🛒 3. Registro de Nueva Venta

En la pestaña **🛒 Nueva Venta (IVA 16/116)**:
1. **Selecciona el Producto:** En el menú desplegable elige el licor que vas a vender (el sistema te avisa cuántas botellas tienes disponibles).
2. **Cantidad y Descuento:** Indica cuántas botellas compra el cliente y el descuento si aplica.
3. **Cálculo Fiscal Automático:**
   - En pantalla verás el desglose instantáneo: Subtotal, Descuento, **IVA incluido ($16/116$)** y Total a cobrar.
   - *(Nota: Los precios ya incluyen IVA conforme a la ley mexicana; la fórmula oficial $16/116$ extrae el impuesto exacto sin distorsiones).*
4. **Confirmar Venta:** Presiona **✅ Confirmar y Surtir Venta Central**. El sistema descuenta las botellas de tu subinventario y registra la venta contable.

---

## 🔄 4. Devoluciones de Clientes

En la pestaña **🔄 Devoluciones**:
1. **Especificar Artículo:** Elige qué producto regresa el cliente.
2. **Seleccionar Venta:** El sistema te lista las ventas realizadas de ese producto con su folio, unidades e importe cobrado.
3. **Ficha de Inspección:** Verás el detalle de la venta original y el reembolso máximo autorizado.
4. **Destino Físico:** El sistema te confirma que la botella regresará directamente a tu conteo de botellas terminadas listas para nueva venta (las materias primas no se alteran).
5. Marca la casilla *"Confirmo recepción física de la botella cerrada"* y presiona **🍾 Procesar Devolución al Subinventario**. El saldo de botellas se restaura de inmediato.

---

## 📊 5. Infograma Comercial y Hoja de Cálculo Excel

En la pestaña **🛡️ Respaldos y Estado**:
1. **Alternar Periodos:** Puedes pulsar **🗓️ Histórico del Mes** o **📆 Histórico Anual** para analizar desde el día 1 del periodo hasta el día de hoy.
2. **Tarjetas KPI:** Consulta de un vistazo:
   - Total de Botellas Vendidas.
   - Tu Producto Estrella (más vendido).
   - Facturación Bruta total en MXN.
   - IVA acumulado a declarar ante el SAT ($16/116$).
3. **Gráfica de Barras:** Te muestra día con día (o mes con mes) cuántas botellas vendiste de cada marca en el tiempo.
4. **Gráfica de Tendencias Normalizadas (Regresión Lineal):**
   - Compara a qué ritmo crecen las ventas de cada producto en una escala de 0% a 100%, con líneas de tendencia que indican la aceleración de demanda ($y = mx + b$).
5. **Histórico en Bruto (Hoja de Cálculo Excel):**
   - Justo debajo verás una tabla con formato idéntico a Microsoft Excel (columnas A–J, numeración de filas y fila de **TOTAL GENERAL SUMA**).
   - Puedes buscar cualquier venta por folio, filtrar por producto y pulsar **📥 Exportar a CSV (Excel)** para descargar la hoja lista para abrir en Excel en tu computadora.

---

## 💾 6. Respaldos Seguros en Google Drive

1. Al final de tu jornada laboral, en la pestaña **🛡️ Respaldos y Estado**, pulsa:
   👉 **💾 Ejecutar Respaldo Manual Inmediato**.
2. El sistema creará una copia cifrada con estándar militar (AES-256) de toda tu base de datos.
3. Con Google Drive para escritorio instalado en tu PC, estos respaldos se guardan en la nube automáticamente, protegiendo tu negocio contra robos, fallas de disco o accidentes.
