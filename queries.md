# Queries: Baseline vs Optimized by DeepseekV4Flash

---
# TPC-C Transaction Queries

## DELIVERY

### Baseline (2 queries per district)
```sql
-- Step 1: get new order id
SELECT NO_O_ID FROM NEW_ORDER
WHERE NO_D_ID = %s AND NO_W_ID = %s AND NO_O_ID > -1 LIMIT 1

-- Step 2: get customer id (separate query)
SELECT O_C_ID FROM ORDERS WHERE O_ID = %s AND O_D_ID = %s AND O_W_ID = %s
```

### Optimized — merged into 1 query
```sql
SELECT NO_O_ID, O_C_ID FROM NEW_ORDER
INNER JOIN ORDERS ON O_ID = NO_O_ID AND O_D_ID = NO_D_ID AND O_W_ID = NO_W_ID
WHERE NO_D_ID = %s AND NO_W_ID = %s LIMIT 1
```

### Shared queries (identical)
```sql
DELETE FROM NEW_ORDER WHERE NO_D_ID = %s AND NO_W_ID = %s AND NO_O_ID = %s

UPDATE ORDERS SET O_CARRIER_ID = %s WHERE O_ID = %s AND O_D_ID = %s AND O_W_ID = %s

UPDATE ORDER_LINE SET OL_DELIVERY_D = %s WHERE OL_O_ID = %s AND OL_D_ID = %s AND OL_W_ID = %s

SELECT SUM(OL_AMOUNT) FROM ORDER_LINE WHERE OL_O_ID = %s AND OL_D_ID = %s AND OL_W_ID = %s

UPDATE CUSTOMER SET C_BALANCE = C_BALANCE + %s WHERE C_ID = %s AND C_D_ID = %s AND C_W_ID = %s
```

## NEW_ORDER

### Baseline — per-row item/stock fetching
```sql
-- item lookup (per row)
SELECT I_PRICE, I_NAME, I_DATA FROM ITEM WHERE I_ID = %s

-- stock lookup (per row, with formatted district column)
SELECT S_QUANTITY, S_DATA, S_YTD, S_ORDER_CNT, S_REMOTE_CNT, S_DIST_%02d
FROM STOCK WHERE S_I_ID = %s AND S_W_ID = %s
```

### Optimized — batched item/stock fetching
```sql
-- item lookup (batched via IN)
SELECT I_ID, I_PRICE, I_NAME, I_DATA FROM ITEM WHERE I_ID IN (%s)

-- stock lookup (batched via tuple IN)
SELECT S_I_ID, S_W_ID, S_QUANTITY, S_DATA, S_YTD, S_ORDER_CNT, S_REMOTE_CNT, S_DIST_%02d
FROM STOCK WHERE (S_I_ID, S_W_ID) IN (%s)
```

### Shared queries (identical)
```sql
SELECT W_TAX FROM WAREHOUSE WHERE W_ID = %s

SELECT D_TAX, D_NEXT_O_ID FROM DISTRICT WHERE D_ID = %s AND D_W_ID = %s

UPDATE DISTRICT SET D_NEXT_O_ID = %s WHERE D_ID = %s AND D_W_ID = %s

SELECT C_DISCOUNT, C_LAST, C_CREDIT FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s

INSERT INTO ORDERS (O_ID, O_D_ID, O_W_ID, O_C_ID, O_ENTRY_D, O_CARRIER_ID, O_OL_CNT, O_ALL_LOCAL)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)

INSERT INTO NEW_ORDER (NO_O_ID, NO_D_ID, NO_W_ID) VALUES (%s, %s, %s)

INSERT INTO ORDER_LINE (OL_O_ID, OL_D_ID, OL_W_ID, OL_NUMBER, OL_I_ID, OL_SUPPLY_W_ID,
                        OL_DELIVERY_D, OL_QUANTITY, OL_AMOUNT, OL_DIST_INFO)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

## ORDER_STATUS (identical)

```sql
SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_BALANCE
FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s

SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_BALANCE
FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_LAST = %s ORDER BY C_FIRST

SELECT O_ID, O_CARRIER_ID, O_ENTRY_D
FROM ORDERS WHERE O_W_ID = %s AND O_D_ID = %s AND O_C_ID = %s ORDER BY O_ID DESC LIMIT 1

