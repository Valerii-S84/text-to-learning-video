# Text to Learning Video

Локальний desktop media generator, який перетворює заздалегідь схвалений
текст однією з підтримуваних мов на навчальний MP3 і karaoke-video MP4.

Користувач задає текст, тему уроку та локальне фонове зображення. Програма
озвучує текст через OpenAI TTS, отримує word timings через Whisper, вирівнює їх
із початковим текстом, створює навчальний темп аудіо, генерує ASS karaoke та
рендерить перевірене відео через FFmpeg.

У продукту є два локальні entrypoint:

- desktop GUI на PySide6 для ручної роботи та preview результатів;
- CLI для повторюваного запуску з командного рядка.

Обидва entrypoint використовують один production pipeline і створюють однакові
готові media artifacts.

## Що вміє програма

- приймає схвалений текст однією з підтримуваних мов без його генерації або переписування;
- підтримує пряме введення тексту та читання з UTF-8-файлу;
- дозволяє вибрати OpenAI TTS voice та instructions у GUI;
- створює MP3 зі сповільненим навчальним темпом і паузами між реченнями;
- синхронізує слова з Whisper timings через deterministic alignment;
- генерує дворядкові ASS karaoke subtitles;
- додає intro з title і topic;
- рендерить MP4 із фоновим зображенням, аудіо та karaoke;
- перевіряє фінальний MP4 через FFprobe;
- показує готові MP3 і MP4 у вбудованих GUI players.

Програма не містить content planner, catalog, database, HTTP API, n8n workflow,
job queue або cloud storage. Вона працює тільки як локальний media generator.

## Системні вимоги

- Windows;
- Python 3.12 або новіший;
- FFmpeg і FFprobe, доступні через `PATH`;
- OpenAI API key;
- доступ до інтернету для TTS і Whisper provider calls.

## Встановлення

Виконайте з кореня репозиторію:

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Запишіть API key у `backend/.env`:

```dotenv
OPENAI_API_KEY=your-api-key
```

Секретний файл `backend/.env` не відстежується Git.

## Запуск GUI

Після встановлення запустіть із кореня репозиторію:

```powershell
.\START_ADMIN.vbs
```

GUI дозволяє задати:

- `Title` — заголовок intro, початково `Annas Brief`;
- `Topic` — тема під заголовком і основа output name;
- `Text` — схвалений текст для озвучення;
- `Language` — мова тексту та ISO-код для Whisper, початково German (`de`);
- `Voice` — OpenAI TTS voice, початково `marin`;
- `Role` — поточний мовний профіль, навчальний оповідач, гумористичний оповідач або диктор новин;
- швидкість мовлення — від 0,85 до 1,20, початково 0,95; змінює швидкість у готових MP3 і MP4;
- `Караоке` — вмикає або вимикає підсвічування слів у відео, початково ввімкнене;
- `Instructions` — інструкції для TTS;
- `Background image` — локальне зображення для відео.

Генерація виконується у worker thread, тому GUI не блокується. Після успішного
запуску стають доступними локальні preview для MP3 і MP4.

Підтримувані мови в GUI та CLI (німецька — початкове значення, а не обмеження):

| Інтерфейс | Код Whisper |
|---|---:|
| German | `de` |
| English | `en` |
| Ukrainian | `uk` |
| Polish | `pl` |
| Dutch | `nl` |

У GUI вибір мови або ролі оновлює редаговані `Instructions`. Ролі працюють із
будь-якою з п’яти мов та обраним голосом. У CLI для `en`, `uk`, `pl` і `nl`
потрібно передати `--instructions`; для `de` початково використовується профіль Anna.

## Запуск CLI

Приклад із текстом із файлу:

```powershell
cd backend
.\.venv\Scripts\python.exe -m anna_backend.cli.make_video `
  --text-file input.txt `
  --topic "Beim Bäcker bestellen" `
  --image resources\images\Anna.png `
  --name baecker-bestellen
```

Приклад із прямим текстом:

