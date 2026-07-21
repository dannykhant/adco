#!/usr/bin/env python
"""
AST-based static checker for generated TPC-C drivers.

Analyses the driver source code using Python AST + source text patterns
to detect likely correctness issues without executing any code.

Usage:
    uv run python tests/ast_checker.py --driver tpcc/drivers/gemini_xxxdriver.py
    uv run python tests/ast_checker.py --auto       # latest gemini driver
    uv run python tests/ast_checker.py --driver ... --verbose
    uv run python tests/ast_checker.py --driver ... --json  # machine-readable
"""

import ast
import os
import sys
import glob
import argparse
import json
import sqlparse
import textwrap
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TRANSACTIONS = ["DELIVERY", "NEW_ORDER", "ORDER_STATUS", "PAYMENT", "STOCK_LEVEL"]
METHODS = ["doDelivery", "doNewOrder", "doOrderStatus", "doPayment", "doStockLevel"]
HELPERS = [
    "_batch_items", "_batch_stock_info", "_batch_update_stock",
    "_batch_insert_order_lines", "_batch_delete_new_orders",
    "_batch_update_orders", "_batch_update_order_lines", "_batch_update_customers",
]


class Check:
    def __init__(self, name: str):
        self.name = name
        self.ok = True
        self.errors: list[str] = []

    def fail(self, msg: str):
        self.ok = False
        self.errors.append(msg)

    @property
    def status(self) -> str:
        return "PASS" if self.ok else "FAIL"


class SourceFile:
    """Wraps a driver source file with AST + line-based access."""

    def __init__(self, path: str):
        self.path = path
        with open(path) as f:
            self.source = f.read()
        self.lines = self.source.split("\n")
        self._parse_error: Optional[str] = None
        try:
            self.tree = ast.parse(self.source)
        except SyntaxError as e:
            self._parse_error = str(e)
            self.tree = ast.parse("")  # dummy placeholder

    def text(self, node: ast.AST) -> str:
        return ast.get_source_segment(self.source, node) or ""

    def find_class(self, name_contains: str = "Driver") -> Optional[ast.ClassDef]:
        for n in ast.walk(self.tree):
            if isinstance(n, ast.ClassDef) and name_contains in n.name:
                return n
        return None

    def find_method(self, cls_node: ast.ClassDef, name: str) -> Optional[ast.FunctionDef]:
        for n in ast.iter_child_nodes(cls_node):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
                return n
        return None

    def method_source(self, method: ast.FunctionDef) -> str:
        return self.text(method) or ""

    def has_self_attr_call(self, tree: ast.AST, obj: str, attr: str) -> bool:
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == attr
                    and isinstance(n.func.value, ast.Attribute)
                    and n.func.value.attr == obj
                    and isinstance(n.func.value.value, ast.Name)
                    and n.func.value.value.id == "self"):
                return True
        return False

    def find_strings(self, tree: ast.AST, substring: str) -> list[str]:
        found = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and substring in n.value:
                found.append(n.value)
        return found

    def find_assign_to_name(self, tree: ast.AST, target_name: str) -> Optional[ast.Assign]:
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id == target_name:
                        return n
        return None

    def has_binop_sequence(self, tree: ast.AST, var_names: list[str]) -> bool:
        """Check if tree contains a left-to-right sequence of BinOp additions like a + b + c."""
        found = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
                left = n.left
                right = n.right
                left_name = (isinstance(left, ast.Name) and left.id) or ""
                right_name = (isinstance(right, ast.Name) and right.id) or ""
                if left_name in var_names or right_name in var_names:
                    found.add(left_name)
                    found.add(right_name)
        return any(v in found for v in var_names)


def check_syntax(sf: SourceFile) -> Check:
    c = Check("syntax")
    if sf._parse_error:
        c.fail(sf._parse_error)
    return c


def check_class_structure(sf: SourceFile) -> Check:
    c = Check("class_structure")
    cls_node = sf.find_class("Driver")
    if cls_node is None:
        c.fail("No class named *Driver found")
        return c

    for m in METHODS:
        method = sf.find_method(cls_node, m)
        if method is None:
            c.fail("Missing method: %s" % m)
        # Verify it is not a trivial pass (unimplemented)
        elif any(
            isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call)
            and isinstance(stmt.exc.func, ast.Attribute)
            and "NotImplementedError" in ast.dump(stmt.exc.func)
            for stmt in ast.walk(method)
        ):
            c.fail("%s raises NotImplementedError (stub)" % m)

    for h in HELPERS:
        method = sf.find_method(cls_node, h)
        if method is None:
            c.fail("Missing helper: %s" % h)

    if not sf.has_self_attr_call(cls_node, "conn", "commit"):
        c.fail("No self.conn.commit() found in any method")
    return c


