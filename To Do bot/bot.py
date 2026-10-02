import os
import sqlite3
import asyncio
import logging
import csv
from datetime import datetime, timedelta
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, FSInputFile
import json
from openai import AsyncOpenAI

# ==================== SOZLAMALAR ====================
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# ==================== BAZA SOZLAMALARI ====================
def init_db():
    try:
        conn = sqlite3.connect('todo.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        digest_date TEXT DEFAULT ''
                    )''')
        c.execute('''CREATE TABLE IF NOT EXISTS tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        name TEXT,
                        deadline TEXT,
                        status INTEGER DEFAULT 0,
                        notified INTEGER DEFAULT 0
                    )''')
        
        new_columns = [
            ("category", "TEXT DEFAULT '➕ Boshqa'"),
            ("priority", "TEXT DEFAULT '🟢 Past'"),
            ("recurring", "TEXT DEFAULT '❌ Bir marta'"),
            ("local_id", "INTEGER DEFAULT 0") # <- YANGI USTUN
        ]
        for col_name, col_type in new_columns:
            try:
                c.execute(f"ALTER TABLE tasks ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass
        
        conn.commit()
    except Exception as e:
        logging.error(f"Baza xatosi: {e}")
    finally:
        conn.close()

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Botni ishga tushirish"),
        BotCommand(command="help", description="Qo'llanma"),
        BotCommand(command="add", description="Yangi vazifa"),
        BotCommand(command="list", description="Faol vazifalar"),
        BotCommand(command="stats", description="Statistika"),
        BotCommand(command="export", description="Vazifalarni Excel(CSV) orqali yuklash"),
        BotCommand(command="cancel", description="Jarayonni bekor qilish")
    ]
    await bot.set_my_commands(commands)

# ==================== KEYBOARDLAR ====================
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Vazifa qo'shish")],
        [KeyboardButton(text="Vazifalarim"), KeyboardButton(text="Statistika")]
    ],
    resize_keyboard=True
)

# FSM (Vazifa qo'shish) uchun Reply tugmalar
cat_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="💼 Ish"), KeyboardButton(text="📚 O'qish")], [KeyboardButton(text="🏠 Uy"), KeyboardButton(text="🛒 Xaridlar"), KeyboardButton(text="➕ Boshqa")]], resize_keyboard=True)
prio_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔴 Yuqori")], [KeyboardButton(text="🟡 O'rtacha")], [KeyboardButton(text="🟢 Past")]], resize_keyboard=True)
rec_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Bir marta")], [KeyboardButton(text="🔄 Har kuni"), KeyboardButton(text="📅 Har haftada")]], resize_keyboard=True)

def task_action_kb(task_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Bajarildi", callback_data=f"done_{task_id}"), InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"del_{task_id}")],
            [InlineKeyboardButton(text="✏️ Tahrirlash (Vaqti va Nomi)", callback_data=f"edit_{task_id}")]
        ]
    )

def reminder_kb(task_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Bajarildi", callback_data=f"done_{task_id}")],
            [InlineKeyboardButton(text="💤 15 daqiqa kuting", callback_data=f"snooze_15_{task_id}"), InlineKeyboardButton(text="💤 1 soat kuting", callback_data=f"snooze_60_{task_id}")]
        ]
    )

def pagination_kb(page, total_pages):
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"page_{page-1}"))
    buttons.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="ignore"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton(text="Oldinga ➡", callback_data=f"page_{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])

# ==================== FSM HOLATLARI ====================
class TaskStates(StatesGroup):
    name = State()
    category = State()
    priority = State()
    recurring = State()
    deadline = State()

class EditTaskStates(StatesGroup):
    task_id = State()
    name = State()
    deadline = State()

# ==================== YORDAMCHI FUNKSIYALAR ====================
def priority_value(prio_str):
    if "Yuqori" in prio_str: return 1
    if "O'rtacha" in prio_str: return 2
    return 3

def get_tasks_text(user_id, page=1, limit=5):
    conn = sqlite3.connect('todo.db')
    c = conn.cursor()
    # Endi local_id ni ham o'qiymiz
    c.execute("SELECT id, local_id, name, deadline, category, priority, recurring FROM tasks WHERE user_id=? AND status=0", (user_id,))
    tasks = c.fetchall()
    conn.close()

    if not tasks:
        return "🎉 Sizda hozircha faol vazifalar yo'q!", None

    tasks.sort(key=lambda x: priority_value(x[5]))
    
    total_pages = (len(tasks) + limit - 1) // limit
    if page > total_pages: page = total_pages
    if page < 1: page = 1
    
    start_idx = (page - 1) * limit
    page_tasks = tasks[start_idx:start_idx + limit]

    text = f"📋 <b>Sizning faol vazifalaringiz (Sahifa: {page}/{total_pages}):</b>\n\n"
    for task in page_tasks:
        real_id, local_id, name, deadline, cat, prio, rec = task
        dl_text = deadline if deadline else "Vaqt yo'q"
        # Userga local_id ni ko'rsatamiz
        text += f"🆔 <b>ID: {local_id}</b> | {prio} | {cat}\n📝 {name}\n⏳ {dl_text} | 🔄 {rec}\n〰〰〰〰〰〰〰〰〰〰〰\n"
    
    text += "\n👇 <i>Vazifani boshqarish uchun chatga uning <b>ID raqamini</b> yuboring.</i>"
    kb = pagination_kb(page, total_pages) if total_pages > 1 else None
    return text, kb

# ==================== HANDLERLAR ====================
@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    conn = sqlite3.connect('todo.db')
    conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.from_user.id,))
    conn.commit()
    conn.close()
    await message.answer("Premium Todo Botga xush kelibsiz! /help orqali qo'llanmani o'qing.", reply_markup=main_kb)

# ---------- OVOZLI XABAR ORQALI VAZIFA QO'SHISH ----------
@dp.message(F.voice)
async def handle_voice_task(message: types.Message):
    msg = await message.answer("🎙 <i>Ovozli xabaringiz tahlil qilinmoqda, kuting...</i>", parse_mode="HTML")
    
    # 1. Ovozli xabarni yuklab olish
    file_id = message.voice.file_id
    file_info = await bot.get_file(file_id)
    filepath = f"temp_voice_{file_id}.ogg"
    await bot.download_file(file_info.file_path, filepath)

    try:
        # 2. Whisper orqali ovozni matnga o'girish
        with open(filepath, "rb") as audio_file:
            transcription = await client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file,
                response_format="text"
            )
        
        # 3. GPT orqali matnni tahlil qilib JSON formatiga keltirish
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
        system_prompt = f"""
        Siz aqlli vazifa menejerisiz. Bugungi sana va vaqt: {now_str}.
        Foydalanuvchi matnidan quyidagi ma'lumotlarni ajratib oling va FAQT JSON formatida qaytaring:
        {{
            "name": "Vazifa nomi",
            "deadline": "DD.MM.YYYY HH:MM (agar vaqt ko'rsatilmagan bo'lsa null)",
            "category": "💼 Ish, 📚 O'qish, 🏠 Uy, 🛒 Xaridlar yoki ➕ Boshqa",
            "priority": "🔴 Yuqori, 🟡 O'rtacha yoki 🟢 Past"
        }}
        Matn: "{transcription}"
        """
        
        response = await client.chat.completions.create(
            model="gpt-4o-mini", # Tez va arzon model
            messages=[{"role": "system", "content": system_prompt}],
            response_format={ "type": "json_object" }
        )
        
        # 4. Natijani ajratib olish va bazaga saqlash
        task_data = json.loads(response.choices[0].message.content)
        
        name = task_data.get("name", transcription)
        deadline = task_data.get("deadline")
        category = task_data.get("category", "➕ Boshqa")
        priority = task_data.get("priority", "🟡 O'rtacha")
        recurring = "❌ Bir marta" # Ovozli orqali hozircha bir martalik
        
        user_id = message.from_user.id
        conn = sqlite3.connect('todo.db')
        c = conn.cursor()
        c.execute("SELECT COALESCE(MAX(local_id), 0) + 1 FROM tasks WHERE user_id=?", (user_id,))
        next_local_id = c.fetchone()[0]

        c.execute("INSERT INTO tasks (user_id, local_id, name, category, priority, recurring, deadline) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (user_id, next_local_id, name, category, priority, recurring, deadline))
        conn.commit()
        conn.close()

        dl_text = deadline if deadline else "Vaqt biriktirilmagan"
        await msg.edit_text(f"✅ <b>Aqlli vazifa qo'shildi!</b> (ID: {next_local_id})\n\n"
                            f"📝 {name}\n"
                            f"📂 {category} | {priority}\n"
                            f"⏳ Muddat: {dl_text}", parse_mode="HTML", reply_markup=main_kb)

    except Exception as e:
        logging.error(f"Ovozli xatosi: {e}")
        await msg.edit_text("⚠ <i>Kechirasiz, ovozli xabarni tahlil qilishda xatolik yuz berdi. Matn orqali urinib ko'ring.</i>", parse_mode="HTML")
    finally:
        # Axlat faylni o'chiramiz
        if os.path.exists(filepath):
            os.remove(filepath)

@dp.message(Command("cancel"))
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Jarayon bekor qilindi.", reply_markup=main_kb)

# ---------- VAZIFA QO'SHISH ----------
@dp.message(Command("add"))
@dp.message(F.text == "Vazifa qo'shish")
async def add_task_start(message: types.Message, state: FSMContext):
    await message.answer("📝 <b>Yangi vazifa nomini kiriting:</b>", parse_mode="HTML", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(TaskStates.name)

@dp.message(TaskStates.name)
async def process_task_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("📂 <b>Kategoriyani tanlang:</b>", parse_mode="HTML", reply_markup=cat_kb)
    await state.set_state(TaskStates.category)

@dp.message(TaskStates.category)
async def process_task_cat(message: types.Message, state: FSMContext):
    await state.update_data(category=message.text)
    await message.answer("🔥 <b>Muhimlik darajasini tanlang:</b>", parse_mode="HTML", reply_markup=prio_kb)
    await state.set_state(TaskStates.priority)

@dp.message(TaskStates.priority)
async def process_task_prio(message: types.Message, state: FSMContext):
    await state.update_data(priority=message.text)
    await message.answer("🔄 <b>Vazifa takrorlanishini tanlang:</b>", parse_mode="HTML", reply_markup=rec_kb)
    await state.set_state(TaskStates.recurring)

@dp.message(TaskStates.recurring)
async def process_task_rec(message: types.Message, state: FSMContext):
    await state.update_data(recurring=message.text)
    await message.answer("⏳ <b>Muddatni kiriting (KK.OO.YYYY SS:MM)</b>\n<i>Masalan: 05.10.2026 14:30</i>\nYoki \"O'tkazib yuborish\" deb yozing:", parse_mode="HTML", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(TaskStates.deadline)

@dp.message(TaskStates.deadline)
async def process_task_deadline(message: types.Message, state: FSMContext):
    text = message.text
    deadline = None
    if text.lower() != "o'tkazib yuborish":
        try:
            dt = datetime.strptime(text, "%d.%m.%Y %H:%M")
            if dt < datetime.now():
                await message.answer("⚠ O'tib ketgan vaqt! Kelajakdagi vaqtni kiriting:")
                return
            deadline = text
        except ValueError:
            await message.answer("⚠ Noto'g'ri format! (KK.OO.YYYY SS:MM) formatida yozing:")
            return

    data = await state.get_data()
    try:
        user_id = message.from_user.id
        conn = sqlite3.connect('todo.db')
        c = conn.cursor()
        
        # Foydalanuvchining shaxsiy oxirgi ID sini topish
        c.execute("SELECT COALESCE(MAX(local_id), 0) + 1 FROM tasks WHERE user_id=?", (user_id,))
        next_local_id = c.fetchone()[0]

        c.execute("INSERT INTO tasks (user_id, local_id, name, category, priority, recurring, deadline) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (user_id, next_local_id, data['name'], data['category'], data['priority'], data['recurring'], deadline))
        conn.commit()
        conn.close()
        
        await message.answer(f"✅ <b>Vazifa muvaffaqiyatli qo'shildi!</b> (Shaxsiy ID: {next_local_id})", parse_mode="HTML", reply_markup=main_kb)
        await state.clear()
    except Exception as e:
        logging.error(f"Save error: {e}")

# ---------- VAZIFALARIM RO'YXATI VA SAHIFALASH ----------
@dp.message(Command("list"))
@dp.message(F.text == "Vazifalarim")
async def my_tasks_handler(message: types.Message, state: FSMContext):
    await state.clear() # <- Qotib qolgan jarayonlarni tozalaydi
    try:
        text, kb = get_tasks_text(message.from_user.id, page=1)
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logging.error(f"Ro'yxat xatosi: {e}")
        await message.answer("⚠ Vazifalarni yuklashda xatolik yuz berdi.", reply_markup=main_kb)

@dp.callback_query(F.data.startswith("page_"))
async def page_callback(callback: types.CallbackQuery):
    page = int(callback.data.split("_")[1])
    text, kb = get_tasks_text(callback.from_user.id, page=page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

# ---------- VAZIFA ID SI KIRITILGANDA ----------
@dp.message(F.text.regexp(r'^\d+$'))
async def task_id_handler(message: types.Message):
    user_local_id = int(message.text)
    conn = sqlite3.connect('todo.db')
    c = conn.cursor()
    # local_id bo'yicha qidiramiz, lekin real id ni olib qolamiz
    c.execute("SELECT id, name, deadline, category, priority, recurring FROM tasks WHERE local_id=? AND user_id=? AND status=0", 
              (user_local_id, message.from_user.id))
    task = c.fetchone()
    conn.close()

    if task:
        real_id, name, deadline, cat, prio, rec = task
        dl_text = deadline if deadline else "Biriktirilmagan"
        text = (f"📌 <b>Tanlangan vazifa (ID: {user_local_id}):</b>\n\n"
                f"📝 <b>Nomi:</b> {name}\n"
                f"📂 <b>Kategoriya:</b> {cat}\n"
                f"🔥 <b>Muhimlik:</b> {prio}\n"
                f"🔄 <b>Takrorlanish:</b> {rec}\n"
                f"⏳ <b>Muddat:</b> {dl_text}\n\nQanday amal bajaramiz?")
        # Tugmalarga real_id ketadi, bu esa xatosiz ishlashini taminlaydi
        await message.answer(text, parse_mode="HTML", reply_markup=task_action_kb(real_id))
    else:
        await message.answer("⚠ Bunday ID raqamli faol vazifangiz topilmadi.")

# ---------- TAHRIRLASH ----------
@dp.callback_query(F.data.startswith("edit_"))
async def edit_task_callback(callback: types.CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split("_")[1])
    await state.update_data(edit_task_id=task_id)
    
    text = ("✏️ Vazifaning <b>yangi nomini</b> kiriting:\n"
            "<i>(O'zgarishsiz qoldirish uchun <b>-</b>, bekor qilish uchun /cancel yuboring)</i>")
    
    await callback.message.answer(text, parse_mode="HTML", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(EditTaskStates.name)
    await callback.answer()

@dp.message(EditTaskStates.name)
async def process_edit_name(message: types.Message, state: FSMContext):
    new_name = None if message.text == "-" else message.text
    await state.update_data(edit_name=new_name)
    
    text = ("⏳ Vazifaning <b>yangi muddatini</b> (KK.OO.YYYY SS:MM) kiriting:\n"
            "<i>(O'zgarishsiz qoldirish uchun <b>-</b> belgisini yuboring)</i>")
    await message.answer(text, parse_mode="HTML")
    await state.set_state(EditTaskStates.deadline)

@dp.message(EditTaskStates.deadline)
async def process_edit_deadline(message: types.Message, state: FSMContext):
    text = message.text
    new_deadline = None
    
    if text != "-":
        if text.lower() == "o'tkazib yuborish":
            new_deadline = "clear"
        else:
            try:
                dt = datetime.strptime(text, "%d.%m.%Y %H:%M")
                if dt < datetime.now():
                    await message.answer("O'tib ketgan vaqt! Qaytadan kelajakdagi vaqtni kiriting:")
                    return
                new_deadline = text
            except ValueError:
                await message.answer("Noto'g'ri format! KK.OO.YYYY SS:MM formatida yozing (yoki - yuboring):")
                return

    data = await state.get_data()
    task_id = data['edit_task_id']
    new_name = data['edit_name']

    conn = sqlite3.connect('todo.db')
    c = conn.cursor()
    c.execute("SELECT name, deadline FROM tasks WHERE id=?", (task_id,))
    old_task = c.fetchone()

    final_name = new_name if new_name else old_task[0]
    
    if new_deadline == "clear":
        final_deadline = None
    elif new_deadline:
        final_deadline = new_deadline
    else:
        final_deadline = old_task[1]

    c.execute("UPDATE tasks SET name=?, deadline=?, notified=0 WHERE id=? AND user_id=?", 
              (final_name, final_deadline, task_id, message.from_user.id))
    conn.commit()
    conn.close()

    await message.answer(f"✅ <b>Vazifa muvaffaqiyatli tahrirlandi!</b>", parse_mode="HTML", reply_markup=main_kb)
    await state.clear()

# ---------- CALLBACKLAR (Bajarildi, Snooze, O'chirish) ----------
@dp.callback_query(F.data.startswith("done_"))
async def mark_done_callback(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    conn = sqlite3.connect('todo.db')
    c = conn.cursor()
    c.execute("SELECT deadline, recurring FROM tasks WHERE id=?", (task_id,))
    task = c.fetchone()
    
    if task:
        deadline_str, recurring = task
        if ("Har kuni" in recurring or "Har haftada" in recurring) and deadline_str:
            try:
                dt = datetime.strptime(deadline_str, "%d.%m.%Y %H:%M")
                new_dt = dt + timedelta(days=1) if "Har kuni" in recurring else dt + timedelta(days=7)
                new_dl = new_dt.strftime("%d.%m.%Y %H:%M")
                c.execute("UPDATE tasks SET deadline=?, notified=0 WHERE id=?", (new_dl, task_id))
                conn.commit()
                await callback.message.edit_text(f"✅ Bajarildi! Vazifa takrorlanuvchi bo'lgani uchun keyingi muddatga o'tkazildi: <b>{new_dl}</b>", parse_mode="HTML")
            except: pass
        else:
            c.execute("UPDATE tasks SET status=1 WHERE id=?", (task_id,))
            conn.commit()
            await callback.message.edit_text(f"✅ <b>Bajarildi! (ID: {task_id})</b>\n\n<s>Vazifa yakunlandi.</s>", parse_mode="HTML")
    conn.close()

@dp.callback_query(F.data.startswith("snooze_"))
async def snooze_callback(callback: types.CallbackQuery):
    _, mins, task_id = callback.data.split("_")
    conn = sqlite3.connect('todo.db')
    c = conn.cursor()
    c.execute("SELECT deadline FROM tasks WHERE id=?", (task_id,))
    task = c.fetchone()
    if task and task[0]:
        try:
            dt = datetime.now() + timedelta(minutes=int(mins))
            new_dl = dt.strftime("%d.%m.%Y %H:%M")
            c.execute("UPDATE tasks SET deadline=?, notified=0 WHERE id=?", (new_dl, task_id))
            conn.commit()
            await callback.message.edit_text(f"💤 Vazifa <b>{mins} daqiqaga</b> kechiktirildi. Vaqti kelsa yana eslataman!", parse_mode="HTML")
        except: pass
    conn.close()

@dp.callback_query(F.data.startswith("del_"))
async def delete_task_callback(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    conn = sqlite3.connect('todo.db')
    conn.execute("UPDATE tasks SET status=-1 WHERE id=?", (task_id,))
    conn.commit()
    conn.close()
    await callback.message.edit_text("🗑 <b>Vazifa o'chirildi.</b>", parse_mode="HTML")

# ---------- STATISTIKA ----------
@dp.message(Command("stats"))
@dp.message(F.text == "Statistika")
async def stats_handler(message: types.Message, state: FSMContext):
    await state.clear() # <- Qotib qolgan jarayonlarni tozalaydi
    try:
        conn = sqlite3.connect('todo.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status != -1", (message.from_user.id,))
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status=1", (message.from_user.id,))
        done = c.fetchone()[0]
        conn.close()
        
        pending = total - done
        text = (f"📊 <b>Umumiy Statistikangiz:</b>\n\n"
                f"📝 Jami vazifalar: {total}\n"
                f"✅ Bajarilganlari: {done}\n"
                f"⏳ Kutilayotgan vazifalar: {pending}")
        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Statistika xatosi: {e}")
        await message.answer("⚠ Statistikani hisoblashda xatolik yuz berdi.", reply_markup=main_kb)

# ---------- EXPORT (CSV YUKLASH) ----------
@dp.message(Command("export"))
async def export_handler(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    conn = sqlite3.connect('todo.db')
    c = conn.cursor()
    c.execute("SELECT id, name, category, priority, recurring, deadline, status FROM tasks WHERE user_id=?", (user_id,))
    tasks = c.fetchall()
    conn.close()

    if not tasks:
        await message.answer("Sizda yuklab olish uchun vazifalar yo'q.")
        return

    filename = f"todo_tarix_{user_id}.csv"
    with open(filename, mode='w', newline='', encoding='utf-8-sig') as file:
        writer = csv.writer(file)
        writer.writerow(["ID", "Vazifa nomi", "Kategoriya", "Muhimligi", "Takrorlanish", "Muddat", "Holati (0=Faol, 1=Bajarilgan)"])
        writer.writerows(tasks)

    doc = FSInputFile(filename)
    await message.answer_document(doc, caption="📊 Barcha vazifalaringiz tarixi (Excel/CSV formatida)")
    os.remove(filename)

# ---------- ORQA FONDAGI ESLATMA VA DIGEST ----------
async def task_scheduler():
    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            conn = sqlite3.connect('todo.db')
            c = conn.cursor()

            # 1. 08:00 DA ERTALABKI XULOSA (MORNING DIGEST)
            if now.hour == 8:
                c.execute("SELECT user_id FROM users WHERE digest_date != ? OR digest_date IS NULL", (today_str,))
                users_to_digest = c.fetchall()
                
                for u in users_to_digest:
                    uid = u[0]
                    c.execute("SELECT name FROM tasks WHERE user_id=? AND status=0 AND deadline LIKE ?", (uid, f"{now.strftime('%d.%m.%Y')}%"))
                    todays_tasks = c.fetchall()
                    
                    if todays_tasks:
                        txt = f"🌅 <b>Xayrli tong! Bugun bajarilishi kerak bo'lgan vazifalar ({len(todays_tasks)} ta):</b>\n\n"
                        for i, t in enumerate(todays_tasks, 1):
                            txt += f"{i}. {t[0]}\n"
                        txt += "\n<i>Kuningiz barakali o'tsin!</i> ☕"
                        try:
                            await bot.send_message(uid, txt, parse_mode="HTML")
                        except: pass
                    
                    c.execute("UPDATE users SET digest_date=? WHERE user_id=?", (today_str, uid))
                    conn.commit()

            # 2. VAZIFALAR VAQTI KELGANDA ESLATISH
            c.execute("SELECT id, user_id, name, deadline FROM tasks WHERE status=0 AND notified=0 AND deadline IS NOT NULL")
            tasks = c.fetchall()
            
            for task in tasks:
                t_id, uid, name, deadline_str = task
                try:
                    dt = datetime.strptime(deadline_str, "%d.%m.%Y %H:%M")
                    if now >= dt:
                        text = f"⏰ <b>ESLATMA! Vaqti keldi!</b>\n\n📝 <b>Vazifa:</b> {name}"
                        await bot.send_message(uid, text, parse_mode="HTML", reply_markup=reminder_kb(t_id))
                        c.execute("UPDATE tasks SET notified=1 WHERE id=?", (t_id,))
                        conn.commit()
                except ValueError: pass
            
            conn.close()
        except Exception as e:
            logging.error(f"Scheduler xatosi: {e}")
        await asyncio.sleep(60)

# ==================== MAIN ====================
async def main():
    init_db()
    await set_bot_commands(bot)
    asyncio.create_task(task_scheduler())
    try:
        print("🚀 SUPER PREMUM Todo Bot ishga tushdi...")
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Bot kutilmaganda to'xtadi: {e}")

if __name__ == "__main__":
    asyncio.run(main())