import os
from datetime import datetime, timedelta, timezone, date, time
from typing import Optional, Literal

from dotenv import load_dotenv
from decimal import Decimal, ROUND_HALF_UP
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator, model_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import PyMongoError, DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def create_indexes():
    db.employees.create_index("emp_code", unique=True)

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True
    )
    db.attendance_logs.create_index([("date", 1), ("emp_code", 1)])
    db.attendance_logs.create_index([("date", 1), ("late_minutes", 1)])
    db.employees.create_index([("department", 1), ("joined_on", 1)])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str, attendance_date: str) -> int:
    h, m = map(int, shift_start.split(":"))
    shift_start_dt = datetime.combine(
        date.fromisoformat(attendance_date),time(h, m),tzinfo=IST
    )
    if shift_start_dt - punch_in > timedelta(hours=12):
        shift_start_dt -= timedelta(days=1)
    elif punch_in - shift_start_dt > timedelta(hours=12):
        shift_start_dt += timedelta(days=1)

    elapsed = punch_in - shift_start_dt

    if elapsed > timedelta(minutes=10):
        return int(elapsed.total_seconds() // 60)
    return 0



def compute_work_hours(punch_in: datetime,punch_out: datetime) -> float:
    seconds = Decimal(
        str((punch_out - punch_in).total_seconds())
    )

    hours = seconds / Decimal("3600")

    return float(hours.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
    )


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    shift_end_dt = datetime.combine(
        date.fromisoformat(date_str),
        time(h, m),
        tzinfo=IST
    )

    # For overnight shifts, the shift ends the next day.
    if shift_end_dt <= datetime.combine(
        date.fromisoformat(date_str),
        time(0, 0),
        tzinfo=IST
    ):
        shift_end_dt += timedelta(days=1)

    elapsed = punch_out - shift_end_dt

    if elapsed >= timedelta(minutes=30):
        return int(elapsed.total_seconds() // 60)

    return 0



# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(
        default="09:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$"
    )
    shift_end: str = Field(
        default="18:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$"
    )
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value):
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("joined_on must be a valid YYYY-MM-DD date")
        if len(value) != 10:
            raise ValueError("joined_on must use YYYY-MM-DD format")
        return value

    @model_validator(mode="after")
    def validate_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self
    



class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = Field(
        default=None,
        ge=100_000_000_000,
        le=4_102_444_800_000
    )
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = Field(
        default=None,
        ge=100_000_000_000,
        le=4_102_444_800_000
    )


