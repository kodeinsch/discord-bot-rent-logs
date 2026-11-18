import os
import json
import asyncio
from datetime import datetime, timezone, timedelta
import discord
from discord.ext import commands
from aiohttp import web
from google import genai
from google.genai import types

# ================= KONFIGURASI =================
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TARGET_CHANNEL_ID = int(os.environ["TARGET_CHANNEL_ID"])
PORT = int(os.environ.get("PORT", 8000))

WIB = timezone(timedelta(hours=7))

# Isi daftar nama siswa per kelas di sini.
# Saat bot pertama kali jalan dan belum ada pesan pinned untuk kelas tersebut,
# bot akan otomatis membuat dan menge-pin pesan awalnya.
DATA_KELAS = {
    "Kelas 7": [
	"Ahmad maulana Herjuan", "Faeyza Eshan Nandana Prasetyo", "Harith Athari Winardi","Tupac Ahmad Shakur"
    ],
    "Kelas 10": [
	"Ahmad Nail Aqil", "Akhtar Haryo Brotoseno", "Aqil Abbasy Abyan", "Daaris Khairan Radenysh", "Dante Akbar Darmawan", "Dzaky Khalfani Dzul Bachtiar", "Fa'iz Akbar Fijie Putra Satria", "Faiq Fawwaz Rabbani", "Fawwaz Ahmad Alghazy Pranata", "Hafizh Nur Azizan", "Muhammad Huzaifi El Mahboeby", "Nazalul Rakan Ramadhan", "Rafi Jerio Nur Fatih", "Ryuza Adhan Nurrahim"
    ],
    "Kelas 11": [
	"Ahmad Fathin", "Al Ghozali Wildan Hermawan", "Dzul Amroin Nahdan", "Edric Salim", "Farros Al Warisi", "Hafizh", "Khenzo Ilkan Al Bathinu", "Muhamad Fatihul Ulum", "Muhammad Adzkarullah", "Muhammad Arief Al Amin", "Muhammad Azka Azizan", "Muhammad Fardan Bakhtiyar", "Muhammad Hanif Abdullah", "Muhammad Putra Rinjani", "Muhammad Rafie Dhiyaulhaq", "Muhammad Raizzain Fadhil Abdillah", "Muhammad Sae Alfian", "Nehan Abdillah Nugroho", "Qeis Alvaro Asy-Sya'bi", "R Muhammad Aubrey Athallah Widjaya", "Rakha Sakhi Arganta", "Rava Satria Medina", "Rayhan Kenzie Avando", "Yazid Farras Fadillah"
    ],
    "Kelas 12": [
	"Abdullah Afif", "Ahmad Shiddiqy Margolang", "Alif Attallah Basya", "Ar Raffi Luthfiansyah Hidayat", "Athallah Zahwan Andhika", "Azzam Dzikri Al-Ghifari", "Fadli Rizky Ramadhan", "Farhan Hakiki", "Fathir Akbar", "Imam Muttaqin Khoirul Anam", "Muhammad", "Muhammad Fadhlullah Rais", "Muhammad Hisyam Fatih Ma'ruf", "Muhammad Khalid Al Miqdad", "Muhammad Zahid Alwan", "Najwan Zen Muttaqin", "Nalom Maula Adam Tazima", "Nugroho Tirta Kamil", "Qaizer Dhiwaqsha AlfarPoetro", "Sora Yudhistira Santosa", "Tamim Al Ghozy"
    ]
}

ai_client = genai.Client(api_key=GEMINI_API_KEY)

# ================= HELPER PESAN PINNED =================
def format_class_message(class_name: str, students_dict: dict) -> str:
    now_str = datetime.now(WIB).strftime("%d/%m %H:%M")
    lines = [f"📌 **{class_name}**"]
    for student, items in students_dict.items():
        val = items.strip() if items and items.strip() else "-"
        lines.append(f"{student}: {val}")
    lines.append(f"\n*Update terakhir: {now_str} WIB*")
    return "\n".join(lines)

def parse_pinned_message(content: str):
    """Membaca teks pesan pinned menjadi (nama_kelas, {nama_siswa: status_pinjaman})"""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("📌 **"):
        return None, {}

    class_name = lines[0].replace("📌 **", "").replace("**", "").strip()
    students = {}
    for line in lines[1:]:
        if line.startswith("*Update terakhir:"):
            continue
        if ":" in line:
            name, status = line.split(":", 1)
            students[name.strip()] = status.strip()
    return class_name, students

async def get_or_init_class_boards(channel: discord.TextChannel):
    """Mengambil seluruh pesan pinned kelas dari Discord, atau membuatnya jika belum ada."""
    pinned_messages = await channel.pins()
    boards = {}  # {class_name: {"message": discord.Message, "students": dict}}

    # Urutkan dari pesan terlama ke terbaru agar urutan kelas tetap rapi
    for msg in reversed(pinned_messages):
        if msg.author.id == channel.guild.me.id:
            class_name, students = parse_pinned_message(msg.content)
            if class_name:
                boards[class_name] = {"message": msg, "students": students}

    # Buat pesan pinned baru jika ada kelas di DATA_KELAS yang belum ter-pin
    for class_name, student_list in DATA_KELAS.items():
        if class_name not in boards:
            initial_students = {name: "-" for name in student_list}
            text = format_class_message(class_name, initial_students)
            sent_msg = await channel.send(text)
            try:
                await sent_msg.pin()
            except discord.Forbidden:
                pass
            boards[class_name] = {"message": sent_msg, "students": initial_students}

    return boards