def check_txn_queries(sf: SourceFile) -> Check:
    c = Check("txn_queries")
    mod = sf.tree
    txn_dict = None
    for n in ast.iter_child_nodes(mod):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "TXN_QUERIES":
                    txn_dict = n.value
    if txn_dict is None:
        c.fail("Module-level TXN_QUERIES dict not found")
        return c
    if not isinstance(txn_dict, ast.Dict):
        c.fail("TXN_QUERIES is not a dict")
        return c

    keys_found = set()
    for k in txn_dict.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys_found.add(k.value)

    for t in TRANSACTIONS:
        if t not in keys_found:
            c.fail("Missing TXN_QUERIES key: %s" % t)

    # TXN_QUERIES values should NOT contain "%%s" (escaped percent in source).
    # That pattern belongs in helpers where Python's % formatting converts it
    # to "%s" for cursor.execute. In TXN_QUERIES, use "%s" directly.
    # Use r"%%s" to find the literal 3-char sequence.
    txn_source = sf.text(txn_dict)
    if r"%%s" in (txn_source or ""):
        for i, k in enumerate(txn_dict.keys):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                val_source = sf.text(txn_dict.values[i])
                if val_source and r"%%s" in val_source:
                    c.fail("TXN_QUERIES[%s] contains literal '%%%%s' in source; "  # 4 % → 2 % in output
                           "use single '%%s' for cursor.execute" % k.value)

    # Check each transaction has at least one query
    for i, k in enumerate(txn_dict.keys):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            val = txn_dict.values[i]
            query_count = sum(
                1 for n in ast.walk(val)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and ("SELECT" in n.value.upper() or "INSERT" in n.value.upper()
                     or "UPDATE" in n.value.upper() or "DELETE" in n.value.upper())
            )
            if query_count == 0:
                c.fail("TXN_QUERIES[%s] appears to have no SQL queries" % k.value)

    return c


def check_column_major_params(sf: SourceFile) -> Check:
    c = Check("column_major_params")
    cls_node = sf.find_class("Driver")
    if cls_node is None:
        c.fail("No driver class found")
        return c

    method = sf.find_method(cls_node, "_batch_update_stock")
    if method is None:
        c.fail("_batch_update_stock not found")
        return c

    src = sf.method_source(method)

    # Check that params concatenation follows column-major order.
    # The source should contain something like:
    #   params = quantity_params + ytd_params + order_cnt_params + remote_cnt_params + where_params
    # Look for the assignment to `params` that concatenates multiple lists.
    assign = sf.find_assign_to_name(method, "params")
    if assign is None:
        c.fail("No `params = ...` assignment in _batch_update_stock")
        return c

    rhs = assign.value
    # Walk the BinOp chain to extract the sequence of variable names
    parts = []
    def collect_binop(node):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            collect_binop(node.left)
            if isinstance(node.right, ast.Name):
                parts.append(node.right.id)
            elif isinstance(node.right, ast.Call):
                parts.append("<call>")
            else:
                parts.append("<expr>")
        elif isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Call):
            parts.append("<call>")
    collect_binop(rhs)

    if not parts:
        c.fail("Could not parse params concatenation order")
        return c

    # Expected column-major order (prefixes may vary)
    expected_prefixes = ["quantity", "ytd", "order_cnt", "remote_cnt", "where"]
    actual_prefixes = []
    for p in parts:
        matched = False
        for prefix in expected_prefixes:
            if p.lower().startswith(prefix):
                actual_prefixes.append(prefix)
                matched = True
                break
        if not matched:
            actual_prefixes.append(p)

    # Check that quantity comes before ytd, etc.
    order_map = {p: i for i, p in enumerate(expected_prefixes)}
    seen = []
    for p in actual_prefixes:
        if p in order_map:
            if seen and order_map[p] < max(order_map[s] for s in seen):
                c.fail("Column-major order violated: params order is %s, expected %s" %
                       (" + ".join(parts), " + ".join(expected_prefixes)))
                return c
            seen.append(p)

    return c


