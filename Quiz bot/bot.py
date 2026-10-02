import os
import re
import json
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
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# ==================== SOZLAMALAR ====================
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# ==================== BAZA VA JSON SOZLAMALARI ====================
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
        conn.commit()
    except Exception as e:
        logging.error(f"Baza xatosi: {e}")
    finally:
        conn.close()

def load_questions():
    if not os.path.exists('questions.json'):
        with open('questions.json', 'w', encoding='utf-8') as f:
            json.dump([], f)
    with open('questions.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def save_questions(new_questions):
    data = load_questions()
    data.extend(new_questions)
    with open('questions.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# ==================== KEYBOARDLAR ====================
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎯 Testni boshlash")],
        [KeyboardButton(text="➕ Savol qo'shish"), KeyboardButton(text="📁 Fayldan yuklash")],
        [KeyboardButton(text="📊 Natijalarim")]
    ],
    resize_keyboard=True
)

stop_quiz_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🛑 Testni yakunlash")]],
    resize_keyboard=True
)

back_cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🔙 Ortga"), KeyboardButton(text="❌ Bekor qilish")]],
    resize_keyboard=True
)

def generate_quiz_kb(options):
    buttons = [[InlineKeyboardButton(text=opt, callback_data=f"ans_{idx}")] for idx, opt in enumerate(options)]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

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

# ==================== TEST YAKUNLASH MANTIG'I ====================
async def finish_quiz(message_or_call, state: FSMContext, forced=False):
    data = await state.get_data()
    correct_answers = data.get('correct_answers', 0)
    current_index = data.get('current_index', 0)
    
    # Agar hech qanday savolga javob bermay to'xtatsa
    if forced and current_index == 0:
        text = "⚠ Siz hali hech qanday savolga javob bermadingiz. Test bekor qilindi."
        if isinstance(message_or_call, types.CallbackQuery):
            await message_or_call.message.answer(text, reply_markup=main_kb)
        else:
            await message_or_call.answer(text, reply_markup=main_kb)
        await state.clear()
        return

    # To'xtatilgan yoki to'liq tugagan paytdagi jami ishlangan savollar
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


# ==================== FAYLLARDAN O'QISH MANTIG'I ====================
def parse_text_block(text):
    questions = []
    blocks = re.split(r'(?i)Savol:', text)[1:]
    for block in blocks:
        try:
            q_text = block.split('A)')[0].strip()
            a_match = re.search(r'A\)(.*?)(?=B\))', block, re.DOTALL | re.IGNORECASE).group(1).strip()
            b_match = re.search(r'B\)(.*?)(?=C\))', block, re.DOTALL | re.IGNORECASE).group(1).strip()
            c_match = re.search(r'C\)(.*?)(?=D\))', block, re.DOTALL | re.IGNORECASE).group(1).strip()
            d_match = re.search(r'D\)(.*?)(?=Javob:)', block, re.DOTALL | re.IGNORECASE).group(1).strip()
            ans_match = re.search(r'(?i)Javob:\s*([A-D])', block).group(1).strip().upper()
            
            opts_dict = {'A': a_match, 'B': b_match, 'C': c_match, 'D': d_match}
            questions.append({
                "question": q_text,
                "options": [a_match, b_match, c_match, d_match],
                "answer": opts_dict[ans_match]
            })
        except: pass
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
            except: pass
    return questions

# ==================== HANDLERLAR ====================

@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Assalomu alaykum! Imtihon va Test botiga xush kelibsiz.", reply_markup=main_kb)

# ---------- 1. TEST ISHLASH ----------
@dp.message(F.text == "🎯 Testni boshlash")
async def start_quiz_handler(message: types.Message, state: FSMContext):
    all_questions = load_questions()
    if not all_questions:
        await message.answer("⚠ Savollar bazasi bo'sh! Avval savol qo'shing.")
        return

    random.shuffle(all_questions)
    # Barcha savollarni aralashtirib beradi (Tugaguncha)
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
    except: pass
    finally: await callback.answer()

# ---------- 2. RUCHNOY SAVOL QO'SHISH (Ortga qaytish bilan) ----------
@dp.message(F.text == "➕ Savol qo'shish")
async def add_q_start(message: types.Message, state: FSMContext):
    await message.answer("📝 Yangi savol matnini kiriting:", reply_markup=back_cancel_kb)
    await state.set_state(AddQState.q_text)

@dp.message(F.text == "❌ Bekor qilish")
async def cancel_add(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Jarayon bekor qilindi.", reply_markup=main_kb)

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
                                       [KeyboardButton(text="🔙 Ortga")]], resize_keyboard=True)
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
    
    new_q = {
        "question": data['q_text'],
        "options": [data['opt_a'], data['opt_b'], data['opt_c'], data['opt_d']],
        "answer": opts_dict[ans]
    }
    
    save_questions([new_q])
    await message.answer("✅ Savol muvaffaqiyatli bazaga qo'shildi!", reply_markup=main_kb)
    await state.clear()

# ---------- 3. FAYL ORQALI YUKLASH (EXCEL, WORD, PDF) ----------
@dp.message(F.text == "📁 Fayldan yuklash")
async def upload_start(message: types.Message, state: FSMContext):
    text = ("📥 <b>Faylni botga yuboring (.pdf, .docx, .xlsx)</b>\n\n"
            "📌 <i>Excel shabloni:</i>\n"
            "1-ustun: Savol, 2..5-ustunlar: A,B,C,D variantlar, 6-ustun: To'g'ri javob (A,B,C,D yoki matni)\n\n"
            "📌 <i>Word/PDF shabloni:</i>\n"
            "Savol: Matn\n"
            "A) Variant 1\nB) Variant 2\nC) Variant 3\nD) Variant 4\n"
            "Javob: A\n\n(Bekor qilish uchun ❌ Bekor qilish bosing)")
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
            await msg.edit_text(f"✅ Fayldan muvaffaqiyatli <b>{len(questions_added)} ta</b> savol bazaga qo'shildi!", parse_mode="HTML")
        else:
            await msg.edit_text("⚠ Fayl ichidan bironta ham shablonga mos savol topilmadi. Shablonni to'g'rilab qayta urinib ko'ring.")
    except Exception as e:
        await msg.edit_text(f"❌ Faylni o'qishda xatolik yuz berdi: {e}")
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)
        
    await message.answer("Bosh menyudasiz:", reply_markup=main_kb)
    await state.clear()

# ---------- NATIJALAR ----------
@dp.message(F.text == "📊 Natijalarim")
async def my_results_handler(message: types.Message):
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
    load_questions() # Fayl yo'q bo'lsa yaratib qo'yadi
    try:
        print("Quiz Bot fayl yuklash tizimi bilan ishga tushdi...")
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Bot kutilmaganda to'xtadi: {e}")

if __name__ == "__main__":
    asyncio.run(main())