import os
from telethon import TelegramClient, events

api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]

client = TelegramClient("mi_sesion_reenviar", api_id, api_hash)

# Define tus pares origen -> destino
PAIRS = [
    (-1003585196721, -1003820294533),
    (-1003020297428, -1003728976509),
]

# Mapas separados por cada origen:  origen_chat_id -> {mensaje_origen_id: mensaje_destino_id}
mapa_por_origen = {origen: {} for origen, _ in PAIRS}

# -------- MENSAJES NUEVOS --------
@client.on(events.NewMessage(chats=[o for o, _ in PAIRS]))
async def on_new_message(event):
    try:
        origen_chat_id = event.chat_id

        # Busca el destino correspondiente a este origen
        destino_chat_id = None
        for o, d in PAIRS:
            if o == origen_chat_id:
                destino_chat_id = d
                break

        if destino_chat_id is None:
            return

        sent_msg = None

        # Texto sin media
        if event.message.text and not event.message.media:
            sent_msg = await client.send_message(
                destino_chat_id,
                event.message.text
            )

        # Media (con caption opcional)
        elif event.message.media:
            sent_msg = await client.send_file(
                destino_chat_id,
                event.message.media,
                caption=event.message.text or ""
            )

        if sent_msg:
            mapa_por_origen[origen_chat_id][event.message.id] = sent_msg.id
            print(f"Mensaje copiado: {origen_chat_id}:{event.message.id} -> {destino_chat_id}:{sent_msg.id}")

    except Exception as e:
        print("Error copiando:", e)

# -------- MENSAJES EDITADOS --------
@client.on(events.MessageEdited(chats=[o for o, _ in PAIRS]))
async def on_edit_message(event):
    try:
        origen_chat_id = event.chat_id
        origen_id = event.message.id

        # Busca destino de ese origen
        destino_chat_id = None
        for o, d in PAIRS:
            if o == origen_chat_id:
                destino_chat_id = d
                break

        if destino_chat_id is None:
            return

        # Si no está mapeado, no hacemos nada
        if origen_id not in mapa_por_origen[origen_chat_id]:
            return

        destino_id = mapa_por_origen[origen_chat_id][origen_id]

        # Editar texto (nota: editar caption de media es otro tema; esto edita texto del mensaje)
        if event.message.text is not None:
            await client.edit_message(
                destino_chat_id,
                destino_id,
                event.message.text
            )
            print(f"Mensaje editado: {origen_chat_id}:{origen_id}")

    except Exception as e:
        print("Error editando:", e)

client.start()
print("Bot activo (2 pares: copiar + editar sin remitente)...")
client.run_until_disconnected()
