# Uso y límites

Widget flotante para Windows que muestra el consumo de Claude Code y Codex. Se mantiene sobre las ventanas, se acopla a los bordes y guarda localmente la posición y las lecturas recientes.

## Requisitos

- Windows 10 u 11.
- Python 3.11 o posterior con Tkinter.
- Pillow, instalada con: py -3 -m pip install --user -r requirements.txt.
- Solo instala e inicia sesión en las herramientas que quieras mostrar: Claude Code para Claude y Codex CLI para Codex.

## Instalación

Clona este repositorio y, desde su carpeta, ejecuta:

    py -3 -m pip install --user -r requirements.txt

Inicia sesión en Claude Code y/o Codex CLI con sus herramientas oficiales. Después abre Abrir.vbs. Para iniciar el widget con Windows, puedes ejecutar .\instalar_inicio.ps1 -Instalar; para quitar ese acceso directo, ejecuta .\instalar_inicio.ps1 -Quitar.

También puedes iniciar desde una terminal con py -3 -m usage_monitor.

## Elegir servicios

Haz clic derecho en el widget y marca Claude, Codex o ambos. La selección se guarda en .runtime/settings.json y se conserva al reiniciar. Siempre debe quedar al menos uno activo.

También puedes elegir servicios para una ejecución concreta:

    py -3 -m usage_monitor --providers claude
    py -3 -m usage_monitor --providers codex
    py -3 -m usage_monitor --providers claude,codex

Este argumento no cambia la selección guardada. Un servicio desactivado no se consulta ni se leen sus lecturas locales. Claude usa Claude Code; Codex usa Codex CLI y la cuenta que allí tenga sesión iniciada.

## Uso

En modo compacto, cada anillo muestra el porcentaje usado de la ventana principal. Al pasar el puntero se amplía y muestra las ventanas, reinicios y antigüedad de los datos. El doble clic actualiza los servicios activos. El clic derecho abre el menú y permite cerrar el widget.

El widget se acopla al borde más cercano, recuerda su posición y se puede arrastrar entre bordes y monitores.

## Cómo se obtienen los datos

### Claude

Claude Code comunica sus límites de suscripción en su evento rate_limit_event. El widget ejecuta una consulta mínima con Claude Code en segundo plano, sin herramientas, ajustes de proyecto, servidores MCP ni persistencia de sesión. Claude Code mantiene la autenticación; el widget no lee ni guarda tokens.

La consulta es una petición real y consume una pequeña parte de la cuota del plan. El ritmo se adapta a la actividad: comienza cada 30 segundos, se reduce a 5 minutos y después a 30 minutos si el porcentaje no cambia. Si falla, espera al menos 5 minutos para reintentar.

Opcionalmente, la línea de estado oficial de Claude Code puede escribir lecturas después de una respuesta. Para activarla, añade esta configuración a ~/.claude/settings.json, sustituyendo <carpeta del proyecto> por la ruta donde clonaste el repositorio:

    "statusLine": {
      "type": "command",
      "command": "py -3 \"<carpeta del proyecto>/claude_statusline.py\""
    }

La línea de estado solo funciona en Claude Code; la app de escritorio y claude.ai no la ejecutan.

### Codex

El widget consulta account/rateLimits/read mediante el app-server de Codex CLI cada 30 segundos. Es una consulta de estado, no inicia conversaciones ni consume tokens. La cuenta usada es la que está iniciada en Codex CLI y puede diferir de la aplicación o el navegador.

## Datos y privacidad

- .runtime/ contiene preferencias y capturas de consumo locales. Git la excluye.
- Las sesiones permanecen gestionadas por Claude Code y Codex CLI. Este proyecto no las copia ni las renueva.
- Las consultas requieren conexión con los proveedores.
- Revisa el código y las dependencias antes de usarlo en otro equipo. La consulta de Claude consume cuota y el comportamiento de proveedores puede cambiar.
- Los datos se muestran en la zona horaria del equipo. Una lectura puede quedar obsoleta si el proveedor no vuelve a entregar información.

## Diagnóstico

- Si Claude no muestra datos, comprueba en una terminal que Claude Code está instalado y mantiene una sesión válida.
- Para probar Codex manualmente: py -3 -m usage_monitor --probe-codex.
- Si el widget no aparece, puede estar acoplado en otro monitor. La posición guardada se encuentra en .runtime/settings.json.

## Pruebas

    py -3 -m unittest discover -s tests -v
    py -3 -m unittest discover -s tests -p ui_smoke.py -v
