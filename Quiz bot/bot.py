import os
import re
import random
import sqlite3
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

import pdfplumber
import docx
import openpyxl

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand

# ==================== SOZLAMALAR ====================
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
# Admin ID ni .env faylidan o'qiydi. Agar u yerda yozilmagan bo'lsa, 0 deb oladi.
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# ==================== BAZA SOZLAMALARI ====================
def init_db():
    try:
        conn = sqlite3.connect('quiz.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS results (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        score INTEGER,
                        total INTEGER,
                        percent REAL,
                        grade TEXT,
                        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''')
        c.execute('''CREATE TABLE IF NOT EXISTS questions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        question TEXT,
                        opt_a TEXT,
                        opt_b TEXT,
                        opt_c TEXT,
                        opt_d TEXT,
                        answer TEXT
                    )''')
        conn.commit()
    except Exception as e:
        logging.error(f"Baza xatosi: {e}")
    finally:
        conn.close()

def load_questions():
    conn = sqlite3.connect('quiz.db')
    c = conn.cursor()
    c.execute("SELECT question, opt_a, opt_b, opt_c, opt_d, answer FROM questions")
    rows = c.fetchall()
    conn.close()
    
    questions = []
    for row in rows:
        questions.append({
            "question": row[0],
            "options": [row[1], row[2], row[3], row[4]],
            "answer": row[5]
        })
    return questions

def save_questions(new_questions):
    conn = sqlite3.connect('quiz.db')
    c = conn.cursor()
    for q in new_questions:
        opts = q["options"]
        c.execute('''INSERT INTO questions (question, opt_a, opt_b, opt_c, opt_d, answer) 
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  (q["question"], opts[0], opts[1], opts[2], opts[3], q["answer"]))
    conn.commit()
    conn.close()

# ==================== BOT MENYUSINI O'RNATISH ====================
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Botni ishga tushirish"),
        BotCommand(command="help", description="Qo'llanma va fayl shablonlari"),
        BotCommand(command="quiz", description="Yangi testni boshlash"),
        BotCommand(command="add", description="Ruchnoy savol qo'shish"),
        BotCommand(command="results", description="Oxirgi natijalarim"),
        BotCommand(command="admin", description="Admin panel"),
        BotCommand(command="cancel", description="Boshlangan amalni bekor qilish")
    ]
    await bot.set_my_commands(commands)

# ==================== KEYBOARDLAR (ASOSIY) ====================
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎯 Testni boshlash")],
        [KeyboardButton(text="➕ Savol qo'shish"), KeyboardButton(text="📁 Fayldan yuklash")],
        [KeyboardButton(text="📊 Natijalarim")]
    ],
    resize_keyboard=True
)

stop_quiz_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🛑 Testni yakunlash")]], resize_keyboard=True)
back_cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Ortga"), KeyboardButton(text="❌ Bekor qilish")]], resize_keyboard=True)

def generate_quiz_kb(options):
    buttons = [[InlineKeyboardButton(text=opt, callback_data=f"ans_{idx}")] for idx, opt in enumerate(options)]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== ADMIN PANEL KEYBOARDLARI ====================
admin_main_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🗑 Barcha savollarni tozalash", callback_data="admin_clear_all")],
    [InlineKeyboardButton(text="✂️ Bittalab tanlab o'chirish", callback_data="admin_page_1")]
])

admin_confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="✅ Ha, hammasini o'chirish", callback_data="confirm_clear_all")],
    [InlineKeyboardButton(text="❌ Yo'q, qaytish", callback_data="admin_panel")]
])