SELECT OL_SUPPLY_W_ID, OL_I_ID, OL_QUANTITY, OL_AMOUNT, OL_DELIVERY_D
FROM ORDER_LINE WHERE OL_W_ID = %s AND OL_D_ID = %s AND OL_O_ID = %s
```

## PAYMENT (identical)

```sql
SELECT W_NAME, W_STREET_1, W_STREET_2, W_CITY, W_STATE, W_ZIP
FROM WAREHOUSE WHERE W_ID = %s

UPDATE WAREHOUSE SET W_YTD = W_YTD + %s WHERE W_ID = %s

SELECT D_NAME, D_STREET_1, D_STREET_2, D_CITY, D_STATE, D_ZIP
FROM DISTRICT WHERE D_W_ID = %s AND D_ID = %s

UPDATE DISTRICT SET D_YTD = D_YTD + %s WHERE D_W_ID = %s AND D_ID = %s

SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_STREET_1, C_STREET_2, C_CITY, C_STATE, C_ZIP,
       C_PHONE, C_SINCE, C_CREDIT, C_CREDIT_LIM, C_DISCOUNT, C_BALANCE, C_YTD_PAYMENT,
       C_PAYMENT_CNT, C_DATA
FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s

SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_STREET_1, C_STREET_2, C_CITY, C_STATE, C_ZIP,
       C_PHONE, C_SINCE, C_CREDIT, C_CREDIT_LIM, C_DISCOUNT, C_BALANCE, C_YTD_PAYMENT,
       C_PAYMENT_CNT, C_DATA
FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_LAST = %s ORDER BY C_FIRST

UPDATE CUSTOMER SET C_BALANCE = %s, C_YTD_PAYMENT = %s, C_PAYMENT_CNT = %s, C_DATA = %s
WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s

UPDATE CUSTOMER SET C_BALANCE = %s, C_YTD_PAYMENT = %s, C_PAYMENT_CNT = %s
WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s

INSERT INTO HISTORY VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
```

## STOCK_LEVEL

### Baseline — old-style comma JOIN
```sql
SELECT COUNT(DISTINCT(OL_I_ID))
FROM ORDER_LINE, STOCK
WHERE OL_W_ID = %s
  AND OL_D_ID = %s
  AND OL_O_ID < %s
  AND OL_O_ID >= %s
  AND S_W_ID = %s
  AND S_I_ID = OL_I_ID
  AND S_QUANTITY < %s
```

### Optimized — modern EXISTS subquery
```sql
SELECT COUNT(DISTINCT(OL_I_ID))
FROM ORDER_LINE
WHERE OL_W_ID = %s
  AND OL_D_ID = %s
  AND OL_O_ID < %s
  AND OL_O_ID >= %s
  AND EXISTS (
    SELECT 1 FROM STOCK
    WHERE S_W_ID = %s
      AND S_I_ID = OL_I_ID
      AND S_QUANTITY < %s
  )
```

---

# Analytic Queries: Baseline vs Optimized

## Q1 — Customer Order History (last 20 orders)

### Baseline
```sql
SELECT o.o_id, o.o_entry_d, o.o_carrier_id,
       COUNT(ol.ol_number) AS item_count,
       SUM(ol.ol_amount) AS total_amount
FROM ORDERS o
JOIN ORDER_LINE ol
    ON o.o_w_id = ol.ol_w_id
   AND o.o_d_id = ol.ol_d_id
   AND o.o_id = ol.ol_o_id
WHERE o.o_w_id = %s
  AND o.o_d_id = %s
  AND o.o_c_id = %s
GROUP BY o.o_id, o.o_entry_d, o.o_carrier_id
ORDER BY o.o_entry_d DESC
LIMIT 20
```

### Optimized (same as baseline)
```sql
SELECT o.o_id, o.o_entry_d, o.o_carrier_id,
       COUNT(ol.ol_number) AS item_count,
       SUM(ol.ol_amount) AS total_amount
FROM ORDERS o
JOIN ORDER_LINE ol
    ON o.o_w_id = ol.ol_w_id
   AND o.o_d_id = ol.ol_d_id
   AND o.o_id = ol.ol_o_id
WHERE o.o_w_id = %s
  AND o.o_d_id = %s
  AND o.o_c_id = %s