def check_w_id_guards(sf: SourceFile) -> Check:
    c = Check("w_id_guards")
    cls_node = sf.find_class("Driver")
    if cls_node is None:
        c.fail("No driver class found")
        return c

    guards = [
        ("_batch_delete_new_orders", "NO_W_ID"),
        ("_batch_update_orders", "O_W_ID"),
        ("_batch_update_order_lines", "OL_W_ID"),
        ("_batch_update_customers", "C_W_ID"),
    ]

    for method_name, column in guards:
        method = sf.find_method(cls_node, method_name)
        if method is None:
            c.fail("Missing helper %s (cannot check w_id guard)" % method_name)
            continue
        src = sf.method_source(method)
        if column not in src:
            c.fail("%s: missing w_id filter '%s'" % (method_name, column))

    return c


def check_stock_quantity_formula(sf: SourceFile) -> Check:
    c = Check("stock_quantity_formula")
    cls_node = sf.find_class("Driver")
    if cls_node is None:
        c.fail("No driver class found")
        return c

    method = sf.find_method(cls_node, "doNewOrder")
    if method is None:
        c.fail("doNewOrder not found")
        return c

    src = sf.method_source(method)

    # Check for the if/else pattern matching TPC-C 2.5.1.3
    # Pattern: if s_quantity >= ol_quantity + 10: s_quantity = s_quantity - ol_quantity
    #        else: s_quantity = s_quantity + 91 - ol_quantity
    patterns = [
        ("s_quantity >= ol_quantity + 10", ">= ol_quantity + 10" in src or ">= ol_quantity+10" in src),
        ("s_quantity - ol_quantity", "s_quantity - ol_quantity" in src or "s_quantity-ol_quantity" in src),
        ("s_quantity + 91 - ol_quantity", "s_quantity + 91 - ol_quantity" in src or "s_quantity+91-ol_quantity" in src),
    ]

    for label, found in patterns:
        if not found:
            c.fail("Stock quantity formula: expected '%s' not found" % label)

    # Verify s_ytd increment
    if "s_ytd += ol_quantity" not in src:
        c.fail("Missing s_ytd += ol_quantity")

    # Verify s_order_cnt increment
    if "s_order_cnt += 1" not in src:
        c.fail("Missing s_order_cnt += 1")

    return c


def check_remote_cnt_increment(sf: SourceFile) -> Check:
    c = Check("remote_cnt_increment")
    cls_node = sf.find_class("Driver")
    if cls_node is None:
        c.fail("No driver class found")
        return c

    method = sf.find_method(cls_node, "doNewOrder")
    if method is None:
        c.fail("doNewOrder not found")
        return c

    src = sf.method_source(method)
    if "ol_supply_w_id != w_id" not in src:
        c.fail("Missing `ol_supply_w_id != w_id` condition")
    if "s_remote_cnt += 1" not in src and "s_remote_cnt = s_remote_cnt + 1" not in src:
        c.fail("Missing `s_remote_cnt += 1`")

    return c


def check_brand_generic(sf: SourceFile) -> Check:
    c = Check("brand_generic")
    cls_node = sf.find_class("Driver")
    if cls_node is None:
        c.fail("No driver class found")
        return c

    method = sf.find_method(cls_node, "doNewOrder")
    if method is None:
        c.fail("doNewOrder not found")
        return c

    src = sf.method_source(method)

    if "ORIGINAL_STRING" not in src:
        c.fail("Brand/generic detection: missing constants.ORIGINAL_STRING reference")
    if "ORIGINAL" not in src:
        c.fail("Brand/generic detection: missing 'ORIGINAL' string check")

    has_brand = "'B'" in src or '"B"' in src
    has_generic = "'G'" in src or '"G"' in src
    if not has_brand:
        c.fail("Brand/generic: missing brand 'B' assignment")
    if not has_generic:
        c.fail("Brand/generic: missing generic 'G' assignment")

    return c


