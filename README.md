# Bot de Telegram: avisos de directos (gratis con GitHub Actions)

Avisa por Telegram cuando un streamer empieza directo en Twitch, YouTube o Kick.
Solo notifica al pasar de offline a online. Corre gratis en GitHub Actions cada 5 minutos,
sin servidor propio. Los avisos pueden llegar con unos minutos de retraso.

## 1. Telegram
1. Habla con [@BotFather](https://t.me/BotFather), envía `/newbot` y copia el token.
2. Escríbele un mensaje a tu bot nuevo.
3. Abre `https://api.telegram.org/bot<TOKEN>/getUpdates` y copia el `chat.id`.

## 2. Credenciales (gratis)
- **Twitch**: app en https://dev.twitch.tv/console/apps → Client ID y Secret.
- **Kick**: app en https://kick.com/settings/developer → Client ID y Secret.
- **YouTube**: no necesita clave.

Si no usas una plataforma, deja su lista vacía en `streamers.json` y no pongas sus claves.

## 3. Subir a GitHub
1. Crea un repositorio **público** (así las Actions son ilimitadas y gratis) y sube todo
   el contenido de esta carpeta, incluida la carpeta oculta `.github`.
2. En el repo: **Settings → Secrets and variables → Actions → New repository secret**.
   Crea estos secretos (los de plataformas que no uses, omítelos):
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`
   - `KICK_CLIENT_ID`, `KICK_CLIENT_SECRET`
3. En **Settings → Actions → General → Workflow permissions** elige
   **Read and write permissions** y guarda (para que el bot pueda guardar `state.json`).
4. Ve a la pestaña **Actions**, abre "Revisar directos" y pulsa **Run workflow** para probar.

Desde ahí corre solo cada 5 minutos.

## 4. Elegir streamers (desde Telegram)
Escríbele al bot en tu chat:

| Comando | Qué hace |
|---|---|
| `/add twitch ibai` | Añade un canal de Twitch |
| `/add kick xqc` | Añade un canal de Kick |
| `/add youtube @ibai` | Añade un canal de YouTube |
| `/remove twitch ibai` | Quita un canal |
| `/list` | Muestra tu lista |
| `/help` | Muestra la ayuda |

También vale pegar el enlace: `/add https://twitch.tv/ibai`.

El bot lee tus mensajes en cada ejecución (cada 5 minutos), así que la respuesta puede
tardar unos minutos. Solo obedece al chat configurado en `TELEGRAM_CHAT_ID`.
Los cambios se guardan solos en `streamers.json` dentro del repo.

También puedes editar `streamers.json` a mano en GitHub (el lápiz en la web).

## Notas importantes
- El repo es público, así que `streamers.json` y `state.json` se ven. Las claves no:
  están en Secrets.
- GitHub puede ejecutar el cron con retrasos de varios minutos en horas de mucha carga.
- GitHub desactiva los workflows programados de repos públicos tras 60 días sin actividad.
  El workflow "Mantener activo" (`keepalive.yml`) hace un commit vacío una vez al mes y
  vuelve a habilitar la revisión para evitarlo. Aun así, si un día dejan de llegar avisos,
  mira la pestaña Actions y pulsa **Enable workflow** si aparece desactivado.
- Al primer arranque te avisará de quien ya esté en directo en ese momento.
- La detección de YouTube lee el HTML de la página y puede romperse si YouTube la cambia.

## Uso local (opcional)
```bash
pip install -r requirements.txt
cp .env.example .env      # y rellénalo
python bot.py             # bucle continuo
python bot.py --once      # una sola revisión
```
