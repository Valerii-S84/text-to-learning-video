# Канонічний опис продукту й архітектури

## 1. Призначення програми

Text to Learning Video — локальний desktop media generator. Він перетворює
заздалегідь схвалений текст однією з підтримуваних мов, topic і локальне
фонове зображення на навчальний MP3 та karaoke-video MP4
(backend/pyproject.toml:5-10;
backend/src/anna_backend/local_pipeline.py:103-186).

Основний інтерактивний інтерфейс — desktop GUI на PySide6. GUI збирає
параметри, запускає генерацію у QThread, показує статус і відкриває готові
локальні медіафайли у QtMultimedia players
(backend/src/anna_backend/admin_app.py:197-306;
backend/src/anna_backend/admin_app.py:372-454).

На Windows користувач запускає GUI через `START_ADMIN.vbs`. Launcher знаходить
`backend/.venv/Scripts/pythonw.exe`, запускає `anna_backend.admin_app` без
видимого console window і показує системне повідомлення, якщо `.venv` відсутній
або процес завершився з помилкою
(START_ADMIN.vbs:1-25).

Другий entrypoint — локальний CLI. Він приймає текст безпосередньо або з
UTF-8-файлу, topic, background image та output name, будує ті самі production
adapters і викликає LocalPipeline
(backend/src/anna_backend/cli/make_video.py:45-97;
backend/src/anna_backend/cli/make_video.py:125-147).

Програма не планує контент і не переписує отриманий текст. LocalPipeline
приймає plain values та передає text безпосередньо TTS adapter
(backend/src/anna_backend/local_pipeline.py:103-121;
backend/src/anna_backend/local_pipeline.py:138-148).

Готові результати — MP3 у ready-audio та валідований MP4 у ready-videos.
Проміжні source MP3, paced WAV і ASS створюються в тимчасовому workspace
(backend/src/anna_backend/local_pipeline.py:128-178).

GUI та CLI підтримують німецьку (`de`), англійську (`en`), українську (`uk`),
польську (`pl`) і нідерландську (`nl`). Німецька — початкове значення.
GUI формує редаговані TTS instructions для обраної мови й ролі; у CLI
`--instructions` обов’язковий для мови, відмінної від `de`
(backend/src/anna_backend/admin_app.py; backend/src/anna_backend/cli/make_video.py).

GUI також підтримує ролі навчального оповідача, гумористичного оповідача й
диктора новин, вибір голосу, швидкість мовлення та вимикач караоке
(backend/src/anna_backend/admin_app.py; backend/src/anna_backend/tts/profile.py).

## 2. Функціонал GUI

AdminWindow має такі елементи:

| Елемент | Фактична роль | Джерело |
|---|---|---|
| Title | Початкове значення «Annas Brief»; передається як intro_title до FFmpegVideoRenderer, а не як аргумент LocalPipeline | backend/src/anna_backend/admin_app.py:206-215; backend/src/anna_backend/admin_app.py:332-369 |
| Topic | Обов’язковий topic для intro; у GUI він також стає output_name | backend/src/anna_backend/admin_app.py:207-207; backend/src/anna_backend/admin_app.py:372-414 |
| Text | Багаторядкове поле; перед запуском whitespace нормалізується до одного рядка | backend/src/anna_backend/admin_app.py:124-125; backend/src/anna_backend/admin_app.py:208-209; backend/src/anna_backend/admin_app.py:372-382 |
| Voice | QComboBox з OpenAI TTS voices; початковий voice — marin | backend/src/anna_backend/admin_app.py:210-212 |
| Instructions | Редаговані TTS instructions, початково з ANNA_MARIN_PROFILE | backend/src/anna_backend/admin_app.py:213-215; backend/src/anna_backend/admin_app.py:332-369 |
| Background image | Read-only path, file picker і масштабований preview | backend/src/anna_backend/admin_app.py:216-231; backend/src/anna_backend/admin_app.py:308-329 |
| Generate video | Валідує обов’язкові GUI-поля, будує pipeline та запускає worker thread | backend/src/anna_backend/admin_app.py:241-243; backend/src/anna_backend/admin_app.py:372-429 |
| Status | Waiting for input → Generating... → Ready або Error: message | backend/src/anna_backend/admin_app.py:244-244; backend/src/anna_backend/admin_app.py:372-454 |
| Loading overlay | Під час генерації накриває форму й preview напівпрозорим шаром із анімованим spinner та блокує взаємодію з основним content | backend/src/anna_backend/admin_app.py:128-194; backend/src/anna_backend/admin_app.py:295-305; backend/src/anna_backend/admin_app.py:425-452 |
| MP3 preview | QMediaPlayer/QAudioOutput з Play audio та Stop audio | backend/src/anna_backend/admin_app.py:246-259; backend/src/anna_backend/admin_app.py:432-441 |
| MP4 preview | QMediaPlayer/QVideoWidget з Play video та Stop video | backend/src/anna_backend/admin_app.py:261-276; backend/src/anna_backend/admin_app.py:432-441 |

