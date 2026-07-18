from __future__ import with_statement

import os
import logging
from pprint import pformat

import tpcc.constants as constants
from .abstractdriver import *

try:
    import MySQLdb as mysql
except ImportError:
    import pymysql as mysql

# ----------------------------------------------------------------------------------------------------------------------
# TXN_QUERIES
# Optimized queries applying the rewrite strategies.
# ----------------------------------------------------------------------------------------------------------------------
TXN_QUERIES = {
    "DELIVERY": {
        # COMBINING_QUERIES: Merging multiple steps into one query.
        # This query finds the oldest new order for each district, joins with ORDERS to get O_C_ID,
        # and LEFT JOINs with a pre-aggregated ORDER_LINE sum to get OL_AMOUNT.
        "batchNewOrders": """
            SELECT n.NO_D_ID, n.NO_O_ID, o.O_C_ID,
                   COALESCE(ol_sum.total_amount, 0.0) AS ol_total
            FROM (SELECT NO_D_ID, MIN(NO_O_ID) AS NO_O_ID
                  FROM NEW_ORDER
                  WHERE NO_W_ID = %s AND NO_O_ID > -1
                  GROUP BY NO_D_ID) n
            JOIN ORDERS o ON o.O_ID = n.NO_O_ID
                         AND o.O_D_ID = n.NO_D_ID
                         AND o.O_W_ID = %s
            LEFT JOIN (
                SELECT OL_O_ID, OL_D_ID, OL_W_ID, SUM(OL_AMOUNT) AS total_amount
                FROM ORDER_LINE
                GROUP BY OL_O_ID, OL_D_ID, OL_W_ID
            ) ol_sum ON ol_sum.OL_O_ID = n.NO_O_ID
                    AND ol_sum.OL_D_ID = n.NO_D_ID
                    AND ol_sum.OL_W_ID = o.O_W_ID
        """,
    },
    "NEW_ORDER": {
        # CONCURRENCY: Batch item info lookup.
        "batchItemInfo": "SELECT I_ID, I_PRICE, I_NAME, I_DATA FROM ITEM WHERE I_ID IN (%s)",
        # COMBINING_QUERIES & JOIN_ORDER_HINTS: Merging WAREHOUSE, DISTRICT, CUSTOMER lookups with STRAIGHT_JOIN.
        "getMiscInfo": """
            SELECT W.W_TAX, D.D_TAX, D.D_NEXT_O_ID, C.C_DISCOUNT, C.C_LAST, C.C_CREDIT
            FROM WAREHOUSE AS W
            STRAIGHT_JOIN DISTRICT AS D ON D.D_W_ID = W.W_ID AND D.D_ID = %s
            STRAIGHT_JOIN CUSTOMER AS C ON C.C_W_ID = W.W_ID AND C.C_D_ID = D.D_ID AND C.C_ID = %s
            WHERE W.W_ID = %s
        """,
        # Individual updates/inserts remain as they are single-row operations per transaction that depend on prior reads.
        "incrementNextOrderId": "UPDATE DISTRICT SET D_NEXT_O_ID = %s WHERE D_ID = %s AND D_W_ID = %s",
        "createOrder": "INSERT INTO ORDERS (O_ID, O_D_ID, O_W_ID, O_C_ID, O_ENTRY_D, O_CARRIER_ID, O_OL_CNT, O_ALL_LOCAL) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        "createNewOrder": "INSERT INTO NEW_ORDER (NO_O_ID, NO_D_ID, NO_W_ID) VALUES (%s, %s, %s)",
        # CONCURRENCY: Batch stock info lookup.
        "batchStockInfo": "SELECT S_I_ID, S_W_ID, S_QUANTITY, S_DATA, S_YTD, S_ORDER_CNT, S_REMOTE_CNT, S_DIST_%02d FROM STOCK WHERE (S_I_ID, S_W_ID) IN (%s)",
        # Stock updates and order line inserts are handled by batch helpers, no direct queries here.
    },
    "ORDER_STATUS": {
        # COMBINING_QUERIES: Merging customer, last order, and order lines.
        # Uses derived table for last order with LIMIT 1, which is efficient.
        "getCustomerByIdWithLines": """
            SELECT c.C_ID, c.C_FIRST, c.C_MIDDLE, c.C_LAST, c.C_BALANCE,
                   lo.O_ID, lo.O_CARRIER_ID, lo.O_ENTRY_D,
                   ol.OL_SUPPLY_W_ID, ol.OL_I_ID, ol.OL_QUANTITY, ol.OL_AMOUNT, ol.OL_DELIVERY_D
            FROM CUSTOMER c
            LEFT JOIN (
                SELECT O_ID, O_CARRIER_ID, O_ENTRY_D, O_W_ID, O_D_ID, O_C_ID
                FROM ORDERS
                WHERE O_W_ID = %s AND O_D_ID = %s AND O_C_ID = %s
                ORDER BY O_ID DESC LIMIT 1
            ) lo ON lo.O_W_ID = c.C_W_ID AND lo.O_D_ID = c.C_D_ID AND lo.O_C_ID = c.C_ID
            LEFT JOIN ORDER_LINE ol ON ol.OL_W_ID = lo.O_W_ID AND ol.OL_D_ID = lo.O_D_ID AND ol.OL_O_ID = lo.O_ID
            WHERE c.C_W_ID = %s AND c.C_D_ID = %s AND c.C_ID = %s
            ORDER BY ol.OL_NUMBER
        """,
        # Standard lookup for customer by last name.
        "getCustomersByLastName": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_BALANCE FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_LAST = %s ORDER BY C_FIRST",
        # COMBINING_QUERIES: Merging last order and order lines after customer is identified by c_last.
        # Uses derived table for last order with LIMIT 1.
        "getLastOrderWithLinesForCustomer": """
            SELECT O.O_ID, O.O_CARRIER_ID, O.O_ENTRY_D,
                   OL.OL_SUPPLY_W_ID, OL.OL_I_ID, OL.OL_QUANTITY, OL.OL_AMOUNT, OL.OL_DELIVERY_D
            FROM (
                SELECT O_ID, O_CARRIER_ID, O_ENTRY_D, O_W_ID, O_D_ID, O_C_ID
                FROM ORDERS
                WHERE O_W_ID = %s AND O_D_ID = %s AND O_C_ID = %s
                ORDER BY O_ID DESC LIMIT 1
            ) O
            LEFT JOIN ORDER_LINE OL
                ON OL.OL_W_ID = O.O_W_ID AND OL.OL_D_ID = O.O_D_ID AND OL.OL_O_ID = O.O_ID
            ORDER BY OL.OL_NUMBER
        """,
    },
    "PAYMENT": {
        # COMBINING_QUERIES & JOIN_ORDER_HINTS: Getting payment warehouse and district info.
        # Separated from customer info to correctly handle remote customers.
        "getWarehouseAndDistrictInfo": """
            SELECT W.W_NAME, W.W_STREET_1, W.W_STREET_2, W.W_CITY, W.W_STATE, W.W_ZIP,
                   D.D_NAME, D.D_STREET_1, D.D_STREET_2, D.D_CITY, D.D_STATE, D.D_ZIP
            FROM WAREHOUSE AS W
            STRAIGHT_JOIN DISTRICT AS D ON D.D_W_ID = W.W_ID AND D.D_ID = %s
            WHERE W.W_ID = %s
        """,
        # Standard lookup for customer by ID.
        "getCustomerById": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_STREET_1, C_STREET_2, C_CITY, C_STATE, C_ZIP, C_PHONE, C_SINCE, C_CREDIT, C_CREDIT_LIM, C_DISCOUNT, C_BALANCE, C_YTD_PAYMENT, C_PAYMENT_CNT, C_DATA FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        # Standard lookup for customer by last name.
        "getCustomersByLastName": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_STREET_1, C_STREET_2, C_CITY, C_STATE, C_ZIP, C_PHONE, C_SINCE, C_CREDIT, C_CREDIT_LIM, C_DISCOUNT, C_BALANCE, C_YTD_PAYMENT, C_PAYMENT_CNT, C_DATA FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_LAST = %s ORDER BY C_FIRST",
        # Individual updates and inserts.
        "updateWarehouseBalance": "UPDATE WAREHOUSE SET W_YTD = W_YTD + %s WHERE W_ID = %s",
        "updateDistrictBalance": "UPDATE DISTRICT SET D_YTD = D_YTD + %s WHERE D_W_ID = %s AND D_ID = %s",
        "updateBCCustomer": "UPDATE CUSTOMER SET C_BALANCE = %s, C_YTD_PAYMENT = %s, C_PAYMENT_CNT = %s, C_DATA = %s WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "updateGCCustomer": "UPDATE CUSTOMER SET C_BALANCE = %s, C_YTD_PAYMENT = %s, C_PAYMENT_CNT = %s WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "insertHistory": "INSERT INTO HISTORY VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
    },
    "STOCK_LEVEL": {
        # SEPARATING_QUERIES: Fetch D_NEXT_O_ID first in Python to then pass calculated range to main query.
        "getDistrictNextOid": "SELECT D_NEXT_O_ID FROM DISTRICT WHERE D_W_ID = %s AND D_ID = %s",
        # PREDICATE_PUSHDOWN: Explicitly filter ORDER_LINE in a derived table before joining STOCK.
        "getStockCount": """
            SELECT COUNT(DISTINCT OL.OL_I_ID)
            FROM (
                SELECT OL_I_ID
                FROM ORDER_LINE
                WHERE OL_W_ID = %s AND OL_D_ID = %s
                  AND OL_O_ID < %s
                  AND OL_O_ID >= %s
            ) AS OL
            JOIN STOCK ON STOCK.S_I_ID = OL.OL_I_ID AND STOCK.S_W_ID = %s
            WHERE STOCK.S_QUANTITY < %s
        """,
    },
}


