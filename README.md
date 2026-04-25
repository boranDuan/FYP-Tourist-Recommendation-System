# Dublin Tourist Recommendation System

A web-based tourist attraction recommendation system focused on Dublin, Ireland.

The system provides personalized attraction suggestions through an interactive map, supports itinerary generation and editing, and includes AI-assisted conversational interaction for trip planning.

## Features

- Interactive Dublin map with POI browsing and route visualization
- Preference-based recommendation from questionnaire and user input
- AI conversational interface for travel Q&A and guided itinerary edits
- Multi-day itinerary generation with pace-aware planning
- Structured itinerary edit operations (add / remove / move / replace)
- Favorite places and trip history management

## Tech Stack

- **Frontend:** HTML, CSS, JavaScript, Jinja2 templates
- **Backend:** Python, Flask
- **Database:** MySQL (via Flask-SQLAlchemy + PyMySQL)
- **External APIs:** Google Maps/Places, OpenAI, OpenWeather (map layer)

## Data Sources

- Smart Dublin
- Fáilte Ireland
- Ireland Open Data Portal

## Project Modules

- **AI Conversational Interface**
- **Preference Matching**
- **Itinerary Generator**
- **Itinerary Edit Manager**

## Quick Start

### 1) Clone and install dependencies

```bash
git clone <your-repo-url>
cd FYP-touristRecommendSystem
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Configure environment variables

Create a `.env` file in the project root (example):

```env
SECRET_KEY=your_secret_key
DATABASE_URL=mysql+pymysql://user:password@localhost/tourist_recommend?charset=utf8mb4
GOOGLE_MAPS_API_KEY=your_google_maps_key
OPENWEATHER_API_KEY=your_openweather_key
OPENAI_API_KEY=your_openai_key
```

### 3) Prepare MySQL database

- Create a MySQL database (e.g., `tourist_recommend`)
- Ensure the user in `DATABASE_URL` has access
- Tables are created automatically on startup through SQLAlchemy

### 4) Run the app

```bash
flask --app app.py run --debug
```

Open: [http://127.0.0.1:5000/map](http://127.0.0.1:5000/map)

## Evaluation

The project is evaluated through:

- Functional testing of recommendation and itinerary workflows
- User-based evaluation on recommendation relevance and usability

## Notes

- This project is designed for academic/research purposes.
- API usage may require valid keys, quota, and compliance with provider terms.