def check_merged_queries(sf: SourceFile) -> Check:
    c = Check("merged_queries")
    # Find TXN_QUERIES values and check for JOIN patterns
    txn_dict = None
    for n in ast.iter_child_nodes(sf.tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "TXN_QUERIES":
                    txn_dict = n.value

    if txn_dict is None or not isinstance(txn_dict, ast.Dict):
        c.fail("TXN_QUERIES not found")
        return c

    # Map keys to their dict values
    txn_values = {}
    for i, k in enumerate(txn_dict.keys):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            txn_values[k.value] = txn_dict.values[i]

    # Check NEW_ORDER has a merged query (w, d, c in one query)
    if "NEW_ORDER" in txn_values:
        has_join = sf.find_strings(txn_values["NEW_ORDER"], "JOIN")
        has_comma = sf.find_strings(txn_values["NEW_ORDER"], "WAREHOUSE")
        if not has_join:
            c.fail("NEW_ORDER: no JOIN found; may not merge w+d+c into one query")

    # Check DELIVERY has a batch query (subquery or GROUP BY)
    if "DELIVERY" in txn_values:
        has_group = sf.find_strings(txn_values["DELIVERY"], "GROUP BY")
        has_subquery = sf.find_strings(txn_values["DELIVERY"], "SELECT")
        if not has_group:
            c.fail("DELIVERY: no GROUP BY found; may not batch district queries")

    # Check ORDER_STATUS has merged order+lines
    if "ORDER_STATUS" in txn_values:
        has_join = sf.find_strings(txn_values["ORDER_STATUS"], "JOIN")
        if not has_join:
            c.fail("ORDER_STATUS: no JOIN found; may not merge order+lines")

    # Check PAYMENT has merged customer+warehouse+district
    if "PAYMENT" in txn_values:
        has_merge = sf.find_strings(txn_values["PAYMENT"], "WAREHOUSE") or \
                    sf.find_strings(txn_values["PAYMENT"], "w.") or \
                    sf.find_strings(txn_values["PAYMENT"], "DISTRICT")
        if not has_merge:
            c.fail("PAYMENT: no merged customer/warehouse/district query found")

    # Check STOCK_LEVEL has a single query with JOIN
    if "STOCK_LEVEL" in txn_values:
        has_join = sf.find_strings(txn_values["STOCK_LEVEL"], "JOIN")
        if not has_join:
            c.fail("STOCK_LEVEL: no JOIN found; may not merge district+stock")

    return c


def check_commit_calls(sf: SourceFile) -> Check:
    c = Check("commit_calls")
    cls_node = sf.find_class("Driver")
    if cls_node is None:
        c.fail("No driver class found")
        return c

    for m in METHODS:
        method = sf.find_method(cls_node, m)
        if method is None:
            continue
        if not sf.has_self_attr_call(method, "conn", "commit"):
            c.fail("%s: missing self.conn.commit()" % m)

    return c


def check_d_next_o_id(sf: SourceFile) -> Check:
    c = Check("d_next_o_id_increment")
    cls_node = sf.find_class("Driver")
    if cls_node is None:
        c.fail("No driver class found")
        return c

    method = sf.find_method(cls_node, "doNewOrder")
    if method is None:
        c.fail("doNewOrder not found")
        return c

    src = sf.method_source(method)
    if "d_next_o_id + 1" not in src and "d_next_o_id+1" not in src:
        c.fail("Missing D_NEXT_O_ID increment (d_next_o_id + 1)")

    return c


def check_batch_stock_info_columns(sf: SourceFile) -> Check:
    c = Check("batch_stock_info_columns")
    cls_node = sf.find_class("Driver")
    if cls_node is None:
        c.fail("No driver class found")
        return c

    method = sf.find_method(cls_node, "_batch_stock_info")
    if method is None:
        c.fail("_batch_stock_info not found")
        return c

    src = sf.method_source(method)
    if "S_DIST_" not in src:
        c.fail("_batch_stock_info: missing S_DIST_%%02d formatting for district column")

    return c


# --- Helper functions for extended checks ---

def _extract_txn_queries_source(sf: SourceFile) -> Optional[dict[str, dict[str, str]]]:
    txn_dict = None
    for n in ast.iter_child_nodes(sf.tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "TXN_QUERIES":
                    txn_dict = n.value
    if not isinstance(txn_dict, ast.Dict):
        return None
    result = {}
    for i, k in enumerate(txn_dict.keys):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            txn_name = k.value
            inner = txn_dict.values[i]
            if not isinstance(inner, ast.Dict):
                continue
            subs = {}
            for j, sk in enumerate(inner.keys):
                if isinstance(sk, ast.Constant) and isinstance(sk.value, str):
                    subs[sk.value] = sf.text(inner.values[j])
            result[txn_name] = subs
    return result


def _count_s_placeholders(text: str) -> int:
    count = 0
    i = 0
    while i < len(text):
        if text[i] == '%':
            if i + 1 < len(text) and text[i + 1] == '%':
                i += 2
                continue
            elif i + 1 < len(text) and text[i + 1] == 's':
                count += 1
                i += 2
                continue
        i += 1
    return count


def _get_q_assignment(method: ast.FunctionDef) -> Optional[ast.Assign]:
    for n in ast.iter_child_nodes(method):
        if isinstance(n, ast.Assign) and len(n.targets) == 1:
            t = n.targets[0]
            if isinstance(t, ast.Name) and t.id == 'q':
                return n
    return None


def _get_txn_name_from_q(assign: ast.Assign) -> Optional[str]:
    v = assign.value
    if isinstance(v, ast.Subscript):
        if isinstance(v.value, ast.Name) and v.value.id == 'TXN_QUERIES':
            if isinstance(v.slice, ast.Constant) and isinstance(v.slice.value, str):
                return v.slice.value
    return None


def _iter_cursor_execute(method: ast.FunctionDef):
    for n in ast.walk(method):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == 'execute'
                and isinstance(n.func.value, ast.Attribute)
                and n.func.value.attr == 'cursor'):
            sql_arg = n.args[0] if len(n.args) > 0 else None
            params_arg = n.args[1] if len(n.args) > 1 else None
            yield n, sql_arg, params_arg


def _get_subscript_key(node: ast.AST) -> Optional[str]:
    if (isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name) and node.value.id == 'q'
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)):
        return node.slice.value
    return None