class RegularizeRequest(BaseModel):
    status: Optional[Literal[
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    ]] = None

    punch_in: Optional[StrictInt] = Field(
        default=None,
        ge=100_000_000_000,
        le=4_102_444_800_000
    )

    punch_out: Optional[StrictInt] = Field(
        default=None,
        ge=100_000_000_000,
        le=4_102_444_800_000
    )

    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
         raise HTTPException(
            status_code=503,
            detail="MongoDB is not available"
        )



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)

    doc = body.model_dump()
    doc["created_at"] = now_utc.replace(tzinfo=IST)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")

    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end": doc["shift_end"],
        "joined_on": doc["joined_on"],
        "created_at": int(now_utc.timestamp() * 1000),
    }


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    if page < 1:
        raise HTTPException(422, "page must be atyleast 1")
    if page_size < 1 or page_size > 100:
        raise HTTPException(422, "page_size must be between 1 and 100")
    q = {}
    if department:
        q["department"] = department
    skip = (page-1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code",1).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}




@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(404, "employee not found")

    if body.punched_at is None:
        ts_utc = datetime.now(timezone.utc)
    else:
        ts_utc = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc
        )

    ts_utc = ts_utc.replace(microsecond=0)

    ts_ist = ts_utc.astimezone(IST)

    attendance_day = ts_ist.date()

 
    if emp["shift_end"] <= emp["shift_start"]:
        if ts_ist.strftime("%H:%M") < emp["shift_end"]:
            attendance_day -= timedelta(days=1)

    d = attendance_day.isoformat()

    # Calculate late minutes using Indian time
    late_minutes = compute_late_minutes(
        ts_ist,
        emp["shift_start"],
        d
    )

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,

        # MongoDB stores this timestamp in UTC
        "punch_in": ts_utc.replace(tzinfo=None),

        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            409,
            "already punched in for this date"
        )

    # Return timestamps in epoch milliseconds as required by the API
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": int(ts_utc.timestamp() * 1000),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": doc["late_minutes"],
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(404, "employee not found")

    if body.punched_at is None:
        out_utc = datetime.now(timezone.utc)
    else:
        out_utc = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc
        )

    out_utc = out_utc.replace(microsecond=0)

    record = db.attendance_logs.find_one(
    {
        "emp_code": body.emp_code,
        "punch_in": {"$ne": None}
    },
    sort=[("punch_in", -1)]
)

    if record is None:
        raise HTTPException(404, "no punch-in record found")

    if record.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")

    in_utc = record["punch_in"].replace(tzinfo=timezone.utc)
    elapsed = out_utc - in_utc

    if elapsed.total_seconds() <= 0:
        raise HTTPException(422, "punch-out must be after punch-in")

    if elapsed > timedelta(hours=24):
        raise HTTPException(
            422,
            "punch-out cannot be more than 24 hours after punch-in"
        )

    seconds = int(elapsed.total_seconds())

    work_hours = float(
        (Decimal(seconds) / Decimal(3600)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )
    )

    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    attendance_day = date.fromisoformat(record["date"])
    end_hour, end_minute = map(int, shift_end.split(":"))

    end_day = attendance_day

    if shift_end <= shift_start:
        end_day += timedelta(days=1)

    shift_end_ist = datetime.combine(
        end_day,
        time(end_hour, end_minute),
        tzinfo=IST
    )

    out_ist = out_utc.astimezone(IST)
    overtime_seconds = (out_ist - shift_end_ist).total_seconds()

    overtime_minutes = 0

    if overtime_seconds >= 30 * 60:
        overtime_minutes = int(overtime_seconds // 60)

    half_day = work_hours < 4.50

    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "punch_out": None
        },
        {
            "$set": {
                "punch_out": out_utc.replace(tzinfo=None),
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day
            }
        },
        return_document=ReturnDocument.AFTER
    )

    if updated is None:
        raise HTTPException(409, "already punched out")

    return {
        "emp_code": updated["emp_code"],
        "date": updated["date"],
        "status": updated["status"],
        "punch_in": int(in_utc.timestamp() * 1000),
        "punch_out": int(out_utc.timestamp() * 1000),
        "work_hours": updated["work_hours"],
        "late_minutes": updated["late_minutes"],
        "overtime_minutes": updated["overtime_minutes"],
        "half_day": updated.get("half_day", False),
        "history": updated.get("history", [])
    }



@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):

    if page < 1:
        raise HTTPException(422, "page must be at least 1")

    if page_size < 1 or page_size > 100:
        raise HTTPException(422, "page_size must be between 1 and 100")

    try:
        if date_from:
            if len(date_from) != 10 or date.fromisoformat(date_from).isoformat() != date_from:
                raise ValueError()

        if date_to:
            if len(date_to) != 10 or date.fromisoformat(date_to).isoformat() != date_to:
                raise ValueError()

    except ValueError:
        raise HTTPException(422, "Dates must use YYYY-MM-DD format")

    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be on or before date_to")
    valid_statuses = ["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]

    if status and status not in valid_statuses:
        raise HTTPException(422, "Invalid status")
    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    if status:
        q["status"] = status

    if date_from or date_to:
        q["date"] = {}

        if date_from:
            q["date"]["$gte"] = date_from

        if date_to:
            q["date"]["$lte"] = date_to

    total = db.attendance_logs.count_documents(q)

    skip = (page - 1) * page_size

    docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    def to_epoch_ms(value):
        if value is None:
            return None

        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)

        return int(value.timestamp() * 1000)

    for d in docs:
        d["punch_in"] = to_epoch_ms(d.get("punch_in"))
        d["punch_out"] = to_epoch_ms(d.get("punch_out"))

        # Convert timestamps in history
        for entry in d.get("history", []):
            entry["at"] = to_epoch_ms(entry.get("at"))

            changes = entry.get("changes", {})

            for field in ["punch_in", "punch_out"]:
                if field in changes:
                    changes[field]["from"] = to_epoch_ms(
                        changes[field].get("from")
                    )
                    changes[field]["to"] = to_epoch_ms(
                        changes[field].get("to")
                    )

    return {
        "items": docs,"total": total,"page": page,"page_size": page_size
    }


