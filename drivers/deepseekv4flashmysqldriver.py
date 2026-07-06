from __future__ import with_statement

import os
import logging
from pprint import pprint, pformat

import constants
from .abstractdriver import *

try:
    import MySQLdb as mysql
except ImportError:
    import pymysql as mysql

TXN_QUERIES = {
    "DELIVERY": {
        "getNewOrder": "SELECT NO_O_ID, O_C_ID FROM NEW_ORDER INNER JOIN ORDERS ON O_ID = NO_O_ID AND O_D_ID = NO_D_ID AND O_W_ID = NO_W_ID WHERE NO_D_ID = %s AND NO_W_ID = %s LIMIT 1",
        "deleteNewOrder": "DELETE FROM NEW_ORDER WHERE NO_D_ID = %s AND NO_W_ID = %s AND NO_O_ID = %s",
        "getCId": "SELECT O_C_ID FROM ORDERS WHERE O_ID = %s AND O_D_ID = %s AND O_W_ID = %s",
        "updateOrders": "UPDATE ORDERS SET O_CARRIER_ID = %s WHERE O_ID = %s AND O_D_ID = %s AND O_W_ID = %s",
        "updateOrderLine": "UPDATE ORDER_LINE SET OL_DELIVERY_D = %s WHERE OL_O_ID = %s AND OL_D_ID = %s AND OL_W_ID = %s",
        "sumOLAmount": "SELECT SUM(OL_AMOUNT) FROM ORDER_LINE WHERE OL_O_ID = %s AND OL_D_ID = %s AND OL_W_ID = %s",
        "updateCustomer": "UPDATE CUSTOMER SET C_BALANCE = C_BALANCE + %s WHERE C_ID = %s AND C_D_ID = %s AND C_W_ID = %s",
    },
    "NEW_ORDER": {
        "getWarehouseTaxRate": "SELECT W_TAX FROM WAREHOUSE WHERE W_ID = %s",
        "getDistrict": "SELECT D_TAX, D_NEXT_O_ID FROM DISTRICT WHERE D_ID = %s AND D_W_ID = %s",
        "incrementNextOrderId": "UPDATE DISTRICT SET D_NEXT_O_ID = %s WHERE D_ID = %s AND D_W_ID = %s",
        "getCustomer": "SELECT C_DISCOUNT, C_LAST, C_CREDIT FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "createOrder": "INSERT INTO ORDERS (O_ID, O_D_ID, O_W_ID, O_C_ID, O_ENTRY_D, O_CARRIER_ID, O_OL_CNT, O_ALL_LOCAL) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        "createNewOrder": "INSERT INTO NEW_ORDER (NO_O_ID, NO_D_ID, NO_W_ID) VALUES (%s, %s, %s)",
        "getItemInfo": "SELECT I_ID, I_PRICE, I_NAME, I_DATA FROM ITEM WHERE I_ID IN (%s)",
        "getStockInfo": "SELECT S_I_ID, S_W_ID, S_QUANTITY, S_DATA, S_YTD, S_ORDER_CNT, S_REMOTE_CNT, S_DIST_%02d FROM STOCK WHERE (S_I_ID, S_W_ID) IN (%s)",
        "updateStock": "UPDATE STOCK SET S_QUANTITY = %s, S_YTD = %s, S_ORDER_CNT = %s, S_REMOTE_CNT = %s WHERE S_I_ID = %s AND S_W_ID = %s",
        "createOrderLine": "INSERT INTO ORDER_LINE (OL_O_ID, OL_D_ID, OL_W_ID, OL_NUMBER, OL_I_ID, OL_SUPPLY_W_ID, OL_DELIVERY_D, OL_QUANTITY, OL_AMOUNT, OL_DIST_INFO) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
    },

    "ORDER_STATUS": {
        "getCustomerByCustomerId": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_BALANCE FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "getCustomersByLastName": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_BALANCE FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_LAST = %s ORDER BY C_FIRST",
        "getLastOrder": "SELECT O_ID, O_CARRIER_ID, O_ENTRY_D FROM ORDERS WHERE O_W_ID = %s AND O_D_ID = %s AND O_C_ID = %s ORDER BY O_ID DESC LIMIT 1",
        "getOrderLines": "SELECT OL_SUPPLY_W_ID, OL_I_ID, OL_QUANTITY, OL_AMOUNT, OL_DELIVERY_D FROM ORDER_LINE WHERE OL_W_ID = %s AND OL_D_ID = %s AND OL_O_ID = %s",
    },

    "PAYMENT": {
        "getWarehouse": "SELECT W_NAME, W_STREET_1, W_STREET_2, W_CITY, W_STATE, W_ZIP FROM WAREHOUSE WHERE W_ID = %s",
        "updateWarehouseBalance": "UPDATE WAREHOUSE SET W_YTD = W_YTD + %s WHERE W_ID = %s",
        "getDistrict": "SELECT D_NAME, D_STREET_1, D_STREET_2, D_CITY, D_STATE, D_ZIP FROM DISTRICT WHERE D_W_ID = %s AND D_ID = %s",
        "updateDistrictBalance": "UPDATE DISTRICT SET D_YTD = D_YTD + %s WHERE D_W_ID  = %s AND D_ID = %s",
        "getCustomerByCustomerId": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_STREET_1, C_STREET_2, C_CITY, C_STATE, C_ZIP, C_PHONE, C_SINCE, C_CREDIT, C_CREDIT_LIM, C_DISCOUNT, C_BALANCE, C_YTD_PAYMENT, C_PAYMENT_CNT, C_DATA FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "getCustomersByLastName": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_STREET_1, C_STREET_2, C_CITY, C_STATE, C_ZIP, C_PHONE, C_SINCE, C_CREDIT, C_CREDIT_LIM, C_DISCOUNT, C_BALANCE, C_YTD_PAYMENT, C_PAYMENT_CNT, C_DATA FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_LAST = %s ORDER BY C_FIRST",
        "updateBCCustomer": "UPDATE CUSTOMER SET C_BALANCE = %s, C_YTD_PAYMENT = %s, C_PAYMENT_CNT = %s, C_DATA = %s WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "updateGCCustomer": "UPDATE CUSTOMER SET C_BALANCE = %s, C_YTD_PAYMENT = %s, C_PAYMENT_CNT = %s WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "insertHistory": "INSERT INTO HISTORY VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
    },

    "STOCK_LEVEL": {
        "getOId": "SELECT D_NEXT_O_ID FROM DISTRICT WHERE D_W_ID = %s AND D_ID = %s",
        "getStockCount": """
            SELECT COUNT(DISTINCT(OL_I_ID)) FROM ORDER_LINE
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
        """,
    },

}


