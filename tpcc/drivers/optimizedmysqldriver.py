from __future__ import with_statement

import os
import logging
from pprint import pprint, pformat

import tpcc.constants as constants
from .abstractdriver import *

try:
    import MySQLdb as mysql
except ImportError:
    import pymysql as mysql

TXN_QUERIES = {
    "DELIVERY": {
        "getNewOrdersBatch": "SELECT NO_D_ID, MIN(NO_O_ID) FROM NEW_ORDER WHERE NO_W_ID = %s AND NO_O_ID > -1 GROUP BY NO_D_ID",
        "getCustomersBatch": "SELECT O_D_ID, O_ID, O_C_ID FROM ORDERS WHERE O_W_ID = %s AND (O_D_ID, O_ID) IN __IN_CLAUSE__",
        "sumOLAmountsBatch": "SELECT OL_D_ID, OL_O_ID, SUM(OL_AMOUNT) FROM ORDER_LINE WHERE OL_W_ID = %s AND (OL_D_ID, OL_O_ID) IN __IN_CLAUSE__ GROUP BY OL_D_ID, OL_O_ID",
        "deleteNewOrder": "DELETE FROM NEW_ORDER WHERE NO_D_ID = %s AND NO_W_ID = %s AND NO_O_ID = %s",
        "updateOrders": "UPDATE ORDERS SET O_CARRIER_ID = %s WHERE O_ID = %s AND O_D_ID = %s AND O_W_ID = %s",
        "updateOrderLine": "UPDATE ORDER_LINE SET OL_DELIVERY_D = %s WHERE OL_O_ID = %s AND OL_D_ID = %s AND OL_W_ID = %s",
        "updateCustomer": "UPDATE CUSTOMER SET C_BALANCE = C_BALANCE + %s WHERE C_ID = %s AND C_D_ID = %s AND C_W_ID = %s",
    },
    "NEW_ORDER": {
        "getWarehouseDistrictCustomer": """
            SELECT W_TAX, D_TAX, D_NEXT_O_ID, C_DISCOUNT, C_LAST, C_CREDIT
            FROM WAREHOUSE
            JOIN DISTRICT ON D_W_ID = W_ID AND D_ID = %s
            JOIN CUSTOMER ON C_W_ID = W_ID AND C_D_ID = D_ID AND C_ID = %s
            WHERE W_ID = %s
        """,
        "incrementNextOrderId": "UPDATE DISTRICT SET D_NEXT_O_ID = %s WHERE D_ID = %s AND D_W_ID = %s",
        "createOrder": "INSERT INTO ORDERS (O_ID, O_D_ID, O_W_ID, O_C_ID, O_ENTRY_D, O_CARRIER_ID, O_OL_CNT, O_ALL_LOCAL) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        "createNewOrder": "INSERT INTO NEW_ORDER (NO_O_ID, NO_D_ID, NO_W_ID) VALUES (%s, %s, %s)",
        "getItemInfo_batch": "SELECT I_ID, I_PRICE, I_NAME, I_DATA FROM ITEM WHERE I_ID IN __IN_CLAUSE__",
        "getStockInfo_batch_template": "SELECT S_I_ID, S_W_ID, S_QUANTITY, S_DATA, S_YTD, S_ORDER_CNT, S_REMOTE_CNT, S_DIST_%02d FROM STOCK WHERE (S_I_ID, S_W_ID) IN __IN_CLAUSE__",
        "updateStock": "UPDATE STOCK SET S_QUANTITY = %s, S_YTD = %s, S_ORDER_CNT = %s, S_REMOTE_CNT = %s WHERE S_I_ID = %s AND S_W_ID = %s",
        "createOrderLine": "INSERT INTO ORDER_LINE (OL_O_ID, OL_D_ID, OL_W_ID, OL_NUMBER, OL_I_ID, OL_SUPPLY_W_ID, OL_DELIVERY_D, OL_QUANTITY, OL_AMOUNT, OL_DIST_INFO) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
    },

    "ORDER_STATUS": {
        "getCustomerByCustomerId": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_BALANCE FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "getCustomersByLastName": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_BALANCE FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_LAST = %s ORDER BY C_FIRST",
        "getLastOrderAndOrderLines": """
            SELECT O_ID, O_CARRIER_ID, O_ENTRY_D, OL_SUPPLY_W_ID, OL_I_ID, OL_QUANTITY, OL_AMOUNT, OL_DELIVERY_D
            FROM ORDERS
            JOIN ORDER_LINE ON OL_W_ID = O_W_ID AND OL_D_ID = O_D_ID AND OL_O_ID = O_ID
            WHERE O_W_ID = %s AND O_D_ID = %s AND O_C_ID = %s
              AND O_ID = (SELECT MAX(O_ID) FROM ORDERS WHERE O_W_ID = %s AND O_D_ID = %s AND O_C_ID = %s)
        """,
    },

    "PAYMENT": {
        "getWarehouseDistrictCustomer": """
            SELECT W_NAME, W_STREET_1, W_STREET_2, W_CITY, W_STATE, W_ZIP,
                   D_NAME, D_STREET_1, D_STREET_2, D_CITY, D_STATE, D_ZIP,
                   C_ID, C_FIRST, C_MIDDLE, C_LAST, C_STREET_1, C_STREET_2, C_CITY,
                   C_STATE, C_ZIP, C_PHONE, C_SINCE, C_CREDIT, C_CREDIT_LIM,
                   C_DISCOUNT, C_BALANCE, C_YTD_PAYMENT, C_PAYMENT_CNT, C_DATA
            FROM WAREHOUSE, DISTRICT, CUSTOMER
            WHERE W_ID = %s AND D_W_ID = W_ID AND D_ID = %s
              AND C_W_ID = %s AND C_D_ID = %s AND C_ID = %s
        """,
        "getWarehouseAndDistrict": """
            SELECT W_NAME, W_STREET_1, W_STREET_2, W_CITY, W_STATE, W_ZIP,
                   D_NAME, D_STREET_1, D_STREET_2, D_CITY, D_STATE, D_ZIP
            FROM WAREHOUSE, DISTRICT
            WHERE W_ID = %s AND D_W_ID = W_ID AND D_ID = %s
        """,
        "updateWarehouseBalance": "UPDATE WAREHOUSE SET W_YTD = W_YTD + %s WHERE W_ID = %s",
        "updateDistrictBalance": "UPDATE DISTRICT SET D_YTD = D_YTD + %s WHERE D_W_ID  = %s AND D_ID = %s",
        "getCustomersByLastName": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_STREET_1, C_STREET_2, C_CITY, C_STATE, C_ZIP, C_PHONE, C_SINCE, C_CREDIT, C_CREDIT_LIM, C_DISCOUNT, C_BALANCE, C_YTD_PAYMENT, C_PAYMENT_CNT, C_DATA FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_LAST = %s ORDER BY C_FIRST",
        "updateBCCustomer": "UPDATE CUSTOMER SET C_BALANCE = %s, C_YTD_PAYMENT = %s, C_PAYMENT_CNT = %s, C_DATA = %s WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "updateGCCustomer": "UPDATE CUSTOMER SET C_BALANCE = %s, C_YTD_PAYMENT = %s, C_PAYMENT_CNT = %s WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "insertHistory": "INSERT INTO HISTORY VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
    },

    "STOCK_LEVEL": {
        "getStockCount": """
            SELECT COUNT(DISTINCT OL_I_ID)
            FROM (
                SELECT OL_I_ID FROM ORDER_LINE
                WHERE OL_W_ID = %s AND OL_D_ID = %s
                  AND OL_O_ID < (SELECT D_NEXT_O_ID FROM DISTRICT WHERE D_W_ID = %s AND D_ID = %s)
                  AND OL_O_ID >= (SELECT D_NEXT_O_ID FROM DISTRICT WHERE D_W_ID = %s AND D_ID = %s) - 20
            ) OL
            JOIN STOCK ON S_I_ID = OL.OL_I_ID AND S_W_ID = %s
            WHERE S_QUANTITY < %s
        """,
    },
}


