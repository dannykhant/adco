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

TXN_QUERIES = {
    "DELIVERY": {
        "batchNewOrders": """
            SELECT n.NO_D_ID, n.NO_O_ID, o.O_C_ID,
                   COALESCE((SELECT SUM(OL_AMOUNT) FROM ORDER_LINE
                    WHERE OL_O_ID = n.NO_O_ID AND OL_D_ID = n.NO_D_ID
                      AND OL_W_ID = o.O_W_ID), 0) AS ol_total
            FROM (SELECT NO_D_ID, MIN(NO_O_ID) AS NO_O_ID
                  FROM NEW_ORDER
                  WHERE NO_W_ID = %s AND NO_O_ID > -1
                  GROUP BY NO_D_ID) n
            JOIN ORDERS o ON o.O_ID = n.NO_O_ID
                         AND o.O_D_ID = n.NO_D_ID
                         AND o.O_W_ID = %s
        """,
    },
    "NEW_ORDER": {
        "batchItemInfo": "SELECT I_ID, I_PRICE, I_NAME, I_DATA FROM ITEM WHERE I_ID IN (%s)",
        "getMiscInfo": """
            SELECT w.W_TAX, d.D_TAX, d.D_NEXT_O_ID, c.C_DISCOUNT, c.C_LAST, c.C_CREDIT
            FROM WAREHOUSE w
            STRAIGHT_JOIN DISTRICT d ON d.D_W_ID = w.W_ID AND d.D_ID = %s
            STRAIGHT_JOIN CUSTOMER c ON c.C_W_ID = w.W_ID AND c.C_D_ID = d.D_ID AND c.C_ID = %s
            WHERE w.W_ID = %s
        """,
        "incrementNextOrderId": "UPDATE DISTRICT SET D_NEXT_O_ID = %s WHERE D_ID = %s AND D_W_ID = %s",
        "createOrder": "INSERT INTO ORDERS (O_ID, O_D_ID, O_W_ID, O_C_ID, O_ENTRY_D, O_CARRIER_ID, O_OL_CNT, O_ALL_LOCAL) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        "createNewOrder": "INSERT INTO NEW_ORDER (NO_O_ID, NO_D_ID, NO_W_ID) VALUES (%s, %s, %s)",
        "batchStockInfo": "SELECT S_I_ID, S_W_ID, S_QUANTITY, S_DATA, S_YTD, S_ORDER_CNT, S_REMOTE_CNT, S_DIST_%02d FROM STOCK WHERE (S_I_ID, S_W_ID) IN (%s)",
        "updateStock": "UPDATE STOCK SET S_QUANTITY = %s, S_YTD = %s, S_ORDER_CNT = %s, S_REMOTE_CNT = %s WHERE S_I_ID = %s AND S_W_ID = %s",
        "createOrderLine": "INSERT INTO ORDER_LINE (OL_O_ID, OL_D_ID, OL_W_ID, OL_NUMBER, OL_I_ID, OL_SUPPLY_W_ID, OL_DELIVERY_D, OL_QUANTITY, OL_AMOUNT, OL_DIST_INFO) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
    },
    "ORDER_STATUS": {
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
            ) lo ON true
            LEFT JOIN ORDER_LINE ol ON ol.OL_W_ID = lo.O_W_ID AND ol.OL_D_ID = lo.O_D_ID AND ol.OL_O_ID = lo.O_ID
            WHERE c.C_W_ID = %s AND c.C_D_ID = %s AND c.C_ID = %s
            ORDER BY ol.OL_NUMBER
        """,
        "getCustomersByLastName": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_BALANCE FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_LAST = %s ORDER BY C_FIRST",
        "getLastOrderWithLines": """
            SELECT O.O_ID, O.O_CARRIER_ID, O.O_ENTRY_D,
                   OL.OL_SUPPLY_W_ID, OL.OL_I_ID, OL.OL_QUANTITY, OL.OL_AMOUNT, OL.OL_DELIVERY_D
            FROM ORDERS O
            LEFT JOIN ORDER_LINE OL
                ON OL.OL_W_ID = O.O_W_ID AND OL.OL_D_ID = O.O_D_ID AND OL.OL_O_ID = O.O_ID
            WHERE O.O_W_ID = %s AND O.O_D_ID = %s AND O.O_C_ID = %s
              AND O.O_ID = (SELECT MAX(O2.O_ID) FROM ORDERS O2
                            WHERE O2.O_W_ID = O.O_W_ID AND O2.O_D_ID = O.O_D_ID AND O2.O_C_ID = O.O_C_ID)
            ORDER BY OL.OL_NUMBER
        """,
    },
    "PAYMENT": {
        "getCustomerByCustomerId": """
            SELECT c.C_ID, c.C_FIRST, c.C_MIDDLE, c.C_LAST,
                   c.C_STREET_1, c.C_STREET_2, c.C_CITY, c.C_STATE, c.C_ZIP,
                   c.C_PHONE, c.C_SINCE, c.C_CREDIT, c.C_CREDIT_LIM,
                   c.C_DISCOUNT, c.C_BALANCE, c.C_YTD_PAYMENT, c.C_PAYMENT_CNT, c.C_DATA,
                   w.W_NAME, w.W_STREET_1, w.W_STREET_2, w.W_CITY, w.W_STATE, w.W_ZIP,
                   d.D_NAME, d.D_STREET_1, d.D_STREET_2, d.D_CITY, d.D_STATE, d.D_ZIP
            FROM WAREHOUSE w
            STRAIGHT_JOIN DISTRICT d ON d.D_W_ID = w.W_ID AND d.D_ID = %s
            STRAIGHT_JOIN CUSTOMER c ON c.C_W_ID = %s AND c.C_D_ID = %s AND c.C_ID = %s
            WHERE w.W_ID = %s
        """,
        "getCustomersByLastName": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_STREET_1, C_STREET_2, C_CITY, C_STATE, C_ZIP, C_PHONE, C_SINCE, C_CREDIT, C_CREDIT_LIM, C_DISCOUNT, C_BALANCE, C_YTD_PAYMENT, C_PAYMENT_CNT, C_DATA FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_LAST = %s ORDER BY C_FIRST",
        "getWarehouseAndDistrict": """
            SELECT w.W_NAME, w.W_STREET_1, w.W_STREET_2, w.W_CITY, w.W_STATE, w.W_ZIP,
                   d.D_NAME, d.D_STREET_1, d.D_STREET_2, d.D_CITY, d.D_STATE, d.D_ZIP
            FROM WAREHOUSE w
            STRAIGHT_JOIN DISTRICT d ON d.D_W_ID = w.W_ID AND d.D_ID = %s
            WHERE w.W_ID = %s
        """,
        "updateWarehouseBalance": "UPDATE WAREHOUSE SET W_YTD = W_YTD + %s WHERE W_ID = %s",
        "updateDistrictBalance": "UPDATE DISTRICT SET D_YTD = D_YTD + %s WHERE D_W_ID = %s AND D_ID = %s",
        "updateBCCustomer": "UPDATE CUSTOMER SET C_BALANCE = %s, C_YTD_PAYMENT = %s, C_PAYMENT_CNT = %s, C_DATA = %s WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "updateGCCustomer": "UPDATE CUSTOMER SET C_BALANCE = %s, C_YTD_PAYMENT = %s, C_PAYMENT_CNT = %s WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "insertHistory": "INSERT INTO HISTORY VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
    },
    "STOCK_LEVEL": {
        "getStockCount": """
            SELECT COUNT(DISTINCT(OL_I_ID))
            FROM ORDER_LINE
            JOIN STOCK ON S_W_ID = OL_W_ID AND S_I_ID = OL_I_ID AND S_QUANTITY < %s
            JOIN (SELECT D_NEXT_O_ID - 20 AS low, D_NEXT_O_ID AS high
                  FROM DISTRICT WHERE D_W_ID = %s AND D_ID = %s) b
            WHERE OL_W_ID = %s AND OL_D_ID = %s
              AND OL_O_ID < b.high
              AND OL_O_ID >= b.low
        """,
    },
}


class Gemini_2_5_Flash_20260718_1304Driver(AbstractDriver):
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
        super(Gemini_2_5_Flash_20260718_1304Driver, self).__init__("candidates", ddl)
        self.host = None
        self.port = None
        self.user = None
        self.password = None
        self.database = None
        self.conn = None
        self.cursor = None

    def makeDefaultConfig(self):
        return Gemini_2_5_Flash_20260718_1304Driver.DEFAULT_CONFIG

    def loadConfig(self, config):
        for key in Gemini_2_5_Flash_20260718_1304Driver.DEFAULT_CONFIG:
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

    def _batch_delete_new_orders(self, w_id, pairs):
        if not pairs:
            return
        # Using multi-value IN clause for efficiency
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
        # Using multi-value IN clause for efficiency
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
        # Using multi-value IN clause for efficiency
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
        # Dynamically construct CASE statements for multiple updates in one query
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

    def doDelivery(self, params):
        q = TXN_QUERIES["DELIVERY"]

        w_id = params["w_id"]
        o_carrier_id = params["o_carrier_id"]
        ol_delivery_d = params["ol_delivery_d"]

        # COMBINING_QUERIES: Fetch all necessary info for all districts in one query
        self.cursor.execute(q["batchNewOrders"], [w_id, w_id])
        rows = self.cursor.fetchall()

        result = []
        if rows:
            pairs = [] # (D_ID, O_ID) for DELETE and UPDATE
            customer_updates = [] # (C_ID, D_ID, OL_TOTAL) for customer balance updates
            for row in rows:
                d_id, no_o_id, c_id, ol_total = row
                assert ol_total is not None, "ol_total is NULL: there are no order lines"
                assert ol_total >= 0.0 # Could be 0 if OL_AMOUNT is 0, or if all items were removed
                pairs.append((d_id, no_o_id))
                customer_updates.append((c_id, d_id, ol_total))
                result.append((d_id, no_o_id))

            # CONCURRENCY / SEPARATING_QUERIES: Perform batch updates
            self._batch_delete_new_orders(w_id, pairs)
            self._batch_update_orders(o_carrier_id, w_id, pairs)
            self._batch_update_order_lines(ol_delivery_d, w_id, pairs)
            self._batch_update_customers(w_id, customer_updates)

        self.conn.commit()
        return result

    def _batch_items(self, i_ids):
        if not i_ids:
            return {}
        # CONCURRENCY: Batch item info lookup using IN clause
        placeholders = ",".join(["%s"] * len(i_ids))
        sql = "SELECT I_ID, I_PRICE, I_NAME, I_DATA FROM ITEM WHERE I_ID IN (%s)" % placeholders
        self.cursor.execute(sql, i_ids)
        rows = self.cursor.fetchall()
        return {row[0]: (row[1], row[2], row[3]) for row in rows}

    def _batch_stock_info(self, d_id, pairs):
        if not pairs:
            return {}
        # CONCURRENCY: Batch stock info lookup using multi-value IN clause
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
        # CONCURRENCY: Batch stock updates using CASE statements
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
        # CONCURRENCY: Batch order line inserts
        placeholders = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(order_lines))
        sql = "INSERT INTO ORDER_LINE (OL_O_ID, OL_D_ID, OL_W_ID, OL_NUMBER, OL_I_ID, OL_SUPPLY_W_ID, OL_DELIVERY_D, OL_QUANTITY, OL_AMOUNT, OL_DIST_INFO) VALUES %s" % placeholders
        flat_params = []
        for line in order_lines:
            flat_params.extend(line)
        self.cursor.execute(sql, flat_params)

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

        # CONCURRENCY: Batch item information lookup
        items_map = self._batch_items(i_ids)

        # Check if all items exist
        for i_id in i_ids:
            if i_id not in items_map:
                # TPC-C 2.4.2.3: If any item is not found, rollback.
                # The driver specification requires returning None.
                self.conn.rollback()
                return None

        # COMBINING_QUERIES + JOIN_ORDER_HINTS: Fetch WAREHOUSE, DISTRICT, CUSTOMER info in one query
        self.cursor.execute(q["getMiscInfo"], [d_id, d_id, c_id, w_id])
        misc_row = self.cursor.fetchone()
        w_tax = misc_row[0]
        d_tax = misc_row[1]
        d_next_o_id = misc_row[2]
        c_discount = misc_row[3]
        c_last = misc_row[4]
        c_credit = misc_row[5]

        ol_cnt = len(i_ids)
        o_carrier_id = constants.NULL_CARRIER_ID

        # These updates/inserts must be sequential as d_next_o_id is modified and used
        self.cursor.execute(q["incrementNextOrderId"], [d_next_o_id + 1, d_id, w_id])
        self.cursor.execute(q["createOrder"], [d_next_o_id, d_id, w_id, c_id, o_entry_d, o_carrier_id, ol_cnt, all_local])
        self.cursor.execute(q["createNewOrder"], [d_next_o_id, d_id, w_id])

        # CONCURRENCY: Batch stock information lookup
        stock_pairs = [(i_ids[i], i_w_ids[i]) for i in range(len(i_ids))]
        stock_map = self._batch_stock_info(d_id, stock_pairs)

        stock_updates = []
        order_lines = []
        item_data = []
        total = 0

        for i in range(len(i_ids)):
            ol_number = i + 1
            ol_supply_w_id = i_w_ids[i]
            ol_i_id = i_ids[i]
            ol_quantity = i_qtys[i]

            itemInfo = items_map[ol_i_id]
            i_price = itemInfo[0]
            i_name = itemInfo[1]
            i_data = itemInfo[2]

            stock_key = (ol_i_id, ol_supply_w_id)
            if stock_key not in stock_map:
                logging.warn("No STOCK record for (ol_i_id=%d, ol_supply_w_id=%d). Skipping this order line." % (ol_i_id, ol_supply_w_id))
                # TPC-C spec says to treat this as an 'item not found' case, but v2 allows partial orders
                # For strict adherence, this should probably lead to a rollback.
                # Following v2's behavior, we continue and it might lead to partial order_lines.
                continue

            stockInfo = stock_map[stock_key]
            s_quantity = stockInfo[0] # S_QUANTITY
            s_data = stockInfo[1]     # S_DATA
            s_ytd = stockInfo[2]      # S_YTD
            s_order_cnt = stockInfo[3] # S_ORDER_CNT
            s_remote_cnt = stockInfo[4] # S_REMOTE_CNT
            s_dist_xx = stockInfo[5]  # S_DIST_XX

            s_ytd += ol_quantity
            if s_quantity >= ol_quantity + 10:
                s_quantity = s_quantity - ol_quantity
            else:
                s_quantity = s_quantity + 91 - ol_quantity
            s_order_cnt += 1

            if ol_supply_w_id != w_id:
                s_remote_cnt += 1

            stock_updates.append((s_quantity, s_ytd, s_order_cnt, s_remote_cnt, ol_i_id, ol_supply_w_id))

            if i_data.find(constants.ORIGINAL_STRING) != -1 and s_data.find(constants.ORIGINAL_STRING) != -1:
                brand_generic = 'B'
            else:
                brand_generic = 'G'

            ol_amount = ol_quantity * i_price
            total += ol_amount

            order_lines.append((d_next_o_id, d_id, w_id, ol_number, ol_i_id, ol_supply_w_id, o_entry_d, ol_quantity, ol_amount, s_dist_xx))

            item_data.append((i_name, s_quantity, brand_generic, i_price, ol_amount))

        # CONCURRENCY: Batch stock updates and order line inserts
        self._batch_update_stock(stock_updates)
        self._batch_insert_order_lines(order_lines)

        self.conn.commit()

        total *= (1 - c_discount) * (1 + w_tax + d_tax)

        customer_info = (c_discount, c_last, c_credit) # Re-extract to match return format
        misc = [(w_tax, d_tax, d_next_o_id, total)]

        return [customer_info, misc, item_data]

    def doOrderStatus(self, params):
        q = TXN_QUERIES["ORDER_STATUS"]

        w_id = params["w_id"]
        d_id = params["d_id"]
        c_id = params["c_id"]
        c_last = params["c_last"]

        assert w_id, pformat(params)
        assert d_id, pformat(params)

        customer = None
        order = None
        orderLines = []

        if c_id is not None:
            # COMBINING_QUERIES: Fetch customer, last order, and order lines in one go
            self.cursor.execute(q["getCustomerByIdWithLines"], [w_id, d_id, c_id, w_id, d_id, c_id])
            rows = self.cursor.fetchall()
            if rows:
                customer = rows[0][:5]
                # Check if an order was found (O_ID is not None)
                if rows[0][5] is not None:
                    order = rows[0][5:8]
                    # Filter out rows where order line data might be NULL if there were no order lines
                    orderLines = tuple((r[8], r[9], r[10], r[11], r[12]) for r in rows if r[8] is not None)
            else:
                # Customer not found
                self.conn.rollback() # Or handle error as per TPC-C spec (abort transaction)
                return [None, None, []] # Return empty lists/None as per return format

        else: # Search by last name
            self.cursor.execute(q["getCustomersByLastName"], [w_id, d_id, c_last])
            all_customers = self.cursor.fetchall()
            if not all_customers:
                self.conn.rollback()
                return [None, None, []] # No customer found
            
            namecnt = len(all_customers)
            index = (namecnt - 1) // 2
            customer_row = all_customers[index]
            customer = customer_row[:5] # C_ID, C_FIRST, C_MIDDLE, C_LAST, C_BALANCE
            c_id = customer[0]

            # COMBINING_QUERIES: Fetch last order and order lines for the selected customer
            self.cursor.execute(q["getLastOrderWithLines"], [w_id, d_id, c_id])
            rows = self.cursor.fetchall()
            if rows:
                order = rows[0][:3] # O_ID, O_CARRIER_ID, O_ENTRY_D
                # Filter out rows where order line data might be NULL if there were no order lines
                orderLines = tuple((r[3], r[4], r[5], r[6], r[7]) for r in rows if r[3] is not None)

        assert customer is not None
        assert c_id is not None

        self.conn.commit()
        return [customer, order, orderLines]

    def doPayment(self, params):
        q = TXN_QUERIES["PAYMENT"]

        w_id = params["w_id"]
        d_id = params["d_id"]
        h_amount = params["h_amount"]
        c_w_id = params["c_w_id"]
        c_d_id = params["c_d_id"]
        c_id = params["c_id"]
        c_last = params["c_last"]
        h_date = params["h_date"]

        warehouse = None
        district = None
        customer = None

        if c_id is not None:
            # COMBINING_QUERIES + JOIN_ORDER_HINTS: Fetch customer, warehouse, district info in one query
            # Parameters for: d.D_ID, c.C_W_ID, c.C_D_ID, c.C_ID, w.W_ID
            self.cursor.execute(q["getCustomerByCustomerId"], [d_id, c_w_id, c_d_id, c_id, w_id])
            row = self.cursor.fetchone()
            if not row:
                self.conn.rollback()
                return [None, None, None] # Customer not found or other data missing

            customer = row[:18]
            warehouse = row[18:24]
            district = row[24:]
        else: # Search by last name
            self.cursor.execute(q["getCustomersByLastName"], [c_w_id, c_d_id, c_last])
            all_customers = self.cursor.fetchall()
            if not all_customers:
                self.conn.rollback()
                return [None, None, None] # No customer found

            namecnt = len(all_customers)
            index = (namecnt - 1) // 2
            customer = all_customers[index]
            c_id = customer[0] # Get C_ID for updates

            # JOIN_ORDER_HINTS: Fetch warehouse and district info
            self.cursor.execute(q["getWarehouseAndDistrict"], [d_id, w_id])
            row = self.cursor.fetchone()
            if not row:
                self.conn.rollback()
                return [None, None, None] # Warehouse/district not found

            warehouse = row[:6]
            district = row[6:]

        assert customer is not None
        assert warehouse is not None
        assert district is not None

        # Update customer balance values based on business logic
        c_balance = customer[14] - h_amount
        c_ytd_payment = customer[15] + h_amount
        c_payment_cnt = customer[16] + 1
        c_data = customer[17]

        # Update warehouse and district YTD balances
        self.cursor.execute(q["updateWarehouseBalance"], [h_amount, w_id])
        self.cursor.execute(q["updateDistrictBalance"], [h_amount, w_id, d_id])

        # Conditional customer data update
        if customer[11] == constants.BAD_CREDIT:
            newData = " ".join(map(str, [c_id, c_d_id, c_w_id, d_id, w_id, h_amount]))
            c_data = (newData + "|" + c_data)
            if len(c_data) > constants.MAX_C_DATA:
                c_data = c_data[:constants.MAX_C_DATA]
            self.cursor.execute(q["updateBCCustomer"], [c_balance, c_ytd_payment, c_payment_cnt, c_data, c_w_id, c_d_id, c_id])
        else:
            # c_data is not updated for good credit customers
            self.cursor.execute(q["updateGCCustomer"], [c_balance, c_ytd_payment, c_payment_cnt, c_w_id, c_d_id, c_id])

        # Insert history record
        h_data = "%s    %s" % (warehouse[0], district[0]) # W_NAME and D_NAME
        self.cursor.execute(q["insertHistory"], [c_id, c_d_id, c_w_id, d_id, w_id, h_date, h_amount, h_data])

        self.conn.commit()

        return [warehouse, district, customer]

    def doStockLevel(self, params):
        q = TXN_QUERIES["STOCK_LEVEL"]

        w_id = params["w_id"]
        d_id = params["d_id"]
        threshold = params["threshold"]

        # COMBINING_QUERIES + PREDICATE_PUSHDOWN:
        # Using a derived table for DISTRICT to get o_id range and joining directly with ORDER_LINE and STOCK.
        # This reduces round-trips to 1 and applies filters early.
        # Parameters for: S_QUANTITY, D_W_ID, D_ID, OL_W_ID, OL_D_ID
        self.cursor.execute(q["getStockCount"], [threshold, w_id, d_id, w_id, d_id])
        result = self.cursor.fetchone()

        self.conn.commit()

        return int(result[0])