def _count_literal_params(node: ast.AST) -> Optional[int]:
    if isinstance(node, (ast.List, ast.Tuple)):
        return len(node.elts)
    return None


def _strip_sql_quotes(source: str) -> str:
    for quote in ('"""', "'''", '"', "'"):
        if source.startswith(quote) and source.endswith(quote):
            return textwrap.dedent(source[len(quote):-len(quote)]).strip()
    return source.strip()


# --- Extended check functions ---

def check_placeholder_count(sf: SourceFile) -> Check:
    c = Check("placeholder_count")
    txn_queries = _extract_txn_queries_source(sf)
    if txn_queries is None:
        c.fail("Could not extract TXN_QUERIES")
        return c
    cls_node = sf.find_class("Driver")
    if cls_node is None:
        c.fail("No driver class found")
        return c
    for method_name in METHODS:
        method = sf.find_method(cls_node, method_name)
        if method is None:
            continue
        q_assign = _get_q_assignment(method)
        if q_assign is None:
            continue
        txn_name = _get_txn_name_from_q(q_assign)
        if txn_name is None or txn_name not in txn_queries:
            continue
        for execute_node, sql_arg, params_arg in _iter_cursor_execute(method):
            subkey = _get_subscript_key(sql_arg)
            if subkey is None or subkey not in txn_queries[txn_name]:
                continue
            sql_source = txn_queries[txn_name][subkey]
            if sql_source is None:
                continue
            sql_clean = _strip_sql_quotes(sql_source)
            placeholders = _count_s_placeholders(sql_clean)
            param_count = _count_literal_params(params_arg)
            if param_count is not None and param_count != placeholders:
                c.fail("%s: cursor.execute(q[%r], ...): %d %%s in SQL but %d params"
                       % (method_name, subkey, placeholders, param_count))
    return c


def check_sql_syntax(sf: SourceFile) -> Check:
    c = Check("sql_syntax")
    txn_queries = _extract_txn_queries_source(sf)
    if txn_queries is None:
        c.fail("Could not extract TXN_QUERIES")
        return c
    for txn_name, subs in txn_queries.items():
        for subkey, sql_source in subs.items():
            if sql_source is None:
                continue
            sql = _strip_sql_quotes(sql_source)
            if not sql:
                continue
            try:
                parsed = sqlparse.parse(sql)
                has_error = False
                for stmt in parsed:
                    for token in stmt.flatten():
                        if token.ttype is sqlparse.tokens.Error:
                            has_error = True
                            break
                    if has_error:
                        break
                if has_error:
                    c.fail("TXN_QUERIES[%s][%s]: SQL syntax error detected by parser" % (txn_name, subkey))
            except Exception as e:
                c.fail("TXN_QUERIES[%s][%s]: SQL parse exception: %s" % (txn_name, subkey, e))
    return c