## ==============================================
## OptimizedmysqlDriver
## ==============================================
class OptimizedmysqlDriver(AbstractDriver):
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
        super(OptimizedmysqlDriver, self).__init__("mysql", ddl)
        self.host = None
        self.port = None
        self.user = None
        self.password = None
        self.database = None
        self.conn = None
        self.cursor = None

    ## ----------------------------------------------
    ## makeDefaultConfig
    ## ----------------------------------------------
    def makeDefaultConfig(self):
        return OptimizedmysqlDriver.DEFAULT_CONFIG

    ## ----------------------------------------------
    ## loadConfig
    ## ----------------------------------------------
    def loadConfig(self, config):
        for key in OptimizedmysqlDriver.DEFAULT_CONFIG:
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

    ## ----------------------------------------------
    ## loadTuples
    ## ----------------------------------------------
    def loadTuples(self, tableName, tuples):
        if len(tuples) == 0:
            return

        p = ["%s"] * len(tuples[0])
        sql = "INSERT INTO %s VALUES (%s)" % (tableName, ",".join(p))
        self.cursor.executemany(sql, tuples)

        logging.debug("Loaded %d tuples for tableName %s" % (len(tuples), tableName))

    ## ----------------------------------------------
    ## loadFinish
    ## ----------------------------------------------
    def loadFinish(self):
        logging.info("Committing changes to database")
        self.conn.commit()

    ## ----------------------------------------------
    ## Batch helper methods for loops
    ## ----------------------------------------------
    def _batch_items(self, i_ids):
        q = TXN_QUERIES["NEW_ORDER"]
        placeholders = ",".join(["%s"] * len(i_ids))
        query = q["getItemInfo_batch"].replace("__IN_CLAUSE__", f"({placeholders})")
        self.cursor.execute(query, i_ids)
        item_map = {row[0]: row[1:] for row in self.cursor.fetchall()}
        items = []
        for i_id in i_ids:
            item = item_map.get(i_id)
            if item is None:
                items.append(())
            else:
                items.append(item)
        return items

    def _batch_stock_info(self, d_id, i_ids, i_w_ids):
        q = TXN_QUERIES["NEW_ORDER"]
        stock_pairs = list(zip(i_ids, i_w_ids))
        placeholders = ",".join(["(%s, %s)"] * len(stock_pairs))
        query = q["getStockInfo_batch_template"] % d_id
        query = query.replace("__IN_CLAUSE__", f"({placeholders})")
        flattened = []
        for s_i_id, s_w_id in stock_pairs:
            flattened.extend([s_i_id, s_w_id])
        self.cursor.execute(query, flattened)
        return {(row[0], row[1]): row for row in self.cursor.fetchall()}

    def _batch_update_stock(self, params_list):
        q = TXN_QUERIES["NEW_ORDER"]
        self.cursor.executemany(q["updateStock"], params_list)

    def _batch_insert_order_lines(self, params_list):
        q = TXN_QUERIES["NEW_ORDER"]
        self.cursor.executemany(q["createOrderLine"], params_list)

    def _batch_delete_new_orders(self, params_list):
        q = TXN_QUERIES["DELIVERY"]
        self.cursor.executemany(q["deleteNewOrder"], params_list)

    def _batch_update_orders(self, params_list):
        q = TXN_QUERIES["DELIVERY"]
        self.cursor.executemany(q["updateOrders"], params_list)

    def _batch_update_order_lines(self, params_list):
        q = TXN_QUERIES["DELIVERY"]
        self.cursor.executemany(q["updateOrderLine"], params_list)

    def _batch_update_customers(self, params_list):
        q = TXN_QUERIES["DELIVERY"]
        self.cursor.executemany(q["updateCustomer"], params_list)

    ## ----------------------------------------------
    ## doDelivery
    ## ----------------------------------------------
    def doDelivery(self, params):
        q = TXN_QUERIES["DELIVERY"]

        w_id = params["w_id"]
        o_carrier_id = params["o_carrier_id"]
        ol_delivery_d = params["ol_delivery_d"]

        self.cursor.execute(q["getNewOrdersBatch"], [w_id])
        new_orders = self.cursor.fetchall()
        if not new_orders:
            self.conn.commit()
            return []

        pairs_placeholders = ",".join(["(%s, %s)"] * len(new_orders))
        flattened_pairs = []
        for d_id, no_o_id in new_orders:
            flattened_pairs.extend([d_id, no_o_id])

        query_cids = q["getCustomersBatch"].replace("__IN_CLAUSE__", f"({pairs_placeholders})")
        self.cursor.execute(query_cids, [w_id] + flattened_pairs)
        customer_ids = {(row[0], row[1]): row[2] for row in self.cursor.fetchall()}

        query_sums = q["sumOLAmountsBatch"].replace("__IN_CLAUSE__", f"({pairs_placeholders})")
        self.cursor.execute(query_sums, [w_id] + flattened_pairs)
        ol_sums = {(row[0], row[1]): row[2] for row in self.cursor.fetchall()}

        delete_new_orders = []
        update_orders = []
        update_order_lines = []
        update_customers = []
        result = []

        for d_id, no_o_id in new_orders:
            c_id = customer_ids.get((d_id, no_o_id))
            ol_total = ol_sums.get((d_id, no_o_id))

            if c_id is None or ol_total is None:
                continue

            assert ol_total is not None, "ol_total is NULL: there are no order lines. This should not happen"
            assert ol_total > 0.0

            delete_new_orders.append((d_id, w_id, no_o_id))
            update_orders.append((o_carrier_id, no_o_id, d_id, w_id))
            update_order_lines.append((ol_delivery_d, no_o_id, d_id, w_id))
            update_customers.append((ol_total, c_id, d_id, w_id))

            result.append((d_id, no_o_id))

        if delete_new_orders:
            self._batch_delete_new_orders(delete_new_orders)
        if update_orders:
            self._batch_update_orders(update_orders)
        if update_order_lines:
            self._batch_update_order_lines(update_order_lines)
        if update_customers:
            self._batch_update_customers(update_customers)

        self.conn.commit()
        return result

    ## ----------------------------------------------
    ## doNewOrder
    ## ----------------------------------------------
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

        items = self._batch_items(i_ids)
        assert len(items) == len(i_ids)

        for item in items:
            if len(item) == 0:
                return

        self.cursor.execute(q["getWarehouseDistrictCustomer"], [d_id, c_id, w_id])
        joined_info = self.cursor.fetchone()
        
        w_tax = joined_info[0]
        d_tax = joined_info[1]
        d_next_o_id = joined_info[2]
        c_discount = joined_info[3]
        c_last = joined_info[4]
        c_credit = joined_info[5]
        customer_info = (c_discount, c_last, c_credit)

        ol_cnt = len(i_ids)
        o_carrier_id = constants.NULL_CARRIER_ID

        self.cursor.execute(q["incrementNextOrderId"], [d_next_o_id + 1, d_id, w_id])
        self.cursor.execute(q["createOrder"], [d_next_o_id, d_id, w_id, c_id, o_entry_d, o_carrier_id, ol_cnt, all_local])
        self.cursor.execute(q["createNewOrder"], [d_next_o_id, d_id, w_id])

        stock_rows = self._batch_stock_info(d_id, i_ids, i_w_ids)

        item_data = []
        total = 0
        update_stock_params = []
        create_order_line_params = []

        for i in range(len(i_ids)):
            ol_number = i + 1
            ol_supply_w_id = i_w_ids[i]
            ol_i_id = i_ids[i]
            ol_quantity = i_qtys[i]

            itemInfo = items[i]
            i_name = itemInfo[1]
            i_data = itemInfo[2]
            i_price = itemInfo[0]

            stockInfo = stock_rows.get((ol_i_id, ol_supply_w_id))
            if stockInfo is None:
                logging.warn("No STOCK record for (ol_i_id=%d, ol_supply_w_id=%d)" % (ol_i_id, ol_supply_w_id))
                continue

            s_quantity = stockInfo[2]
            s_ytd = stockInfo[4]
            s_order_cnt = stockInfo[5]
            s_remote_cnt = stockInfo[6]
            s_data = stockInfo[3]
            s_dist_xx = stockInfo[7]

            s_ytd += ol_quantity
            if s_quantity >= ol_quantity + 10:
                s_quantity = s_quantity - ol_quantity
            else:
                s_quantity = s_quantity + 91 - ol_quantity
            s_order_cnt += 1

            if ol_supply_w_id != w_id:
                s_remote_cnt += 1

            update_stock_params.append((s_quantity, s_ytd, s_order_cnt, s_remote_cnt, ol_i_id, ol_supply_w_id))

            if i_data.find(constants.ORIGINAL_STRING) != -1 and s_data.find(constants.ORIGINAL_STRING) != -1:
                brand_generic = 'B'
            else:
                brand_generic = 'G'

            ol_amount = ol_quantity * i_price
            total += ol_amount

            create_order_line_params.append((d_next_o_id, d_id, w_id, ol_number, ol_i_id, ol_supply_w_id, o_entry_d, ol_quantity, ol_amount, s_dist_xx))

            item_data.append((i_name, s_quantity, brand_generic, i_price, ol_amount))

        if update_stock_params:
            self._batch_update_stock(update_stock_params)
        if create_order_line_params:
            self._batch_insert_order_lines(create_order_line_params)

        self.conn.commit()

        total *= (1 - c_discount) * (1 + w_tax + d_tax)

        misc = [(w_tax, d_tax, d_next_o_id, total)]

        return [customer_info, misc, item_data]

    ## ----------------------------------------------
    ## doOrderStatus
    ## ----------------------------------------------
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

        self.cursor.execute(q["getLastOrderAndOrderLines"], [w_id, d_id, c_id, w_id, d_id, c_id])
        rows = self.cursor.fetchall()

        if rows:
            order = (rows[0][0], rows[0][1], rows[0][2])
            orderLines = [r[3:8] for r in rows]
        else:
            order = None
            orderLines = []

        self.conn.commit()
        return [customer, order, orderLines]

    ## ----------------------------------------------
    ## doPayment
    ## ----------------------------------------------
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
            self.cursor.execute(q["getWarehouseDistrictCustomer"], [w_id, d_id, c_w_id, c_d_id, c_id])
            joined_info = self.cursor.fetchone()
            warehouse = joined_info[0:6]
            district = joined_info[6:12]
            customer = joined_info[12:]
        else:
            self.cursor.execute(q["getCustomersByLastName"], [c_w_id, c_d_id, c_last])
            all_customers = self.cursor.fetchall()
            assert len(all_customers) > 0
            namecnt = len(all_customers)
            index = (namecnt - 1) // 2
            customer = all_customers[index]
            c_id = customer[0]

            self.cursor.execute(q["getWarehouseAndDistrict"], [w_id, d_id])
            joined_info = self.cursor.fetchone()
            warehouse = joined_info[0:6]
            district = joined_info[6:12]

        assert len(customer) > 0
        c_balance = customer[14] - h_amount
        c_ytd_payment = customer[15] + h_amount
        c_payment_cnt = customer[16] + 1
        c_data = customer[17]

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

    ## ----------------------------------------------
    ## doStockLevel
    ## ----------------------------------------------
    def doStockLevel(self, params):
        q = TXN_QUERIES["STOCK_LEVEL"]

        w_id = params["w_id"]
        d_id = params["d_id"]
        threshold = params["threshold"]

        self.cursor.execute(q["getStockCount"], [w_id, d_id, w_id, d_id, w_id, d_id, w_id, threshold])
        result = self.cursor.fetchone()

        self.conn.commit()

        return int(result[0])