GROUP BY o.o_id, o.o_entry_d, o.o_carrier_id
ORDER BY o.o_entry_d DESC
LIMIT 20
```

## Q2 — Customer Spending Summary (top 50)

### Baseline
```sql
SELECT c.c_id, c.c_first, c.c_last,
       COUNT(DISTINCT o.o_id) AS total_orders,
       SUM(ol.ol_amount) AS total_spent
FROM CUSTOMER c
JOIN ORDERS o
    ON c.c_w_id = o.o_w_id
   AND c.c_d_id = o.o_d_id
   AND c.c_id = o.o_c_id
JOIN ORDER_LINE ol
    ON o.o_w_id = ol.ol_w_id
   AND o.o_d_id = ol.ol_d_id
   AND o.o_id = ol.ol_o_id
WHERE c.c_w_id = %s
GROUP BY c.c_id, c.c_first, c.c_last
ORDER BY total_spent DESC
LIMIT 50
```

### Optimized — pre-aggregate ORDER_LINE before join
```sql
SELECT c.c_id, c.c_first, c.c_last,
       COUNT(*) AS total_orders,
       SUM(t.order_total) AS total_spent
FROM CUSTOMER c
JOIN ORDERS o
    ON c.c_w_id = o.o_w_id AND c.c_d_id = o.o_d_id AND c.c_id = o.o_c_id
JOIN (
    SELECT ol_w_id, ol_d_id, ol_o_id, SUM(ol_amount) AS order_total
    FROM ORDER_LINE
    GROUP BY ol_w_id, ol_d_id, ol_o_id
) t ON t.ol_w_id = o.o_w_id AND t.ol_d_id = o.o_d_id AND t.ol_o_id = o.o_id
WHERE c.c_w_id = %s
GROUP BY c.c_id, c.c_first, c.c_last
ORDER BY total_spent DESC
LIMIT 50
```

## Q3 — Top-Selling Items

### Baseline
```sql
SELECT i.i_id, i.i_name,
       COUNT(*) AS order_count,
       SUM(ol.ol_quantity) AS total_quantity,
       SUM(ol.ol_amount) AS revenue
FROM ITEM i
JOIN ORDER_LINE ol
    ON i.i_id = ol.ol_i_id
GROUP BY i.i_id, i.i_name
HAVING COUNT(*) > %s
ORDER BY revenue DESC
LIMIT 100
```

### Optimized — pre-aggregate ORDER_LINE, then join ITEM
```sql
SELECT i.i_id, i.i_name,
       t.order_count, t.total_quantity, t.revenue
FROM (
    SELECT ol_i_id,
           COUNT(*) AS order_count,
           SUM(ol_quantity) AS total_quantity,
           SUM(ol_amount) AS revenue
    FROM ORDER_LINE
    GROUP BY ol_i_id
    HAVING COUNT(*) > %s
    ORDER BY revenue DESC
    LIMIT 100
) t
JOIN ITEM i ON i.i_id = t.ol_i_id
ORDER BY t.revenue DESC
```

## Q4 — Warehouse Revenue

### Baseline — joins through WAREHOUSE → DISTRICT → ORDERS → ORDER_LINE
```sql
SELECT w.w_id,
       COUNT(DISTINCT o.o_id) AS total_orders,
       SUM(ol.ol_amount) AS revenue
FROM WAREHOUSE w
JOIN DISTRICT d ON w.w_id = d.d_w_id
JOIN ORDERS o ON d.d_w_id = o.o_w_id AND d.d_id = o.o_d_id
JOIN ORDER_LINE ol
    ON o.o_w_id = ol.ol_w_id
   AND o.o_d_id = ol.ol_d_id
   AND o.o_id = ol.ol_o_id
GROUP BY w.w_id
ORDER BY revenue DESC
```

### Optimized — direct GROUP BY on ORDER_LINE.ol_w_id (eliminates 3 joins)
```sql
SELECT ol_w_id AS w_id,
       COUNT(DISTINCT ol_o_id) AS total_orders,
       SUM(ol_amount) AS revenue