def get_admin_questions_kb(page=1, limit=5):
    conn = sqlite3.connect('quiz.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM questions")
    total = c.fetchone()[0]
    
    if total == 0:
        conn.close()
        return "📭 Bazada hech qanday savol yo'q.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Admin Panelga qaytish", callback_data="admin_panel")]])
        
    total_pages = (total + limit - 1) // limit
    if page > total_pages: page = total_pages
    if page < 1: page = 1
    
    offset = (page - 1) * limit
    c.execute("SELECT id, question FROM questions LIMIT ? OFFSET ?", (limit, offset))
    questions = c.fetchall()
    conn.close()
    
    text = f"📂 <b>Bzadagi savollar ro'yxati (Sahifa: {page}/{total_pages}):</b>\n\n"
    buttons = []
    row = []
    
    for q in questions:
        q_id, q_text = q
        short_q = q_text[:35] + "..." if len(q_text) > 35 else q_text
        text += f"🆔 <b>{q_id}</b>: {short_q}\n"
        
        row.append(InlineKeyboardButton(text=f"🗑 {q_id}", callback_data=f"admin_del_{q_id}_{page}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
            
    if row:
        buttons.append(row)
        
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"admin_page_{page-1}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="Oldinga ➡️", callback_data=f"admin_page_{page+1}"))
        
    if nav_row:
        buttons.append(nav_row)
        
    buttons.append([InlineKeyboardButton(text="🔙 Admin Panelga qaytish", callback_data="admin_panel")])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== FSM HOLATLARI ====================
class QuizState(StatesGroup):
    testing = State()

class AddQState(StatesGroup):
    q_text = State()
    opt_a = State()
    opt_b = State()
    opt_c = State()
    opt_d = State()
    correct_ans = State()

class UploadState(StatesGroup):
    waiting_file = State()

# ==================== AQLLI FAYL O'QISH MANTIG'I ====================
def parse_text_block(text):
    questions = []
    blocks = re.split(r'\n\s*(?:Savol:|\d+[\.\)])\s*', "\n" + text, flags=re.IGNORECASE)
    for block in blocks:
        if not block.strip(): continue
        try:
            pattern = r'\n?\s*([\*\+]?)([A-D])[\)\.](.*?)(?=\n\s*[\*\+]?[A-D][\)\.]|\n\s*(?:Javob|To\'g\'ri)|$)'
            opts_match = re.findall(pattern, block, flags=re.IGNORECASE | re.DOTALL)
            
            if len(opts_match) >= 4:
                q_text = re.split(r'\n?\s*[\*\+]?A[\)\.]', block, flags=re.IGNORECASE)[0].strip()
                options = []
                correct_ans = None
                
                for marker, letter, opt_text in opts_match[:4]: 
                    clean_opt = opt_text.strip()
                    options.append(clean_opt)
                    if marker in ['*', '+']: 
                        correct_ans = clean_opt
                        
                if not correct_ans:
                    ans_match = re.search(r'(?:Javob|To\'g\'ri|Javobi)[\s:]*([A-D])', block, flags=re.IGNORECASE)
                    if ans_match:
                        ans_letter = ans_match.group(1).upper()
                        idx = {'A':0, 'B':1, 'C':2, 'D':3}[ans_letter]
                        correct_ans = options[idx]
                        
                if q_text and len(options) == 4 and correct_ans:
                    questions.append({
                        "question": q_text,
                        "options": options,
                        "answer": correct_ans
                    })
        except Exception:
            pass
    return questions

def parse_excel(filepath):
    questions = []
    wb = openpyxl.load_workbook(filepath)
    ws = wb.active
    for row in ws.iter_rows(min_row=2, values_only=True):
        if len(row) >= 6 and all(row[:6]):
            try:
                opts = [str(row[1]).strip(), str(row[2]).strip(), str(row[3]).strip(), str(row[4]).strip()]
                ans_val = str(row[5]).strip().upper()
                correct = opts[{'A':0, 'B':1, 'C':2, 'D':3}[ans_val]] if ans_val in ['A','B','C','D'] else str(row[5]).strip()
                questions.append({
                    "question": str(row[0]).strip(),
                    "options": opts,
                    "answer": correct
                })
            except Exception:
                pass
    return questions

# ==================== TEST MANTIG'I ====================
async def finish_quiz(message_or_call, state: FSMContext, forced=False):
    data = await state.get_data()
    correct_answers = data.get('correct_answers', 0)
    current_index = data.get('current_index', 0)
    
    if forced and current_index == 0:
        text = "⚠ Siz hali hech qanday savolga javob bermadingiz. Test bekor qilindi."
        if isinstance(message_or_call, types.CallbackQuery):
            await message_or_call.message.answer(text, reply_markup=main_kb)
        else:
            await message_or_call.answer(text, reply_markup=main_kb)
        await state.clear()
        return

    total = current_index
    percent = round((correct_answers / total) * 100, 2)
    
    if percent >= 90: grade = "A (A'lo)"
    elif percent >= 70: grade = "B (Yaxshi)"
    elif percent >= 50: grade = "C (Qoniqarli)"
    else: grade = "D (Qoniqarsiz)"

    try:
        user_id = message_or_call.from_user.id
        conn = sqlite3.connect('quiz.db')
        conn.execute("INSERT INTO results (user_id, score, total, percent, grade) VALUES (?, ?, ?, ?, ?)",
                  (user_id, correct_answers, total, percent, grade))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Natijani saqlashda xato: {e}")

    status_text = "to'xtatildi" if forced else "yakunlandi"
    text = (f"🏁 <b>Test {status_text}!</b>\n\n"
            f"✅ To'g'ri javoblar: {correct_answers}/{total}\n"
            f"📊 Foiz: {percent}%\n"
            f"🎓 Baho: {grade}")
    
    if isinstance(message_or_call, types.CallbackQuery):
        await message_or_call.message.answer(text, parse_mode="HTML", reply_markup=main_kb)
    else:
        await message_or_call.answer(text, parse_mode="HTML", reply_markup=main_kb)
    
    await state.clear()

async def send_next_question(message_or_call, state: FSMContext):
    data = await state.get_data()
    questions = data.get('questions', [])
    current_index = data.get('current_index', 0)

    if current_index >= len(questions):
        await finish_quiz(message_or_call, state, forced=False)
        return

    current_q = questions[current_index]
    options = current_q['options'].copy()
    random.shuffle(options)
    await state.update_data(current_options=options)

    text = f"❓ <b>{current_index + 1}-savol:</b>\n{current_q['question']}"
    kb = generate_quiz_kb(options)

    if isinstance(message_or_call, types.CallbackQuery):
        await message_or_call.message.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await message_or_call.answer(text, parse_mode="HTML", reply_markup=kb)

# ==================== ADMIN PANEL HANDLERLARI ====================
@dp.message(Command("admin"))
async def admin_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    
    conn = sqlite3.connect('quiz.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM questions")
    total = c.fetchone()[0]
    conn.close()
    
    await message.answer(f"🛠 <b>Admin Panelga xush kelibsiz!</b>\n\nBazada jami <b>{total} ta</b> savol mavjud. Nima ish bajaramiz?", 
                         parse_mode="HTML", reply_markup=admin_main_kb)

@dp.callback_query(F.data == "admin_panel")
async def back_to_admin_panel(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect('quiz.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM questions")
    total = c.fetchone()[0]
    conn.close()
    await callback.message.edit_text(f"🛠 <b>Admin Panelga xush kelibsiz!</b>\n\nBazada jami <b>{total} ta</b> savol mavjud. Nima ish bajaramiz?", 
                                     parse_mode="HTML", reply_markup=admin_main_kb)

@dp.callback_query(F.data == "admin_clear_all")
async def admin_clear_all_confirm(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.edit_text("⚠ <b>DIQQAT!</b>\n\nBarcha savollarni bazadan o'chirib tashlaysizmi? Bu amalni ortga qaytarib bo'lmaydi!", 
                                     parse_mode="HTML", reply_markup=admin_confirm_kb)

@dp.callback_query(F.data == "confirm_clear_all")
async def execute_clear_all(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect('quiz.db')
    c = conn.cursor()
    c.execute("DELETE FROM questions")
    c.execute("DELETE FROM sqlite_sequence WHERE name='questions'")
    conn.commit()
    conn.close()
    await callback.message.edit_text("✅ <b>Barcha savollar bazadan o'chirildi!</b> Bazangiz bo'm-bo'sh.", 
                                     parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Qaytish", callback_data="admin_panel")]]))

@dp.callback_query(F.data.startswith("admin_page_"))
async def admin_pagination(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    page = int(callback.data.split("_")[2])
    text, kb = get_admin_questions_kb(page=page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data.startswith("admin_del_"))
async def admin_delete_single(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    parts = callback.data.split("_")
    q_id = int(parts[2])
    current_page = int(parts[3])
    
    conn = sqlite3.connect('quiz.db')
    c = conn.cursor()
    c.execute("DELETE FROM questions WHERE id=?", (q_id,))
    conn.commit()
    conn.close()
    
    await callback.answer(f"ID: {q_id} savol o'chirildi!", show_alert=False)
    text, kb = get_admin_questions_kb(page=current_page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

# ==================== QOLGAN HANDLERLAR ====================
@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Assalomu alaykum! Imtihon va Test botiga xush kelibsiz.\n\n"
                         "Qo'llanma bilan tanishish uchun /help buyrug'ini bosing.", reply_markup=main_kb)

@dp.message(Command("help"))
async def help_handler(message: types.Message):
    text = (
        "📖 <b>Quiz Bot Qo'llanmasi:</b>\n\n"
        "<b>Asosiy buyruqlar:</b>\n"
        "🔹 /quiz — Testni boshlash\n"
        "🔹 /add — Bitta savol qo'shish (ruchnoy)\n"
        "🔹 /results — Oxirgi natijalarni ko'rish\n"
        "🔹 /cancel — Har qanday jarayonni bekor qilish\n\n"
        "📁 <b>Fayl yuklash qoidalari (PDF/Word):</b>\n"
        "Savollaringiz quyidagi 2 ta usuldan birida bo'lishi kerak:\n\n"
        "<i>1-usul (Yulduzcha orqali - tavsiya etiladi):</i>\n"
        "1. O'zbekiston poytaxti qayer?\n"
        "A. Samarqand\n"
        "*B. Toshkent\n"
        "C. Buxoro\n"
        "D. Navoiy\n\n"
        "<i>2-usul (Javob orqali):</i>\n"
        "Savol: O'zbekiston poytaxti qayer?\n"
        "A) Samarqand\n"
        "B) Toshkent\n"
        "C) Buxoro\n"
        "D) Navoiy\n"
        "Javob: B"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("cancel"))
@dp.message(F.text == "❌ Bekor qilish")
async def cancel_handler(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Hozircha bekor qilinadigan jarayon yo'q.", reply_markup=main_kb)
        return
    await state.clear()
    await message.answer("❌ Jarayon to'xtatildi. Bosh menyudasiz.", reply_markup=main_kb)

@dp.message(Command("quiz"))
@dp.message(F.text == "🎯 Testni boshlash")
async def start_quiz_handler(message: types.Message, state: FSMContext):
    await state.clear()
    all_questions = load_questions()
    if not all_questions:
        await message.answer("⚠ Savollar bazasi bo'sh! Avval savol qo'shing yoki fayl yuklang.")
        return

    random.shuffle(all_questions)
    await state.update_data(questions=all_questions, current_index=0, correct_answers=0)
    await state.set_state(QuizState.testing)
    
    await message.answer(f"🚀 Test boshlandi! Jami savollar: {len(all_questions)} ta.\n"
                         f"Tugallash uchun pastdagi tugmani bosing.", reply_markup=stop_quiz_kb)
    await send_next_question(message, state)

@dp.message(QuizState.testing, F.text == "🛑 Testni yakunlash")
async def stop_quiz_handler(message: types.Message, state: FSMContext):
    await finish_quiz(message, state, forced=True)

@dp.callback_query(QuizState.testing, F.data.startswith("ans_"))
async def process_answer(callback: types.CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        current_index, current_options, correct_answers = data['current_index'], data['current_options'], data['correct_answers']
        current_q = data['questions'][current_index]
        
        chosen_text = current_options[int(callback.data.split("_")[1])]
        is_correct = (chosen_text == current_q['answer'])

        result_text = f"❓ <b>{current_index + 1}-savol:</b>\n{current_q['question']}\n\n"
        if is_correct:
            result_text += f"✅ <b>To'g'ri javob berdingiz!</b>"
            await state.update_data(correct_answers=correct_answers + 1)
        else:
            result_text += f"❌ <b>Noto'g'ri.</b> To'g'ri javob: <b>{current_q['answer']}</b>"

        await callback.message.edit_text(result_text, parse_mode="HTML")
        await state.update_data(current_index=current_index + 1)
        await send_next_question(callback, state)
    except Exception:
        pass
    finally:
        await callback.answer()

# ---------- RUCHNOY SAVOL QO'SHISH ----------
@dp.message(Command("add"))
@dp.message(F.text == "➕ Savol qo'shish")
async def add_q_start(message: types.Message, state: FSMContext):
    await message.answer("📝 Yangi savol matnini kiriting:", reply_markup=back_cancel_kb)
    await state.set_state(AddQState.q_text)

@dp.message(AddQState.q_text)
async def add_q_text(message: types.Message, state: FSMContext):
    if message.text == "🔙 Ortga":
        await message.answer("Bu eng birinchi qadam. Bekor qilish uchun ❌ Bekor qilish ni bosing.")
        return
    await state.update_data(q_text=message.text)
    await message.answer("A) variantni kiriting:")
    await state.set_state(AddQState.opt_a)

@dp.message(AddQState.opt_a)
async def add_q_a(message: types.Message, state: FSMContext):
    if message.text == "🔙 Ortga":
        await message.answer("📝 Savol matnini qayta kiriting:")
        await state.set_state(AddQState.q_text)
        return
    await state.update_data(opt_a=message.text)
    await message.answer("B) variantni kiriting:")
    await state.set_state(AddQState.opt_b)

@dp.message(AddQState.opt_b)
async def add_q_b(message: types.Message, state: FSMContext):
    if message.text == "🔙 Ortga":
        await message.answer("A) variantni qayta kiriting:")
        await state.set_state(AddQState.opt_a)
        return
    await state.update_data(opt_b=message.text)
    await message.answer("C) variantni kiriting:")
    await state.set_state(AddQState.opt_c)

@dp.message(AddQState.opt_c)
async def add_q_c(message: types.Message, state: FSMContext):
    if message.text == "🔙 Ortga":
        await message.answer("B) variantni qayta kiriting:")
        await state.set_state(AddQState.opt_b)
        return
    await state.update_data(opt_c=message.text)
    await message.answer("D) variantni kiriting:")
    await state.set_state(AddQState.opt_d)

@dp.message(AddQState.opt_d)
async def add_q_d(message: types.Message, state: FSMContext):
    if message.text == "🔙 Ortga":
        await message.answer("C) variantni qayta kiriting:")
        await state.set_state(AddQState.opt_c)
        return
    await state.update_data(opt_d=message.text)
    
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="A"), KeyboardButton(text="B")], 
                                       [KeyboardButton(text="C"), KeyboardButton(text="D")],
                                       [KeyboardButton(text="🔙 Ortga"), KeyboardButton(text="❌ Bekor qilish")]], resize_keyboard=True)
    await message.answer("To'g'ri javobni tanlang (A, B, C yoki D):", reply_markup=kb)
    await state.set_state(AddQState.correct_ans)

@dp.message(AddQState.correct_ans)
async def add_q_correct(message: types.Message, state: FSMContext):
    if message.text == "🔙 Ortga":
        await message.answer("D) variantni qayta kiriting:", reply_markup=back_cancel_kb)
        await state.set_state(AddQState.opt_d)
        return
    
    ans = message.text.upper()
    if ans not in ['A', 'B', 'C', 'D']:
        await message.answer("Faqat A, B, C yoki D harflaridan birini yuboring!")
        return

    data = await state.get_data()
    opts_dict = {'A': data['opt_a'], 'B': data['opt_b'], 'C': data['opt_c'], 'D': data['opt_d']}
    
    new_q = [{
        "question": data['q_text'],
        "options": [data['opt_a'], data['opt_b'], data['opt_c'], data['opt_d']],
        "answer": opts_dict[ans]
    }]
    
    save_questions(new_q)
    await message.answer("✅ Savol muvaffaqiyatli bazaga qo'shildi!", reply_markup=main_kb)
    await state.clear()

# ---------- FAYL ORQALI YUKLASH ----------
@dp.message(F.text == "📁 Fayldan yuklash")
async def upload_start(message: types.Message, state: FSMContext):
    text = ("📥 <b>Faylni botga yuboring (.pdf, .docx, .xlsx)</b>\n\n"
            "(Bekor qilish uchun ❌ Bekor qilish yoki /cancel bosing)")
    await message.answer(text, parse_mode="HTML", reply_markup=back_cancel_kb)
    await state.set_state(UploadState.waiting_file)

@dp.message(UploadState.waiting_file, F.document)
async def process_file(message: types.Message, state: FSMContext):
    doc = message.document
    filename = doc.file_name.lower()
    
    if not (filename.endswith('.pdf') or filename.endswith('.docx') or filename.endswith('.xlsx')):
        await message.answer("⚠ Faqat PDF, DOCX yoki XLSX fayl qabul qilinadi!")
        return

    msg = await message.answer("⏳ Fayl o'qilmoqda, kuting...")
    filepath = f"temp_{doc.file_id}_{filename}"
    await bot.download(doc, destination=filepath)
    
    questions_added = []
    try:
        if filename.endswith('.xlsx'):
            questions_added = parse_excel(filepath)
        else:
            text_content = ""
            if filename.endswith('.pdf'):
                with pdfplumber.open(filepath) as pdf:
                    text_content = "\n".join([page.extract_text() for page in pdf.pages if page.extract_text()])
            elif filename.endswith('.docx'):
                d = docx.Document(filepath)
                text_content = "\n".join([p.text for p in d.paragraphs])
            
            questions_added = parse_text_block(text_content)

        if questions_added:
            save_questions(questions_added)
            
            await state.update_data(file_questions=questions_added)
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Fayldagi testni boshlash", callback_data="start_file_quiz")]])
            
            await msg.edit_text(f"✅ Fayldan muvaffaqiyatli <b>{len(questions_added)} ta</b> savol bazaga qo'shildi!\n\nAynan shu fayldagi savollardan test ishlash uchun quyidagi tugmani bosing:", parse_mode="HTML", reply_markup=kb)
        else:
            await msg.edit_text("⚠ Fayl ichidan shablonga mos savol topilmadi. Shablonni tekshirib qayta urinib ko'ring (/help).")
    except Exception as e:
        await msg.edit_text(f"❌ Faylni o'qishda xatolik yuz berdi: {e}")
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)
        
    await message.answer("Bosh menyudasiz:", reply_markup=main_kb)
    # MUHIM: Xotirada saqlangan fayl savollarini o'chirmasligi uchun None ishlatamiz
    await state.set_state(None)

@dp.callback_query(F.data == "start_file_quiz")
async def start_file_quiz_handler(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    file_questions = data.get('file_questions', [])
    
    if not file_questions:
        await callback.answer("⚠ Savollar topilmadi yoki xotiradan o'chirilgan!", show_alert=True)
        return

    random.shuffle(file_questions)
    await state.update_data(questions=file_questions, current_index=0, correct_answers=0)
    await state.set_state(QuizState.testing)
    
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"🚀 Fayldagi test boshlandi! Jami savollar: {len(file_questions)} ta.\n"
                         f"Tugallash uchun pastdagi tugmani bosing.", reply_markup=stop_quiz_kb)
    await send_next_question(callback.message, state)
    await callback.answer()

# ---------- NATIJALAR ----------
@dp.message(Command("results"))
@dp.message(F.text == "📊 Natijalarim")
async def my_results_handler(message: types.Message, state: FSMContext):
    await state.clear()
    conn = sqlite3.connect('quiz.db')
    c = conn.cursor()
    c.execute("SELECT score, total, percent, grade, date FROM results WHERE user_id=? ORDER BY id DESC LIMIT 5", (message.from_user.id,))
    results = c.fetchall()
    conn.close()

    if not results:
        await message.answer("Sizda hozircha natijalar yo'q. Testni boshlang!")
        return

    text = "📊 <b>Oxirgi 5 ta urinishingiz:</b>\n\n"
    for idx, res in enumerate(results, 1):
        score, total, percent, grade, dt_str = res
        dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        text += f"<b>{idx}.</b> 🗓 {dt_obj.strftime('%d.%m.%Y %H:%M')}\n   Natija: {score}/{total} ({percent}%)\n   Baho: {grade}\n\n"

    await message.answer(text, parse_mode="HTML")

# ==================== MAIN ====================
async def main():
    init_db()
    await set_bot_commands(bot)
    try:
        print("🚀 Super Quiz Bot ishga tushdi...")
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Bot kutilmaganda to'xtadi: {e}")

if __name__ == "__main__":
    asyncio.run(main())