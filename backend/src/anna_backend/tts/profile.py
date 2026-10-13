from dataclasses import dataclass

SPEAKING_ROLES = {
    "educational": (
        "Навчальний оповідач — акцент на фактах",
        """\
Оповідай навчальну історію як уважний, компетентний наставник. Голос спокійний,
доброзичливий і впевнений. Вимовляй слова чітко, з природними наголосами, без
скандування та перебільшеної артикуляції. Тримай помірний, рівний темп.
Групуй слова за змістом. Голосом виділяй головні факти, терміни, імена, числа,
дати та причинно-наслідкові зв'язки, які вже є в тексті. Перед важливим фактом
зроби коротку природну паузу, сам факт промов трохи повільніше. Відділяй
пояснення від висновку інтонацією, щоб слухач легко розумів і запам'ятовував.
Зберігай живу оповідь; не перетворюй кожне слово на акцент. Не додавай пояснень,
прикладів або висновків від себе. Уникай повчального тону, монотонності,
надмірного пафосу та довгих пауз.
""".strip(),
    ),
    "humorous": (
        "Гумористичний оповідач — історії та анекдоти",
        """\
Розповідай смішну історію як дотепний співрозмовник із хорошим відчуттям ритму.
Звучання тепле, невимушене, з легкою усмішкою в голосі. Починай природно,
не видаючи розв'язку завчасно. Поступово змінюй енергію відповідно до сюжету.
Перед комічним поворотом зроби коротку паузу, а фінальну репліку промов чітко,
з точним логічним наголосом. Дай жарту прозвучати без пояснення, чому він смішний.
У діалогах ледь змінюй інтонацію персонажів, зберігаючи обраний тембр і розбірливість.
Уникай карикатурних акцентів, крику, награного реготу та безперервної веселості.
Не додавай сміху, вигуків, власних жартів або реплік, яких немає в тексті.
Не розтягуй паузи й не прискорюйся настільки, щоб губилися слова.
""".strip(),
    ),
    "news": (
        "Професійний диктор — короткі новини",
        """\
Озвучуй коротку новину як професійний диктор інформаційного випуску: зібрано,
чітко, спокійно та впевнено. Початок має одразу привертати увагу змістом,
без рекламного натиску чи сенсаційності. Тримай компактний, рівний темп;
відділяй смислові блоки короткими паузами. Виразно вимовляй імена, назви,
числа й дати; на складних фрагментах трохи сповільнюйся.
Логічним наголосом виділяй головну подію та найважливіші подробиці.
Зберігай нейтральність і стриману людяність відповідно до змісту новини.
Припущення, цитати й непідтверджені твердження не подавай інтонаційно як
встановлені факти. Завершуй думки впевненою, природною інтонацією.
Не додавай привітань, коментарів, оцінок, підсумків або прощання, яких немає
в тексті. Уникай драматизації, монотонності та затягнутих пауз.
""".strip(),
    ),
}

ANNA_MARIN_INSTRUCTIONS = """\
Speak in natural Standard German as Anna recording a private voice message to her close friend
Maria shortly after the day's events. Tell the experience; do not sound as if you are reading a
letter. Sound like a feminine adult around 30-35: warm, calm, close, and naturally attractive,
with a slightly lower comfortable register and a light smile. The appeal must come from mature
warmth and presence, never seduction, flirtation, or a polished commercial voice.

Keep Anna thoughtful, observant, and quietly resilient. Use intimate conversational phrasing,
small natural changes of energy, and short breathing pauses, never an announcer, advertisement,
teacher, audiobook, or children's narrator. Use clean Hochdeutsch and clear learner-friendly
articulation without spelling words out or exaggerating consonants. Maintain an unhurried,
steady cadence. Keep transitions between sentences brief and natural, with no extended silence.
Group words into meaningful phrases and gently stress key content words and useful connectors.

Let emotion follow the meaning. When Anna admits that she was nervous, allow a brief
self-conscious smile or tiny hesitation, but never uncertainty in German pronunciation. When
someone helps or a problem becomes easier, release a little tension and add quiet relief. Use
soft warmth for family and friendship and calm pride when Anna notices progress. Keep all these
changes subtle and truthful.

Use a natural falling intonation for statements and a light rise only for real questions. Avoid
melodrama, sing-song rhythm, breathiness, whispering, exaggerated cheerfulness, and theatrical
pauses. Pronounce Anna, Maria, Marko, and Leo consistently in German. Do not imitate learner
errors or a foreign accent: the audio is a reliable pronunciation model for German learners.
"""


@dataclass(frozen=True, slots=True)
class SpeechProfile:
    model: str
    voice: str
    instructions: str
    response_format: str = "mp3"
    speed: float = 1.0

    def validate(self) -> None:
        if not self.model.strip():
            raise ValueError("TTS model must not be empty")
        if not self.voice.strip():
            raise ValueError("TTS voice must not be empty")
        if not 0.25 <= self.speed <= 4.0:
            raise ValueError("TTS speed must be between 0.25 and 4.0")
        if not self.instructions.strip():
            raise ValueError("TTS instructions must not be empty")


ANNA_MARIN_PROFILE = SpeechProfile(
    model="gpt-4o-mini-tts",
    voice="marin",
    instructions=ANNA_MARIN_INSTRUCTIONS,
)
ANNA_MARIN_PROFILE.validate()
