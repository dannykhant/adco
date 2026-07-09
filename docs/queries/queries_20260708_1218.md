# Queries: Baseline vs Optimized

## How to read this

Each transaction section shows:
- **Baseline**: the set of SQL queries the application issues (fragmented from an app POV)
- **Optimized**: the rewritten SQL with fewer round-trips
- **Rationale**: which fragmentations were merged and why

The metric that matters: **number of database round-trips per transaction call**.

---

# TPC-C Transaction Queries

## DELIVERY

### Application flow

Loop over 10 districts. Per district:
1. `SELECT NO_O_ID FROM NEW_ORDER` → get a pending order
2. `SELECT O_C_ID FROM ORDERS` → find the customer who placed it
3. `SELECT SUM(OL_AMOUNT) FROM ORDER_LINE` → total value
4. `DELETE FROM NEW_ORDER` → remove from queue
5. `UPDATE ORDERS SET O_CARRIER_ID` → mark delivered
6. `UPDATE ORDER_LINE SET OL_DELIVERY_D` → set delivery timestamp
7. `UPDATE CUSTOMER SET C_BALANCE` → add the amount to balance

**Fragmentation**: 10 districts × 7 queries = **70 round-trips** per call.

### Baseline (per district, 7 queries)

```sql
SELECT NO_O_ID FROM NEW_ORDER
WHERE NO_D_ID = %s AND NO_W_ID = %s AND NO_O_ID > -1 LIMIT 1;

SELECT O_C_ID FROM ORDERS
WHERE O_ID = %s AND O_D_ID = %s AND O_W_ID = %s;

SELECT SUM(OL_AMOUNT) FROM ORDER_LINE
WHERE OL_O_ID = %s AND OL_D_ID = %s AND OL_W_ID = %s;

DELETE FROM NEW_ORDER WHERE NO_D_ID = %s AND NO_W_ID = %s AND NO_O_ID = %s;

UPDATE ORDERS SET O_CARRIER_ID = %s
WHERE O_ID = %s AND O_D_ID = %s AND O_W_ID = %s;

UPDATE ORDER_LINE SET OL_DELIVERY_D = %s
WHERE OL_O_ID = %s AND OL_D_ID = %s AND OL_W_ID = %s;

UPDATE CUSTOMER SET C_BALANCE = C_BALANCE + %s
WHERE C_ID = %s AND C_D_ID = %s AND C_W_ID = %s;
```

### Optimized (1 batch + 1 SELECT + 4 writes per district = **~26-51 round-trips**)

**Optimization 1** — Fetch one pending order per district using `GROUP BY NO_D_ID` with `MIN(NO_O_ID)`, joined to ORDERS for customer ID. Returns at most 10 rows (one per district).

The `SUM(OL_AMOUNT)` is NOT inlined — it stays as a separate query per order because a correlated subquery would run for every row (including irrelevant ones) and can't be limited.

```sql
SELECT n.NO_D_ID, n.NO_O_ID, o.O_C_ID
FROM (SELECT NO_D_ID, MIN(NO_O_ID) AS NO_O_ID
      FROM NEW_ORDER
      WHERE NO_W_ID = %s AND NO_O_ID > -1
      GROUP BY NO_D_ID) n
JOIN ORDERS o ON o.O_ID = n.NO_O_ID
             AND o.O_D_ID = n.NO_D_ID
             AND o.O_W_ID = %s;
```

Then per result row (1 SELECT + 4 writes):

```sql
SELECT SUM(OL_AMOUNT) FROM ORDER_LINE
WHERE OL_O_ID = %s AND OL_D_ID = %s AND OL_W_ID = %s;

DELETE FROM NEW_ORDER WHERE NO_D_ID = %s AND NO_W_ID = %s AND NO_O_ID = %s;

UPDATE ORDERS SET O_CARRIER_ID = %s
WHERE O_ID = %s AND O_D_ID = %s AND O_W_ID = %s;

UPDATE ORDER_LINE SET OL_DELIVERY_D = %s
WHERE OL_O_ID = %s AND OL_D_ID = %s AND OL_W_ID = %s;

UPDATE CUSTOMER SET C_BALANCE = C_BALANCE + %s
WHERE C_ID = %s AND C_D_ID = %s AND C_W_ID = %s;
```

| | Baseline | Optimized |
|---|---|---|
| District loop queries | 10 × 7 = 70 | 1 + N × 5 |
| Typical (5-10 districts have orders) | 70 | **~26-51** |