ANALYTIC_QUERIES = {
    "Q1": """
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
    """,
    "Q2": """
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
    """,
    "Q3": """
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
    """,
    "Q4": """
        SELECT ol_w_id AS w_id,
               COUNT(DISTINCT ol_o_id) AS total_orders,
               SUM(ol_amount) AS revenue
        FROM ORDER_LINE
        GROUP BY ol_w_id
        ORDER BY revenue DESC
    """,
    "Q5": """
        SELECT c.c_w_id, c.c_d_id, c.c_id, c.c_first, c.c_last
        FROM CUSTOMER c
        WHERE NOT EXISTS (
            SELECT 1 FROM ORDERS o
            WHERE o.o_w_id = c.c_w_id AND o.o_d_id = c.c_d_id AND o.o_c_id = c.c_id
        )
    """,
    "Q6": """
        SELECT ol_w_id, ol_d_id, AVG(order_total) AS avg_order_value
        FROM (
            SELECT ol_w_id, ol_d_id, ol_o_id, SUM(ol_amount) AS order_total
            FROM ORDER_LINE
            GROUP BY ol_w_id, ol_d_id, ol_o_id
        ) t
        GROUP BY ol_w_id, ol_d_id
        ORDER BY ol_w_id, ol_d_id
    """,
    "Q7": """
        SELECT s.s_w_id,
               COUNT(*) AS total_items,
               SUM(s.s_quantity * i.i_price) AS inventory_value
        FROM STOCK s
        JOIN ITEM i ON s.s_i_id = i.i_id
        GROUP BY s.s_w_id
        ORDER BY inventory_value DESC
    """,
    "Q8": """
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
    """,
    "Q9": """
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
    """,
    "Q10": """
        SELECT ol_supply_w_id,
               COUNT(*) AS supplied_lines,
               COUNT(DISTINCT ol_o_id) AS affected_orders,
               SUM(ol_quantity) AS total_quantity,
               SUM(ol_amount) AS total_revenue
        FROM ORDER_LINE
        GROUP BY ol_supply_w_id
        ORDER BY total_revenue DESC
    """,
}


