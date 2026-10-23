# Employee Attendance & Analytics API

A REST API built with **FastAPI** and **MongoDB** for managing employee records, recording attendance, correcting attendance entries, and generating attendance analytics.

## Features

### 1. Employee Management
- Create employee records.
- Retrieve and list employee records.

### 2. Attendance Management
- Record employee punch-in and punch-out.
- Retrieve attendance records with filters and pagination.
- Validate attendance requests and handle conflicting operations.

### 3. Attendance Regularization
- Correct existing attendance records.
- Record the regularization reason and reviewer details.
- Recalculate derived attendance fields and preserve correction history.

### 4. Attendance Analytics
- Calculate monthly attendance summaries for employees.
- Summarize attendance by department.
- Rank employees by total late minutes.
- Generate daily department attendance trends.

### 5. Database and Query Optimization
- Create required MongoDB indexes when the application starts.
- Enforce data uniqueness where required.
- Inspect MongoDB query execution plans through an admin explain endpoint.

## Tech Stack

- **Language:** Python 3.11+
- **Web Framework:** FastAPI
- **Database Driver:** PyMongo
- **Database:** MongoDB 6.0+
- **ASGI Server:** Uvicorn

## Project Structure

```text
.
├── app/
│   └── main.py
├── requirements.txt
├── README.md
├── REVIEW.md
└── DECISIONS.md
```

### File Descriptions

- `app/main.py` — Contains the API implementation, endpoints, and supporting logic.
- `requirements.txt` — Lists the Python dependencies required by the project.
- `README.md` — Provides project documentation and setup instructions.
- `REVIEW.md` — Documents identified defects, test cases, and fixes.
- `DECISIONS.md` — Records technical decisions and their rationale.

## Prerequisites

Before running the project, ensure you have installed:

- Python 3.11 or later.
- MongoDB 6.0 or later, either locally or through a hosted instance.
- Git.

## Installation and Setup

### 1. Clone the Repository

```bash
git clone https://github.com/Akash-paira/Employee_Attendance.git
cd Employee_Attendance
```

### 2. Create a Virtual Environment

**Windows:**

```bash
python -m venv venv
venv\Scripts\activate
```

**Linux / macOS:**

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Configure the following environment variables before starting the application:

- `MONGO_URI` — `mongodb://localhost:27017`
- `MONGO_DB` —  `hrone_assignment`

### 5. Start the Application

```bash
uvicorn app.main:app --reload --port 8000
```
## API Documentation

FastAPI provides interactive API documentation when the application is running.

- **Swagger UI:** `http://127.0.0.1:8000/docs`
- **ReDoc:** `http://127.0.0.1:8000/redoc`

Use these interfaces to explore available endpoints, inspect request and response schemas, and test API operations.

## Testing and Verification

Verify the application using the following checks:

- Confirm that the application starts successfully.
- Check the health endpoint, if implemented.
- Verify MongoDB connectivity and required indexes.
- Test employee creation and retrieval.
- Test punch-in and punch-out operations.
- Verify pagination, filters, and timestamp validation.
- Test attendance regularization and concurrent requests.
- Validate analytics results and leaderboard rankings.
- Inspect query execution plans using the admin explain endpoint.

## Code Review

See [`REVIEW.md`](REVIEW.md) for the documented issues, test scenarios, and proposed or implemented fixes.

See [`DECISIONS.md`](DECISIONS.md) for the technical decisions and their rationale.

## Notes

- Configure MongoDB using environment variables before running the application.
- Attendance calculations follows or must follow the defined shift, grace-period, rounding, and overtime rules.
- API timestamps follows or must follow the specified epoch-milliseconds contract.
- Database indexes and atomic updates help maintain data integrity under concurrent requests.