Кнопки preview вимкнені до успішного результату. Після success GUI встановлює
локальні audio_path і video_path з LocalMediaResult як media sources, вмикає
controls і ставить статус Ready
(backend/src/anna_backend/admin_app.py:246-273;
backend/src/anna_backend/admin_app.py:432-442).

Після старту worker thread основний content стає disabled, `QStackedLayout`
показує `_LoadingOverlay`, а timer анімує spinner. Після завершення — незалежно
від success або failure — overlay і timer зупиняються, content знову стає
активним. Unit-тести перевіряють обидва сценарії
(backend/src/anna_backend/admin_app.py:295-305;
backend/src/anna_backend/admin_app.py:425-454;
backend/tests/unit/test_admin_app.py:245-310;
backend/tests/unit/test_admin_app.py:318-346).

Генерація не виконується в GUI thread. _GenerationWorker викликає
asyncio.run(LocalPipeline.run(...)) у QThread і повертає succeeded, failed та
finished signals
(backend/src/anna_backend/admin_app.py:79-125;
backend/src/anna_backend/admin_app.py:403-429).

GUI відповідає за presentation concerns: введення, початкову перевірку
обов’язкових полів, composition production adapters, thread lifecycle,
loading state, статус і preview. Він не виконує TTS, alignment, pacing або
render algorithms
(backend/src/anna_backend/admin_app.py:197-454).

## 3. Технологічний стек

| Компонент | Використання | Джерело |
|---|---|---|
| Python 3.12+ | Мова й мінімальна підтримувана версія package | backend/pyproject.toml:5-10 |
| PySide6 Widgets | Desktop GUI | backend/pyproject.toml:11-16; backend/src/anna_backend/admin_app.py:8-26 |
| QtMultimedia | Локальне відтворення MP3/MP4 | backend/src/anna_backend/admin_app.py:10-11; backend/src/anna_backend/admin_app.py:185-228 |
| OpenAI Audio Speech API | TTS через AsyncOpenAI | backend/src/anna_backend/tts/client.py:8-18; backend/src/anna_backend/tts/client.py:51-70 |
| OpenAI Transcriptions API | Whisper word timestamps через whisper-1 | backend/src/anna_backend/whisper/client.py:54-94 |
| RapidFuzz | ratio для deterministic token similarity | backend/src/anna_backend/alignment/aligner.py:7-8; backend/src/anna_backend/alignment/aligner.py:38-39 |
| FFmpeg | pacing, ready MP3 encoding і final video render | backend/src/anna_backend/audio/learning.py:285-422; backend/src/anna_backend/video/ffmpeg.py:386-546 |
| FFprobe | audio duration та MP4 stream/container validation | backend/src/anna_backend/audio/learning.py:158-183; backend/src/anna_backend/video/ffmpeg.py:279-384 |
| ASS | Deterministic karaoke subtitle file | backend/src/anna_backend/subtitles/ass.py:20-33; backend/src/anna_backend/subtitles/ass.py:144-178 |
| pytest / pytest-asyncio | Test tooling | backend/pyproject.toml:18-30 |
| Ruff | Lint/format policy для Python 3.12 | backend/pyproject.toml:18-23; backend/pyproject.toml:32-37 |

## 4. Архітектура

Фактична схема є lightweight Clean / Ports-and-Adapters:

~~~text
GUI ─────┐
         ├──> LocalPipeline
CLI ─────┘

GUI/CLI ──> production adapters
LocalPipeline ──> application-owned Protocols / neutral media types
~~~

GUI та CLI є composition roots: вони знають concrete production adapters і
передають їх у LocalPipeline
(backend/src/anna_backend/admin_app.py:332-369;
backend/src/anna_backend/cli/make_video.py:125-139).

GUI імпортує concrete media adapters локально всередині `_build_pipeline`, а
не під час імпорту `admin_app` module. Це відкладене завантаження dependencies;
архітектурна відповідальність composition root залишається в GUI
(backend/src/anna_backend/admin_app.py:332-369).

