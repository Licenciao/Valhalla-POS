#  VALHALLA — Sistema Inteligente de Inventario y Ventas

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13-2F3D82?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.13" />
  <img src="https://img.shields.io/badge/Django-5.1_REST-3D71A3?style=for-the-badge&logo=django&logoColor=white" alt="Django 5.1" />
  <img src="https://img.shields.io/badge/Flutter-3.29_Dart-2C2863?style=for-the-badge&logo=flutter&logoColor=white" alt="Flutter 3.29" />
  <img src="https://img.shields.io/badge/SQLite-Cifrado_Outbox-321950?style=for-the-badge&logo=sqlite&logoColor=white" alt="SQLite" />
  <img src="https://img.shields.io/badge/Docker-Ready-2E0A32?style=for-the-badge&logo=docker&logoColor=white" alt="Docker" />
  <img src="https://img.shields.io/badge/Costo_Hosting-$0.00_MXN-10b981?style=for-the-badge&logo=cashapp&logoColor=white" alt="Costo $0 MXN" />
</p>

---

##  Visión General del Proyecto

**Valhalla** es una plataforma integral de gestión de inventarios, formulación de recetas, control de producción y terminal de punto de venta (POS) multi-producto, diseñada a la medida para destilerías artesanales, embotelladoras y comercios especializados.

El sistema resuelve la complejidad del negocio mediante un modelo de **doble inventario**:
1. **Inventario Central de Materias Primas:** Control exacto de insumos en granel (alcoholes, destilados y esencias en litros con precisión decimal; botellas de vidrio, corchos y etiquetas en piezas enteras).
2. **Subinventario de Producto Terminado:** Gestión de botellas listas para su comercialización, cuyo stock se genera mediante un proceso de **embotellado dinámico** que descuenta automáticamente los insumos exactos del inventario central según la receta configurada.

---

##  Filosofía: Minimalismo Técnico y Soberanía Total

En un mercado saturado de software de suscripción costoso y configuraciones en la nube difíciles de mantener, **Valhalla** fue concebido bajo dos principios rectores:

### 1. Minimalismo Técnico para el Usuario Final
* **Cero comandos de consola:** El usuario no requiere conocimientos técnicos ni lidiar con terminales. El sistema se instala, inicia y detiene con scripts de automatización de 1 clic (`.bat`).
* **Cero dependencias pesadas en producción:** La interfaz web de escritorio es una Single Page Application (SPA) ultraligera en HTML5/Vanilla JS y CSS nativo que no requiere Node.js, Webpack ni transpiladores para funcionar.
* **Experiencia de usuario fluida:** Interfaz visual unificada con la paleta de identidad corporativa en 5 tonos institucionales:
  - `Sapphire Blue (#3D71A3)` — Acentos activos, indicadores y navegación.
  - `Royal Indigo (#2F3D82)` — Acciones principales, totales y confirmaciones.
  - `Deep Violet (#2C2863)` — Encabezados estructurales y tarjetas.
  - `Dark Eggplant (#321950)` — Gradientes institucionales y series de datos.
  - `Night Purple (#2E0A32)` — Fondo profundo y contraste de alta visibilidad.

### 2. Costo \$0.00 MXN en Infraestructura y Hosting
* **Sin servidores en la nube pública:** No pagas mensualidades en AWS, Google Cloud, Heroku ni Firebase.
* **Privacidad Absoluta:** Toda la información contable, recetas y ventas reside exclusivamente dentro de las computadoras del negocio, garantizando confidencialidad industrial.

---

##  Conexión en Red Local (LAN) y Operación Offline

El sistema opera sobre la red Wi-Fi o cableada local del establecimiento, permitiendo que la computadora principal actúe como **Servidor Central Seguro** sin exponer puertos a internet.

### Arquitectura de Conexión LAN

