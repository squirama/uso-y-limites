# Uso y límites

Widget flotante para Windows que muestra cuánto te queda de la cuota de **Claude** y de
**Codex**. Es pequeño, está siempre por encima de todas las ventanas y se pega a los bordes
de la pantalla como un imán. Puedes mostrar los dos servicios o solo uno.

## Requisitos

- Windows 10 u 11.
- Python 3.11 o posterior con Tkinter (incluido en el instalador oficial de python.org).
- Pillow, para dibujar los anillos y el texto suavizados (se instala con `requirements.txt`).
- Solo las herramientas de los servicios que quieras ver, con sesión iniciada:
  - **Claude:** [Claude Code](https://code.claude.com) (`claude` en la terminal).
  - **Codex:** Codex CLI (`codex login`).

## Instalación

```powershell
git clone https://github.com/squirama/uso-y-limites.git
cd uso-y-limites
py -3 -m pip install --user -r requirements.txt
```

1. Inicia sesión en Claude Code y/o en Codex CLI con sus herramientas oficiales.
2. Haz doble clic en **Abrir.vbs**. Arranca el widget sin ventana de consola.
3. Opcional: para que se abra al encender el ordenador, ejecuta desde la carpeta del
   proyecto:

   ```powershell
   .\instalar_inicio.ps1 -Instalar
   ```

   Crea el acceso directo `Uso y limites.lnk` en la carpeta de Inicio de Windows
   (`shell:startup`). Para quitarlo, usa `.\instalar_inicio.ps1 -Quitar`. Si ya existe un
   acceso directo con ese nombre que apunta a otra carpeta, el script avisa y no lo toca.

También puedes arrancarlo desde una terminal con `py -3 -m usage_monitor`.

## Elegir servicios

Por defecto se muestran los dos. Para cambiarlo, haz clic derecho en el widget y marca o
desmarca **Claude** y **Codex**. Siempre debe quedar uno marcado. La elección se guarda en
`.runtime/settings.json` y se recuerda al reiniciar.

Para una sola ejecución, sin cambiar lo guardado:

```powershell
py -3 -m usage_monitor --providers claude
py -3 -m usage_monitor --providers codex
py -3 -m usage_monitor --providers claude,codex
```

Un servicio desactivado no hace ninguna consulta ni lee sus datos locales, y no necesita su
herramienta instalada. Con un solo servicio, el widget es un círculo con un único anillo.

## Uso del widget

**Compacto:** un anillo por servicio, coral para Claude y verde para Codex. Muestra el
porcentaje usado de la ventana de 5 horas. Un guion significa que todavía no hay datos.

**Ampliado:** al dejar el puntero encima crece un poco y muestra, para cada servicio:

- Uso de la sesión de 5 horas y de la semana, con su barra.
- Hora a la que se restablece cada límite y cuánto falta.
- Hace cuánto se leyeron los datos, o «Actualizando…» mientras consulta.
- Si llevas cinco minutos sin actividad, indica «sin actividad» y reduce las consultas;
  al volver, actualiza ambos servicios y recupera el ritmo habitual.
- El anillo y cada barra cambian a ámbar desde el 80 % y a rojo desde el 95 %.
- «Avisos» en el menú muestra una notificación de Windows una vez por cada sesión de
  cinco horas al llegar al 90 %. Puedes desactivarlos desde el mismo menú.
- El motivo si no ha podido leer los datos, por ejemplo una sesión caducada.

**Imán:**

- Al soltarlo, se pega al borde más cercano de la pantalla en la que esté. Cerca de una
  esquina, encaja en ella.
- Pegado a un borde, se desliza a lo largo de ese lado.
- Para despegarlo hay que tirar con fuerza: hasta unos 70 píxeles apenas se mueve, como un
  imán de verdad; a partir de ahí se suelta.
- Al llegar a la pared se aplasta y rebota como una pelota.
- Con los dos servicios, en los bordes izquierdo y derecho se coloca en vertical, y arriba
  y abajo en horizontal.
- Recuerda el borde y la posición entre sesiones, también con varios monitores.

**Ratón:**

- Doble clic: actualiza al momento los servicios activos y reinicia el ritmo rápido de
  consultas de Claude.
- Clic derecho: menú para elegir servicios, «Actualizar» y «Cerrar». Es la única forma de
  cerrarlo, porque el widget no tiene barra de título.

## Claude: la cuota y cómo se lee

### Qué es la cuota

Los planes de Claude tienen dos límites de uso, los mismos que aparecen en
<https://claude.ai/settings/usage>:

- **Sesión de 5 horas:** se restablece a una hora fija tras empezar a usarla.
- **Semana:** se restablece una vez por semana.

Los dos se comparten entre claude.ai, la app de escritorio y Claude Code. El widget muestra
el porcentaje usado y la hora de reinicio de cada uno. No calcula costes en dinero.

### De dónde salen los datos

Claude no ofrece una consulta oficial de cuota como la de Codex, y la app de escritorio no
expone esos datos. Pero cada vez que Claude Code recibe una respuesta, el servidor le
informa del estado de la cuota. Claude Code lo publica de dos formas, y el widget usa las
dos:

1. **Consulta automática** (principal). El widget ejecuta Claude Code en segundo plano, sin
   ventana, con una petición mínima, y lee el evento `rate_limit_event` de su salida
   `stream-json`. Ese evento trae `unifiedWindows.five_hour` y `unifiedWindows.seven_day`
   con el porcentaje usado (`utilization`, de 0 a 1) y la hora de reinicio (`resetsAt`).
   Código: `usage_monitor/claude_probe.py`.
2. **Línea de estado de Claude Code** (opcional, sin coste). Si usas Claude Code en una
   terminal, Claude Code pasa `rate_limits.five_hour` y `rate_limits.seven_day` a su línea
   de estado tras cada respuesta. `claude_statusline.py` guarda esos datos en
   `.runtime/claude.json` y muestra `5h: 75% · 7d: 55%` abajo en la terminal. El widget lee
   ese archivo cada 3 segundos, así que mientras trabajas en la terminal recibe datos sin
   hacer consultas propias. Para activarla, añade esto a `~/.claude/settings.json`,
   cambiando `<carpeta del proyecto>` por la ruta donde clonaste el repositorio:

   ```json
   "statusLine": {
     "type": "command",
     "command": "py -3 \"<carpeta del proyecto>/claude_statusline.py\""
   }
   ```

   La línea de estado solo funciona en sesiones de Claude Code en terminal. La pestaña Code
   de la app de escritorio de Claude no la ejecuta.

La sesión la gestiona siempre Claude Code. El widget no lee, copia ni renueva tokens.

### Cómo se consigue que gaste tan pocos tokens

La consulta automática es una petición real a Claude, así que cuenta para tu cuota. Una
llamada normal a `claude -p` carga el prompt de sistema completo de Claude Code, sus
herramientas, los servidores MCP, los plugins, los hooks y tu `CLAUDE.md`: en una prueba
fueron unos **49.000 tokens** de entrada. La consulta del widget se recortó a unos
**1.500 tokens de Haiku**, unas 30 veces menos. Se usa este comando:

```text
claude -p "ok" --model haiku --output-format stream-json --verbose --max-turns 1
       --tools "" --system-prompt "Responde solo: ok" --strict-mcp-config
       --disable-slash-commands --no-session-persistence --setting-sources ""
```

| Opción | Por qué |
| --- | --- |
| `--model haiku` | El modelo más barato; el dato de cuota es el mismo con cualquier modelo. |
| `-p "ok"` y `--max-turns 1` | Un único turno con una pregunta de una palabra; la respuesta es «ok». |
| `--system-prompt "Responde solo: ok"` | Sustituye el prompt de sistema completo de Claude Code. |
| `--tools ""` | Sin herramientas: no se envían sus definiciones. |
| `--strict-mcp-config` | No carga tus servidores MCP. |
| `--setting-sources ""` | No carga tus ajustes, hooks ni plugins (tampoco la línea de estado). |
| `--disable-slash-commands` | No carga skills ni comandos. |
| `--no-session-persistence` | No guarda la conversación en tu historial. |
| `--output-format stream-json --verbose` | Necesario para recibir el evento `rate_limit_event`. |

Además se ejecuta en una carpeta temporal vacía, para que no cargue el `CLAUDE.md` ni la
configuración de ningún proyecto, sin consola visible y con un límite de 90 segundos.

No se usa `--bare`, que sería aún más pequeño, porque solo admite autenticación con clave
de API y no la sesión de un plan de suscripción.

**Coste medido:** unos 1.500 tokens de entrada y 4 de salida por consulta, en unos
5 segundos. A precio de API equivaldría a unos 0,0003 $ por consulta. Con un plan de
suscripción no se paga por consulta, pero consume una parte muy pequeña de la cuota. Como
el porcentaje se muestra redondeado, la consulta casi nunca lo mueve por sí sola.

### Cuándo consulta (ritmo adaptativo)

Para no gastar cuando no hace falta, la frecuencia se adapta a la actividad
(`usage_monitor/schedule.py`):

1. Al abrir el widget consulta al momento y sigue **cada 30 segundos**.
2. Si en **2 minutos** el porcentaje no cambia, pasa a **cada 5 minutos**.
3. Si en la siguiente consulta sigue igual, pasa a **cada 30 minutos**.
4. Vuelve a cada 30 segundos si el contador cambia, en cualquier fase, o si haces doble
   clic.
5. Si una consulta falla (sin sesión o sin red), espera al menos 5 minutos antes de
   reintentar.

Las lecturas de la línea de estado pueden acelerar el ritmo si traen un cambio, pero nunca
lo frenan.

| Situación | Consultas por hora | Tokens de Haiku por hora |
| --- | --- | --- |
| Trabajando, el contador se mueve | unas 120 | unos 180.000 |
| En reposo, fase de 30 minutos | 2 | unos 3.000 |
| Widget cerrado o Claude desactivado | 0 | 0 |

## Codex

El widget consulta `account/rateLimits/read` a través del `app-server` de Codex CLI, cada
**30 segundos** y con doble clic. Es una consulta de estado de la cuenta: no inicia
conversaciones ni gasta tokens. Código: `usage_monitor/codex.py`.

Usa la cuenta con la que tengas iniciada sesión en Codex CLI, que puede no ser la misma que
la de la app de Codex. Si una cuenta no informa de algún porcentaje, se muestra como
ausente, nunca como cero.

## Datos y privacidad

- `.runtime/` guarda solo preferencias (servicios elegidos y posición del widget) y las
  últimas lecturas de cuota. Está excluida de Git y no contiene credenciales.
- `.runtime/statusline-diagnostico.json` registra la hora de la última llamada de la línea
  de estado y los nombres de los campos recibidos, nunca su contenido. Sirve para
  diagnosticar.
- Las sesiones de Claude Code y Codex las gestionan sus propias herramientas.
- Las horas se muestran en la zona horaria del ordenador.
- Los datos se consideran antiguos tras 31 minutos o al pasar la hora de reinicio prevista;
  el widget no supone que el uso vuelve a cero a esa hora.
- El comportamiento de Claude Code y de Codex CLI puede cambiar en futuras versiones.
  Revisa el código antes de usarlo en otro equipo.

## Archivos

| Ruta | Función |
| --- | --- |
| `Abrir.vbs`, `app.pyw` | Arranque sin consola. |
| `instalar_inicio.ps1` | Activa o quita el arranque con Windows. |
| `usage_monitor/ui.py` | Ventana flotante: imán, resistencia, rebote, ampliación, ratón, selección de servicios y ritmo de consultas. |
| `usage_monitor/render.py` | Dibujo con Pillow, supersampleado a 3x para suavizar bordes. |
| `usage_monitor/claude_probe.py` | Consulta automática mínima a Claude Code. |
| `usage_monitor/schedule.py` | Ritmo adaptativo de Claude (30 s, 5 min, 30 min). |
| `usage_monitor/statusline.py`, `claude_statusline.py` | Receptor de la línea de estado de Claude Code. |
| `usage_monitor/codex.py` | Consulta a Codex CLI por `app-server`. |
| `usage_monitor/models.py` | Validación de datos. |
| `usage_monitor/storage.py` | Lectura y escritura segura de `.runtime/`. |
| `usage_monitor/__main__.py` | Arranque desde terminal y opción `--providers`. |

## Diagnóstico

- **Anillo de Claude con guion o mensaje de error:** abre una terminal y ejecuta `claude`
  para comprobar que la sesión sigue iniciada.
- **Probar la consulta de Claude a mano** (gasta una consulta):

  ```powershell
  py -3 -c "from usage_monitor.claude_probe import ClaudeProbe; print(ClaudeProbe().fetch())"
  ```

- **Probar Codex a mano:**

  ```powershell
  py -3 -m usage_monitor --probe-codex
  ```

- **La línea de estado no escribe datos:** revisa
  `.runtime/statusline-diagnostico.json`. Si no existe, Claude Code no está ejecutando el
  script; si existe sin `rate_limits`, aún no ha llegado ninguna respuesta en esa sesión.
- **El widget no aparece:** puede estar en otro monitor. Borra la clave `dock` de
  `.runtime/settings.json` y vuelve a abrirlo; aparecerá arriba a la derecha.

## Pruebas

```powershell
py -3 -m unittest discover -s tests -v
py -3 -m unittest discover -s tests -p ui_smoke.py -v
```

Cubren la validación de datos y de ajustes, el ritmo adaptativo, el protocolo de Codex, el
dibujo con uno y dos servicios, la opción `--providers` y pruebas reales de la ventana:
tirón corto y largo, imán al borde, orientación, ampliación, selección de servicios sin
consultas de los desactivados, un único bucle de consulta tras activar y desactivar, y
cierre limpio.

## Alternativas descartadas

- **Extensión del navegador:** leía la página de Uso de claude.ai, pero obligaba a tener el
  navegador abierto con esa pestaña.
- **Leer el token de `~/.claude/.credentials.json`:** caduca, y renovarlo desde fuera podría
  cerrar la sesión de Claude Code. Además, el servicio de cuota que usaría no está
  documentado.
- **App de escritorio de Claude:** no expone los datos de cuota.

## Referencias

- [Codex app-server](https://learn.chatgpt.com/docs/app-server)
- [Línea de estado de Claude Code](https://code.claude.com/docs/en/statusline)
- [Referencia de la CLI de Claude Code (`claude -p`)](https://code.claude.com/docs/en/cli-reference)

## Licencia

[MIT](LICENSE).
