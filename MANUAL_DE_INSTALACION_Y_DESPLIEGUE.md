# 🚀 Manual de Instalación y Despliegue en la PC Servidor — Valhalla

> **Destinatario:** Administrador del Sistema / Instalador  
> **Sistema:** Valhalla — Control de Inventario y Ventas  
> **Plataforma Servidora:** PC Principal con Windows 10/11 (64-bit)  
> **Costo de Licencias e Infraestructura:** **$0.00 MXN**

---

## 📋 ¿Qué te toca hacer a ti? (Paso a Paso Explícito)

Solo debes realizar **3 sencillos pasos** directamente en la PC que fungirá como Servidor Principal:

### Paso 1: Ejecutar el Instalador del Servidor
1. Ve a la carpeta raíz del proyecto: `G:\IAs\`
2. Haz doble clic sobre el archivo:
   👉 **`instalar_servidor.bat`**
3. Si Windows te pide confirmación de permisos de Administrador (*"¿Desea permitir que esta aplicación haga cambios en el dispositivo?"*), pulsa **Sí**.
4. El script configurará automáticamente:
   - La regla en el Firewall de Windows Defender para abrir el puerto **8000**.
   - Las migraciones y tablas de la base de datos local.
   - Te mostrará en pantalla la dirección IP exacta de tu red local (por ejemplo: `http://192.168.1.70:8000/`).

---

### Paso 2: Activar el Inicio Automático (Opcional pero muy recomendado)
Para que el servidor esté siempre activo sin necesidad de que abras una ventana cada mañana al prender la computadora:
1. En la carpeta raíz `G:\IAs\`, haz doble clic sobre:
   👉 **`configurar_autoarranque.bat`**
2. Acepta los permisos de Administrador.
3. ¡Listo! A partir de ese momento, cada vez que enciendas la PC e inicies sesión en Windows, el servidor Valhalla arrancará de forma silenciosa en segundo plano.

---

### Paso 3: Abrir y Comprobar la Aplicación

#### A. En la PC Principal:
* Haz doble clic en **`iniciar_servidor.bat`** (o abre tu navegador web: Chrome, Edge, Firefox).
* Ingresa a: **`http://127.0.0.1:8000/`**
* Inicia sesión con:
  - **Usuario:** `admin`
  - **Contraseña:** `Admin-password-123!`

#### B. En tu Celular o Tablet (Ventas en Piso / Mostrador):
1. Conecta el celular a la **misma red Wi-Fi** donde está la PC (ej. `INFINITUM2AB6`).
2. Abre el navegador (Google Chrome o Safari) y escribe la dirección IP que te dio el instalador (asegúrate de escribir **`http://`**):
   👉 **`http://192.168.1.70:8000/`**
3. Inicia sesión con el mismo usuario `admin` y contraseña `Admin-password-123!`.
4. **Instalar como App Nativa en el Celular (PWA de 1 Clic):**
   - En **Android (Chrome):** Toca los 3 puntos superiores $\rightarrow$ Selecciona **"Agregar a la pantalla principal"** o **"Instalar aplicación"**.
   - En **iPhone (Safari):** Toca el botón Compartir $\rightarrow$ Selecciona **"Agregar al inicio"**.
   - Aparecerá el icono de **Valhalla** en la pantalla de inicio de tu celular, abriéndose a pantalla completa como una app nativa.

---

## 📂 Inventario de Documentos y Scripts de Despliegue

Todos los ejecutables y manuales están organizados en la raíz del proyecto `G:\IAs\`:

| Documento / Script | Tipo | Propósito |
| :--- | :---: | :--- |
| [`valhalla-movil.apk`](file:///G:/IAs/valhalla-movil.apk) | APK Android | Aplicación móvil nativa para teléfonos celulares y tablets Android. |
| [`instalar_servidor.bat`](file:///G:/IAs/instalar_servidor.bat) | Script Batch | Instalador inicial: Abre el firewall y prepara la base de datos. |
| [`iniciar_servidor.bat`](file:///G:/IAs/iniciar_servidor.bat) | Script Batch | Inicia el servidor en red (`0.0.0.0:8000`) y abre el navegador automáticamente. |
| [`detener_servidor.bat`](file:///G:/IAs/detener_servidor.bat) | Script Batch | Detiene el servicio y libera el puerto 8000 limpiamente. |
| [`configurar_autoarranque.bat`](file:///G:/IAs/configurar_autoarranque.bat) | Script Batch | Registra la Tarea Programada para auto-arranque con Windows. |
| [`Dockerfile`](file:///G:/IAs/Dockerfile) | Config Docker | Especificación de contenedor para despliegue en Docker Desktop / Linux. |
| [`docker-compose.yml`](file:///G:/IAs/docker-compose.yml) | Config Docker | Orquestación multi-contenedor con volúmenes persistentes. |
| [`MANUAL_DE_USUARIO_FINAL.md`](file:///G:/IAs/MANUAL_DE_USUARIO_FINAL.md) | Documento | Guía operativa de uso para el cliente y vendedores. |
| [`STITCH_FRONTEND_GUIDE.md`](file:///G:/IAs/STITCH_FRONTEND_GUIDE.md) | Documento | Guía de personalización visual de interfaces para Stitch. |

---

## ☁️ Configuración de Respaldos en Google Drive

El sistema cuenta con un subsistema integrado de copias de seguridad con cifrado **AES-256-GCM**.

### Para vincular la copia automática a Google Drive:
1. Descarga e instala en la PC **Google Drive para escritorio** (gratuito desde google.com/drive/download/).
2. Inicia sesión con la cuenta de Google del negocio. Se creará una unidad virtual (ej. `G:\` o carpeta `Google Drive`).
3. En la pestaña **🛡️ Respaldos y Estado** de la aplicación Valhalla, presiona:
   👉 **💾 Ejecutar Respaldo Manual Inmediato**.
4. Los archivos de respaldo generados tienen el formato:
   `backup_<UUID>_<FECHA>.enc`
   *(Son archivos comprimidos y cifrados militarmente; aunque alguien los robe en Drive, no puede leerlos sin la clave maestra).*
5. Configura Google Drive para sincronizar la carpeta `apps/api/backups/`. ¡Tus datos quedan asegurados fuera de la PC automáticamente todos los días a las 22:00 hrs!

---

## 🛠️ Diagnóstico y Resolución de Problemas

* **¿El celular no conecta?:**
  - Confirma que el celular esté en la misma red Wi-Fi que la PC (no en datos móviles 4G/5G).
  - Vuelve a ejecutar `instalar_servidor.bat` como Administrador para revalidar la regla del Firewall.
  - Asegúrate de escribir `http://` en lugar de `https://`.
* **¿Cambió la IP del módem al reiniciar el router?:**
  - Ejecuta `iniciar_servidor.bat`; el script detectará la nueva IP y te la imprimirá en letras grandes en la consola.