`ponytail:` sumOLAmount stays separate — a correlated subquery in the batch would execute for every row returned by the GROUP BY subquery (still max 10, so not the problem), but the real issue was the original attempt used no GROUP BY/LIMIT, returning thousands of rows × the subquery = catastrophic. The writes per district are vectorizable but not worth the complexity for max 10 rows.

---

## NEW_ORDER

### Application flow

1. For each item (N = 5-15): `SELECT I_PRICE, I_NAME, I_DATA FROM ITEM` **← per-item loop #1**
2. `SELECT W_TAX FROM WAREHOUSE`
3. `SELECT D_TAX, D_NEXT_O_ID FROM DISTRICT`
4. `SELECT C_DISCOUNT, C_LAST, C_CREDIT FROM CUSTOMER`
5. `UPDATE DISTRICT SET D_NEXT_O_ID`
6. `INSERT INTO ORDERS`
7. `INSERT INTO NEW_ORDER`
8. For each item: `SELECT S_QUANTITY, ... FROM STOCK` **← per-item loop #2**
9. For each item: `UPDATE STOCK SET ...`
10. For each item: `INSERT INTO ORDER_LINE`

**Fragmentation**: Two per-item SELECT loops (N queries each), three independent SELECTs (warehouse, district, customer), and three per-item write loops.

### Baseline (N items, N = 5-15)

```sql
-- Per-item loop #1: N queries
SELECT I_PRICE, I_NAME, I_DATA FROM ITEM WHERE I_ID = %s;

-- Independent SELECTs: 3 queries
SELECT W_TAX FROM WAREHOUSE WHERE W_ID = %s;
SELECT D_TAX, D_NEXT_O_ID FROM DISTRICT WHERE D_ID = %s AND D_W_ID = %s;
SELECT C_DISCOUNT, C_LAST, C_CREDIT FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s;

-- 3 writes
UPDATE DISTRICT SET D_NEXT_O_ID = %s WHERE D_ID = %s AND D_W_ID = %s;
INSERT INTO ORDERS (...) VALUES (...);
INSERT INTO NEW_ORDER (...) VALUES (...);

-- Per-item loop #2: N queries
SELECT S_QUANTITY, S_DATA, S_YTD, S_ORDER_CNT, S_REMOTE_CNT, S_DIST_%02d
FROM STOCK WHERE S_I_ID = %s AND S_W_ID = %s;

-- Per-item loop #3: N writes
UPDATE STOCK SET S_QUANTITY = %s, ... WHERE S_I_ID = %s AND S_W_ID = %s;

-- Per-item loop #4: N writes
INSERT INTO ORDER_LINE (...) VALUES (...);
```

**Total**: 3 + 2N SELECTs + 3 + 2N writes = **6 + 4N round-trips** (26-66 for N=5-15).

### Optimized (3 SELECTs + 3 writes + 2 batch ops = **8 round-trips**)

**Optimization 1** — Batch all ITEM lookups into one `IN` query.
**Optimization 2** — Batch all STOCK lookups into one `(S_I_ID, S_W_ID) IN (...)` query.
**Optimization 3** — Merge independent SELECTs (warehouse, district, customer) via a single multi-table query.
**Optimization 4** — Batch STOCK updates using `CASE` expression (single UPDATE with conditional branches).
**Optimization 5** — Batch ORDER_LINE inserts using multi-row `VALUES`.

```sql
-- Batch item lookup: 1 query instead of N
SELECT I_ID, I_PRICE, I_NAME, I_DATA FROM ITEM
WHERE I_ID IN (%s, %s, ..., %s);

-- Merge independent SELECTs: 1 query instead of 3
SELECT w.W_TAX, d.D_TAX, d.D_NEXT_O_ID, c.C_DISCOUNT, c.C_LAST, c.C_CREDIT
FROM WAREHOUSE w
JOIN DISTRICT d ON d.D_W_ID = w.W_ID AND d.D_ID = %s
JOIN CUSTOMER c ON c.C_W_ID = w.W_ID AND c.C_D_ID = %s AND c.C_ID = %s
WHERE w.W_ID = %s;

-- 3 writes
UPDATE DISTRICT SET D_NEXT_O_ID = %s WHERE D_ID = %s AND D_W_ID = %s;
INSERT INTO ORDERS (...) VALUES (...);
INSERT INTO NEW_ORDER (...) VALUES (...);

-- Batch stock lookup: 1 query instead of N
SELECT S_I_ID, S_W_ID, S_QUANTITY, S_DATA, S_YTD, S_ORDER_CNT, S_REMOTE_CNT, S_DIST_%02d
FROM STOCK WHERE (S_I_ID, S_W_ID) IN ((%s,%s), (%s,%s), ...);

-- Batch stock update: 1 query instead of N
UPDATE STOCK SET
  S_QUANTITY = CASE
    WHEN (S_I_ID, S_W_ID) = (%s,%s) THEN %s
    ...
    ELSE S_QUANTITY
  END,
  S_YTD = CASE ... END,
  S_ORDER_CNT = CASE ... END,
  S_REMOTE_CNT = CASE ... END
WHERE (S_I_ID = %s AND S_W_ID = %s) OR ...;

-- Batch order line insert: 1 query instead of N
INSERT INTO ORDER_LINE (...) VALUES
  (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s),
  (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s),
  ...;
```

