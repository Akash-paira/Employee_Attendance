Employee Attendance & Analytics API
A REST API built with FastAPI and MongoDB for managing employee records, recording attendance, correcting attendance entries, and generating attendance analytics.
Features
- Create and list employees.
- Record employee punch-in and punch-out.
- Retrieve attendance records with filters and pagination.
- Regularize an attendance record with a reason and reviewer details.
- Calculate monthly employee attendance summaries.
- Summarize attendance by department.
- Rank employees by total late minutes.
- Generate daily department attendance trends.
- Inspect MongoDB query execution plans through an admin explain endpoint.
- Create database indexes when the application starts.

Tech Stack
- Python 3.11+
- FastAPI
- PyMongo
- MongoDB 6.0+
- Uvicorn
Project Structure
.
├── app/
│   └── main.py
├── requirements.txt
├── README.md
├── REVIEW.md
└── DECISIONS.md
## Local setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt  
MONGO_URI=mongodb://localhost:27017
MONGO_DB=hrone_assignment
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```
## Submit
A public Git repository with `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md` and a short `README.md`.
No `.env`, no secrets, no Dockerfile.
