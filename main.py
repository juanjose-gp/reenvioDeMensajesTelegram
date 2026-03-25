import os
import asyncio
import threading
import time
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    MessageNotModifiedError,
    FloodWaitError,
    MediaCaptionTooLongError,
)

# ---------------- CONFIG ----------------
SESSION_STRING = os.environ["SESSION_STRING"]
api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]

client = TelegramClient(StringSession(SESSION_STRING), api_id, api_hash)

# -------------------------------------------------
# RUTAS DE FOROS: (chat origen, topic_id) -> chat destino
# -------------------------------------------------
FORUM_PAIRS = {
    (-1003805449629, 3): -1003832259307,  # tema PRO / topic 3
    (-1003805449629, 2): -1003786011342,  # tema BASIC / topic 2
    (-1003805449629, 2417): -1003554150595,         # intermedio
}

ORIGENES = list({chat_id for chat_id, _ in FORUM_PAIRS.keys()})

# clave_origen -> {mensaje_origen_id: mensaje_destino_id}
mapa_por_origen = {forum_key: {} for forum_key in FORUM_PAIRS.keys()}

# Anti-duplicados para edits
ultimo_edit_procesado = {}
ultimo_intento_edit = {}
VENTANA_EDIT = 3  # segundos


def extraer_topic_id(event):
    reply = getattr(event.message, "reply_to", None)
    if not reply:
        return None

    top_id = getattr(reply, "reply_to_top_id", None)
    if top_id is not None:
        return top_id

    msg_id = getattr(reply, "reply_to_msg_id", None)
    if getattr(reply, "forum_topic", False) and msg_id is not None:
        return msg_id

    return None


def resolver_destino(event):
    chat_id = event.chat_id
    topic_id = extraer_topic_id(event)

    if topic_id is not None:
        destino = FORUM_PAIRS.get((chat_id, topic_id))
        if destino:
            return destino, ("forum", (chat_id, topic_id), topic_id)

    return None, (None, None, topic_id)


def firma_mensaje_editado(event):
    texto = event.message.text or ""
    tiene_media = event.message.media is not None
    media_tipo = type(event.message.media).__name__ if event.message.media else None
    return (texto, tiene_media, media_tipo)


# ---------------- DEBUG MENSAJES ----------------
@client.on(events.NewMessage)
async def debug(event):
    try:
        title = getattr(event.chat, "title", None) or getattr(event.chat, "username", None) or "SIN_TITULO"
        reply = getattr(event.message, "reply_to", None)
        topic_id = extraer_topic_id(event)

        print(
            "[DEBUG] "
            f"chat_id={event.chat_id} | "
            f"chat={title} | "
            f"msg_id={event.message.id} | "
            f"texto={event.raw_text[:80]!r} | "
            f"forum={getattr(event.chat, 'forum', None)} | "
            f"reply_to_msg_id={getattr(reply, 'reply_to_msg_id', None) if reply else None} | "
            f"reply_to_top_id={getattr(reply, 'reply_to_top_id', None) if reply else None} | "
            f"forum_topic={getattr(reply, 'forum_topic', None) if reply else None} | "
            f"topic_id_resuelto={topic_id}",
            flush=True
        )
    except Exception as e:
        print(f"[DEBUG ERROR] {repr(e)}", flush=True)


# ---------------- BOT REENVIO ----------------
@client.on(events.NewMessage(chats=ORIGENES))
async def forward(event):
    try:
        origen = event.chat_id
        destino, meta = resolver_destino(event)
        route_type, map_key, topic_id = meta

        if not destino:
            print(
                f"[REENVIO] Sin destino para origen={origen} | topic_id={topic_id}",
                flush=True
            )
            return

        sent_msg = None

        reply_to_destino = None
        if event.message.reply_to and getattr(event.message.reply_to, "reply_to_msg_id", None):
            replied_origen_id = event.message.reply_to.reply_to_msg_id
            reply_to_destino = mapa_por_origen.get(map_key, {}).get(replied_origen_id)

            print(
                f"[REENVIO] Reply detectado | "
                f"route_type={route_type} | "
                f"map_key={map_key} | "
                f"origen={origen}:{event.message.id} | "
                f"responde_a={replied_origen_id} | "
                f"reply_to_destino={reply_to_destino}",
                flush=True
            )

        if event.message.media:
            caption_seguro = (event.message.text or "")[:1024]
            sent_msg = await client.send_file(
                destino,
                event.message.media,
                caption=caption_seguro,
                reply_to=reply_to_destino
            )
        elif event.message.text:
            sent_msg = await client.send_message(
                destino,
                event.message.text,
                reply_to=reply_to_destino
            )

        if sent_msg:
            mapa_por_origen[map_key][event.message.id] = sent_msg.id
            print(
                f"[REENVIO] Copiado | "
                f"route_type={route_type} | "
                f"topic_id={topic_id} | "
                f"{origen}:{event.message.id} -> {destino}:{sent_msg.id}",
                flush=True
            )
        else:
            print(
                f"[REENVIO] Ignorado: {origen}:{event.message.id} "
                f"(sin texto ni media compatible)",
                flush=True
            )

    except FloodWaitError as e:
        print(f"[FLOOD][REENVIO] Esperando {e.seconds} segundos", flush=True)
        await asyncio.sleep(e.seconds)

    except Exception as e:
        print(f"[ERROR][REENVIO] {repr(e)}", flush=True)