LocalPipeline належить application layer. Він визначає порядок use case,
валідацію, bounded retry policy, тимчасовий workspace, validation gate і
publication
(backend/src/anna_backend/local_pipeline.py:20-21;
backend/src/anna_backend/local_pipeline.py:85-186).

LocalPipeline залежить від шести application-owned Protocols, а не імпортує
OpenAI або FFmpeg concrete classes
(backend/src/anna_backend/local_pipeline.py:7-18;
backend/src/anna_backend/local_pipeline.py:24-101).

Neutral immutable media types містять лише word timings, alignment,
video-validation report і paths/metadata готового result
(backend/src/anna_backend/media_types.py:1-51).

### Шість портів

| Порт | Вхід | Вихід / контракт | Джерело |
|---|---|---|---|
| TextToSpeech | spoken_text, destination | Path до synthesized audio | backend/src/anna_backend/local_pipeline.py:24-25 |
| Transcriber | audio_path, strategy | tuple WordTiming | backend/src/anna_backend/local_pipeline.py:28-34 |
| AudioPacer | source, AlignmentResult, destination | paced AlignmentResult | backend/src/anna_backend/local_pipeline.py:37-44 |
| ReadyAudioEncoder | source, destination | Path до ready audio candidate | backend/src/anna_backend/local_pipeline.py:47-53 |
| SubtitleRenderer | AlignmentResult, destination | Path до subtitles | backend/src/anna_backend/local_pipeline.py:56-61 |
| VideoRenderer | background, audio, subtitles, destination, topic; окремо video validation | Path та VideoValidationResult | backend/src/anna_backend/local_pipeline.py:64-82 |

### Чому DeterministicTextAligner не є портом

DeterministicTextAligner створюється самим LocalPipeline з application-owned
threshold 97.0; його не інжектують entrypoint-и
(backend/src/anna_backend/local_pipeline.py:20-21;
backend/src/anna_backend/local_pipeline.py:116-127).

Aligner є детермінованим локальним policy/algorithm: нормалізує token-и,
порівнює їх через RapidFuzz, будує mapping, інтерполює допустимі внутрішні
пропуски і перевіряє score та timestamps
(backend/src/anna_backend/alignment/aligner.py:23-168).

Він не перетинає зовнішню I/O boundary і не має другої production
implementation. Тому це внутрішній application component, тоді як ports
ізолюють OpenAI, FFmpeg/FFprobe й ASS adapters
(backend/src/anna_backend/local_pipeline.py:9-12;
backend/src/anna_backend/local_pipeline.py:24-82).

### Межі відповідальності

| Шар | Відповідальність | Не виконує | Джерело |
|---|---|---|---|
| GUI | Form state, basic required-field checks, adapter composition, QThread, blocking loading overlay, status, preview | Media algorithms | backend/src/anna_backend/admin_app.py:128-454 |
| CLI | Argument/file input, API-key loading, adapter composition, result serialization | Media algorithms | backend/src/anna_backend/cli/make_video.py:30-151 |
| Application | Use-case order, input contract, align policy, bounded attempts, validation/publication gates | Provider/subprocess details | backend/src/anna_backend/local_pipeline.py:85-307 |
| Adapters | OpenAI calls, FFmpeg/FFprobe processes, ASS serialization | Overall workflow policy | backend/src/anna_backend/tts/client.py:17-73; backend/src/anna_backend/whisper/client.py:54-94; backend/src/anna_backend/audio/learning.py:285-422; backend/src/anna_backend/subtitles/ass.py:144-178; backend/src/anna_backend/video/ffmpeg.py:386-621 |

## 5. Повний runtime-цикл

### Послідовність від Generate video