# ----------------------------------------------------------------------------------------------------------------------
# Gemini_2_5_Flash_20260718_1635Driver
# ----------------------------------------------------------------------------------------------------------------------
class Gemini_2_5_Flash_20260718_1635Driver(AbstractDriver):
    DEFAULT_CONFIG = {
        "host": ("MySQL server hostname", "localhost"),
        "port": ("MySQL server port", 3306),
        "user": ("MySQL user name", "root"),
        "password": ("MySQL user password", ""),
        "database": ("MySQL database name", "tpcc"),
    }

    def __init__(self, ddl):
        mysql_ddl = os.path.join(os.path.dirname(ddl), "tpcc.mysql.sql")
        if os.path.exists(mysql_ddl):
            ddl = mysql_ddl
        super(Gemini_2_5_Flash_20260718_1635Driver, self).__init__("candidates", ddl)
        self.host = None
        self.port = None
        self.user = None
        self.password = None
        self.database = None
        self.conn = None
        self.cursor = None

    def makeDefaultConfig(self):
        return Gemini_2_5_Flash_20260718_1635Driver.DEFAULT_CONFIG

    def loadConfig(self, config):
        for key in Gemini_2_5_Flash_20260718_1635Driver.DEFAULT_CONFIG:
            assert key in config, "Missing parameter '%s' in %s configuration" % (key, self.name)

        self.host = str(config["host"])
        self.port = int(config["port"])
        self.user = str(config["user"])
        self.password = str(config["password"])
        self.database = str(config["database"])

        admin_conn = mysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            charset='utf8',
        )
        admin_cursor = admin_conn.cursor()

        if config.get("reset"):
            admin_cursor.execute("DROP DATABASE IF EXISTS `%s`" % self.database)

        admin_cursor.execute("CREATE DATABASE IF NOT EXISTS `%s`" % self.database)
        admin_cursor.execute("USE `%s`" % self.database)

        self._execute_ddl(admin_cursor)

        admin_cursor.close()