def check_txn_queries_coverage(sf: SourceFile) -> Check:
    c = Check("txn_queries_coverage")
    txn_queries = _extract_txn_queries_source(sf)
    if txn_queries is None:
        c.fail("Could not extract TXN_QUERIES")
        return c
    cls_node = sf.find_class("Driver")
    if cls_node is None:
        c.fail("No driver class found")
        return c
    for method_name in METHODS:
        method = sf.find_method(cls_node, method_name)
        if method is None:
            continue
        q_assign = _get_q_assignment(method)
        if q_assign is None:
            continue
        txn_name = _get_txn_name_from_q(q_assign)
        if txn_name is None:
            continue
        for execute_node, sql_arg, params_arg in _iter_cursor_execute(method):
            subkey = _get_subscript_key(sql_arg)
            if subkey is not None:
                if txn_name not in txn_queries or subkey not in txn_queries[txn_name]:
                    c.fail("%s: q[%r] referenced but not found in TXN_QUERIES" % (method_name, subkey))
    return c


def check_rollback_usage(sf: SourceFile) -> Check:
    c = Check("rollback_usage")
    cls_node = sf.find_class("Driver")
    if cls_node is None:
        c.fail("No driver class found")
        return c
    for method_name in METHODS:
        method = sf.find_method(cls_node, method_name)
        if method is None:
            continue
        has_rollback = sf.has_self_attr_call(method, "conn", "rollback")
        has_commit = sf.has_self_attr_call(method, "conn", "commit")
        if has_rollback and not has_commit:
            c.fail("%s: self.conn.rollback() without self.conn.commit()" % method_name)
    return c


def check_batch_placeholder_format(sf: SourceFile) -> Check:
    c = Check("batch_placeholder_format")
    cls_node = sf.find_class("Driver")
    if cls_node is None:
        c.fail("No driver class found")
        return c

    for method_name in HELPERS:
        method = sf.find_method(cls_node, method_name)
        if method is None:
            continue

        parent_map = {
            child: node
            for node in ast.walk(method)
            for child in ast.iter_child_nodes(node)
        }

        for node in ast.walk(method):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if "%%" not in node.value:
                continue

            parent = parent_map.get(node)
            is_format_string = (
                isinstance(parent, ast.BinOp)
                and isinstance(parent.op, ast.Mod)
                and parent.left is node
            )
            if not is_format_string:
                c.fail(method_name + ": string literal contains '%%s' (should use single '%s')")
                break

    return c


def check_stock_level_query_scope(sf: SourceFile) -> Check:
    c = Check("stock_level_query_scope")
    txn_queries = _extract_txn_queries_source(sf)
    if txn_queries is None:
        c.fail("Could not extract TXN_QUERIES")
        return c

    sql_source = txn_queries.get("STOCK_LEVEL", {}).get("getStockCount")
    if sql_source is None:
        c.fail("STOCK_LEVEL.getStockCount query not found")
        return c

    sql = _strip_sql_quotes(sql_source)

    import re
    for m in re.finditer(r'\b([a-zA-Z_]\w*)\.(low|high)(_?\w*)\b', sql, re.IGNORECASE):
        text_before = sql[:m.start()]
        paren_depth = text_before.count('(') - text_before.count(')')
        if paren_depth > 0:
            alias, full_ref = m.group(1), m.group(0)
            c.fail("STOCK_LEVEL: '%s' is inside a subquery (paren depth %d) — "
                   "derived table alias '%s' not visible in this scope"
                   % (full_ref, paren_depth, alias))
            return c

    return c


