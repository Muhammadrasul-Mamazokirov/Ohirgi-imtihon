import os
import sqlite3
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# .env fayldan token va muhit o'zgaruvchilarini yuklash
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# Bot va Dispatcher yaratish
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Logging sozlamalari
logging.basicConfig(level=logging.INFO)

# ==================== BAZA SOZLAMALARI ====================
def init_db():
    try:
        conn = sqlite3.connect('todo.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        name TEXT,
                        deadline TEXT,
                        status INTEGER DEFAULT 0
                    )''')
        conn.commit()
    except Exception as e:
        logging.error(f"Baza xatosi: {e}")
    finally:
        conn.close()

# ==================== KEYBOARDLAR ====================
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Vazifa qo'shish")],
        [KeyboardButton(text="Vazifalarim"), KeyboardButton(text="Statistika")]
    ],
    resize_keyboard=True
)

def task_kb(task_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Bajarildi", callback_data=f"done_{task_id}"),
                InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"del_{task_id}")
            ]
        ]
    )

# ==================== FSM HOLATLARI ====================
class TaskStates(StatesGroup):
    name = State()
    deadline = State()

# ==================== HANDLERLAR ====================

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    try:
        conn = sqlite3.connect('todo.db')
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.from_user.id,))
        conn.commit()
        conn.close()
        await message.answer("Assalomu alaykum! Todo botga xush kelibsiz. Quyidagi menyudan foydalaning:", reply_markup=main_kb)
    except Exception as e:
        logging.error(f"/start xatosi: {e}")

@dp.message(F.text == "Vazifa qo'shish")
async def add_task_start(message: types.Message, state: FSMContext):
    await message.answer("Yangi vazifa nomini kiriting:")
    await state.set_state(TaskStates.name)

@dp.message(TaskStates.name)
async def process_task_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Muddatni kiriting (KK.OO.YYYY) yoki \"O'tkazib yuborish\" deb yozing:")
    await state.set_state(TaskStates.deadline)

@dp.message(TaskStates.deadline)
async def process_task_deadline(message: types.Message, state: FSMContext):
    text = message.text
    deadline = None
    
    if text != "O'tkazib yuborish":
        try:
            # Sani tekshirish
            dt = datetime.strptime(text, "%d.%m.%Y")
            if dt.date() < datetime.now().date():
                await message.answer("O'tib ketgan sana kiritdingiz! Iltimos, kelajakdagi sanani yoki \"O'tkazib yuborish\" so'zini kiriting:")
                return
            deadline = text
        except ValueError:
            await message.answer("Noto'g'ri format! Iltimos, KK.OO.YYYY formatida yozing yoki \"O'tkazib yuborish\" deb kiriting:")
            return

    data = await state.get_data()
    name = data['name']

    try:
        conn = sqlite3.connect('todo.db')
        c = conn.cursor()
        c.execute("INSERT INTO tasks (user_id, name, deadline) VALUES (?, ?, ?)",
                  (message.from_user.id, name, deadline))
        conn.commit()
        conn.close()
        
        await message.answer("✅ Vazifa muvaffaqiyatli qo'shildi!", reply_markup=main_kb)
        await state.clear()
    except Exception as e:
        logging.error(f"Vazifa saqlashda xato: {e}")
        await message.answer("Xatolik yuz berdi. Iltimos qayta urinib ko'ring.")
        await state.clear()

@dp.message(F.text == "Vazifalarim")
async def my_tasks_handler(message: types.Message):
    try:
        conn = sqlite3.connect('todo.db')
        c = conn.cursor()
        c.execute("SELECT id, name, deadline FROM tasks WHERE user_id=? AND status=0", (message.from_user.id,))
        tasks = c.fetchall()
        conn.close()

        if not tasks:
            await message.answer("🎉 Sizda hozircha faol vazifalar yo'q. Dam olishingiz mumkin!")
            return

        for task in tasks:
            t_id, name, deadline = task
            dl_text = deadline if deadline else "Biriktirilmagan"
            text = f"📌 <b>Vazifa:</b> {name}\n⏳ <b>Muddat:</b> {dl_text}"
            
            await message.answer(text, parse_mode="HTML", reply_markup=task_kb(t_id))
    except Exception as e:
        logging.error(f"Vazifalarim qismida xato: {e}")

@dp.callback_query(F.data.startswith("done_"))
async def mark_done_callback(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    try:
        conn = sqlite3.connect('todo.db')
        c = conn.cursor()
        c.execute("UPDATE tasks SET status=1 WHERE id=? AND user_id=?", (task_id, callback.from_user.id))
        conn.commit()
        conn.close()

        # Xabarni tahrirlash (yangi xabar yubormaslik)
        await callback.message.edit_text(
            f"✅ <b>Bajarildi!</b>\n\n<s>{callback.message.text}</s>", 
            parse_mode="HTML", 
            reply_markup=None
        )
    except Exception as e:
        logging.error(f"Done callback xatosi: {e}")
    finally:
        await callback.answer("Vazifa bajarilgan deb belgilandi!")

@dp.callback_query(F.data.startswith("del_"))
async def delete_task_callback(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    try:
        conn = sqlite3.connect('todo.db')
        c = conn.cursor()
        c.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (task_id, callback.from_user.id))
        conn.commit()
        conn.close()

        # Xabarni o'chirildi degan yozuvga o'zgartirish
        await callback.message.edit_text("🗑 <b>Vazifa o'chirildi.</b>", parse_mode="HTML", reply_markup=None)
    except Exception as e:
        logging.error(f"Delete callback xatosi: {e}")
    finally:
        await callback.answer("O'chirildi!")

@dp.message(F.text == "Statistika")
async def stats_handler(message: types.Message):
    try:
        conn = sqlite3.connect('todo.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM tasks WHERE user_id=?", (message.from_user.id,))
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status=1", (message.from_user.id,))
        done = c.fetchone()[0]
        conn.close()

        pending = total - done
        text = (f"📊 <b>Statistikangiz:</b>\n\n"
                f"📝 Jami vazifalar: {total}\n"
                f"✅ Bajarilgan: {done}\n"
                f"⏳ Qolgan vazifalar: {pending}")
        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Statistika xatosi: {e}")

# ==================== BOTNI ISHGA TUSHIRISH ====================
async def main():
    init_db()
    try:
        print("Bot ishga tushdi...")
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Bot to'xtab qoldi: {e}")

if __name__ == "__main__":
    asyncio.run(main())