```mermaid
flowchart TD
    subgraph LAN[" RED LOCAL WI-FI DEL ESTABLECIMIENTO"]
        PC["🖥️ PC SERVIDOR CENTRAL (Windows 10/11)<br/>IP Local: 192.168.1.70:8000<br/>Django REST API + Base de Datos Central"]
        
        NAV["💻 Navegador Web en la PC<br/>Dashboard / Inventario / Facturación"]
        PC --- NAV

        WIFI((" Router Wi-Fi Local"))
        PC <--> WIFI

        MOB1[" Terminal Android 1 (Piso de Venta)<br/>Valhalla APK Nativa"]
        MOB2[" Terminal Android 2 (Almacén / Cava)<br/>Valhalla APK Nativa"]
        
        WIFI <--> MOB1
        WIFI <--> MOB2
    end
```

### ¿Cómo funciona la conexión sin pagar hosting?
1. **Arranque en la PC Principal:** Al ejecutar `iniciar_servidor.bat`, el motor de Valhalla arranca en el puerto `8000` y detecta la dirección IP local asignada por el módem del negocio (ej. `192.168.1.70`).
2. **Acceso Inmediato en Piso:** Cualquier teléfono celular, tablet o laptop conectada a la misma red Wi-Fi puede acceder instantáneamente escribiendo `http://192.168.1.70:8000/` en el navegador o abriendo la aplicación móvil nativa.
3. **Resiliencia Offline-First (Modo Outbox SQLite):**
   - Si el vendedor baja al sótano, ingresa a la bodega o realiza una entrega fuera del alcance de la red Wi-Fi, la aplicación **Android sigue funcionando al 100%**.
   - Las ventas se guardan de forma segura y cifrada en la base de datos local SQLite del teléfono con estado `PENDING`.
   - En cuanto el dispositivo vuelve a detectar la red Wi-Fi, la app sincroniza las ventas pendientes en segundo plano mediante peticiones con cabeceras `Idempotency-Key`, evitando duplicidad de folios y descontando el stock central en tiempo real.

---

## ⚙️ Opciones y Modos de Uso para el Usuario

El ecosistema Valhalla ofrece dos modos de interacción perfectamente sincronizados:

### Modo Servidor / Escritorio (PC Host)
* **Tablero de Control y Analítica:** Resumen financiero en tiempo real, KPIs de facturación bruta, IVA trasladado y desglose de botellas vendidas.
* **Modelo Matemático de Tendencias:** Motor de proyección comercial con regresión de pendiente ($m$) y coeficiente de determinación $R^2$ para identificar productos en aceleración.
* **Gestión de Materias Primas:** Registro de insumos en litros o piezas, costos promedio ponderados y actualización de existencias por compras o mermas.
* **Recetario Dinámico y Embotellado:** Configuración visual de qué ingredientes consume cada botella. Con un clic en *"Embotellar"*, el sistema verifica disponibilidad y descuenta del almacén central.
* **Punto de Venta Mostrador:** Carrito multi-producto con cálculo exacto de impuestos según la **fórmula fiscal 16/116**:
  $$\text{Base Gravable} = \frac{\text{Precio Final}}{1.16}, \quad \text{IVA (16\%)} = \text{Precio Final} \times \left(\frac{16}{116}\right)$$
* **Recepción de Ventas Móviles:** Bandeja de entrada para auditar, surtir o aceptar ventas reportadas por los operadores de piso.

### Modo Móvil / Vendedor de Piso (APK Android)
* **Arranque Directo:** Ingresa de inmediato al catálogo de productos terminados listos para cobrar.
* **Carrito y Ventas Rápidas:** Agregar productos con cantidades y descuentos con un toque.
* **Operación Sin Conexión:** Venta en bodega o mostrador sin depender de internet.
* **Ajuste de Conexión:** Menú de configuración accesible mediante el icono de engranaje para ingresar o cambiar la IP del servidor central.

---

## Recorrido Visual y Cualidades de Cada Módulo