# Runner
ALL_CHECKS = [
    ("syntax", check_syntax, "File is valid Python"),
    ("class_structure", check_class_structure, "Driver class, methods, and helpers exist"),
    ("txn_queries", check_txn_queries, "TXN_QUERIES dict structure and SQL hygiene"),
    ("w_id_guards", check_w_id_guards, "DELIVERY batch writes have per-warehouse guards"),
    ("column_major_params", check_column_major_params, "_batch_update_stock uses column-major param order"),
    ("stock_quantity_formula", check_stock_quantity_formula, "Stock update follows TPC-C 2.5.1.3"),
    ("remote_cnt_increment", check_remote_cnt_increment, "s_remote_cnt increments for remote items"),
    ("brand_generic", check_brand_generic, "Brand/generic detection uses ORIGINAL_STRING"),
    ("merged_queries", check_merged_queries, "TXN_QUERIES contain merged JOIN queries"),
    ("commit_calls", check_commit_calls, "All transaction methods call self.conn.commit()"),
    ("d_next_o_id_increment", check_d_next_o_id, "District next order ID is incremented"),
    ("batch_stock_info_columns", check_batch_stock_info_columns, "_batch_stock_info selects S_DIST_XX column"),
    ("placeholder_count", check_placeholder_count, "%s placeholders match param count in cursor.execute calls"),
    ("sql_syntax", check_sql_syntax, "SQL queries in TXN_QUERIES parse correctly"),
    ("txn_queries_coverage", check_txn_queries_coverage, "All TXN_QUERIES entries are used, all refs exist"),
    ("rollback_usage", check_rollback_usage, "No unsafe self.conn.rollback() calls in transaction methods"),
    ("batch_placeholder_format", check_batch_placeholder_format, "Batch helper placeholders use single '%s', not '%%s'"),
    ("stock_level_query_scope", check_stock_level_query_scope, "STOCK_LEVEL derived table alias references are in correct scope"),
]


def run_checks(sf: SourceFile, include: Optional[set[str]] = None,
               exclude: Optional[set[str]] = None) -> list[Check]:
    results = []
    for name, fn, desc in ALL_CHECKS:
        if include and name not in include:
            continue
        if exclude and name in exclude:
            continue
        # Skip structural checks if file has syntax errors
        if sf._parse_error and name != "syntax":
            result = Check(name)
            result.ok = False
            result.errors.append("Skipped due to syntax error")
            results.append(result)
            continue
        results.append(fn(sf))
    return results


def main():
    parser = argparse.ArgumentParser(
        description="AST-based static checker for generated TPC-C drivers")
    parser.add_argument("--driver", help="Path to driver file")
    parser.add_argument("--auto", action="store_true",
                        help="Use latest gemini*driver.py")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON instead of human-readable")
    parser.add_argument("--check", nargs="*",
                        help="Run only specific checks (default: all)")
    parser.add_argument("--exclude", nargs="*",
                        help="Skip specific checks")
    args = parser.parse_args()

    if args.auto:
        drivers_dir = os.path.join(PROJECT_ROOT, "tpcc", "drivers")
        candidates = glob.glob(os.path.join(drivers_dir, "gemini*driver.py"))
        if not candidates:
            print("No generated driver files found (*gemini*driver.py)")
            sys.exit(1)
        latest = max(candidates, key=os.path.getmtime)
        driver_path = latest
    elif args.driver:
        driver_path = os.path.realpath(args.driver)
        if not os.path.exists(driver_path):
            driver_path = os.path.join(PROJECT_ROOT, "tpcc", "drivers", args.driver)
        if not os.path.exists(driver_path):
            print("Driver file not found: %s" % args.driver)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)

    include = set(args.check) if args.check else None
    exclude = set(args.exclude) if args.exclude else None

    sf = SourceFile(driver_path)
    results = run_checks(sf, include, exclude)

    total = len(results)
    passed = sum(1 for r in results if r.ok)
    failed = total - passed

    if args.json:
        output = {
            "driver": driver_path,
            "checks": [
                {"name": r.name, "status": r.status, "errors": r.errors}
                for r in results
            ],
            "passed": passed,
            "failed": failed,
            "total": total,
        }
        print(json.dumps(output, indent=2))
        sys.exit(1 if failed else 0)
        return

    print("=" * 60)
    print("AST Checker: %s" % os.path.basename(driver_path))
    print("=" * 60)
    for r in results:
        marker = "\u2713" if r.ok else "\u2717"
        print("  %s %s" % (marker, r.name))
        if not r.ok and args.verbose:
            for e in r.errors:
                print("       %s" % e)
    print("-" * 60)
    print("  %d/%d passed, %d failed" % (passed, total, failed))
    print("=" * 60)

    if failed:
        print()
        for r in results:
            if not r.ok:
                print("[%s] %s" % (r.status, r.name))
                for e in r.errors:
                    print("  %s" % e)
        print()

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
