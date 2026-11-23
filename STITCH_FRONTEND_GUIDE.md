# 🎨 Guía de Arquitectura Frontend para Stitch — Valhalla

> **Destinatario:** Stitch (Diseñador / Generador / Especialista de Frontend)  
> **Objetivo:** Guía integral para auditar, rediseñar y modificar la interfaz de usuario de Valhalla conservando la integridad de los contratos de API y las reglas de negocio.

---

## 📂 1. Carpeta de Trabajo para Stitch

Para que Stitch empiece a trabajar de inmediato, indícale esta carpeta:

```text
G:\IAs\apps\clients\web_app\
```

* **Archivo principal a modificar:** [`index.html`](file:///G:/IAs/apps/clients/web_app/index.html) (Single Page Application autónoma con HTML5, CSS3 moderno, JavaScript ES6+ y Chart.js).
* **Activo local de gráficos:** [`chart.umd.min.js`](file:///G:/IAs/apps/clients/web_app/chart.umd.min.js) (Librería Chart.js servida en local sin requerir internet).
* **Servidor en vivo para previsualizar:** `http://127.0.0.1:8000/` o `http://192.168.1.70:8000/`

---

## 🗺️ 2. Mapa de Secciones Modificables en `index.html`

El archivo [`index.html`](file:///G:/IAs/apps/clients/web_app/index.html) está estructurado de forma modular y semántica. A continuación se detallan las secciones exactas que Stitch puede modificar:

### 🎨 Bloque A: Sistema de Diseño y Estilos CSS (Líneas 10 – 210)
* **Variables globales (`:root`):**
  - `--primary: #176B58` (Verde esmeralda corporativo)
  - `--primary-dark: #0f4b3d`
  - `--primary-light: #e8f4f1`
  - `--accent: #d97706` (Ámbar)
  - `--bg: #f8fafc`
  - `--surface: #ffffff`
  - `--border: #e2e8f0`
* **Componentes CSS:** Tarjetas (`.card`), Botones (`.btn`, `.btn-secondary`, `.btn-sm`), Pestañas (`.nav-tabs`, `.tab-btn`), Badges, Notificaciones (`.toast`), Cuadrícula KPI (`.kpi-card`) y Estilos de Hoja de Cálculo Excel (`.excel-card`, `.excel-table`, `.excel-total-row`).
* **Tipografía:** `'Inter'` para interfaz y `'JetBrains Mono'` para cifras numéricas, monedas e identificadores.

---

### 🖥️ Bloque B: Encabezado y Navegación Principal (Líneas 215 – 240)
* **Header (`<header>`):** Título, indicador del estado de la API (`#apiStatus`) y credencial del usuario conectado (`#userBadge`).
* **Barra de Pestañas (`.nav-tabs`):**
  1. `📦 Catálogo y Existencias` (`#tab-stock`)
  2. `🛒 Nueva Venta (IVA 16/116)` (`#tab-sales`)
  3. `🔄 Devoluciones` (`#tab-returns`)
  4. `🛡️ Respaldos y Estado` (`#tab-system`)

---

### 📦 Bloque C: Pestaña 1 — Catálogo y Subinventario (Líneas 245 – 360)
* **Buscador en tiempo real:** `<input id="productSearch">`
* **Botones de Alta de Catálogo:**
  - `🍾 ➕ Nueva Botella / Producto` (`openNewProductModal()`): Abre el diálogo de registro de nuevo producto vendible con constructor interactivo de receta.
  - `🌿 ➕ Agregar Materia Prima` (`openNewComponentModal()`): Abre el diálogo para dar de alta materias primas personalizables especificando piezas o litros.
* **Tabla de Productos Vendibles:**
  - Columnas: ID, Producto, SKU, Precio Final (con IVA), IVA (16/116), Cantidad (Subinventario de botellas terminadas), y Acciones (`🍾 + Agregar Botella`).
* **Tabla de Existencias Centrales de Insumos:**
  - Materias primas a granel (Líquidos en litros, Botellas vacías, Corchos y nuevos insumos en piezas).

---

### 🛒 Bloque D: Pestaña 2 — Nueva Venta Central (Líneas 365 – 430)
* **Selector de Producto:** `<select id="saleProductSelect">` (muestra precio unitario y botellas disponibles en subinventario).
* **Campos de Captura:** Cantidad de botellas a vender (`#saleQty`) y Descuento en MXN (`#saleDiscount`).
* **Panel de Cálculo Fiscal en Tiempo Real:**
  - Subtotal Bruto (`#calcGross`)
  - Descuento aplicado (`#calcDiscount`)
  - IVA Trasladado incluido mediante fórmula legal $16/116$ (`#calcTax`)
  - Total Neto a Pagar (`#calcTotal`)
* **Acción:** `<button onclick="submitSale()">` (valida stock de botellas terminadas antes de emitir la venta).

---

### 🔄 Bloque E: Pestaña 3 — Devolución de Botellas al Subinventario (Líneas 435 – 540)
* **Selector de Artículo a Devolver:** `<select id="returnProductSelect">`
* **Venta Asociada:** `<select id="returnSaleSelect">` y búsqueda manual por ID (`#returnSaleId`).
* **Ficha Dinámica del Artículo:**
  - Precio original, unidades vendidas, reembolso máximo posible.
  - Indicador de destino físico: la botella reingresa completa al subinventario del producto; las materias primas centrales se mantienen intactas.
* **Confirmación y Acción:**
  - Checkbox de confirmación: `#returnPhysicalCheck`.
  - Botón: `<button onclick="submitReturn()">`.

---

### 📊 Bloque F: Pestaña 4 — Infograma, Analítica y Tabla Tipo Excel (Líneas 545 – 760)
1. **Control de Rango Temporal:**
   - Botones toggle: `🗓️ Histórico del Mes` vs `📆 Histórico Anual` (`#btnPeriodMonth`, `#btnPeriodYear`).
   - Etiqueta del intervalo activo: `#analyticsPeriodLabel`.
2. **Infograma de KPIs:**
   - 🍾 Total de Botellas Vendidas (`#kpiTotalBottles`).
   - 🏆 Producto Estrella / Líder (`#kpiTopProduct`).
   - 💰 Facturación Bruta (`#kpiTotalRevenue`).
   - 🏛️ IVA Trasladado 16/116 (`#kpiTotalTax`).
3. **Gráfica de Barras:** Canvas `#barSalesChart` (Eje X: Tiempo, Eje Y: Cantidad vendida por producto).
4. **Gráfica de Tendencias Normalizadas (0% – 100%) y Regresión Lineal:**
   - Canvas `#trendLineChart` (curvas de adopción y rectas de regresión OLS $y = mx + b$).
   - Caja explicativa de pendientes y velocidades: `#regressionAnalysisBox`.
5. **Histórico en Bruto (Tipo Hoja de Excel):**
   - Contenedor `.excel-card` con barra de herramientas verde Excel `#107c41`.
   - Filtro por producto (`#excelProductFilter`) y buscador de celdas (`#excelSearchInput`).
   - Barra de fórmulas `fx =SUMA(...)` y resumen de selección (`#excelSelectionSummary`).
   - Fila de cabecera con letras `A`–`J` y números de fila laterales `1` a `N`.
   - Fila de **TOTAL GENERAL (SUMA DE HOJA)** en amarillo contable `#fef9c3`.
   - Botón de exportación: `📥 Exportar a CSV (Excel)` (`exportExcelToCSV()`).
6. **Subsistema de Respaldos:** Monitor de copias cifradas y botón de respaldo manual inmediato.

---

### 🍾 Bloque G: Diálogos Modales de Gestión de Inventario (Líneas 765 – 920)
* **Modal 1 — Embotellado / Agregar Botella:** `#addBottlesModal`
  - Entrada: Cantidad de botellas a fabricar (`#modalBottleQty`).
  - Previsualización de Descuento de Receta: Lista `#modalRecipePreview` que calcula en vivo el consumo de insumos.
* **Modal 2 — Agregar Materia Prima Personalizable:** `#newComponentModal`
  - Selector de Unidad Estándar: Piezas (`pza`, precisión entera) o Litros (`L`, precisión decimal).
  - Captura de nombre, tipo, SKU, existencia inicial y costo promedio unitario.
* **Modal 3 — Dar de Alta Nueva Botella / Producto Vendible:** `#newProductModal`
  - Captura de nombre, SKU y precio final con cálculo instantáneo de IVA $16/116$.
  - **Constructor Dinámico de Receta (`#recipeRowsContainer`):** Permite añadir filas con cualquier combinación de insumos y cantidades que se descontarán por botella llena al embotellar.

---

### ⚙️ Bloque H: Lógica JavaScript y Capa de Datos (Líneas 815 – 1794)
* **Conexión API:** Variable `API_BASE` (se adapta automáticamente al host actual).
* **Autenticación:** `init()` obtiene token CSRF y autentica sesión de aplicación.
* **Carga de Datos:** `loadStockAndCatalog()`, `loadSalesForReturns()`, `loadAnalyticsAndHistory()`.
* **Motor Matemático:**
  - `updateSaleCalc()`: Cálculo exacto de IVA con `(total * 16 / 116)`.
  - `updateAnalyticsAndCharts()`: Particionado temporal, normalización porcentual acumulada y ajuste por mínimos cuadrados ordinarios (OLS).
  - `exportExcelToCSV()`: Generación de archivo CSV con BOM UTF-8 (`\uFEFF`) para compatibilidad directa con Microsoft Excel.

---

## 🛑 3. Reglas e Invariantes que Stitch NO Debe Romper

1. **Rutas de Endpoints de la API:**
   - `GET /api/v1/products/pages/inicio` $\rightarrow$ Catálogo de productos.
   - `GET /api/v1/inventory/stock/pages/inicio` $\rightarrow$ Existencias centrales de insumos.
   - `GET /api/v1/sales?limit=1000` $\rightarrow$ Historial completo de ventas.
   - `POST /api/v1/sales` $\rightarrow$ Registro y surtido de venta.
   - `POST /api/v1/products/<id>/add-bottles` $\rightarrow$ Embotellado al subinventario.
   - `POST /api/v1/sales/<id>/returns` $\rightarrow$ Devolución (enviar `{ "to_subinventory": true }`).
2. **Autenticación y Seguridad:**
   - Toda solicitud de modificación (`POST`) debe incluir:
     - Header `'X-CSRFToken': csrfToken`
     - Opción `credentials: 'include'`
3. **Cálculo Fiscal:**
   - La fórmula de IVA es estricta por norma fiscal de la empresa:
     $$\text{IVA} = \text{Total} \times \frac{16}{116}$$
4. **IDs de Gráficos:**
   - Si se rediseña el layout, mantener los IDs `<canvas id="barSalesChart">` y `<canvas id="trendLineChart">` o actualizar sus referencias correspondientes en `updateAnalyticsAndCharts()`.

---

## 📖 4. Documentación Complementaria de Referencia

* 🎨 **Wireframes y Especificación Visual:** [`docs/plan/04-flujos-y-pantallas.md`](file:///G:/IAs/docs/plan/04-flujos-y-pantallas.md)
* 🔌 **Contrato de API Completo:** [`docs/plan/11-borrador-contrato-api.md`](file:///G:/IAs/docs/plan/11-borrador-contrato-api.md)
* 📐 **Reglas de Negocio y Métricas:** [`docs/plan/02-reglas-de-negocio-y-metricas.md`](file:///G:/IAs/docs/plan/02-reglas-de-negocio-y-metricas.md)
* 📱 **Cliente Alternativo en Flutter:** [`apps/clients/lib/views/inventory_view.dart`](file:///G:/IAs/apps/clients/lib/views/inventory_view.dart)