@app.patch("/attendance/{emp_code}/{date_str}")
def regularize_attendance(
    emp_code: str,
    date_str: str,
    body: RegularizeRequest
):
    # Validate attendance date
    try:
        if (
            len(date_str) != 10
            or date.fromisoformat(date_str).isoformat() != date_str
        ):
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "date must use YYYY-MM-DD format")

    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date_str
    })
    if record is None:
        raise HTTPException(404, "attendance record not found")

    supplied = body.model_fields_set

    # Build only the fields the client actually supplied.
    requested = {}
    for field in ("status", "punch_in", "punch_out"):
        if field in supplied:
            requested[field] = getattr(body, field)

    if not requested:
        raise HTTPException(422, "No attendance changes supplied")

    status = requested.get("status", record["status"])

    # ABSENT/LEAVE cannot be combined with punch timestamps.
    if status in ("ABSENT", "LEAVE"):
        if (
            requested.get("punch_in") is not None
            or requested.get("punch_out") is not None
        ):
            raise HTTPException(
                422,
                "ABSENT/LEAVE cannot include punch_in or punch_out"
            )
        punch_in_utc = None
        punch_out_utc = None
    else:
        # Convert epoch milliseconds to whole-second UTC datetimes.
        if requested.get("punch_in") is not None:
            punch_in_utc = datetime.fromtimestamp(
                requested["punch_in"] // 1000,
                tz=timezone.utc
            )
        else:
            punch_in_utc = record.get("punch_in")
            if punch_in_utc is not None and punch_in_utc.tzinfo is None:
                punch_in_utc = punch_in_utc.replace(tzinfo=timezone.utc)

        if requested.get("punch_out") is not None:
            punch_out_utc = datetime.fromtimestamp(
                requested["punch_out"] // 1000,
                tz=timezone.utc
            )
        else:
            punch_out_utc = record.get("punch_out")
            if punch_out_utc is not None and punch_out_utc.tzinfo is None:
                punch_out_utc = punch_out_utc.replace(tzinfo=timezone.utc)

        # Presence statuses require a punch-in timestamp.
        if punch_in_utc is None:
            raise HTTPException(
                422,
                "PRESENT/WFH/ON_DUTY requires punch_in"
            )

        punch_in_utc = punch_in_utc.replace(microsecond=0)
        if punch_out_utc is not None:
            punch_out_utc = punch_out_utc.replace(microsecond=0)

        # Punch-in must belong to this attendance date under R1.
        punch_in_ist = punch_in_utc.astimezone(IST)
        attendance_day = punch_in_ist.date()

        if emp["shift_end"] <= emp["shift_start"]:
            if punch_in_ist.strftime("%H:%M") < emp["shift_end"]:
                attendance_day -= timedelta(days=1)

        if attendance_day.isoformat() != date_str:
            raise HTTPException(
                422,
                "punch_in must belong to the requested attendance date"
            )

        if punch_out_utc is not None:
            elapsed = punch_out_utc - punch_in_utc
            if elapsed.total_seconds() <= 0:
                raise HTTPException(
                    422,
                    "punch_out must be after punch_in"
                )
            if elapsed > timedelta(hours=24):
                raise HTTPException(
                    422,
                    "punch_out cannot be more than 24 hours after punch_in"
                )

    # Recalculate all derived values.
    if status in ("ABSENT", "LEAVE"):
        late_minutes = 0
        work_hours = None
        overtime_minutes = 0
        half_day = False
    else:
        late_minutes = compute_late_minutes(
            punch_in_utc.astimezone(IST),
            emp["shift_start"],
            date_str
        )

        if punch_out_utc is None:
            work_hours = None
            overtime_minutes = 0
            half_day = False
        else:
            work_hours = compute_work_hours(
                punch_in_utc,
                punch_out_utc
            )

            # Compute shift end correctly, including overnight shifts.
            end_hour, end_minute = map(
                int, emp["shift_end"].split(":")
            )
            shift_end_day = date.fromisoformat(date_str)

            if emp["shift_end"] <= emp["shift_start"]:
                shift_end_day += timedelta(days=1)

            shift_end_ist = datetime.combine(
                shift_end_day,
                time(end_hour, end_minute),
                tzinfo=IST
            )

            punch_out_ist = punch_out_utc.astimezone(IST)
            overtime_elapsed = punch_out_ist - shift_end_ist

            if overtime_elapsed >= timedelta(minutes=30):
                overtime_minutes = int(
                    overtime_elapsed.total_seconds() // 60
                )
            else:
                overtime_minutes = 0

            half_day = work_hours < 4.50

    # MongoDB stores instants as naive UTC datetimes.
    new_values = {
        "status": status,
        "punch_in": (
            punch_in_utc.astimezone(timezone.utc).replace(tzinfo=None)
            if punch_in_utc is not None else None
        ),
        "punch_out": (
            punch_out_utc.astimezone(timezone.utc).replace(tzinfo=None)
            if punch_out_utc is not None else None
        ),
        "late_minutes": late_minutes,
        "work_hours": work_hours,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day
    }

    # Record only fields whose stored values actually change.
    changes = {}

    for field, new_value in new_values.items():
        old_value = record.get(field)

        if field in ("punch_in", "punch_out") and old_value is not None:
            if old_value.tzinfo is None:
                old_value = old_value.replace(tzinfo=timezone.utc)
            else:
                old_value = old_value.astimezone(timezone.utc)

            if new_value is not None:
                compare_value = new_value.replace(tzinfo=timezone.utc)
            else:
                compare_value = None

            old_compare = old_value
        else:
            old_compare = old_value
            compare_value = new_value

        if old_compare != compare_value:
            changes[field] = {
                "from": old_value.replace(tzinfo=None)
                if field in ("punch_in", "punch_out") and old_value is not None
                else old_value,
                "to": new_value
            }

    if not changes:
        raise HTTPException(422, "No attendance changes detected")

    now_utc = datetime.now(timezone.utc).replace(microsecond=0)

    history_entry = {
        "at": now_utc.replace(tzinfo=None),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes
    }

    # Compare existing history to avoid silently overwriting concurrent edits.
    updated = db.attendance_logs.find_one_and_update(
        {
        "_id": record["_id"],
        "status": record["status"],
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
        "late_minutes": record.get("late_minutes", 0),
        "work_hours": record.get("work_hours"),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False)
    },
        {
            "$set": new_values,
            "$push": {"history": history_entry}
        },
        return_document=ReturnDocument.AFTER
    )

    if updated is None:
        raise HTTPException(
            409,
            "Attendance was modified concurrently; please retry"
        )

    def epoch_ms(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return int(value.timestamp() * 1000)

    result = {
        key: value
        for key, value in updated.items()
        if key != "_id"
    }

    result["punch_in"] = epoch_ms(updated.get("punch_in"))
    result["punch_out"] = epoch_ms(updated.get("punch_out"))

    for entry in result.get("history", []):
        entry["at"] = epoch_ms(entry.get("at"))
        for field in ("punch_in", "punch_out"):
            if field in entry.get("changes", {}):
                change = entry["changes"][field]
                change["from"] = epoch_ms(change.get("from"))
                change["to"] = epoch_ms(change.get("to"))

    return result


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    # Validate month format: YYYY-MM
    try:
        if (
            len(month) != 7
            or month[4] != "-"
            or datetime.strptime(month, "%Y-%m").strftime("%Y-%m") != month
        ):
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "month must use YYYY-MM format")

    emp = db.employees.find_one({"emp_code": emp_code})

    if emp is None:
        raise HTTPException(404, "employee not found")

    month_start = f"{month}-01"

    year, month_number = map(int, month.split("-"))
    if month_number == 12:
        next_month = f"{year + 1}-01-01"
    else:
        next_month = f"{year}-{month_number + 1:02d}-01"

    # Count weekdays from the later of joined_on and month_start.
    start_date = max(
        date.fromisoformat(emp["joined_on"]),
        date.fromisoformat(month_start)
    )
    end_date = date.fromisoformat(next_month)

    working_days = 0
    current_day = start_date

    while current_day < end_date:
        if current_day.weekday() < 5:
            working_days += 1
        current_day += timedelta(days=1)

    # Summarize attendance records using MongoDB aggregation.
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start,
                    "$lt": next_month
                }
            }
        },
        {
            "$addFields": {
                "_day_of_week": {
                    "$dayOfWeek": {
                        "$dateFromString": {
                            "dateString": "$date",
                            "format": "%Y-%m-%d"
                        }
                    }
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$in": ["$_day_of_week", [2, 3, 4, 5, 6]]}
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": "$late_minutes"
                },
                "total_overtime_minutes": {
                  "$sum": "$overtime_minutes"
                }   
            }
        }
    ]

    result = list(db.attendance_logs.aggregate(pipeline))

    if result:
        summary = result[0]
        present_days = float(summary["present_days"])
        leave_days = int(summary["leave_days"])
        late_count = int(summary["late_count"])
        total_late_minutes = int(summary["total_late_minutes"])
        total_overtime_minutes = int(summary["total_overtime_minutes"])
    else:
        present_days = 0.0
        leave_days = 0
        late_count = 0
        total_late_minutes = 0
        total_overtime_minutes = 0

    attendance_pct = None

    if working_days > 0:
        attendance_pct = float(
            (Decimal(str(present_days)) * Decimal("100") /
             Decimal(working_days)).quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP
            )
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str,
    department: Optional[str] = None
):
    # Validate month format: YYYY-MM
    try:
        if (
            len(month) != 7
            or datetime.strptime(month, "%Y-%m").strftime("%Y-%m") != month
        ):
            raise ValueError()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format"
        )

    year, month_number = map(int, month.split("-"))
    month_start = f"{month}-01"

    if month_number == 12:
        next_month = f"{year + 1}-01-01"
    else:
        next_month = f"{year}-{month_number + 1:02d}-01"

    # Headcount includes employees who joined by month end.
    employee_match = {
        "joined_on": {"$lt": next_month}
    }

    if department is not None:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"emp_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$emp_code"]},
                                    {"$gte": ["$date", month_start]},
                                    {"$lt": ["$date", next_month]}
                                ]
                            }
                        }
                    },
                    {
                        "$addFields": {
                            "_day_of_week": {
                                "$dayOfWeek": {
                                    "$dateFromString": {
                                        "dateString": "$date",
                                        "format": "%Y-%m-%d"
                                    }
                                }
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$group": {
                "_id": "$department",
                "employee_codes": {"$addToSet": "$emp_code"},

                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    },
                                    {
                                        "$in": [
                                            "$logs._day_of_week",
                                            [2, 3, 4, 5, 6]
                                        ]
                                    }
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$logs.half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },

                "work_hours_sum": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    },
                                    {"$ne": ["$logs.work_hours", None]}
                                ]
                            },
                            "$logs.work_hours",
                            0
                        ]
                    }
                },

                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    },
                                    {"$ne": ["$logs.work_hours", None]}
                                ]
                            },
                            1,
                            0
                        ]
                    }
                },

                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$logs.late_minutes", 0]},
                            1,
                            0
                        ]
                    }
                },

                "total_late_minutes": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$logs.late_minutes", 0]},
                            "$logs.late_minutes",
                            0
                        ]
                    }
                },

                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "LEAVE"]},
                            1,
                            0
                        ]
                    }
                },

                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "ON_DUTY"]},
                            1,
                            0
                        ]
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": {"$size": "$employee_codes"},
                "present_days": 1,
                "work_hours_sum": 1,
                "work_hours_count": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1
            }
        },
        {"$sort": {"department": 1}}
    ]

    rows = list(db.employees.aggregate(pipeline))
    items = []

    for row in rows:
        if row["headcount"] == 0:
            continue

        avg_work_hours = None

        if row["work_hours_count"] > 0:
            average = (
                Decimal(str(row["work_hours_sum"]))
                / Decimal(row["work_hours_count"])
            )

            avg_work_hours = float(
                average.quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP
                )
            )

        items.append({
            "department": row["department"],
            "headcount": row["headcount"],
            "present_days": float(row["present_days"]),
            "avg_work_hours": avg_work_hours,
            "late_count": row["late_count"],
            "total_late_minutes": row["total_late_minutes"],
            "leave_count": row["leave_count"],
            "on_duty_count": row["on_duty_count"]
        })

    return {
        "month": month,
        "items": items
    }


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None
):
    # Validate month
    try:
        if (
            len(month) != 7
            or datetime.strptime(month, "%Y-%m").strftime("%Y-%m") != month
        ):
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "month must use YYYY-MM format")

    if limit < 1 or limit > 50:
        raise HTTPException(422, "limit must be between 1 and 50")

    year, month_number = map(int, month.split("-"))
    month_start = f"{month}-01"

    if month_number == 12:
        next_month = f"{year + 1}-01-01"
    else:
        next_month = f"{year}-{month_number + 1:02d}-01"

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start,
                    "$lt": next_month
                },
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1}
            }
        },
        {
            "$match": {
                "total_late_minutes": {"$gt": 0}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"}
    ]

    if department is not None:
        pipeline.append({
            "$match": {
                "employee.department": department
            }
        })

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {
                    "total_late_minutes": -1
                },
                "output": {
                    "rank": {
                        "$rank": {}
                    }
                }
            }
        },
        {
            "$match": {
                "rank": {"$lte": limit}
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1
            }
        },
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1
            }
        }
    ])

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items
    }


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(alias="from"),
    to: str = Query()
):
    # Validate dates
    try:
        if (
            len(from_date) != 10
            or date.fromisoformat(from_date).isoformat() != from_date
            or len(to) != 10
            or date.fromisoformat(to).isoformat() != to
        ):
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "from and to must use YYYY-MM-DD format")

    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to)

    if end < start:
        raise HTTPException(422, "'to' must be on or after 'from'")

    day_count = (end - start).days + 1
    if day_count > 92:
        raise HTTPException(422, "Date range cannot exceed 92 days")

    if not db.employees.find_one({"department": department}):
        raise HTTPException(404, "department not found")

    pipeline = [
        {
            "$match": {
                "department": department,
                "joined_on": {"$lte": to}
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "emp_code": "$emp_code",
                    "joined_on": "$joined_on"
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$emp_code"]},
                                    {"$gte": ["$date", from_date]},
                                    {"$lte": ["$date", to]}
                                ]
                            }
                        }
                    },
                    {
                        "$addFields": {
                            "day": {
                                "$dateFromString": {
                                    "dateString": "$date",
                                    "format": "%Y-%m-%d"
                                }
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": {
                    "date": "$logs.day",
                    "emp_code": "$emp_code"
                },
                "joined_on": {"$first": "$joined_on"},
                "status": {"$first": "$logs.status"},
                "half_day": {"$first": "$logs.half_day"},
                "late_minutes": {"$first": "$logs.late_minutes"}
            }
        },
        {
            "$group": {
                "_id": "$_id.date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"]
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0
                        ]
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "day": "$_id",
                "present_count": 1,
                "late_count": 1
            }
        },
        {
            "$densify": {
                "field": "day",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [
                        datetime.combine(start, time.min),
                        datetime.combine(end + timedelta(days=1), time.min)
                    ]
                }
            }
        },
        {
            "$set": {
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
                "is_working_day": {
                    "$not": [
                        {
                            "$in": [
                                {"$dayOfWeek": "$day"},
                                [1, 7]
                            ]
                        }
                    ]
                },
                "headcount": {
                    "$size": {
                        "$filter": {
                            "input": {
                                "$literal": list(
                                    db.employees.find(
                                        {
                                            "department": department,
                                            "joined_on": {"$lte": to}
                                        },
                                        {"_id": 0, "joined_on": 1}
                                    )
                                )
                            },
                            "as": "employee",
                            "cond": {
                                "$lte": [
                                    "$$employee.joined_on",
                                    {
                                        "$dateToString": {
                                            "format": "%Y-%m-%d",
                                            "date": "$day"
                                        }
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]}
                            ]
                        },
                        {
                            "$round": [
                                {"$divide": ["$present_count", "$headcount"]},
                                4
                            ]
                        },
                        None
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"day": 1},
                "output": {
                    "moving_avg_raw": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]}
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$day"
                    }
                },
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_raw": 1
            }
        },
        {"$sort": {"date": 1}}
    ]

    rows = list(db.employees.aggregate(pipeline))

    items = []
    for row in rows:
        raw_avg = row.get("moving_avg_raw")
        moving_avg = None

        if raw_avg is not None:
            moving_avg = float(
                Decimal(str(raw_avg)).quantize(
                    Decimal("0.0001"),
                    rounding=ROUND_HALF_UP
                )
            )

        items.append({
            "date": row["date"],
            "is_working_day": row["is_working_day"],
            "headcount": row["headcount"],
            "present_count": row["present_count"],
            "late_count": row["late_count"],
            "attendance_rate": row.get("attendance_rate"),
            "moving_avg_7d": moving_avg
        })

    return {
        "department": department,
        "items": items
    }


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20
):
    allowed = [
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend"
    ]

    if endpoint not in allowed:
        raise HTTPException(422, "Invalid endpoint")

    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(422, "Invalid pagination parameters")

    if limit < 1 or limit > 50:
        raise HTTPException(422, "limit must be between 1 and 50")

    if month is not None:
        try:
            if (
                len(month) != 7
                or datetime.strptime(month, "%Y-%m").strftime("%Y-%m") != month
            ):
                raise ValueError()
        except ValueError:
            raise HTTPException(422, "month must use YYYY-MM format")

    if endpoint == "attendance_list":
        query = {}

        if emp_code:
            query["emp_code"] = emp_code
        if status:
            if status not in ["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]:
                raise HTTPException(422, "Invalid status")
            query["status"] = status

        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to

        skip = (page - 1) * page_size

        command = {
            "find": "attendance_logs",
            "filter": query,
            "sort": {"date": -1, "emp_code": 1},
            "skip": skip,
            "limit": page_size
        }
        collection_name = "attendance_logs"

    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                422,
                "emp_code and month are required for employee_monthly"
            )

        year, month_number = map(int, month.split("-"))
        next_month = (
            f"{year + 1}-01-01"
            if month_number == 12
            else f"{year}-{month_number + 1:02d}-01"
        )

        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {"$gte": f"{month}-01", "$lt": next_month}
                }
            },
            {"$sort": {"date": 1}}
        ]

        command = {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {}
        }
        collection_name = "attendance_logs"

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month is required")

        # Explain the employee-side query used by the summary.
        query = {"joined_on": {"$exists": True}}
        if department:
            query["department"] = department

        command = {
            "find": "employees",
            "filter": query
        }
        collection_name = "employees"

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required")

        year, month_number = map(int, month.split("-"))
        next_month = (
            f"{year + 1}-01-01"
            if month_number == 12
            else f"{year}-{month_number + 1:02d}-01"
        )

        pipeline = [
            {
                "$match": {
                    "date": {"$gte": f"{month}-01", "$lt": next_month},
                    "late_minutes": {"$gt": 0}
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"}
                }
            }
        ]

        command = {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {}
        }
        collection_name = "attendance_logs"

    else:
        if not department or not from_date or not to:
            raise HTTPException(
                422,
                "department, from and to are required for department_trend"
            )

        try:
            start = date.fromisoformat(from_date)
            end = date.fromisoformat(to)
            if start.isoformat() != from_date or end.isoformat() != to:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, "from and to must use YYYY-MM-DD format")

        if end < start or (end - start).days + 1 > 92:
            raise HTTPException(422, "Invalid trend date range")

        command = {
            "find": "employees",
            "filter": {
                "department": department,
                "joined_on": {"$lte": to}
            }
        }
        collection_name = "employees"

    try:
        result = db.command({
            "explain": command,
            "verbosity": "executionStats"
        })
    except PyMongoError as exc:
        raise HTTPException(
            500,
            f"MongoDB explain failed: {str(exc)}"
        )

    return {
        "endpoint": endpoint,
        "collection": collection_name,
        "explain": result
    }