FROM ORDER_LINE
GROUP BY ol_w_id
ORDER BY revenue DESC
```

## Q5 — Customers With No Orders

### Baseline
```sql
SELECT c.c_w_id, c.c_d_id, c.c_id, c.c_first, c.c_last
FROM CUSTOMER c
WHERE NOT EXISTS (
    SELECT 1
    FROM ORDERS o
    WHERE o.o_w_id = c.c_w_id
      AND o.o_d_id = c.c_d_id
      AND o.o_c_id = c.c_id
)
```

### Optimized (same as baseline)
```sql
SELECT c.c_w_id, c.c_d_id, c.c_id, c.c_first, c.c_last
FROM CUSTOMER c
WHERE NOT EXISTS (
    SELECT 1 FROM ORDERS o
    WHERE o.o_w_id = c.c_w_id AND o.o_d_id = c.c_d_id AND o.o_c_id = c.c_id
)
```

## Q6 — Average Order Value per District

### Baseline
```sql
SELECT t.o_w_id, t.o_d_id, AVG(t.order_total) AS avg_order_value
FROM (
    SELECT o.o_w_id, o.o_d_id, o.o_id, SUM(ol.ol_amount) AS order_total
    FROM ORDERS o
    JOIN ORDER_LINE ol
        ON o.o_w_id = ol.ol_w_id
       AND o.o_d_id = ol.ol_d_id
       AND o.o_id = ol.ol_o_id
    GROUP BY o.o_w_id, o.o_d_id, o.o_id
) t
GROUP BY t.o_w_id, t.o_d_id
ORDER BY t.o_w_id, t.o_d_id
```

### Optimized — pre-aggregate ORDER_LINE directly (eliminates ORDERS join)
```sql
SELECT ol_w_id, ol_d_id, AVG(order_total) AS avg_order_value
FROM (
    SELECT ol_w_id, ol_d_id, ol_o_id, SUM(ol_amount) AS order_total
    FROM ORDER_LINE
    GROUP BY ol_w_id, ol_d_id, ol_o_id
) t
GROUP BY ol_w_id, ol_d_id
ORDER BY ol_w_id, ol_d_id
```

## Q7 — Warehouse Inventory Value

### Baseline
```sql
SELECT s.s_w_id,
       COUNT(*) AS total_items,
       SUM(s.s_quantity * i.i_price) AS inventory_value
FROM STOCK s
JOIN ITEM i ON s.s_i_id = i.i_id
GROUP BY s.s_w_id
ORDER BY inventory_value DESC
```

### Optimized (same as baseline)
```sql
SELECT s.s_w_id,
       COUNT(*) AS total_items,
       SUM(s.s_quantity * i.i_price) AS inventory_value
