# KNOWLEDGE_BASE: DATABASE_QUERY_REWRITE_AND_OPTIMIZATION

## 1. COMBINING_QUERIES
*   **Definition**: Merging multiple isolated or sequential queries into a unified execution plan.
*   **Objective**: Minimize application-to-database network round-trips; maximize global query optimization visibility.
*   **Mechanisms**: 
    *   Consolidating linear Common Table Expressions (CTEs) or nested subqueries into single-pass table scans.
    *   Replacing downstream procedural loops with declarative set operations (`UNION ALL`, multi-key joins).
*   **TPC-C Example (PAYMENT — 3 queries → 1 join)**:
    ```python
    # FROM: 3 round-trips
    cur.execute("SELECT W_NAME, W_STREET_1, W_STREET_2, W_CITY, W_STATE, W_ZIP FROM WAREHOUSE WHERE W_ID = %s", [w_id])
    warehouse = cur.fetchone()
    cur.execute("SELECT D_NAME, D_STREET_1, D_STREET_2, D_CITY, D_STATE, D_ZIP FROM DISTRICT WHERE D_W_ID = %s AND D_ID = %s", [w_id, d_id])
    district = cur.fetchone()
    cur.execute("SELECT C_FIRST, C_MIDDLE, C_LAST, C_STREET_1, C_STREET_2, C_CITY, C_STATE, C_ZIP, C_PHONE, C_SINCE, C_CREDIT, C_CREDIT_LIM, C_DISCOUNT, C_BALANCE, C_YTD_PAYMENT, C_PAYMENT_CNT, C_DATA FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s", [c_w_id, c_d_id, c_id])
    customer = cur.fetchone()

    # TO: 1 round-trip — 3-table comma join
    cur.execute("""
        SELECT W_NAME, W_STREET_1, W_STREET_2, W_CITY, W_STATE, W_ZIP,
               D_NAME, D_STREET_1, D_STREET_2, D_CITY, D_STATE, D_ZIP,
               C_FIRST, C_MIDDLE, C_LAST, C_STREET_1, C_STREET_2, C_CITY,
               C_STATE, C_ZIP, C_PHONE, C_SINCE, C_CREDIT, C_CREDIT_LIM,
               C_DISCOUNT, C_BALANCE, C_YTD_PAYMENT, C_PAYMENT_CNT, C_DATA
        FROM WAREHOUSE, DISTRICT, CUSTOMER
        WHERE W_ID = %s AND D_W_ID = W_ID AND D_ID = %s
          AND C_W_ID = %s AND C_D_ID = %s AND C_ID = %s
    """, [w_id, d_id, c_w_id, c_d_id, c_id])
    warehouse, district, customer = cur.fetchone()
    ```

## 2. PREDICATE_PUSHDOWN
*   **Definition**: (Corrected from *impredicated pushdown*). Filtering data at the earliest possible stage in the execution pipeline, typically at the storage engine or data-source layer.
*   **Objective**: Minimize disk I/O, reduce memory consumption, and prevent unnecessary network payload transfer during joins/shuffles.
*   **Mechanisms**: Evaluating `WHERE` filter clauses during `TableScan` operations before the data is emitted to computing, joining, or aggregating operators.
*   **TPC-C Example (STOCK_LEVEL — filter ORDER_LINE before joining STOCK)**:
    ```python
    # FROM: filter applied after join
    cur.execute("""
        SELECT COUNT(DISTINCT S_I_ID)
        FROM ORDER_LINE, STOCK
        WHERE OL_W_ID = %s AND OL_D_ID = %s
          AND OL_O_ID < %s AND OL_O_ID >= %s
          AND S_W_ID = %s AND S_I_ID = OL_I_ID
          AND S_QUANTITY < %s
    """, [w_id, d_id, o_id, o_id - 20, w_id, threshold])

    # TO: derived table pushes ORDER_LINE filter before join
    cur.execute("""
        SELECT COUNT(DISTINCT S_I_ID)
        FROM (
            SELECT OL_I_ID FROM ORDER_LINE
            WHERE OL_W_ID = %s AND OL_D_ID = %s
              AND OL_O_ID < %s AND OL_O_ID >= %s
        ) OL
        JOIN STOCK ON S_I_ID = OL.OL_I_ID AND S_W_ID = %s
        WHERE S_QUANTITY < %s
    """, [w_id, d_id, o_id, o_id - 20, w_id, threshold])
    ```
    The derived table materializes only the qualifying `OL_I_ID` values before the JOIN, so STOCK scan only needs to match those IDs instead of joining all ORDER_LINE rows.

