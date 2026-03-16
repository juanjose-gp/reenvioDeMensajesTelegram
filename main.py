import os
import asyncio
import threading
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ---------------- CONFIG ----------------
SESSION_STRING = os.environ["SESSION_STRING"]
api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]

client = TelegramClient(StringSession(SESSION_STRING), api_id, api_hash)

PAIRS = [
    (-1003020297428, -1003728976509),
    #subir  a git paar es server
]

ORIGENES = [o for o, _ in PAIRS]
pair_map = dict(PAIRS)

# origen_chat_id -> {mensaje_origen_id: mensaje_destino_id}
mapa_por_origen = {origen: {} for origen, _ in PAIRS}


# ---------------- DEBUG MENSAJES ----------------
@client.on(events.NewMessage)
async def debug(event):
    try:
        title = getattr(event.chat, "title", None) or getattr(event.chat, "username", None) or "SIN_TITULO"
        reply = getattr(event.message, "reply_to", None)

        print(
            "[DEBUG] "
            f"chat_id={event.chat_id} | "
            f"chat={title} | "
            f"msg_id={event.message.id} | "
            f"texto={event.raw_text[:80]!r} | "
            f"forum={getattr(event.chat, 'forum', None)} | "
            f"reply_to_msg_id={getattr(reply, 'reply_to_msg_id', None) if reply else None} | "
            f"reply_to_top_id={getattr(reply, 'reply_to_top_id', None) if reply else None} | "
            f"forum_topic={getattr(reply, 'forum_topic', None) if reply else None}",
            flush=True
        )
    except Exception as e:
        print(f"[DEBUG ERROR] {repr(e)}", flush=True)


# ---------------- BOT REENVIO ----------------
@client.on(events.NewMessage(chats=ORIGENES))
async def forward(event):
    try:
        origen = event.chat_id
        destino = pair_map.get(origen)

        if not destino:
            print(f"[REENVIO] Sin destino para origen={origen}", flush=True)
            return

        sent_msg = None

        # Detectar reply al mensaje original
        reply_to_destino = None
        if event.message.reply_to and getattr(event.message.reply_to, "reply_to_msg_id", None):
            replied_origen_id = event.message.reply_to.reply_to_msg_id
            reply_to_destino = mapa_por_origen[origen].get(replied_origen_id)

            print(
                f"[REENVIO] Reply detectado | "
                f"origen={origen}:{event.message.id} | "
                f"responde_a={replied_origen_id} | "
                f"reply_to_destino={reply_to_destino}",
                flush=True
            )

        # Media con caption opcional
        if event.message.media:
            sent_msg = await client.send_file(
                destino,
                event.message.media,
                caption=event.message.text or "",
                reply_to=reply_to_destino
            )

        # Texto sin media
        elif event.message.text:
            sent_msg = await client.send_message(
                destino,
                event.message.text,
                reply_to=reply_to_destino
            )

        if sent_msg:
            mapa_por_origen[origen][event.message.id] = sent_msg.id
            print(
                f"[REENVIO] Copiado: "
                f"{origen}:{event.message.id} -> {destino}:{sent_msg.id}",
                flush=True
            )
        else:
            print(
                f"[REENVIO] Ignorado: {origen}:{event.message.id} "
                f"(sin texto ni media compatible)",
                flush=True
            )

    except Exception as e:
        print(f"[ERROR][REENVIO] {repr(e)}", flush=True)


# ---------------- EDICION DE MENSAJES ----------------
@client.on(events.MessageEdited(chats=ORIGENES))
async def on_edit(event):
    try:
        origen = event.chat_id
        destino = pair_map.get(origen)

        if not destino:
            print(f"[EDIT] Sin destino para origen={origen}", flush=True)
            return

        origen_msg_id = event.message.id
        destino_msg_id = mapa_por_origen[origen].get(origen_msg_id)

        if not destino_msg_id:
            print(f"[EDIT] No encontré mapeo para editar {origen}:{origen_msg_id}", flush=True)
            return

        nuevo_texto = event.message.text or ""

        await client.edit_message(destino, destino_msg_id, nuevo_texto)
        print(
            f"[EDIT] Editado: "
            f"{origen}:{origen_msg_id} -> {destino}:{destino_msg_id}",
            flush=True
        )

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