FROM STOCK s
JOIN ITEM i ON s.s_i_id = i.i_id
GROUP BY s.s_w_id
ORDER BY inventory_value DESC
```

## Q8 — High-Value Customers (above district average)

### Baseline — correlated subquery
```sql
SELECT cs.*
FROM (
    SELECT c.c_w_id, c.c_d_id, c.c_id, SUM(ol.ol_amount) AS total_spent
    FROM CUSTOMER c
    JOIN ORDERS o
        ON c.c_w_id = o.o_w_id AND c.c_d_id = o.o_d_id AND c.c_id = o.o_c_id
    JOIN ORDER_LINE ol
        ON o.o_w_id = ol.ol_w_id AND o.o_d_id = ol.ol_d_id AND o.o_id = ol.ol_o_id
    GROUP BY c.c_w_id, c.c_d_id, c.c_id
) cs
WHERE cs.total_spent > (
    SELECT AVG(total_spent)
    FROM (
        SELECT c2.c_w_id, c2.c_d_id, SUM(ol2.ol_amount) AS total_spent
        FROM CUSTOMER c2
        JOIN ORDERS o2
            ON c2.c_w_id = o2.o_w_id AND c2.c_d_id = o2.o_d_id AND c2.c_id = o2.o_c_id
        JOIN ORDER_LINE ol2
            ON o2.o_w_id = ol2.ol_w_id AND o2.o_d_id = ol2.ol_d_id AND o2.o_id = ol2.ol_o_id
        GROUP BY c2.c_w_id, c2.c_d_id, c2.c_id
    ) inner_cs
    WHERE inner_cs.c_w_id = cs.c_w_id AND inner_cs.c_d_id = cs.c_d_id
)
ORDER BY cs.c_w_id, cs.c_d_id, cs.total_spent DESC
```

### Optimized — JOIN with derived table instead of correlated subquery
```sql
SELECT cs.c_w_id, cs.c_d_id, cs.c_id, cs.total_spent
FROM (
    SELECT c.c_w_id, c.c_d_id, c.c_id, SUM(ol.ol_amount) AS total_spent
    FROM CUSTOMER c
    JOIN ORDERS o
        ON c.c_w_id = o.o_w_id AND c.c_d_id = o.o_d_id AND c.c_id = o.o_c_id
    JOIN ORDER_LINE ol
        ON o.o_w_id = ol.ol_w_id AND o.o_d_id = ol.ol_d_id AND o.o_id = ol.ol_o_id
    GROUP BY c.c_w_id, c.c_d_id, c.c_id
) cs
JOIN (
    SELECT c_w_id, c_d_id, AVG(total_spent) AS district_avg
    FROM (
        SELECT c2.c_w_id, c2.c_d_id, SUM(ol2.ol_amount) AS total_spent
        FROM CUSTOMER c2
        JOIN ORDERS o2
            ON c2.c_w_id = o2.o_w_id AND c2.c_d_id = o2.o_d_id AND c2.c_id = o2.o_c_id
        JOIN ORDER_LINE ol2
            ON o2.o_w_id = ol2.ol_w_id AND o2.o_d_id = ol2.ol_d_id AND o2.o_id = ol2.ol_o_id
        GROUP BY c2.c_w_id, c2.c_d_id, c2.c_id
    ) inner_cs
    GROUP BY c_w_id, c_d_id
) da ON da.c_w_id = cs.c_w_id AND da.c_d_id = cs.c_d_id
WHERE cs.total_spent > da.district_avg
ORDER BY cs.c_w_id, cs.c_d_id, cs.total_spent DESC
```

## Q9 — Top 100 Orders by Value

### Baseline — join then group then limit
```sql
SELECT o.o_w_id, o.o_d_id, o.o_id,
       c.c_first, c.c_last,
       COUNT(ol.ol_number) AS total_items,
       SUM(ol.ol_amount) AS total_amount
FROM ORDERS o
JOIN CUSTOMER c
    ON o.o_w_id = c.c_w_id AND o.o_d_id = c.c_d_id AND o.o_c_id = c.c_id
JOIN ORDER_LINE ol
    ON o.o_w_id = ol.ol_w_id AND o.o_d_id = ol.ol_d_id AND o.o_id = ol.ol_o_id
GROUP BY o.o_w_id, o.o_d_id, o.o_id, c.c_first, c.c_last
ORDER BY total_amount DESC
LIMIT 100
```

### Optimized — pre-aggregate ORDER_LINE, limit first, then join
```sql
SELECT o.o_w_id, o.o_d_id, o.o_id, c.c_first, c.c_last,
       t.total_items, t.total_amount
FROM (
    SELECT ol_w_id, ol_d_id, ol_o_id,
           COUNT(*) AS total_items,
           SUM(ol_amount) AS total_amount
    FROM ORDER_LINE
    GROUP BY ol_w_id, ol_d_id, ol_o_id
    ORDER BY total_amount DESC
    LIMIT 100
) t
JOIN ORDERS o ON o.o_w_id = t.ol_w_id AND o.o_d_id = t.ol_d_id AND o.o_id = t.ol_o_id
JOIN CUSTOMER c ON c.c_w_id = o.o_w_id AND c.c_d_id = o.o_d_id AND c.c_id = o.o_c_id
ORDER BY t.total_amount DESC
```

## Q10 — Supplier Analysis

### Baseline
```sql
SELECT ol.ol_supply_w_id,
       COUNT(*) AS supplied_lines,
       COUNT(DISTINCT ol.ol_o_id) AS affected_orders,
       SUM(ol.ol_quantity) AS total_quantity,
       SUM(ol.ol_amount) AS total_revenue
FROM ORDER_LINE ol
GROUP BY ol.ol_supply_w_id
ORDER BY total_revenue DESC
```

### Optimized (same as baseline)
```sql
SELECT ol_supply_w_id,
       COUNT(*) AS supplied_lines,
       COUNT(DISTINCT ol_o_id) AS affected_orders,
       SUM(ol_quantity) AS total_quantity,
       SUM(ol_amount) AS total_revenue
FROM ORDER_LINE
GROUP BY ol_supply_w_id
ORDER BY total_revenue DESC
```