El sistema cuenta con una interfaz web de alta resolución diseñada para una operación fluida, profesional y sin fricción operativa. A continuación se presentan las cualidades clave de cada pantalla:

### 1. Panel de Control Central (Inicio)
![Inicio - Panel de Control Central](docs/screenshots/01_inicio_dashboard.png)

* **Dashboard Ejecutivo Operativo:** Vista inicial libre de elementos distractores, orientada al personal que ya conoce la dinámica del negocio y busca agilidad.
* **Accesos Rápidos de 1 Clic:** Cuatro tarjetas principales para ingresar directamente a las operaciones más frecuentes: *Realizar una Venta*, *Catálogo de Productos*, *Materias Primas e Insumos*, e *Historial y Facturación*.
* **Jerarquía Lateral Optimizada:** Menú de navegación minimalista con apartado inferior exclusivo de **Usuario**, que centraliza el perfil activo (`admin`) y el estado de conexión en tiempo real con la API del servidor central (`API: 127.0.0.1:8000`).

---

### 2. Gestión de Inventario Dual (Catálogo Vendible y Materias Primas)
![Catálogo de Productos y Existencias Centrales](docs/screenshots/02_catalogo_e_insumos.png)

* **Catálogo de Productos Vendibles:** Registro claro de botellas terminadas listas para piso de venta con su código SKU, precio final, cálculo automático de IVA (16/116) y stock actual en el subinventario de mostrador.
* **Embotellado en 1 Clic (`+ Botella`):** Al presionar el botón de embotellado, el sistema valida la receta del producto y descuenta automáticamente los insumos exactos del almacén central.
* **Existencias Centrales de Materias Primas:** Control estricto de insumos a granel:
  - Líquidos y alcoholes con **precisión decimal de hasta 6 cifras** (ej. `25.500000 L` de líquido doble destilado).
  - Componentes sólidos por piezas enteras (botellas vacías y corchos).
  - Cálculo dinámico de costo promedio ponderado vigente por unidad de insumo para valorizar el inventario en tiempo real.

---

### 3. Reingreso por Devolución de Botellas al Subinventario
![Reingreso por Devolución de Botellas](docs/screenshots/03_devoluciones_subinventario.png)

* **Trazabilidad Total por Folio de Venta:** Cada devolución se enlaza estrictamente a una transacción previa registrada (ej. `Venta #29`), protegiendo la integridad financiera y evitando reembolsos arbitrarios.
* **Destino Inteligente del Reingreso Físico:** El sistema reconoce que una botella devuelta cerrada no debe descomponerse en sus ingredientes de origen; por ende, suma directamente al subinventario de producto terminado para su reventa inmediata sin alterar las materias primas centrales.
* **Auditoría y Validación Física:** Incluye control de importe pagado vs. monto de reembolso aprobado y requiere confirmación explícita mediante casilla de inspección física antes de registrar la operación.

---

### 4. Infograma Financiero, KPIs y Gráfica Temporal
![Infograma de Ventas y Comportamiento Comercial](docs/screenshots/04_kpis_y_grafica_barras.png)

* **Filtros Temporales Consolidados:** Conmutación rápida entre el histórico del mes en curso y el acumulado anual.
* **KPIs Directivos en Tiempo Real:**
  - **Botellas Vendidas:** Volumen total colocado y número de transacciones del periodo.
  - **Producto Líder:** SKU de mayor rendimiento comercial y porcentaje de participación en el volumen total.
  - **Facturación Bruta:** Ingresos totales recaudados en moneda nacional (MXN).
  - **IVA Trasladado (16/116):** Cálculo fiscal automático para auditorías tributarias.
* **Gráfica de Barras Multiproducto:** Distribución cronológica día por día de las cantidades vendidas por cada presentación, facilitando la detección de días pico y patrones semanales de consumo.

---

### 5.  Analista Estadístico Integrado: Regresión Lineal OLS y Curvas de Adopción
![Tendencias Normalizadas y Regresión OLS](docs/screenshots/05_analista_estadistico_ols.png)