## 3. JOIN_ORDER_HINTS
*   **Definition**: Explicit developer directives embedded within a query to override the default cost-based optimizer (CBO) execution path.
*   **Objective**: Correct suboptimal query plans caused by stale, missing, or inaccurate database catalog statistics.
*   **Mechanisms**: Passing database-specific syntactic tokens (e.g., SQL comments like `/*+ STREAMJOIN() */` or Dataframe API methods like Spark's `.hint("broadcast")`) to force explicit join strategies (e.g., broadcast hash join vs. shuffle sort-merge join).
*   **TPC-C Example (NEW_ORDER — force WAREHOUSE → DISTRICT → CUSTOMER)**:
    ```python
    # FROM: optimizer chooses join order based on stats
    cur.execute("""
        SELECT W_TAX, D_TAX, C_DISCOUNT, C_LAST, C_CREDIT
        FROM WAREHOUSE, DISTRICT, CUSTOMER
        WHERE W_ID = %s AND D_W_ID = W_ID AND D_ID = %s
          AND C_W_ID = %s AND C_D_ID = %s AND C_ID = %s
    """, [w_id, d_id, w_id, d_id, c_id])

    # TO: STRAIGHT_JOIN forces known-efficient order
    cur.execute("""
        SELECT W_TAX, D_TAX, C_DISCOUNT, C_LAST, C_CREDIT
        FROM WAREHOUSE
        STRAIGHT_JOIN DISTRICT ON D_W_ID = W_ID AND D_ID = %s
        STRAIGHT_JOIN CUSTOMER ON C_W_ID = W_ID AND C_D_ID = D_ID AND C_ID = %s
        WHERE W_ID = %s
    """, [d_id, c_id, w_id])
    ```
    WAREHOUSE is the smallest table (~4 rows with 4 warehouses). Scanning it first and joining DISTRICT (10 per warehouse) and CUSTOMER (3000 per district) through the narrowest possible path avoids a full CUSTOMER scan.

## 4. SEPARATING_QUERIES
*   **Definition**: The intentional deconstruction of a highly complex, monolithic query into smaller, isolated intermediate steps.
*   **Objective**: Prevent resource exhaustion (OOM errors, disk spilling) and eliminate optimizer bottlenecks on deeply nested execution trees.
*   **Mechanisms**: 
    *   Materializing massive multi-join intermediate results into temporary tables or materialized views.
    *   Implementing windowed query splitting (chunking batch operations chronologically or by ID ranges) to alleviate long-lived transactional locks.
*   **TPC-C Example (DELIVERY — separate batch read from batch writes)**:
    ```python
    # FROM: monolithic query with subquery for row-per-district totals
    cur.execute("""
        SELECT NO_D_ID, NO_O_ID, ...,
               (SELECT SUM(OL_AMOUNT) FROM ORDER_LINE
                WHERE OL_O_ID = NO_O_ID AND OL_D_ID = NO_D_ID AND OL_W_ID = %s)
        FROM NEW_ORDER
        WHERE NO_W_ID = %s
        GROUP BY NO_D_ID
    """, [w_id, w_id])
    orders = cur.fetchall()
    for d_id, o_id, ... in orders:
        cur.execute("DELETE FROM NEW_ORDER WHERE NO_O_ID = %s AND NO_D_ID = %s AND NO_W_ID = %s", [o_id, d_id, w_id])
        ...

    # TO: separate into intermediate step + independent batch writes
    cur.execute("""
        SELECT NO_D_ID, NO_O_ID,
               COALESCE((SELECT SUM(OL_AMOUNT) FROM ORDER_LINE
                         WHERE OL_O_ID = NO_O_ID AND OL_D_ID = NO_D_ID AND OL_W_ID = %s), 0)
        FROM NEW_ORDER WHERE NO_W_ID = %s GROUP BY NO_D_ID
    """, [w_id, w_id])
    orders = cur.fetchall()
    d_ids = [r[0] for r in orders]
    o_ids = [r[1] for r in orders]
    ol_totals = [r[2] for r in orders]

    cur.execute("DELETE FROM NEW_ORDER WHERE (NO_D_ID, NO_O_ID) IN (%s) AND NO_W_ID = %s",
                _to_pairs(d_ids, o_ids) + [w_id])
    cur.execute("UPDATE ORDERS ... (O_ID, O_D_ID) IN (%s) AND O_W_ID = %s", ...)
    ```
    Separating the SUM(OL_AMOUNT) subquery into the initial batch read keeps write queries simple and avoids re-executing the correlated subquery for each batch write.

## 5. CONCURRENCY
*   **Definition**: Structuring queries or application workloads to maximize parallel hardware execution.
*   **Objective**: Maxrage horizontal/vertical scaling to decrease overall latency and elevate transaction throughput.
*   **Mechanisms**:
    *   **Intra-query Parallelism**: Rewriting queries to allow the engine to partition data blocks across separate CPU cores or cluster worker nodes.
    *   **Decoupled Execution**: Splitting single-thread sequential code blocks into topologically independent queries that run asynchronously when free of data-lineage dependencies.
*   **TPC-C Example (NEW_ORDER — sequential per-item loop → set-based batch)**:
    ```python
    # FROM: N sequential round-trips (blocking, single-threaded)
    all_items = []
    for i in range(ol_cnt):
        cur.execute("SELECT I_PRICE, I_NAME, I_DATA FROM ITEM WHERE I_ID = %s", [i_ids[i]])
        all_items.append(cur.fetchone())
        cur.execute("""SELECT S_QUANTITY, S_YTD, S_ORDER_CNT, S_REMOTE_CNT, S_DATA, S_DIST_%02d
                       FROM STOCK WHERE S_I_ID = %s AND S_W_ID = %s""" % (d_id,), [i_ids[i], i_w_ids[i]])
        all_stock.append(cur.fetchone())

    # TO: 2 batch queries — MySQL can scan ITEM and STOCK indices in parallel
    placeholders = ','.join(['%s'] * ol_cnt)
    cur.execute(f"SELECT I_ID, I_PRICE, I_NAME, I_DATA FROM ITEM WHERE I_ID IN ({placeholders})",
                i_ids)
    item_rows = {r[0]: r for r in cur.fetchall()}
    stock_pairs = list(zip(i_ids, i_w_ids))
    stock_placeholders = ','.join(['(%s,%s)'] * len(stock_pairs))
    cur.execute(f"""SELECT S_I_ID, S_W_ID, S_QUANTITY, S_YTD, S_ORDER_CNT, S_REMOTE_CNT, S_DATA,
                           S_DIST_{d_id:02d}
                    FROM STOCK WHERE (S_I_ID, S_W_ID) IN ({stock_placeholders})""",
                [v for pair in stock_pairs for v in pair])
    stock_rows = {(r[0], r[1]): r for r in cur.fetchall()}
    ```
    Replacing N individual queries with two set-based `IN (...)` queries eliminates N-2 round-trips and allows MySQL's executor to scan ITEM and STOCK in parallel across multiple cores.