class Deepseekv4FlashmysqlDriver(AbstractDriver):
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
        super(Deepseekv4FlashmysqlDriver, self).__init__("deepseekv4flashmysql", ddl)
        self.host = None
        self.port = None
        self.user = None
        self.password = None
        self.database = None
        self.conn = None
        self.cursor = None

    def makeDefaultConfig(self):
        return Deepseekv4FlashmysqlDriver.DEFAULT_CONFIG

    def loadConfig(self, config):
        for key in Deepseekv4FlashmysqlDriver.DEFAULT_CONFIG:
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
                cursor.execute(statement)
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

    def doDelivery(self, params):
        q = TXN_QUERIES["DELIVERY"]

        w_id = params["w_id"]
        o_carrier_id = params["o_carrier_id"]
        ol_delivery_d = params["ol_delivery_d"]

        result = []
        for d_id in range(1, constants.DISTRICTS_PER_WAREHOUSE + 1):
            self.cursor.execute(q["getNewOrder"], [d_id, w_id])
            row = self.cursor.fetchone()
            if row is None:
                continue
            no_o_id = row[0]
            c_id = row[1]

            self.cursor.execute(q["sumOLAmount"], [no_o_id, d_id, w_id])
            ol_total = self.cursor.fetchone()[0]

            self.cursor.execute(q["deleteNewOrder"], [d_id, w_id, no_o_id])
            self.cursor.execute(q["updateOrders"], [o_carrier_id, no_o_id, d_id, w_id])
            self.cursor.execute(q["updateOrderLine"], [ol_delivery_d, no_o_id, d_id, w_id])

            assert ol_total is not None, "ol_total is NULL: there are no order lines. This should not happen"
            assert ol_total > 0.0

            self.cursor.execute(q["updateCustomer"], [ol_total, c_id, d_id, w_id])

            result.append((d_id, no_o_id))

        self.conn.commit()
        return result

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
        params = []

        for s_quantity, s_ytd, s_order_cnt, s_remote_cnt, i_id, w_id in updates:
            quantity_cases.append("WHEN (S_I_ID, S_W_ID) = (%s, %s) THEN %s")
            params.extend([i_id, w_id, s_quantity])
            ytd_cases.append("WHEN (S_I_ID, S_W_ID) = (%s, %s) THEN %s")
            params.extend([i_id, w_id, s_ytd])
            order_cnt_cases.append("WHEN (S_I_ID, S_W_ID) = (%s, %s) THEN %s")
            params.extend([i_id, w_id, s_order_cnt])
            remote_cnt_cases.append("WHEN (S_I_ID, S_W_ID) = (%s, %s) THEN %s")
            params.extend([i_id, w_id, s_remote_cnt])
            where_clauses.append("(S_I_ID = %s AND S_W_ID = %s)")
            params.extend([i_id, w_id])

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

        items_map = self._batch_items(i_ids)

        for i_id in i_ids:
            if i_id not in items_map:
                return

        self.cursor.execute(q["getWarehouseTaxRate"], [w_id])
        w_tax = self.cursor.fetchone()[0]

        self.cursor.execute(q["getDistrict"], [d_id, w_id])
        district_info = self.cursor.fetchone()
        d_tax = district_info[0]
        d_next_o_id = district_info[1]

        self.cursor.execute(q["getCustomer"], [w_id, d_id, c_id])
        customer_info = self.cursor.fetchone()
        c_discount = customer_info[0]

        ol_cnt = len(i_ids)
        o_carrier_id = constants.NULL_CARRIER_ID

        self.cursor.execute(q["incrementNextOrderId"], [d_next_o_id + 1, d_id, w_id])
        self.cursor.execute(q["createOrder"], [d_next_o_id, d_id, w_id, c_id, o_entry_d, o_carrier_id, ol_cnt, all_local])
        self.cursor.execute(q["createNewOrder"], [d_next_o_id, d_id, w_id])

        stock_pairs = []
        for i in range(len(i_ids)):
            stock_pairs.append((i_ids[i], i_w_ids[i]))

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
                logging.warn("No STOCK record for (ol_i_id=%d, ol_supply_w_id=%d)" % (ol_i_id, ol_supply_w_id))
                continue

            stockInfo = stock_map[stock_key]
            s_quantity = stockInfo[0]
            s_data = stockInfo[1]
            s_ytd = stockInfo[2]
            s_order_cnt = stockInfo[3]
            s_remote_cnt = stockInfo[4]
            s_dist_xx = stockInfo[5]

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

        self._batch_update_stock(stock_updates)
        self._batch_insert_order_lines(order_lines)

        self.conn.commit()

        total *= (1 - c_discount) * (1 + w_tax + d_tax)

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

        if c_id is not None:
            self.cursor.execute(q["getCustomerByCustomerId"], [w_id, d_id, c_id])
            customer = self.cursor.fetchone()
        else:
            self.cursor.execute(q["getCustomersByLastName"], [w_id, d_id, c_last])
            all_customers = self.cursor.fetchall()
            assert len(all_customers) > 0
            namecnt = len(all_customers)
            index = (namecnt - 1) // 2
            customer = all_customers[index]
            c_id = customer[0]
        assert len(customer) > 0
        assert c_id is not None

        self.cursor.execute(q["getLastOrder"], [w_id, d_id, c_id])
        order = self.cursor.fetchone()
        if order:
            self.cursor.execute(q["getOrderLines"], [w_id, d_id, order[0]])
            orderLines = self.cursor.fetchall()
        else:
            orderLines = []

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

        if c_id is not None:
            self.cursor.execute(q["getCustomerByCustomerId"], [w_id, d_id, c_id])
            customer = self.cursor.fetchone()
        else:
            self.cursor.execute(q["getCustomersByLastName"], [w_id, d_id, c_last])
            all_customers = self.cursor.fetchall()
            assert len(all_customers) > 0
            namecnt = len(all_customers)
            index = (namecnt - 1) // 2
            customer = all_customers[index]
            c_id = customer[0]
        assert len(customer) > 0
        c_balance = customer[14] - h_amount
        c_ytd_payment = customer[15] + h_amount
        c_payment_cnt = customer[16] + 1
        c_data = customer[17]

        self.cursor.execute(q["getWarehouse"], [w_id])
        warehouse = self.cursor.fetchone()

        self.cursor.execute(q["getDistrict"], [w_id, d_id])
        district = self.cursor.fetchone()

        self.cursor.execute(q["updateWarehouseBalance"], [h_amount, w_id])
        self.cursor.execute(q["updateDistrictBalance"], [h_amount, w_id, d_id])

        if customer[11] == constants.BAD_CREDIT:
            newData = " ".join(map(str, [c_id, c_d_id, c_w_id, d_id, w_id, h_amount]))
            c_data = (newData + "|" + c_data)
            if len(c_data) > constants.MAX_C_DATA:
                c_data = c_data[:constants.MAX_C_DATA]
            self.cursor.execute(q["updateBCCustomer"], [c_balance, c_ytd_payment, c_payment_cnt, c_data, c_w_id, c_d_id, c_id])
        else:
            c_data = ""
            self.cursor.execute(q["updateGCCustomer"], [c_balance, c_ytd_payment, c_payment_cnt, c_w_id, c_d_id, c_id])

        h_data = "%s    %s" % (warehouse[0], district[0])
        self.cursor.execute(q["insertHistory"], [c_id, c_d_id, c_w_id, d_id, w_id, h_date, h_amount, h_data])

        self.conn.commit()

        return [warehouse, district, customer]

    def doStockLevel(self, params):
        q = TXN_QUERIES["STOCK_LEVEL"]

        w_id = params["w_id"]
        d_id = params["d_id"]
        threshold = params["threshold"]

        self.cursor.execute(q["getOId"], [w_id, d_id])
        result = self.cursor.fetchone()
        assert result
        o_id = result[0]

        self.cursor.execute(q["getStockCount"], [w_id, d_id, o_id, (o_id - 20), w_id, threshold])
        result = self.cursor.fetchone()

        self.conn.commit()

        return int(result[0])

    def doAnalyticsQuery(self, query_name, params=None):
        if query_name not in ANALYTIC_QUERIES:
            raise ValueError("Unknown analytic query: %s" % query_name)
        sql = ANALYTIC_QUERIES[query_name]
        if params:
            self.cursor.execute(sql, params)
        else:
            self.cursor.execute(sql)
        rows = self.cursor.fetchall()
        self.conn.commit()
        return rows