| | Baseline | Optimized |
|---|---|---|
| SELECT round-trips | 3 + 2N | 1 + 1 + 1 = 3 |
| Write round-trips | 3 + 2N | 3 + 1 + 1 = 5 |
| **Total** | **6 + 4N** | **8** (constant, regardless of N) |

---

## ORDER_STATUS

### Application flow (by-c_id path)

1. `SELECT C_ID, C_FIRST, ... FROM CUSTOMER` → customer info
2. `SELECT O_ID, O_CARRIER_ID, O_ENTRY_D FROM ORDERS` → last order
3. `SELECT OL_SUPPLY_W_ID, ... FROM ORDER_LINE` → order lines (conditional)

**Fragmentation**: Queries 1 and 2 operate on the same `(w_id, d_id, c_id)` key but hit different tables. They could be merged via `LEFT JOIN`.

### Baseline

```sql
-- Query 1
SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_BALANCE
FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s;

-- Query 2
SELECT O_ID, O_CARRIER_ID, O_ENTRY_D
FROM ORDERS WHERE O_W_ID = %s AND O_D_ID = %s AND O_C_ID = %s
ORDER BY O_ID DESC LIMIT 1;

-- Query 3 (conditional on query 2 having a result)
SELECT OL_SUPPLY_W_ID, OL_I_ID, OL_QUANTITY, OL_AMOUNT, OL_DELIVERY_D
FROM ORDER_LINE WHERE OL_W_ID = %s AND OL_D_ID = %s AND OL_O_ID = %s;
```

### Optimized

**Optimization** — Merge customer lookup + last order into one `LEFT JOIN` (customer exists even if no orders).

```sql
-- Merged query 1+2: 1 query instead of 2
SELECT c.C_ID, c.C_FIRST, c.C_MIDDLE, c.C_LAST, c.C_BALANCE,
       o.O_ID, o.O_CARRIER_ID, o.O_ENTRY_D
FROM CUSTOMER c
LEFT JOIN ORDERS o
  ON o.O_W_ID = c.C_W_ID AND o.O_D_ID = c.C_D_ID AND o.O_C_ID = c.C_ID
WHERE c.C_W_ID = %s AND c.C_D_ID = %s AND c.C_ID = %s
ORDER BY o.O_ID DESC LIMIT 1;

-- Query 3 (conditional — only if O_ID IS NOT NULL)
SELECT OL_SUPPLY_W_ID, OL_I_ID, OL_QUANTITY, OL_AMOUNT, OL_DELIVERY_D
FROM ORDER_LINE WHERE OL_W_ID = %s AND OL_D_ID = %s AND OL_O_ID = %s;
```

| | Baseline | Optimized |
|---|---|---|
| c_id path | 2-3 queries | **1-2 queries** |

`ponytail:` The by-c_last path can't merge because we need the median customer's `c_id` before we can query their last order. Not worth restructuring for one path.

---

## PAYMENT

### Application flow

1. `SELECT C_ID, C_FIRST, ... FROM CUSTOMER` → find customer (by id or last name)
2. `SELECT W_NAME, ... FROM WAREHOUSE` → warehouse info
3. `SELECT D_NAME, ... FROM DISTRICT` → district info
4. `UPDATE WAREHOUSE SET W_YTD` → update warehouse YTD
5. `UPDATE DISTRICT SET D_YTD` → update district YTD
6. `UPDATE CUSTOMER SET C_BALANCE, ...` → update customer balance
7. `INSERT INTO HISTORY` → record the payment

**Fragmentation**: Queries 2 and 3 are independent SELECTs that can be merged (they share `w_id` and `d_id`).

### Baseline

```sql
-- Query 1
SELECT C_ID, C_FIRST, ..., C_BALANCE, C_YTD_PAYMENT, C_PAYMENT_CNT, C_DATA
FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s;

-- Query 2
SELECT W_NAME, W_STREET_1, W_STREET_2, W_CITY, W_STATE, W_ZIP
FROM WAREHOUSE WHERE W_ID = %s;

-- Query 3
SELECT D_NAME, D_STREET_1, D_STREET_2, D_CITY, D_STATE, D_ZIP
FROM DISTRICT WHERE D_W_ID = %s AND D_ID = %s;

-- Queries 4–7 are scalar writes, keep as-is
```