| Крок | Вхід → вихід | Відповідальний компонент | Джерело |
|---|---|---|---|
| 1. User input | title/topic/text/voice/instructions/background → form values | AdminWindow | backend/src/anna_backend/admin_app.py:206-240; backend/src/anna_backend/admin_app.py:372-382 |
| 2. GUI precheck | form values → accepted values або status error | AdminWindow._prepare_generation | backend/src/anna_backend/admin_app.py:372-400 |
| 3. Build adapters | title/voice/instructions/API key → LocalPipeline | AdminWindow._build_pipeline | backend/src/anna_backend/admin_app.py:332-369 |
| 4. Start worker | pipeline + plain inputs → QThread execution; content disabled і loading overlay active | AdminWindow / _GenerationWorker | backend/src/anna_backend/admin_app.py:79-125; backend/src/anna_backend/admin_app.py:403-429 |
| 5. Validate input | text/topic/background/output_name → resolved background + safe name або exception | LocalPipeline._validate_inputs | backend/src/anna_backend/local_pipeline.py:116-124; backend/src/anna_backend/local_pipeline.py:193-224 |
| 6. Workspace | safe name + tmp dir → per-run temporary paths | LocalPipeline | backend/src/anna_backend/local_pipeline.py:125-136 |
| 7. TTS | approved text → source.mp3 | TextToSpeech / OpenAIAudioGenerator | backend/src/anna_backend/local_pipeline.py:138-140; backend/src/anna_backend/tts/client.py:33-73 |
| 8. Whisper | source.mp3 + strategy → provider word timings | Transcriber / OpenAIWhisperWordTranscriber | backend/src/anna_backend/local_pipeline.py:142-148; backend/src/anna_backend/whisper/client.py:78-94 |
| 9. Alignment | approved text + timings → AlignmentResult | DeterministicTextAligner | backend/src/anna_backend/local_pipeline.py:232-258; backend/src/anna_backend/alignment/aligner.py:146-176 |
| 10. Terminal gate | mapping → pass або AlignmentError, незалежно від уже прийнятного score | align_spoken_text | backend/src/anna_backend/alignment/aligner.py:154-168; backend/tests/unit/test_media_pipeline.py:71-83 |
| 11. Audio pacing | source audio + alignment → paced.wav + shifted AlignmentResult | AudioPacer / FFmpegLearningAudioPacer | backend/src/anna_backend/local_pipeline.py:261-282; backend/src/anna_backend/audio/learning.py:341-422 |
| 12. ASS karaoke | paced alignment → karaoke.ass | SubtitleRenderer / AssSubtitleRenderer | backend/src/anna_backend/local_pipeline.py:272-281; backend/src/anna_backend/subtitles/ass.py:144-178 |
| 13. Ready MP3 | paced.wav → MP3 candidate | ReadyAudioEncoder / FFmpegReadyAudioEncoder | backend/src/anna_backend/local_pipeline.py:277-281; backend/src/anna_backend/audio/learning.py:285-338 |
| 14. Video render | image + paced WAV + ASS + intro topic → MP4 candidate | VideoRenderer / FFmpegVideoRenderer | backend/src/anna_backend/local_pipeline.py:285-301; backend/src/anna_backend/video/ffmpeg.py:386-546 |
| 15. Candidate checks | MP3/MP4 candidates → non-empty guarantee | LocalPipeline | backend/src/anna_backend/local_pipeline.py:227-229; backend/src/anna_backend/local_pipeline.py:302-303 |
| 16. Media validation | MP4 candidate + MP3 candidate → VideoValidationResult | FFmpegVideoRenderer / FFprobe validator | backend/src/anna_backend/local_pipeline.py:304-307; backend/src/anna_backend/video/ffmpeg.py:279-384; backend/src/anna_backend/video/ffmpeg.py:613-621 |
| 17. Publication | validated candidates → ready-audio/name.mp3 + ready-videos/name.mp4 | LocalPipeline | backend/src/anna_backend/local_pipeline.py:173-178 |
| 18. Result | final paths + score + strategy + hash → LocalMediaResult | LocalPipeline | backend/src/anna_backend/local_pipeline.py:180-186; backend/src/anna_backend/media_types.py:45-51 |
| 19. Finish UI | LocalMediaResult або error → Ready/Error status; overlay hidden, content restored; після success MP3/MP4 controls enabled | AdminWindow | backend/src/anna_backend/admin_app.py:432-454 |

## 6. Failure safety

Input validation виконується до TTS та Whisper. Відкидаються порожні text і
topic, multiline text, topic з префіксом Tag N:, відсутній/порожній background
та небезпечний output name
(backend/src/anna_backend/local_pipeline.py:116-139;
backend/src/anna_backend/local_pipeline.py:193-224).

Один pipeline run містить рівно один application-call TextToSpeech.synthesize.
Whisper retry loop не повторює TTS
(backend/src/anna_backend/local_pipeline.py:138-148;
backend/src/anna_backend/local_pipeline.py:232-258).

OpenAIAudioGenerator і OpenAIWhisperWordTranscriber створюють AsyncOpenAI з
max_retries=0, тому adapter не додає прихованих SDK retries
(backend/src/anna_backend/tts/client.py:20-31;
backend/src/anna_backend/whisper/client.py:57-69).

WHISPER_STRATEGIES містить рівно word і sentence. Pipeline завершується після
першого успішного alignment або fail після primary та одного alternate
(backend/src/anna_backend/local_pipeline.py:20-21;
backend/src/anna_backend/local_pipeline.py:232-258).

