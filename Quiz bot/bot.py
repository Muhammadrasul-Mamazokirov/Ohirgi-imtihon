import os
import json
import random
import sqlite3
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# Env va Bot sozlamalari
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

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
        conn.commit()
    except Exception as e:
        logging.error(f"Baza yaratishda xato: {e}")
    finally:
        conn.close()

# ==================== KEYBOARDLAR ====================
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎯 Testni boshlash")],
        [KeyboardButton(text="📊 Natijalarim")]
    ],
    resize_keyboard=True
)

def generate_quiz_kb(options):
    # Inline tugmalarga index (0, 1, 2, 3) ni biriktiramiz
    buttons = []
    for idx, opt in enumerate(options):
        buttons.append([InlineKeyboardButton(text=opt, callback_data=f"ans_{idx}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== FSM HOLATLARI ====================
class QuizState(StatesGroup):
    testing = State()

# ==================== TEST MANTIG'I ====================
async def send_next_question(message_or_call, state: FSMContext):
    data = await state.get_data()
    questions = data.get('questions', [])
    current_index = data.get('current_index', 0)

    if current_index >= len(questions):
        # TEST TUGADI
        correct_answers = data.get('correct_answers', 0)
        total = len(questions)
        percent = round((correct_answers / total) * 100, 2)
        
        if percent >= 90:
            grade = "A (A'lo)"
        elif percent >= 70:
            grade = "B (Yaxshi)"
        elif percent >= 50:
            grade = "C (Qoniqarli)"
        else:
            grade = "D (Qoniqarsiz)"

        # Natijani bazaga saqlash
        try:
            user_id = message_or_call.from_user.id
            conn = sqlite3.connect('quiz.db')
            c = conn.cursor()
            c.execute("INSERT INTO results (user_id, score, total, percent, grade) VALUES (?, ?, ?, ?, ?)",
                      (user_id, correct_answers, total, percent, grade))
            conn.commit()
            conn.close()
        except Exception as e:
            logging.error(f"Natijani saqlashda xato: {e}")

        text = (f"🏁 <b>Test yakunlandi!</b>\n\n"
                f"✅ To'g'ri javoblar: {correct_answers}/{total}\n"
                f"📊 Foiz: {percent}%\n"
                f"🎓 Baho: {grade}")
        
        if isinstance(message_or_call, types.CallbackQuery):
            await message_or_call.message.answer(text, parse_mode="HTML", reply_markup=main_kb)
        else:
            await message_or_call.answer(text, parse_mode="HTML", reply_markup=main_kb)
        
        await state.clear()
        return

    # KEYINGI SAVOLNI YUBORISH
    current_q = questions[current_index]
    
    # Variantlarni aralashtiramiz (tasodifiy)
    options = current_q['options'].copy()
    random.shuffle(options)
    
    # Aralashtirilgan variantlarni xotirada saqlaymiz (callbackda tekshirish uchun)
    await state.update_data(current_options=options)

    text = f"❓ <b>{current_index + 1}-savol:</b>\n{current_q['question']}"
    kb = generate_quiz_kb(options)

    if isinstance(message_or_call, types.CallbackQuery):
        await message_or_call.message.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await message_or_call.answer(text, parse_mode="HTML", reply_markup=kb)

# ==================== HANDLERLAR ====================

@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Assalomu alaykum! Quiz botga xush kelibsiz.\nTestni boshlash uchun quyidagi tugmani bosing:", reply_markup=main_kb)

@dp.message(F.text == "🎯 Testni boshlash")
async def start_quiz_handler(message: types.Message, state: FSMContext):
    try:
        # Fayl yo'lini avtomatik topish
        base_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(base_dir, 'questions.json')

        with open(file_path, 'r', encoding='utf-8') as f:
            all_questions = json.load(f)
        
        if not all_questions:
            await message.answer("Savollar bazasi bo'sh!")
            return

        random.shuffle(all_questions)
        selected_questions = all_questions[:10]

        await state.update_data(
            questions=selected_questions,
            current_index=0,
            correct_answers=0
        )
        await state.set_state(QuizState.testing)
        
        await message.answer("🚀 Test boshlandi! Omad yor bo'lsin.", reply_markup=types.ReplyKeyboardRemove())
        await send_next_question(message, state)

    except Exception as e:
        logging.error(f"Test boshlashda xato: {e}")
        await message.answer(f"Xatolik yuz berdi: {e}") # Xatolik sababini botda ko'rsatib turamiz

@dp.callback_query(QuizState.testing, F.data.startswith("ans_"))
async def process_answer(callback: types.CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        questions = data.get('questions')
        current_index = data.get('current_index')
        current_options = data.get('current_options')
        correct_answers = data.get('correct_answers')

        current_q = questions[current_index]
        true_answer = current_q['answer']
        
        # Foydalanuvchi tanlagan indeksni ajratib olish
        chosen_idx = int(callback.data.split("_")[1])
        chosen_text = current_options[chosen_idx]

        is_correct = (chosen_text == true_answer)

        # Xabarni yangilash (inline tugmalarni o'chirib, natijani ko'rsatamiz - double bosishni oldini oladi)
        result_text = f"❓ <b>{current_index + 1}-savol:</b>\n{current_q['question']}\n\n"
        if is_correct:
            result_text += f"✅ <b>To'g'ri javob berdingiz!</b>\nSizning javob: {chosen_text}"
            await state.update_data(correct_answers=correct_answers + 1)
        else:
            result_text += f"❌ <b>Noto'g'ri javob.</b>\nSizning javob: {chosen_text}\nTo'g'ri javob: <b>{true_answer}</b>"

        await callback.message.edit_text(result_text, parse_mode="HTML", reply_markup=None)

        # Keyingi savolga o'tish
        await state.update_data(current_index=current_index + 1)
        await send_next_question(callback, state)

    except Exception as e:
        logging.error(f"Javobni qayta ishlashda xato: {e}")
    finally:
        await callback.answer()

@dp.message(F.text == "📊 Natijalarim")
@dp.message(Command("natijalarim"))
async def my_results_handler(message: types.Message):
    try:
        conn = sqlite3.connect('quiz.db')
        c = conn.cursor()
        # Oxirgi 5 ta natijani olish
        c.execute('''SELECT score, total, percent, grade, date 
                     FROM results 
                     WHERE user_id=? 
                     ORDER BY id DESC LIMIT 5''', (message.from_user.id,))
        results = c.fetchall()
        conn.close()

        if not results:
            await message.answer("Sizda hozircha natijalar yo'q. Testni boshlang!")
            return

        text = "📊 <b>Oxirgi 5 ta urinishingiz:</b>\n\n"
        for idx, res in enumerate(results, 1):
            score, total, percent, grade, dt_str = res
            # Sanani chiroyli formatlash
            dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            formatted_date = dt_obj.strftime("%d.%m.%Y %H:%M")
            
            text += (f"<b>{idx}.</b> 🗓 {formatted_date}\n"
                     f"   Natija: {score}/{total} ({percent}%)\n"
                     f"   Baho: {grade}\n\n")

        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Natijalarni ko'rsatishda xato: {e}")
        await message.answer("Natijalarni yuklashda xatolik yuz berdi.")

# ==================== MAIN ====================
async def main():
    init_db()
    try:
        print("Quiz Bot ishga tushdi...")
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Bot kutilmaganda to'xtadi: {e}")

if __name__ == "__main__":
    asyncio.run(main())