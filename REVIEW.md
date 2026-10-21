#	Where (function / line)	What is wrong	How you'd notice it (test, input, or symptom)	How you fixed it
# Code Review Report

## 1. create_indexes / startup

**Issue:** An invalid startup hook prevents the app from starting; missing indexes weaken uniqueness and query performance.

**Test:** Start from a clean clone, call `GET /health`, and inspect MongoDB indexes.

**Fix:** Use a valid FastAPI startup hook and create required indexes idempotently.

---

## 2. MongoDB Configuration

**Issue:** The app must use environment-provided `MONGO_URI` and `MONGO_DB`, rather than relying on hard-coded connection details.

**Test:** Set test environment values and verify which database is selected.

**Fix:** Read both values from environment variables, allowing injected values to take priority.

---

## 3. compute_late_minutes — Overnight Shifts

**Issue:** Comparing punch-in to shift start on the same date mishandles overnight shifts.

**Test:** Test a shift crossing midnight and a punch-in after midnight.

**Fix:** Resolve the correct shift-start date before calculating lateness.

---

## 4. compute_late_minutes — Grace Period

**Issue:** The 10-minute grace boundary and whole-minute flooring must match the contract exactly.

**Test:** Test exactly 10 minutes late and 10 minutes plus a few seconds.

**Fix:** Apply the strict `> 10 minutes` rule and floor whole minutes.

---

## 5. compute_work_hours

**Issue:** Built-in `round()` uses ties-to-even and may disagree with the required `ROUND_HALF_UP` rounding.

**Test:** Test a duration whose hour value is halfway between hundredths.

**Fix:** Use `Decimal` with `ROUND_HALF_UP`.

---

## 6. compute_overtime

**Issue:** Using shift end on the attendance date mishandles overnight shifts. Overtime must only count when it reaches 30 minutes.

**Test:** Test overnight shift end and punch-outs 29 and 30 minutes later.

**Fix:** Resolve the correct shift-end date, floor minutes, and apply the 30-minute minimum.

---

## 7. POST /attendance/punch-in

**Issue:** An existence check followed by insert is not safe against simultaneous requests.

**Test:** Send concurrent punch-ins for the same employee/date.

**Fix:** Enforce a unique `(emp_code, date)` index and translate duplicate-key errors to `409 Conflict`.

---

## 8. POST /attendance/punch-out

**Issue:** Separate read/check/update operations can allow two punch-outs to race or select the wrong day's record.

**Test:** Send simultaneous punch-outs and test an employee with multiple attendance dates.

**Fix:** Use the intended employee/date and an atomic conditional update. Return `409 Conflict` to a losing request.

---

## 9. GET /employees and GET /attendance

**Issue:** Pagination must validate bounds and calculate the total using the same filters as the returned page.

**Test:** Try `page=0`, `page_size=101`, and a filter with no matches.

**Fix:** Validate bounds and apply filters, sorting, skip, and limit in MongoDB.

---

## 10. Timestamp Validation / Conversion

**Issue:** Accepting seconds, strings, floats, or out-of-range values can create incorrect instants. API instants must be epoch milliseconds.

**Test:** Submit invalid timestamp types or a seconds-based timestamp.

**Fix:** Validate integer epoch milliseconds and convert between API values and UTC BSON datetimes at the boundary.

---

## 11. PATCH /attendance/{emp_code}/{date}

**Issue:** Regularization must recompute derived fields and preserve history during concurrent corrections.

**Test:** Update a record and inspect derived values/history; then send concurrent updates.

**Fix:** Recompute derived fields and use a conditional atomic update that appends history. Reject stale updates.

---

## 12. Analytics Pipelines

**Issue:** Aggregations can miss employees without logs or mishandle join dates, weekdays, status values, and half-days.

**Test:** Test a no-log employee, mid-month joiner, weekend record, and half-day.

**Fix:** Aggregate in MongoDB and apply the contract's employee/date/status rules.

---

## 13. Late Leaderboard

**Issue:** Ranking by array position or limiting before ranking gives incorrect competition ranks and cutoff tie handling.

**Test:** Use totals such as `100, 80, 80, 60` with a limit cutting through the tie.

**Fix:** Use competition ranking (`1, 2, 2, 4`), sort ties by `emp_code`, and apply the limit after ranking.

---

## 14. Department Trend

**Issue:** Omitting days without records loses weekends/gaps. Computing the moving average in Python violates the pipeline requirement.

**Test:** Request a range containing weekends and dates with no logs.

**Fix:** Densify dates in MongoDB and calculate the moving average in the aggregation pipeline.

---

## 15. Admin Explain Endpoint

**Issue:** Explaining a simplified query does not prove that the actual endpoint's filters, sort, and pagination use indexes.

**Test:** Compare query shapes and inspect `IXSCAN`/`COLLSCAN` in execution stats.

**Fix:** Build explain commands from the same relevant filters and query/pipeline as the endpoint.

---

## Summary

- Total issues documented: 15
- Focus areas: Startup configuration, MongoDB indexes, concurrency, attendance calculations, pagination, timestamp validation, analytics, ranking, and query performance.
- Verification: Run the specified test cases and inspect API responses, MongoDB records, indexes, and execution statistics.



**Reviewed and not considered defects**
- A list endpoint returning 200 OK with an empty list is valid when no records match; it differs from a missing employee in an operation that requires that employee.
- MongoDB's internal _id may exist in storage, but it should not be used as the public employee identity or attendance-record identifier.
- Storing instants as UTC BSON datetimes is correct; epoch-millisecond conversion and IST attendance-date rules belong at the API/business-rule boundary.
- A unique compound index on (emp_code, date) is intentional because an attendance record is identified by that pair and the index also supports lookups beginning with emp_code.