admin_conn.close()

        self.conn = mysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset='utf8',
        )
        self.cursor = self.conn.cursor()

    def _execute_ddl(self, cursor):
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        with open(self.ddl, 'r') as f:
            sql = f.read()
        for statement in sql.split(';'):
            statement = statement.strip()
            if statement:
                stmt = statement.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS")
                try:
                    cursor.execute(stmt)
                except mysql.OperationalError as e:
                    if e.args[0] not in (1061, 1005):
                        raise
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

    def loadTuples(self, tableName, tuples):
        if len(tuples) == 0:
            return
        p = ["%s"] * len(tuples[0])
        sql = "INSERT INTO %s VALUES (%s)" % (tableName, ",".join(p))
        self.cursor.executemany(sql, tuples)
        logging.debug("Loaded %d tuples for tableName %s" % (len(tuples), tableName))

    def loadFinish(self):
        logging.info("Committing changes to database")
        self.conn.commit()

    # ------------------------------------------------------------------------------------------------------------------
    # Batch Helpers - Copied Verbatim from v2
    # ------------------------------------------------------------------------------------------------------------------
    def _batch_delete_new_orders(self, w_id, pairs):
        if not pairs:
            return
        placeholders = ",".join(["(%s,%s)"] * len(pairs))
        sql = "DELETE FROM NEW_ORDER WHERE NO_W_ID = %%s AND (NO_D_ID, NO_O_ID) IN (%s)" % placeholders
        flat = [w_id]
        for d_id, no_o_id in pairs:
            flat.append(d_id)
            flat.append(no_o_id)
        self.cursor.execute(sql, flat)

    def _batch_update_orders(self, o_carrier_id, w_id, pairs):
        if not pairs:
            return
        placeholders = ",".join(["(%s,%s)"] * len(pairs))
        sql = "UPDATE ORDERS SET O_CARRIER_ID = %%s WHERE O_W_ID = %%s AND (O_ID, O_D_ID) IN (%s)" % placeholders
        params = [o_carrier_id, w_id]
        for d_id, no_o_id in pairs:
            params.append(no_o_id)
            params.append(d_id)
        self.cursor.execute(sql, params)

    def _batch_update_order_lines(self, ol_delivery_d, w_id, pairs):
        if not pairs:
            return
        placeholders = ",".join(["(%s,%s)"] * len(pairs))
        sql = "UPDATE ORDER_LINE SET OL_DELIVERY_D = %%s WHERE OL_W_ID = %%s AND (OL_O_ID, OL_D_ID) IN (%s)" % placeholders
        params = [ol_delivery_d, w_id]
        for d_id, no_o_id in pairs:
            params.append(no_o_id)
            params.append(d_id)
        self.cursor.execute(sql, params)

    def _batch_update_customers(self, w_id, updates):
        if not updates:
            return
        case_whens = []
        case_params = []
        for c_id, d_id, ol_total in updates:
            case_whens.append("WHEN C_ID = %s AND C_D_ID = %s THEN %s")
            case_params.extend([c_id, d_id, ol_total])
        where_clauses = []
        where_params = []
        for c_id, d_id, _ in updates:
            where_clauses.append("(C_ID = %s AND C_D_ID = %s)")
            where_params.extend([c_id, d_id])
        sql = """UPDATE CUSTOMER SET C_BALANCE = C_BALANCE + CASE
            %s
            ELSE 0
        END WHERE C_W_ID = %%s AND (%s)""" % (
            " ".join(case_whens),
            " OR ".join(where_clauses),
        )
        self.cursor.execute(sql, case_params + [w_id] + where_params)

    def _batch_items(self, i_ids):
        if not i_ids:
            return {}
        placeholders = ",".join(["%s"] * len(i_ids))
        sql = "SELECT I_ID, I_PRICE, I_NAME, I_DATA FROM ITEM WHERE I_ID IN (%s)" % placeholders
        self.cursor.execute(sql, i_ids)
        rows = self.cursor.fetchall()
        return {row[0]: (row[1], row[2], row[3]) for row in rows}

    def _batch_stock_info(self, d_id, pairs):
        if not pairs:
            return {}
        dist_col = "S_DIST_%02d" % d_id
        placeholders = ",".join(["(%s,%s)"] * len(pairs))
        sql = "SELECT S_I_ID, S_W_ID, S_QUANTITY, S_DATA, S_YTD, S_ORDER_CNT, S_REMOTE_CNT, %s FROM STOCK WHERE (S_I_ID, S_W_ID) IN (%s)" % (dist_col, placeholders)
        flat_params = []
        for i_id, w_id in pairs:
            flat_params.append(i_id)
            flat_params.append(w_id)
        self.cursor.execute(sql, flat_params)
        rows = self.cursor.fetchall()
        return {(row[0], row[1]): row[2:] for row in rows}

    def _batch_update_stock(self, updates):
        if not updates:
            return
        quantity_cases = []
        ytd_cases = []
        order_cnt_cases = []
        remote_cnt_cases = []
        where_clauses = []
        quantity_params = []
        ytd_params = []
        order_cnt_params = []
        remote_cnt_params = []
        where_params = []

        for s_quantity, s_ytd, s_order_cnt, s_remote_cnt, i_id, w_id in updates:
            quantity_cases.append("WHEN (S_I_ID, S_W_ID) = (%s, %s) THEN %s")
            quantity_params.extend([i_id, w_id, s_quantity])
            ytd_cases.append("WHEN (S_I_ID, S_W_ID) = (%s, %s) THEN %s")
            ytd_params.extend([i_id, w_id, s_ytd])
            order_cnt_cases.append("WHEN (S_I_ID, S_W_ID) = (%s, %s) THEN %s")
            order_cnt_params.extend([i_id, w_id, s_order_cnt])
            remote_cnt_cases.append("WHEN (S_I_ID, S_W_ID) = (%s, %s) THEN %s")
            remote_cnt_params.extend([i_id, w_id, s_remote_cnt])
            where_clauses.append("(S_I_ID = %s AND S_W_ID = %s)")
            where_params.extend([i_id, w_id])
        params = quantity_params + ytd_params + order_cnt_params + remote_cnt_params + where_params

        sql = """UPDATE STOCK SET
            S_QUANTITY = CASE
                %s
                ELSE S_QUANTITY
            END,
            S_YTD = CASE
                %s
                ELSE S_YTD
            END,
            S_ORDER_CNT = CASE
                %s
                ELSE S_ORDER_CNT
            END,
            S_REMOTE_CNT = CASE
                %s
                ELSE S_REMOTE_CNT
            END
            WHERE %s""" % (
            " ".join(quantity_cases),
            " ".join(ytd_cases),
            " ".join(order_cnt_cases),
            " ".join(remote_cnt_cases),
            " OR ".join(where_clauses),
        )
        self.cursor.execute(sql, params)

    def _batch_insert_order_lines(self, order_lines):
        if not order_lines:
            return
        placeholders = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(order_lines))
        sql = "INSERT INTO ORDER_LINE (OL_O_ID, OL_D_ID, OL_W_ID, OL_NUMBER, OL_I_ID, OL_SUPPLY_W_ID, OL_DELIVERY_D, OL_QUANTITY, OL_AMOUNT, OL_DIST_INFO) VALUES %s" % placeholders
        flat_params = []
        for line in order_lines:
            flat_params.extend(line)
        self.cursor.execute(sql, flat_params)
    # ------------------------------------------------------------------------------------------------------------------


    # ------------------------------------------------------------------------------------------------------------------
    # Transaction Methods
    # ------------------------------------------------------------------------------------------------------------------
    def doDelivery(self, params):
        q = TXN_QUERIES["DELIVERY"]

        w_id = params["w_id"]
        o_carrier_id = params["o_carrier_id"]
        ol_delivery_d = params["ol_delivery_d"]

        # COMBINING_QUERIES: Fetch all necessary info in one batch query across all districts.
        self.cursor.execute(q["batchNewOrders"], [w_id, w_id])
        rows = self.cursor.fetchall()

        result = []
        if rows:
            pairs = [] # (d_id, no_o_id) for batch deletes/updates
            customer_updates = [] # (c_id, d_id, ol_total) for batch customer balance updates
            for row in rows:
                d_id, no_o_id, c_id, ol_total = row
                assert ol_total is not None, "ol_total is NULL: there are no order lines"
                assert ol_total >= 0.0 # TPC-C allows 0 for some items, but typically > 0.0
                pairs.append((d_id, no_o_id))
                customer_updates.append((c_id, d_id, ol_total))
                result.append((d_id, no_o_id))

            # CONCURRENCY/BATCHING: Use batch helpers for all DML operations.
            self._batch_delete_new_orders(w_id, pairs)
            self._batch_update_orders(o_carrier_id, w_id, pairs)
            self._batch_update_order_lines(ol_delivery_d, w_id, pairs)
            self._batch_update_customers(w_id, customer_updates)

        self.conn.commit()
        return result

    def doNewOrder(self, params):
        q = TXN_QUERIES["NEW_ORDER"]

        w_id = params["w_id"]
        d_id = params["d_id"]
        c_id = params["c_id"]
        o_entry_d = params["o_entry_d"]
        i_ids = params["i_ids"]
        i_w_ids = params["i_w_ids"]
        i_qtys = params["i_qtys"]

        assert len(i_ids) > 0
        assert len(i_ids) == len(i_w_ids)
        assert len(i_ids) == len(i_qtys)

        all_local = True
        for i in range(len(i_ids)):
            all_local = all_local and i_w_ids[i] == w_id

        # CONCURRENCY: Batch item info lookup
        items_map = self._batch_items(i_ids)
        for i_id in i_ids:
            if i_id not in items_map:
                # TPC-C spec says if any item is not found, rollback.
                # However, the driver might choose to return None and let the caller rollback.
                # Per spec, if no item is found, return with no order created.
                return None

        # COMBINING_QUERIES & JOIN_ORDER_HINTS: Fetch WAREHOUSE, DISTRICT, CUSTOMER info in one go.
        self.cursor.execute(q["getMiscInfo"], [d_id, c_id, w_id])
        misc_row = self.cursor.fetchone()
        w_tax = misc_row[0]
        d_tax = misc_row[1]
        d_next_o_id = misc_row[2]
        c_discount = misc_row[3]
        customer_info_for_return = misc_row[3:6] # C_DISCOUNT, C_LAST, C_CREDIT for return value

        ol_cnt = len(i_ids)
        o_carrier_id = constants.NULL_CARRIER_ID # NULL_CARRIER_ID

        # Individual updates/inserts for core order creation.
        self.cursor.execute(q["incrementNextOrderId"], [d_next_o_id + 1, d_id, w_id])
        self.cursor.execute(q["createOrder"], [d_next_o_id, d_id, w_id, c_id, o_entry_d, o_carrier_id, ol_cnt, all_local])
        self.cursor.execute(q["createNewOrder"], [d_next_o_id, d_id, w_id])

        # CONCURRENCY: Batch stock info lookup.
        stock_pairs = [(i_ids[i], i_w_ids[i]) for i in range(len(i_ids))]
        stock_map = self._batch_stock_info(d_id, stock_pairs)

        stock_updates = []
        order_lines = []
        item_data = [] # For return value
        total = 0

        for i in range(len(i_ids)):
            ol_number = i + 1
            ol_supply_w_id = i_w_ids[i]
            ol_i_id = i_ids[i]
            ol_quantity = i_qtys[i]

            itemInfo = items_map.get(ol_i_id)
            # This check is already done above. If itemInfo is None, we would have returned.
            i_price = itemInfo[0]
            i_name = itemInfo[1]
            i_data = itemInfo[2]

            stock_key = (ol_i_id, ol_supply_w_id)
            stockInfo = stock_map.get(stock_key)
            if stockInfo is None:
                # This should not happen if _batch_stock_info returns comprehensive map.
                # If an item has no stock, it's an integrity error or bad test data.
                logging.warn("No STOCK record for (ol_i_id=%d, ol_supply_w_id=%d)" % (ol_i_id, ol_supply_w_id))
                continue

            s_quantity = stockInfo[0]
            s_data = stockInfo[1]
            s_ytd = stockInfo[2]
            s_order_cnt = stockInfo[3]
            s_remote_cnt = stockInfo[4]
            s_dist_xx = stockInfo[5]

            # TPC-C 2.5.1.3 Stock Quantity Logic
            s_ytd += ol_quantity
            if s_quantity >= ol_quantity + 10:
                s_quantity = s_quantity - ol_quantity
            else:
                s_quantity = s_quantity + 91 - ol_quantity
            s_order_cnt += 1
            if ol_supply_w_id != w_id:
                s_remote_cnt += 1

            stock_updates.append((s_quantity, s_ytd, s_order_cnt, s_remote_cnt, ol_i_id, ol_supply_w_id))

            if constants.ORIGINAL_STRING in i_data and constants.ORIGINAL_STRING in s_data:
                brand_generic = 'B'
            else:
                brand_generic = 'G'

            ol_amount = ol_quantity * i_price
            total += ol_amount

            # Prepare data for batch insert of ORDER_LINE
            order_lines.append((d_next_o_id, d_id, w_id, ol_number, ol_i_id, ol_supply_w_id, o_entry_d, ol_quantity, ol_amount, s_dist_xx))

            item_data.append((i_name, s_quantity, brand_generic, i_price, ol_amount))

        # CONCURRENCY/BATCHING: Use batch helpers for DML operations on Stock and Order_Line.
        self._batch_update_stock(stock_updates)
        self._batch_insert_order_lines(order_lines)

        self.conn.commit()

        total *= (1 - c_discount) * (1 + w_tax + d_tax)

        # Build return values
        misc = [(w_tax, d_tax, d_next_o_id, total)]

        return [customer_info_for_return, misc, item_data]

    def doOrderStatus(self, params):
        q = TXN_QUERIES["ORDER_STATUS"]

        w_id = params["w_id"]
        d_id = params["d_id"]
        c_id = params["c_id"]
        c_last = params["c_last"]

        assert w_id, pformat(params)
        assert d_id, pformat(params)

        if c_id is not None:
            # COMBINING_QUERIES: Fetch customer, last order, and order lines in one query.
            self.cursor.execute(q["getCustomerByIdWithLines"], [w_id, d_id, c_id, w_id, d_id, c_id])
            rows = self.cursor.fetchall()
            if not rows: # Customer or order not found, though TPC-C implies they should exist.
                customer = None
                order = None
                orderLines = []
            else:
                customer = rows[0][:5]
                if rows[0][5] is not None: # Check if order details are present (LEFT JOIN result)
                    order = rows[0][5:8]
                    orderLines = tuple((r[8], r[9], r[10], r[11], r[12]) for r in rows if r[8] is not None)
                else:
                    order = None
                    orderLines = ()
        else: # c_last is provided
            self.cursor.execute(q["getCustomersByLastName"], [w_id, d_id, c_last])
            all_customers = self.cursor.fetchall()
            assert len(all_customers) > 0 # TPC-C spec: "If no customer is found, the transaction is aborted."
            namecnt = len(all_customers)
            index = (namecnt - 1) // 2
            customer = all_customers[index]
            c_id = customer[0] # Get the median customer's C_ID

            # COMBINING_QUERIES: Fetch last order and its order lines for the identified customer.
            self.cursor.execute(q["getLastOrderWithLinesForCustomer"], [w_id, d_id, c_id])
            rows = self.cursor.fetchall()
            if rows:
                order = rows[0][:3]
                orderLines = tuple((r[3], r[4], r[5], r[6], r[7]) for r in rows if r[3] is not None)
            else:
                order = None
                orderLines = ()

        assert customer is not None
        assert c_id is not None

        self.conn.commit()
        return [customer, order, orderLines]

    def doPayment(self, params):
        q = TXN_QUERIES["PAYMENT"]

        w_id = params["w_id"]
        d_id = params["d_id"]
        h_amount = params["h_amount"]
        c_w_id = params["c_w_id"] # Customer's home W_ID
        c_d_id = params["c_d_id"] # Customer's home D_ID
        c_id = params["c_id"]
        c_last = params["c_last"]
        h_date = params["h_date"]

        # COMBINING_QUERIES & JOIN_ORDER_HINTS: Fetch payment WAREHOUSE and DISTRICT info.
        self.cursor.execute(q["getWarehouseAndDistrictInfo"], [d_id, w_id])
        wd_info = self.cursor.fetchone()
        warehouse = wd_info[:6]
        district = wd_info[6:]

        customer = None
        if c_id is not None:
            # Fetch customer details using their home W/D/C IDs.
            self.cursor.execute(q["getCustomerById"], [c_w_id, c_d_id, c_id])
            customer = self.cursor.fetchone()
        else: # c_last is provided
            # Fetch customers by last name using their home W/D.
            self.cursor.execute(q["getCustomersByLastName"], [c_w_id, c_d_id, c_last])
            all_customers = self.cursor.fetchall()
            assert len(all_customers) > 0
            namecnt = len(all_customers)
            index = (namecnt - 1) // 2
            customer = all_customers[index]
            c_id = customer[0] # Update c_id with the median customer's ID

        assert customer is not None and len(customer) > 0

        # Update customer balance details
        c_balance = customer[14] - h_amount
        c_ytd_payment = customer[15] + h_amount
        c_payment_cnt = customer[16] + 1
        c_data = customer[17]

        # Individual updates for warehouse and district balances
        self.cursor.execute(q["updateWarehouseBalance"], [h_amount, w_id])
        self.cursor.execute(q["updateDistrictBalance"], [h_amount, w_id, d_id])

        if customer[11] == constants.BAD_CREDIT:
            newData = " ".join(map(str, [c_id, c_d_id, c_w_id, d_id, w_id, h_amount]))
            c_data = (newData + "|" + c_data)
            if len(c_data) > constants.MAX_C_DATA:
                c_data = c_data[:constants.MAX_C_DATA]
            self.cursor.execute(q["updateBCCustomer"], [c_balance, c_ytd_payment, c_payment_cnt, c_data, c_w_id, c_d_id, c_id])
        else:
            # c_data is not updated for good credit customers
            self.cursor.execute(q["updateGCCustomer"], [c_balance, c_ytd_payment, c_payment_cnt, c_w_id, c_d_id, c_id])

        h_data = "%s    %s" % (warehouse[0], district[0])
        self.cursor.execute(q["insertHistory"], [c_id, c_d_id, c_w_id, d_id, w_id, h_date, h_amount, h_data])

        self.conn.commit()

        # Return customer's full original data + warehouse and district of payment
        return [warehouse, district, customer]

    def doStockLevel(self, params):
        q = TXN_QUERIES["STOCK_LEVEL"]

        w_id = params["w_id"]
        d_id = params["d_id"]
        threshold = params["threshold"]

        # SEPARATING_QUERIES: First get D_NEXT_O_ID, then use it in the main query.
        self.cursor.execute(q["getDistrictNextOid"], [w_id, d_id])
        result = self.cursor.fetchone()
        assert result, "District (W_ID=%d, D_ID=%d) not found" % (w_id, d_id)
        d_next_o_id = result[0]

        o_id_high = d_next_o_id
        o_id_low = o_id_high - 20

        # PREDICATE_PUSHDOWN: Filter ORDER_LINE in a derived table before joining STOCK.
        self.cursor.execute(q["getStockCount"], [w_id, d_id, o_id_high, o_id_low, w_id, threshold])
        result = self.cursor.fetchone()

        self.conn.commit()

        return int(result[0])
