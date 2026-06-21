# Studynest — AI-Powered PDF Learning Assistant

Upload your notes, textbook chapters, or slides, and Studynest turns them into quizzes,
flashcards, smart notes, and a personal AI tutor — all in one app, one server, no separate
frontend/backend deployment.

## Features

- **Smart PDF analysis** — extracts topics, definitions, formulas, and a summary
- **5 quiz types** — Multiple Choice, True/False, Fill in the Blank, Match the Following, Short Answer
- **Difficulty levels** — Easy / Medium / Hard
- **Flashcards** — auto-generated, with flip animation, "mark as learned," and bookmarking
- **Progress dashboard** — quizzes completed, average score, strong/weak topics, study streak
- **Gamification** — XP, levels, streaks, achievement badges
- **Revision mode** — quizzes built only from weak topics and previously wrong answers
- **Timed quiz mode** — set a time limit and race the clock
- **AI Tutor (RAG)** — chat about a document; answers are grounded only in its content
- **Smart notes** — one-page summary, bullet notes, exam revision notes, cheat sheet

## Setup

```bash
cd studynest
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:5050** in your browser.

## Connect your AI key

Studynest needs an AI key to generate quizzes, flashcards, tutor answers, and notes.

1. Go to **Settings** in the sidebar
2. Choose **Anthropic**, **OpenAI**, or **Gemini**
3. Paste your API key and save

Get a key at:
- Anthropic: https://console.anthropic.com → API Keys
- OpenAI: https://platform.openai.com → API Keys
- Gemini: https://aistudio.google.com/apikey → Create API key (free tier available, no payment method required)

Your key is stored only in the local SQLite database (`instance/studynest.db`) on your
machine and is sent directly to the provider you chose — nowhere else.

## Tech stack

- **Backend**: Flask (Python), SQLite — single process, no separate API/server split
- **Frontend**: Server-rendered Jinja2 templates + Tailwind CSS (CDN) + vanilla JS
- **PDF parsing**: pypdf
- **AI**: your own Anthropic or OpenAI key, called directly from the Flask backend

## Project structure

```
studynest/
├── app.py                 # Flask app: routes, AI calls, gamification logic
├── requirements.txt
├── templates/              # All pages (Jinja2)
├── static/
│   ├── css/app.css         # Design system
│   ├── js/app.js           # Shared interactions
│   └── img/                # Logo & icon (SVG)
├── uploads/                 # Uploaded PDFs land here
└── instance/
    └── studynest.db         # SQLite database (created on first run)
```

## Notes

- This runs as a single Flask server — frontend and backend are not deployed separately.
- All data (PDFs, quizzes, flashcards, progress, XP) is local to your machine via SQLite.
- Short-answer questions are scored as "attempted" rather than auto-graded right/wrong,
  since free-text grading needs human or AI judgment — review them yourself on the result page.
