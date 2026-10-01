import sqlite3

class Book:
    def __init__(self, title, author, year):
        self.title = title
        self.author = author
        self.year = year

class Member:
    def __init__(self, name, phone):
        self.name = name
        self.phone = phone

class Library:
    def __init__(self, db_name="library.db"):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            author TEXT,
            year INTEGER,
            status TEXT DEFAULT 'bor'
        )
        """)
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT
        )
        """)
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS issued_books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER,
            member_id INTEGER,
            FOREIGN KEY(book_id) REFERENCES books(id),
            FOREIGN KEY(member_id) REFERENCES members(id)
        )
        """)
        self.conn.commit()

    def add_book(self, book: Book):
        self.cursor.execute("INSERT INTO books (title, author, year) VALUES (?, ?, ?)", 
                            (book.title, book.author, book.year))
        self.conn.commit()
        print("\n✅ Kitob bazaga qo'shildi!")

    def get_all_books(self):
        self.cursor.execute("SELECT id, title, author, status FROM books")
        books = self.cursor.fetchall()
        if not books:
            print("\nKutubxonada hozircha kitoblar yo'q.")
        else:
            print("\n--- Barcha kitoblar ---")
            for b in books:
                print(f"ID: {b[0]} | Nomi: {b[1]} | Muallif: {b[2]} | Holati: {b[3]}")

    def search_book(self, query):
        self.cursor.execute("SELECT id, title, author, status FROM books WHERE title LIKE ? OR author LIKE ?", 
                            (f"%{query}%", f"%{query}%"))
        results = self.cursor.fetchall()
        if not results:
            print("\n❌ Hech narsa topilmadi.")
        else:
            print(f"\n--- Qidiruv natijalari ('{query}') ---")
            for b in results:
                print(f"ID: {b[0]} | Nomi: {b[1]} | Muallif: {b[2]} | Holati: {b[3]}")

    def add_member(self, member: Member):
        self.cursor.execute("INSERT INTO members (name, phone) VALUES (?, ?)", 
                            (member.name, member.phone))
        self.conn.commit()
        print("\n✅ Yangi a'zo ro'yxatga olindi!")

    def issue_book(self, member_id, book_id):
        # 1. Kitobni tekshirish
        self.cursor.execute("SELECT status FROM books WHERE id=?", (book_id,))
        book_res = self.cursor.fetchone()
        if not book_res:
            print("\n❌ Bunday ID ga ega kitob topilmadi.")
            return
        if book_res[0] == 'berilgan':
            print("\n❌ Bu kitob allaqachon boshqa kishiga berilgan!")
            return
        
        # 2. A'zoni tekshirish
        self.cursor.execute("SELECT id FROM members WHERE id=?", (member_id,))
        if not self.cursor.fetchone():
            print("\n❌ Bunday ID ga ega a'zo topilmadi.")
            return

        # 3. Kitobni berish
        self.cursor.execute("UPDATE books SET status='berilgan' WHERE id=?", (book_id,))
        self.cursor.execute("INSERT INTO issued_books (book_id, member_id) VALUES (?, ?)", (book_id, member_id))
        self.conn.commit()
        print("\n✅ Kitob a'zoga muvaffaqiyatli berildi!")

    def return_book(self, book_id):
        self.cursor.execute("SELECT status FROM books WHERE id=?", (book_id,))
        res = self.cursor.fetchone()
        if not res:
            print("\n❌ Bunday ID ga ega kitob topilmadi.")
            return
        if res[0] == 'bor':
            print("\n❌ Bu kitob hech kimga berilmagan (Kutubxonada turibdi).")
            return

        self.cursor.execute("UPDATE books SET status='bor' WHERE id=?", (book_id,))
        self.cursor.execute("DELETE FROM issued_books WHERE book_id=?", (book_id,))
        self.conn.commit()
        print("\n✅ Kitob kutubxonaga qaytarildi!")

    def get_issued_books(self):
        self.cursor.execute("""
        SELECT b.id, b.title, m.name 
        FROM issued_books ib
        JOIN books b ON ib.book_id = b.id
        JOIN members m ON ib.member_id = m.id
        """)
        issued = self.cursor.fetchall()
        if not issued:
            print("\nHozircha ijaraga berilgan kitoblar yo'q.")
        else:
            print("\n--- Kimda qaysi kitob bor ---")
            for b in issued:
                print(f"Kitob ID: {b[0]} | Nomi: {b[1]} ➡️ O'qiyotgan a'zo: {b[2]}")

def main():
    library = Library()

    while True:
        print("\n=== KUTUBXONA MENYUSI ===")
        print("1. Kitob qo'shish")
        print("2. Barcha kitoblar")
        print("3. Kitob qidirish")
        print("4. A'zo qo'shish")
        print("5. Kitob berish")
        print("6. Kitobni qaytarish")
        print("7. Kimda qaysi kitob bor")
        print("0. Chiqish")
        
        choice = input("Tanlovingizni kiriting: ")

        if choice == '1':
            title = input("Kitob nomi: ")
            author = input("Muallifi: ")
            year = input("Yili (faqat raqam): ")
            if year.isdigit():
                library.add_book(Book(title, author, int(year)))
            else:
                print("\n❌ Xato: Yil faqat raqamlardan iborat bo'lishi kerak!")

        elif choice == '2':
            library.get_all_books()

        elif choice == '3':
            query = input("Qidirilayotgan kitob nomi yoki muallifini kiriting: ")
            library.search_book(query)

        elif choice == '4':
            name = input("A'zo ismi: ")
            phone = input("Telefon raqami: ")
            library.add_member(Member(name, phone))

        elif choice == '5':
            member_id = input("A'zo ID raqamini kiriting: ")
            book_id = input("Kitob ID raqamini kiriting: ")
            
            if member_id.isdigit() and book_id.isdigit():
                library.issue_book(int(member_id), int(book_id))
            else:
                print("\n❌ Xato: ID faqat raqamlardan iborat bo'lishi kerak!")

        elif choice == '6':
            book_id = input("Qaytarilayotgan kitob ID raqamini kiriting: ")
            if book_id.isdigit():
                library.return_book(int(book_id))
            else:
                print("\n❌ Xato: ID faqat raqamlardan iborat bo'lishi kerak!")

        elif choice == '7':
            library.get_issued_books()

        elif choice == '0':
            print("\nDastur tugatildi. Xayr!")
            break

        else:
            print("\n❌ Noto'g'ri tanlov. Iltimos, menyudagi raqamlardan birini tanlang.")

if __name__ == "__main__":
    main()