Una de las grandes cualidades que distinguen a **Valhalla POS** frente a los puntos de venta convencionales es su **analista estadístico de rendimiento incorporado**, el cual procesa las transacciones en tiempo real para pronosticar el comportamiento de la demanda de cada producto a lo largo del mes.

#### Fundamento Matemático y Cualidades del Analista:
1. **Normalización de Adopción Acumulada ($0\%$ a $100\%$):** Convierte el avance de ventas de cada producto en una curva porcentual normalizada. Esto permite comparar de forma equitativa el ritmo de adopción entre productos de volumen masivo (ej. presentaciones de 750 ml) y productos de alta gama o menor rotación (ej. miniaturas o ediciones especiales).
2. **Ajuste de Línea de Tendencia por Mínimos Cuadrados Ordinarios (OLS):**
   $$y = mx + b$$
   - **Pendiente de Tendencia / Velocidad Comercial ($m$):** Cuantifica el porcentaje de penetración que gana el producto en cada paso temporal del mes ($\Delta y / \Delta x$).
   - **Coeficiente de Determinación ($R^2$):** Evalúa la robustez estadística y consistencia de la serie de ventas. Un $R^2$ cercano a $1.0$ refleja una demanda constante y predecible.
3. **Diagnóstico Predictivo Automatizado:**
   - 🟢 **Fuerte ritmo de aceleración comercial ($m \ge +4.0\%/\text{paso}$):** Identifica productos estrella con rápida tracción (ej. *Valhalla Mini 375* con $m = +6.21\%/\text{paso}, R^2 = 0.884$; *Valhalla 750* con $m = +5.57\%/\text{paso}, R^2 = 0.858$).
   - 🔵 **Crecimiento sostenido ($0\% < m < +4.0\%/\text{paso}$):** Demanda estable que mantiene el flujo de caja regular.
   - ⚪ **Sin aceleración ($m = 0.00\%/\text{paso}$):** Alerta proactiva sobre productos estancados que no han registrado ventas en el periodo, permitiendo al administrador tomar decisiones comerciales inmediatas (promociones, rotación o combos).

---

### 6. Histórico de Ventas en Bruto — Hoja de Cálculo en Vivo y Exportación
![Hoja de Cálculo en Bruto](docs/screenshots/06_hoja_calculo_ventas.png)

* **Visor Tabular Estilo Hoja de Cálculo:** Interfaz contable interactiva con filtrado en tiempo real por producto o búsqueda instantánea dentro de las celdas.
* **Desglose Fiscal Riguroso:** Cada fila expone folio, fecha y hora de emisión, producto, SKU, piezas vendidas, precio unitario, descuentos aplicados, subtotal antes de impuestos, desglose de IVA (16/116) y total neto cobrado.
* **Barra de Totales Dinámica (`fx`):** Cálculo reactivo de registros mostrados, unidades sumadas, subtotal acumulado, IVA total e importe consolidado en MXN.
* **Exportación a CSV / Excel:** Botón de un solo clic que descarga la hoja de cálculo lista para importar en software contable o suites ofimáticas (Microsoft Excel, Google Sheets, LibreOffice).

---

##  Especificaciones Técnicas y Stack de Desarrollo

```mermaid
flowchart LR
    subgraph Frontend[" CAPA DE CLIENTES"]
        SPA["Web App SPA<br/>HTML5 + CSS3 + Vanilla JS<br/>Chart.js Local"]
        FLUTTER["App Android Nativa<br/>Flutter 3.29 / Dart<br/>SQLite Local Cifrado"]
    end

    subgraph Backend[" MOTOR CENTRAL"]
        DJANGO["Django 5.1 REST Framework<br/>Python 3.13"]
        DUAL["Doble Motor de Ejecución<br/>(Nativo Windows Daemon / Docker Compose)"]
    end

    subgraph Data[" PERSISTENCIA"]
        DB["SQLite3 / PostgreSQL<br/>Transacciones ACID e Idempotencia"]
        DRIVE["Respaldo Diario Cifrado<br/>(Google Drive Automático)"]
    end

    Frontend --> Backend
    Backend --> Data
```