```powershell
cd backend
.\.venv\Scripts\python.exe -m anna_backend.cli.make_video `
  --text "Ich hätte gern zwei Brötchen, bitte." `
  --topic "Beim Bäcker bestellen" `
  --background resources\images\Anna.png `
  --output-name baecker-bestellen
```

Приклад українського запуску:

```powershell
cd backend
.\.venv\Scripts\python.exe -m anna_backend.cli.make_video `
  --text "Сьогодні ми вивчаємо українську мову." `
  --topic "Українська мова" `
  --background resources\images\Anna.png `
  --output-name ukrayinska-mova `
  --language uk `
  --instructions "Говори природною українською мовою, тепло, спокійно й чітко."
```

CLI arguments:

| Argument | Обов'язковість | Призначення |
|---|---:|---|
| `--text` | один із двох | Схвалений текст безпосередньо в команді |
| `--text-file` | один із двох | UTF-8-файл зі схваленим текстом |
| `--topic` | так | Тема під `Anna's Brief` у video intro |
| `--background`, `--image` | так | Локальне фонове зображення |
| `--output-name`, `--name` | так | Базове ім'я готових MP3 і MP4 |
| `--language` | ні | Код мови `de`, `en`, `uk`, `pl` або `nl`; початково `de` |
| `--instructions` | для не-`de` | TTS instructions; для German без аргументу використовується чинний профіль Anna |

Після успіху CLI повертає JSON із шляхами до artifacts, alignment score,
використаною Whisper strategy та SHA-256 готового відео. Помилка завершує команду
з exit code `2` і зрозумілим повідомленням у stderr.

## Результати

Готові файли публікуються тільки після успішного render і validation:

```text
backend/ready-audio/<output-name>.mp3
backend/ready-videos/<output-name>.mp4
```

Source MP3, paced WAV, ASS і кандидати на публікацію створюються у тимчасовому
workspace під `backend/tmp/` та не є довготривалим станом продукту.

## Runtime pipeline

```text
GUI або CLI
    ↓
валідація локальних input values
    ↓
OpenAI TTS — один виклик на run
    ↓
Whisper word timings — primary strategy та максимум одна alternate
    ↓
deterministic alignment — мінімальний score 97%
    ↓
learning-audio pacing та ready MP3 encoding
    ↓
ASS karaoke generation
    ↓
FFmpeg MP4 render
    ↓
FFprobe validation
    ↓
atomic publication у ready-audio/ та ready-videos/
```

Якщо alignment не проходить після двох дозволених Whisper strategies, run
завершується помилкою без нового TTS-виклику. Невалідний або частково створений
artifact не замінює наявний готовий результат.

## Структура репозиторію

```text
START_ADMIN.vbs                   запуск desktop GUI
backend/
├── pyproject.toml                package і dependencies
├── input.txt                     приклад локального text input
├── resources/                    fonts і стандартне background image
├── ready-audio/                  готові MP3
├── ready-videos/                 готові MP4
├── src/anna_backend/
│   ├── admin_app.py              PySide6 GUI composition root
│   ├── cli/make_video.py         CLI composition root
│   ├── local_pipeline.py         порядок і safety policy media run
│   ├── alignment/                deterministic text/timing alignment
│   ├── audio/                    learning pacing і MP3 encoding
│   ├── subtitles/                ASS karaoke
│   ├── tts/                      OpenAI TTS adapter і voice profile
│   ├── whisper/                  OpenAI word-timing adapter
│   └── video/                    FFmpeg render і FFprobe validation
└── tests/                        provider-free unit та integration tests
docs/
└── ARCHITECTURE_AND_PRODUCT.md   детальний канонічний опис системи
```

## Перевірка

Усі тести використовують fakes або frozen local fixtures і не виконують платні
OpenAI calls.

```powershell
cd backend
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\ruff.exe check --no-cache src/anna_backend tests
```

Детальний опис компонентів, відповідальностей, failure safety та повного
runtime-циклу дивіться у
[`docs/ARCHITECTURE_AND_PRODUCT.md`](docs/ARCHITECTURE_AND_PRODUCT.md).
