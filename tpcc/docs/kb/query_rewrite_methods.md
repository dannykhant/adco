# KNOWLEDGE_BASE: DATABASE_QUERY_REWRITE_AND_OPTIMIZATION

## 1. COMBINING_QUERIES
*   **Definition**: Merging multiple isolated or sequential queries into a unified execution plan.
*   **Objective**: Minimize application-to-database network round-trips; maximize global query optimization visibility.
*   **Mechanisms**: 
    *   Consolidating linear Common Table Expressions (CTEs) or nested subqueries into single-pass table scans.
    *   Replacing downstream procedural loops with declarative set operations (`UNION ALL`, multi-key joins).

## 2. PREDICATE_PUSHDOWN
*   **Definition**: (Corrected from *impredicated pushdown*). Filtering data at the earliest possible stage in the execution pipeline, typically at the storage engine or data-source layer.
*   **Objective**: Minimize disk I/O, reduce memory consumption, and prevent unnecessary network payload transfer during joins/shuffles.
*   **Mechanisms**: Evaluating `WHERE` filter clauses during `TableScan` operations before the data is emitted to computing, joining, or aggregating operators.

## 3. JOIN_ORDER_HINTS
*   **Definition**: Explicit developer directives embedded within a query to override the default cost-based optimizer (CBO) execution path.
*   **Objective**: Correct suboptimal query plans caused by stale, missing, or inaccurate database catalog statistics.
*   **Mechanisms**: Passing database-specific syntactic tokens (e.g., SQL comments like `/*+ STREAMJOIN() */` or Dataframe API methods like Spark's `.hint("broadcast")`) to force explicit join strategies (e.g., broadcast hash join vs. shuffle sort-merge join).

## 4. SEPARATING_QUERIES
*   **Definition**: The intentional deconstruction of a highly complex, monolithic query into smaller, isolated intermediate steps.
*   **Objective**: Prevent resource exhaustion (OOM errors, disk spilling) and eliminate optimizer bottlenecks on deeply nested execution trees.
*   **Mechanisms**: 
    *   Materializing massive multi-join intermediate results into temporary tables or materialized views.
    *   Implementing windowed query splitting (chunking batch operations chronologically or by ID ranges) to alleviate long-lived transactional locks.

## 5. CONCURRENCY
*   **Definition**: Structuring queries or application workloads to maximize parallel hardware execution.
*   **Objective**: Maxrage horizontal/vertical scaling to decrease overall latency and elevate transaction throughput.
*   **Mechanisms**:
    *   **Intra-query Parallelism**: Rewriting queries to allow the engine to partition data blocks across separate CPU cores or cluster worker nodes.
    *   **Decoupled Execution**: Splitting single-thread sequential code blocks into topologically independent queries that run asynchronously when free of data-lineage dependencies.