Alignment threshold дорівнює 97%. Навіть коли global score проходить threshold,
відсутнє підтвердження останнього token окремо викликає AlignmentError
(backend/src/anna_backend/alignment/aligner.py:146-168;
backend/tests/unit/test_media_pipeline.py:71-83).

Кожен ready artifact публікується через os.replace лише після validation.
Це атомарна заміна окремого файла; код не реалізує спільну транзакцію для
пари MP3+MP4
(backend/src/anna_backend/local_pipeline.py:162-178).

Alignment failure і validator failure до publication не замінюють уже наявні
ready artifacts; це окремо зафіксовано regression-тестами
(backend/tests/unit/test_local_pipeline.py:233-270;
backend/tests/unit/test_local_pipeline.py:273-309).

FFmpeg/FFprobe subprocess calls використовують CREATE_NO_WINDOW на Windows.
Ця поведінка має regression-test для всіх media subprocess call sites
(backend/src/anna_backend/audio/learning.py:14-14;
backend/src/anna_backend/audio/learning.py:135-146;
backend/src/anna_backend/video/ffmpeg.py:18-18;
backend/src/anna_backend/video/ffmpeg.py:120-199;
backend/tests/unit/test_media_pipeline.py:363-405).

## 7. Ключова файлова структура

~~~text
START_ADMIN.vbs
backend/
├── pyproject.toml
├── src/anna_backend/
│   ├── admin_app.py
│   ├── cli/make_video.py
│   ├── local_pipeline.py
│   ├── media_types.py
│   ├── tts/
│   │   ├── client.py
│   │   └── profile.py
│   ├── whisper/client.py
│   ├── alignment/aligner.py
│   ├── audio/learning.py
│   ├── subtitles/ass.py
│   └── video/ffmpeg.py
└── tests/
    ├── integration/test_local_pipeline_e2e.py
    └── unit/
        ├── test_admin_app.py
        ├── test_final_mp4_validation.py
        ├── test_local_pipeline_dependencies.py
        ├── test_local_pipeline.py
        ├── test_make_video_cli.py
        ├── test_media_parameters.py
        ├── test_media_pipeline.py
        ├── test_tts_adapter.py
        └── test_tts_profile.py
~~~

## 8. Що свідомо відсутнє

У поточному production dependency set і runtime composition немає:

- database або ORM;
- HTTP server чи routes;
- browser frontend;
- jobs, workers або scheduler infrastructure;
- repositories;
- DI-container;
- factories або registries;
- cloud storage;
- enterprise application layers понад описані entrypoints, application
  workflow, ports/types та adapters.

Ця межа підтверджується малим dependency list, прямою composition concrete
adapters у двох entrypoints і plain-value signature LocalPipeline.run
(backend/pyproject.toml:11-16;
backend/src/anna_backend/admin_app.py:332-369;
backend/src/anna_backend/cli/make_video.py:125-139;
backend/src/anna_backend/local_pipeline.py:103-113).

## 9. Поточні відомі відкриті питання

У перевіреному production/test scope немає підтвердженого відкритого питання,
яке слід канонізувати як відомий дефект.

Karaoke synchronization має явний offset -0.15 s та regression-test, що cues
не перекриваються після early-start offset. Сам код не підтверджує наявність
невирішеного sync bug
(backend/src/anna_backend/subtitles/ass.py:20-33;
backend/src/anna_backend/subtitles/ass.py:144-178;
backend/tests/unit/test_media_pipeline.py:165-180).

Windows console flashing також не є відкритим питанням у поточному стані:
media subprocess calls передають CREATE_NO_WINDOW, а test suite це перевіряє
(backend/src/anna_backend/audio/learning.py:14-14;
backend/src/anna_backend/video/ffmpeg.py:18-18;
backend/tests/unit/test_media_pipeline.py:363-405).

## Підсумкова схема

~~~text
USER
 ↓
START_ADMIN.vbs → PySide6 GUI / CLI
 ↓
LocalPipeline
 ↓
Ports
 ↓
OpenAI + FFmpeg + ASS
 ↓
MP3 + MP4
~~~

Схема показує фактичний dependency flow: entrypoints складають adapters,
LocalPipeline оркеструє ports, а validated media повертаються як
LocalMediaResult
(backend/src/anna_backend/admin_app.py:332-454;
backend/src/anna_backend/cli/make_video.py:125-151;
backend/src/anna_backend/local_pipeline.py:85-186).