| Componente | Tecnología | Detalle Técnico |
| :--- | :--- | :--- |
| **Backend API** | Python 3.13 / Django 5.1 REST | API RESTful con soporte CSRF, rate-limiting, paginación y cabeceras de idempotencia |
| **Motor de Ejecución** | Dual: Nativo Windows & Docker | Arranque en segundo plano mediante `python manage.py runserver` o contenedor `Dockerfile` |
| **Base de Datos** | SQLite 3 / PostgreSQL | Esquema relacional con integridad referencial, inventarios transaccionales y auditoría |
| **Cliente Web Escritorio** | SPA Híbrida (Vanilla JS / CSS3) | Single Page Application sin dependencias externas, estilizada con la paleta de 5 colores |
| **Cliente Móvil** | Flutter 3.29.0 / Dart 3.7.0 | Aplicación para Android con patrón Outbox SQLite y sincronización bidireccional |
| **Pruebas Automatizadas** | Flutter Test / Django Test | **31 pruebas unitarias y de widgets** en verde con 100% de éxito |

---

##  Guía de Despliegue en 3 Pasos (Para la PC Servidor)

No se requiere instalar entornos de desarrollo complejos. En la PC que servirá como servidor principal:

### Paso 1: Clonar el Repositorio
```bash
git clone https://github.com/<tu-usuario>/valhalla.git
cd valhalla
```

### Paso 2: Ejecutar el Instalador Automatizado
Haz doble clic sobre el archivo:
**`instalar_servidor.bat`**
*El asistente creará la regla en el Firewall de Windows Defender para el puerto 8000 y configurará la base de datos automáticamente.*

### Paso 3: Iniciar el Sistema
Haz doble clic sobre:
 **`iniciar_servidor.bat`**
*El sistema abrirá tu navegador automáticamente en `http://127.0.0.1:8000/` y te mostrará la dirección IP para conectar tus celulares.*

####  Credenciales por Defecto:
* **Usuario:** `admin`
* **Contraseña:** `Admin-password-123!`

---

##  Instalación de la Aplicación Móvil en Android

Para instalar la aplicación en los teléfonos de tu equipo de ventas:
1. Ve a la sección de [Releases de este repositorio](https://github.com) y descarga el archivo:
    **`valhalla-movil.apk`**
2. Pásalo a tu teléfono Android o descárgalo directamente desde el navegador del celular.
3. Pulsa sobre el archivo descargado y selecciona **Instalar** (permite la instalación de aplicaciones de origen desconocido si el sistema te lo solicita).
4. Abre la app, pulsa el engranaje superior derecho e introduce la IP de tu PC Servidor (ej. `http://192.168.1.70:8000`). ¡Listo para vender!

---

##  Scripts de Automatización Incluidos

En la raíz del proyecto encontrarás los siguientes accesos directos:

* 🟢 **`iniciar_servidor.bat`:** Inicia el servidor central en segundo plano y abre la interfaz web.
* 🔴 **`detener_servidor.bat`:** Cierra limpiamente todos los procesos de Valhalla en el puerto 8000.
* ⚙️ **`instalar_servidor.bat`:** Ejecuta migraciones y abre el firewall local con permisos de Administrador.
* 🔄 **`configurar_autoarranque.bat`:** Registra el servidor para que arranque solo cada vez que se enciende la PC.

---

##  Licencia

Este proyecto está distribuido bajo la licencia **MIT**. Consulta el archivo `LICENSE` para más detalles.

---

<p align="center">
  <b>Valhalla — Software de Gestión y Control Comercial</b><br/>
  <i>Desarrollado con arquitectura local segura, alto rendimiento y costo cero en la nube.</i>
</p>
