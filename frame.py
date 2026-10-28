import os

import telebot
from telebot.types import MessageEntity

BOT_TOKEN = os.environ.get('FRAME_BOT_TOKEN', '').strip()
if not BOT_TOKEN:
    raise SystemExit('Set FRAME_BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)


@bot.message_handler(commands=['start'])
def start_command(handler_message):
    # Текст сообщения с плейсхолдером для кастомного эмодзи
    # Символ-заглушка для custom emoji — любой emoji, например "⭐"
    text = (
        "Приветствую, вы попали в бота-идентификатора вашей личности, "
        "верификация Frame дает вам возможность выдать себя за нормальную личность, "
        "в случае некоторых недочетов ваша верификация будет снята. "
        "Оформить верификацию можно командой /offer ⭐"
    )

    # Находим позицию символа ⭐ в тексте (UTF-16 offset)
    # Нужно вычислить offset в UTF-16 единицах
    emoji_char = "⭐"
    emoji_pos = text.index(emoji_char)

    # Вычисляем UTF-16 offset
    utf16_offset = len(text[:emoji_pos].encode('utf-16-le')) // 2
    utf16_length = len(emoji_char.encode('utf-16-le')) // 2

    entity = MessageEntity(
        type="custom_emoji",
        offset=utf16_offset,
        length=utf16_length,
        custom_emoji_id="5341801360344000141"
    )

    bot.send_message(
        chat_id=handler_message.chat.id,
        text=text,
        entities=[entity]
    )


@bot.message_handler(commands=['offer'])
def offer_command(handler_message):
    text = "Покупка временно недоступна, попробуйте сообщить администратору ⭐"

    emoji_char = "⭐"
    emoji_pos = text.index(emoji_char)

    utf16_offset = len(text[:emoji_pos].encode('utf-16-le')) // 2
    utf16_length = len(emoji_char.encode('utf-16-le')) // 2

    entity = MessageEntity(
        type="custom_emoji",
        offset=utf16_offset,
        length=utf16_length,
        custom_emoji_id="5472267631979405211"
    )

    bot.send_message(
        chat_id=handler_message.chat.id,
        text=text,
        entities=[entity]
    )


print("Бот запущен...")
bot.infinity_polling()