### Optimized

**Optimization** — Merge warehouse + district lookup into one `JOIN` query (they share the same `w_id` and `d_id`).

```sql
-- Query 1 (unchanged — depends on c_id/c_last)
SELECT ... FROM CUSTOMER WHERE ...;

-- Merged query 2+3: 1 query instead of 2
SELECT w.W_NAME, w.W_STREET_1, w.W_STREET_2, w.W_CITY, w.W_STATE, w.W_ZIP,
       d.D_NAME, d.D_STREET_1, d.D_STREET_2, d.D_CITY, d.D_STATE, d.D_ZIP
FROM WAREHOUSE w
JOIN DISTRICT d ON d.D_W_ID = w.W_ID AND d.D_ID = %s
WHERE w.W_ID = %s;

-- Queries 4–7: writes, keep as-is
```

| | Baseline | Optimized |
|---|---|---|
| SELECT round-trips | 3 | **2** |
| Total round-trips | 7 | **6** |

`ponytail:` Could also merge customer lookup with warehouse+district when searching by `c_id` (known upfront), but not when searching by `c_last` (need median first). Keeping it simple.

---

## STOCK_LEVEL

### Application flow

1. `SELECT D_NEXT_O_ID FROM DISTRICT` → get the next OID
2. `SELECT COUNT(DISTINCT(OL_I_ID)) FROM ORDER_LINE, STOCK WHERE ...` → threshold check

**Fragmentation**: Query 1 is only used to compute the OID bounds for query 2. Could be inlined.

### Baseline

```sql
-- Query 1
SELECT D_NEXT_O_ID FROM DISTRICT WHERE D_W_ID = %s AND D_ID = %s;

-- Query 2 (old-style comma join)
SELECT COUNT(DISTINCT(OL_I_ID))
FROM ORDER_LINE, STOCK
WHERE OL_W_ID = %s AND OL_D_ID = %s
  AND OL_O_ID < %s AND OL_O_ID >= %s
  AND S_W_ID = %s AND S_I_ID = OL_I_ID AND S_QUANTITY < %s;
```

### Optimized

**Optimization 1** — Merge both queries into one by inlining the `D_NEXT_O_ID` subquery.
**Optimization 2** — Use `EXISTS` subquery instead of comma join (avoids cartesian intermediate).

```sql
-- Merged query: 1 query instead of 2, with EXISTS instead of comma join
SELECT COUNT(DISTINCT(OL_I_ID))
FROM ORDER_LINE
WHERE OL_W_ID = %s AND OL_D_ID = %s
  AND OL_O_ID < (SELECT D_NEXT_O_ID FROM DISTRICT WHERE D_W_ID = %s AND D_ID = %s)
  AND OL_O_ID >= (SELECT D_NEXT_O_ID - 20 FROM DISTRICT WHERE D_W_ID = %s AND D_ID = %s)
  AND EXISTS (
    SELECT 1 FROM STOCK
    WHERE S_W_ID = %s AND S_I_ID = OL_I_ID AND S_QUANTITY < %s
  );
```

| | Baseline | Optimized |
|---|---|---|
| Round-trips | 2 | **1** |

`ponytail:` Duplicates the DISTRICT subquery. A CTE avoids the duplication on MySQL 8+ but the simplicity loss isn't worth it for a one-off.

---

# Summary

## Round-trip reduction (transaction queries)

| Transaction | Baseline RTs | Optimized RTs | Factor |
|---|---|---|---|
| DELIVERY (10 districts) | 70 | ~26-51 | ~1.4-2.7× |
| NEW_ORDER (N=10 avg) | 46 | 8 | **~6×** |
| ORDER_STATUS (by c_id) | 3 | 2 | 1.5× |
| PAYMENT | 7 | 6 | 1.2× |
| STOCK_LEVEL | 2 | 1 | **2×** |

## Techniques used

| Technique | Applied to | Impact |
|---|---|---|
| Batch SELECT via IN clause | NEW_ORDER item/stock lookups | N→1 per loop |
| Batch UPDATE via CASE | NEW_ORDER stock updates | N→1 per loop |
| Batch INSERT via multi-row VALUES | NEW_ORDER order lines | N→1 per loop |
| Merge consecutive SELECTs via JOIN | DELIVERY, ORDER_STATUS, PAYMENT | 2→1 |
| Inline subquery to eliminate query | STOCK_LEVEL | 2→1 |