# ================= PEMROSESAN AI (GEMINI) =================
def process_with_gemini(user_message: str, author_name: str, current_boards_state: dict):
    prompt = f"""
Kamu adalah asisten pengelola papan peminjaman device (HP, laptop, dll) di sekolah.
Berikut adalah state papan peminjaman saat ini per kelas (format JSON):
{json.dumps(current_boards_state, indent=2, ensure_ascii=False)}

Pesan chat baru dari "{author_name}":
"{user_message}"

Aturan pemrosesan:
1. Tentukan apakah pesan ini bermaksud menambah peminjaman ("pinjam"), mengurangi/mengembalikan ("kembali"), atau mengoreksi data peminjaman device. Jika hanya obrolan biasa atau pertanyaan tanpa instruksi perubahan, kembalikan "has_changes": false.
2. Pencocokan nama siswa bersifat fleksibel (case-insensitive, bisa nama panggilan jika unik, atau multi-siswa sekaligus seperti "namaSiswa1, namaSiswa2").
3. Jika user menyebut "semua siswa kelas X balikin/kembalikan <device>", terapkan pengembalian <device> tersebut ke seluruh siswa di kelas X.
4. Format penulisan kolom nilai per siswa:
   - Jika tidak meminjam apa pun: "-"
   - Jika meminjam tanpa alasan: tulis nama device huruf kecil, misal "hp" atau "laptop"
   - Jika meminjam dengan alasan: tulis "<device> (<alasan singkat>)", misal "hp (validasi wa)"
   - Jika meminjam lebih dari 1 device: pisahkan dengan koma, misal "hp (validasi wa), laptop (les)"
   - Saat mengembalikan salah satu device (misal "kembaliin laptop" padahal sedang pinjam "hp (validasi wa), laptop (les)"), hapus HANYA device yang dikembalikan sehingga tersisa "hp (validasi wa)". Jika semua device sudah dikembalikan, ubah menjadi "-".
5. Kembalikan HANYA kelas-kelas yang mengalami perubahan di dalam objek "updated_classes". Jangan ubah urutan atau daftar nama siswa yang sudah ada di kelas tersebut.

Kembalikan HANYA JSON valid dengan struktur persis:
{{
  "has_changes": true/false,
  "updated_classes": {{
    "Kelas 7": {{
      "namaSiswa1": "-",
      "namaSiswa2": "laptop"
    }}
  }},
  "reply": "Konfirmasi singkat dan jelas dalam bahasa Indonesia"
}}
"""
    response = ai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1
        )
    )
    return json.loads(response.text)

# ================= DISCORD BOT SETUP =================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Lock agar jika ada 2 siswa mengirim pesan bersamaan, edit pesan tidak saling menimpa
board_lock = asyncio.Lock()

@bot.event
async def on_ready():
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        async with board_lock:
            await get_or_init_class_boards(channel)
    print(f"Bot peminjaman device aktif sebagai {bot.user}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.channel.id != TARGET_CHANNEL_ID:
        return

    # Perintah manual opsional untuk refresh/sinkronisasi daftar nama siswa dari DATA_KELAS
    if message.content.strip().lower() == "!sync-kelas":
        async with board_lock:
            boards = await get_or_init_class_boards(message.channel)
            for class_name, student_list in DATA_KELAS.items():
                if class_name in boards:
                    existing = boards[class_name]["students"]
                    merged = {name: existing.get(name, "-") for name in student_list}
                    await boards[class_name]["message"].edit(
                        content=format_class_message(class_name, merged)
                    )
        await message.reply("✅ Daftar nama siswa per kelas berhasil disinkronkan.", mention_author=False)
        return

    async with board_lock:
        boards = await get_or_init_class_boards(message.channel)
        current_state = {cls: data["students"] for cls, data in boards.items()}

        try:
            result = await asyncio.to_thread(
                process_with_gemini,
                message.content,
                message.author.display_name,
                current_state
            )
        except Exception as e:
            print(f"Error Gemini: {e}")
            return

        if not result.get("has_changes"):
            return

        updated_classes = result.get("updated_classes", {})
        any_edited = False

        for class_name, new_students_map in updated_classes.items():
            if class_name in boards and isinstance(new_students_map, dict):
                # Pertahankan urutan asli nama siswa
                original_students = boards[class_name]["students"]
                for student_name in original_students:
                    if student_name in new_students_map:
                        original_students[student_name] = new_students_map[student_name]

                new_text = format_class_message(class_name, original_students)
                await boards[class_name]["message"].edit(content=new_text)
                any_edited = True

        if any_edited:
            await message.add_reaction("✅")
            if result.get("reply"):
                await message.reply(result["reply"], mention_author=False)

# ================= HEALTH CHECK SERVER UNTUK KOYEB =================
async def health_check(request):
    return web.Response(text="Bot Peminjaman Device Aktif!")

async def start_services():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(start_services())