# ---------------- EDICION DE MENSAJES ----------------
@client.on(events.MessageEdited(chats=ORIGENES))
async def on_edit(event):
    try:
        origen = event.chat_id
        destino, meta = resolver_destino(event)
        route_type, map_key, topic_id = meta

        if not destino:
            print(
                f"[EDIT] Sin destino para origen={origen} | topic_id={topic_id}",
                flush=True
            )
            return

        origen_msg_id = event.message.id
        destino_msg_id = mapa_por_origen.get(map_key, {}).get(origen_msg_id)

        if not destino_msg_id:
            print(
                f"[EDIT] No encontré mapeo para editar "
                f"{origen}:{origen_msg_id} | map_key={map_key}",
                flush=True
            )
            return

        # Debounce
        ahora = time.time()
        clave_tiempo = (map_key, origen_msg_id)
        if ahora - ultimo_intento_edit.get(clave_tiempo, 0) < VENTANA_EDIT:
            print(f"[EDIT] Debounce ignorado: {origen}:{origen_msg_id}", flush=True)
            return
        ultimo_intento_edit[clave_tiempo] = ahora

        # Deduplicación
        firma = firma_mensaje_editado(event)
        clave_edit = (map_key, origen_msg_id)
        if ultimo_edit_procesado.get(clave_edit) == firma:
            print(f"[EDIT] Duplicado ignorado: {origen}:{origen_msg_id}", flush=True)
            return

        nuevo_texto = event.message.text or ""

        if event.message.media:
            caption_seguro = nuevo_texto[:1024]

            try:
                await client.edit_message(
                    destino,
                    destino_msg_id,
                    text=caption_seguro,
                    file=event.message.media
                )

                ultimo_edit_procesado[clave_edit] = firma

                print(
                    f"[EDIT] Editado con media | "
                    f"{origen}:{origen_msg_id} -> {destino}:{destino_msg_id}",
                    flush=True
                )

            except MediaCaptionTooLongError:
                print(
                    f"[EDIT] Caption demasiado largo, ignorado: {origen}:{origen_msg_id}",
                    flush=True
                )
                return

            except MessageNotModifiedError:
                print(
                    f"[EDIT] Sin cambios reales en media/texto: {origen}:{origen_msg_id}",
                    flush=True
                )
                return

            return

        try:
            await client.edit_message(
                destino,
                destino_msg_id,
                text=nuevo_texto
            )

            ultimo_edit_procesado[clave_edit] = firma

            print(
                f"[EDIT] Editado texto | "
                f"{origen}:{origen_msg_id} -> {destino}:{destino_msg_id}",
                flush=True
            )

        except MessageNotModifiedError:
            print(
                f"[EDIT] Sin cambios reales en texto: {origen}:{origen_msg_id}",
                flush=True
            )

    except FloodWaitError as e:
        print(f"[FLOOD][EDIT] Esperando {e.seconds} segundos", flush=True)
        await asyncio.sleep(e.seconds)

    except Exception as e:
        print(f"[ERROR][EDIT] {repr(e)}", flush=True)


# ---------------- BOT LOOP ----------------
def run_bot():
    print("[SYSTEM] Iniciando bot...", flush=True)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def main():
        await client.start()
        me = await client.get_me()
        print(f"[SYSTEM] Bot activo como: {me.id}", flush=True)
        print(f"[SYSTEM] ORIGENES: {ORIGENES}", flush=True)
        print(f"[SYSTEM] FORUM_PAIRS: {FORUM_PAIRS}", flush=True)
        await client.run_until_disconnected()

    loop.run_until_complete(main())


# ---------------- WEB ----------------
app = Flask(__name__)

@app.get("/")
def health():
    return "OK", 200

@app.get("/ping")
def ping():
    return "PONG", 200


if __name__ == "__main__":
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()

    port = int(os.environ.get("PORT", "10000"))
    print(f"[SYSTEM] Web listening on port {port}", flush=True)
    app.run(host="0.0.0.0", port=port)