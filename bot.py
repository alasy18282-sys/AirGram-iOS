import os

import telebot

TOKEN = os.environ.get('FILE_ID_BOT_TOKEN', '').strip()
if not TOKEN:
    raise SystemExit('Set FILE_ID_BOT_TOKEN')
# Сюда нужно будет вставить длинную строку, которую выдаст бот
FILE_ID = "5773859930561845221" 

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "Отправьте мне файл, чтобы узнать его ID, или любое сообщение, чтобы получить файл из базы.")

# Служебная функция: узнаем ID любого файла, который вы пришлете
@bot.message_handler(content_types=['document', 'sticker', 'video', 'photo'])
def get_id(message):
    file_id = ""
    if message.document:
        file_id = message.document.file_id
    elif message.sticker:
        file_id = message.sticker.file_id
    
    bot.reply_to(message, f"File ID этого объекта:\n\n{file_id}")

# Отправка файла по запросу
@bot.message_handler(func=lambda m: True)
def send_file(message):
    try:
        bot.send_document(message.chat.id, FILE_ID)
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка. Скорее всего, File ID неверен.\n\nДетали: {e}")

if __name__ == "__main__":
    print("Бот запущен...")
    bot.infinity_polling()