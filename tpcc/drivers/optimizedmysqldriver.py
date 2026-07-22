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
        "getNewOrdersBatch": """
            SELECT NO_D_ID, MIN(NO_O_ID) 
            FROM NEW_ORDER 
            WHERE NO_W_ID = %s AND NO_O_ID > -1 
            GROUP BY NO_D_ID
        """,
        "getOrderTotalsBatch": """
            SELECT O_D_ID, O_ID, O_C_ID, SUM(OL_AMOUNT)
            FROM ORDERS
            JOIN ORDER_LINE ON OL_W_ID = O_W_ID AND OL_D_ID = O_D_ID AND OL_O_ID = O_ID
            WHERE O_W_ID = %s AND (O_D_ID, O_ID) IN __IN_CLAUSE__
            GROUP BY O_D_ID, O_ID, O_C_ID
        """,
        "deleteNewOrder": "DELETE FROM NEW_ORDER WHERE NO_D_ID = %s AND NO_W_ID = %s AND NO_O_ID = %s",
        "updateOrders": "UPDATE ORDERS SET O_CARRIER_ID = %s WHERE O_ID = %s AND O_D_ID = %s AND O_W_ID = %s",
        "updateOrderLine": "UPDATE ORDER_LINE SET OL_DELIVERY_D = %s WHERE OL_O_ID = %s AND OL_D_ID = %s AND OL_W_ID = %s",
        "updateCustomer": "UPDATE CUSTOMER SET C_BALANCE = C_BALANCE + %s WHERE C_ID = %s AND C_D_ID = %s AND C_W_ID = %s",
    },
    "NEW_ORDER": {
        "getNewOrderConfig": """
            SELECT W_TAX, D_TAX, D_NEXT_O_ID, C_DISCOUNT, C_LAST, C_CREDIT 
            FROM WAREHOUSE
            JOIN DISTRICT ON D_W_ID = W_ID AND D_ID = %s
            JOIN CUSTOMER ON C_W_ID = W_ID AND C_D_ID = D_ID AND C_ID = %s
            WHERE W_ID = %s
        """,
        "incrementNextOrderId": "UPDATE DISTRICT SET D_NEXT_O_ID = %s WHERE D_ID = %s AND D_W_ID = %s",
        "createOrder": "INSERT INTO ORDERS (O_ID, O_D_ID, O_W_ID, O_C_ID, O_ENTRY_D, O_CARRIER_ID, O_OL_CNT, O_ALL_LOCAL) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        "createNewOrder": "INSERT INTO NEW_ORDER (NO_O_ID, NO_D_ID, NO_W_ID) VALUES (%s, %s, %s)",
        "getItemInfo_batch_template": "SELECT I_ID, I_PRICE, I_NAME, I_DATA FROM ITEM WHERE I_ID IN __IN_CLAUSE__",
        "getStockInfo_batch_template": """
            SELECT S_I_ID, S_W_ID, S_QUANTITY, S_DATA, S_YTD, S_ORDER_CNT, S_REMOTE_CNT, S_DIST_%02d 
            FROM STOCK 
            WHERE (S_I_ID, S_W_ID) IN __IN_CLAUSE__
        """,
        "updateStock": "UPDATE STOCK SET S_QUANTITY = %s, S_YTD = %s, S_ORDER_CNT = %s, S_REMOTE_CNT = %s WHERE S_I_ID = %s AND S_W_ID = %s",
        "createOrderLine": "INSERT INTO ORDER_LINE (OL_O_ID, OL_D_ID, OL_W_ID, OL_NUMBER, OL_I_ID, OL_SUPPLY_W_ID, OL_DELIVERY_D, OL_QUANTITY, OL_AMOUNT, OL_DIST_INFO) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
    },
    "ORDER_STATUS": {
        "getCustomerByCustomerId": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_BALANCE FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "getCustomersByLastName": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_BALANCE FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_LAST = %s ORDER BY C_FIRST",
        "getLastOrderAndLines": """
            SELECT O_ID, O_CARRIER_ID, O_ENTRY_D, OL_SUPPLY_W_ID, OL_I_ID, OL_QUANTITY, OL_AMOUNT, OL_DELIVERY_D
            FROM ORDERS
            JOIN ORDER_LINE ON OL_W_ID = O_W_ID AND OL_D_ID = O_D_ID AND OL_O_ID = O_ID
            WHERE O_W_ID = %s AND O_D_ID = %s AND O_C_ID = %s
              AND O_ID = (SELECT MAX(O_ID) FROM ORDERS WHERE O_W_ID = %s AND O_D_ID = %s AND O_C_ID = %s)
        """,
    },
    "PAYMENT": {
        "getCustomerWithWarehouseAndDistrict": """
            SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_STREET_1, C_STREET_2, C_CITY, C_STATE, C_ZIP, C_PHONE, C_SINCE, C_CREDIT, C_CREDIT_LIM, C_DISCOUNT, C_BALANCE, C_YTD_PAYMENT, C_PAYMENT_CNT, C_DATA,
                   W_NAME, W_STREET_1, W_STREET_2, W_CITY, W_STATE, W_ZIP,
                   D_NAME, D_STREET_1, D_STREET_2, D_CITY, D_STATE, D_ZIP
            FROM CUSTOMER
            JOIN WAREHOUSE ON W_ID = C_W_ID
            JOIN DISTRICT ON D_W_ID = C_W_ID AND D_ID = C_D_ID
            WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s
        """,
        "getWarehouseAndDistrict": """
            SELECT W_NAME, W_STREET_1, W_STREET_2, W_CITY, W_STATE, W_ZIP,
                   D_NAME, D_STREET_1, D_STREET_2, D_CITY, D_STATE, D_ZIP
            FROM WAREHOUSE, DISTRICT
            WHERE W_ID = %s AND D_W_ID = W_ID AND D_ID = %s
        """,
        "getCustomersByLastName": "SELECT C_ID, C_FIRST, C_MIDDLE, C_LAST, C_STREET_1, C_STREET_2, C_CITY, C_STATE, C_ZIP, C_PHONE, C_SINCE, C_CREDIT, C_CREDIT_LIM, C_DISCOUNT, C_BALANCE, C_YTD_PAYMENT, C_PAYMENT_CNT, C_DATA FROM CUSTOMER WHERE C_W_ID = %s AND C_D_ID = %s AND C_LAST = %s ORDER BY C_FIRST",
        "updateWarehouseBalance": "UPDATE WAREHOUSE SET W_YTD = W_YTD + %s WHERE W_ID = %s",
        "updateDistrictBalance": "UPDATE DISTRICT SET D_YTD = D_YTD + %s WHERE D_W_ID  = %s AND D_ID = %s",
        "updateBCCustomer": "UPDATE CUSTOMER SET C_BALANCE = %s, C_YTD_PAYMENT = %s, C_PAYMENT_CNT = %s, C_DATA = %s WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "updateGCCustomer": "UPDATE CUSTOMER SET C_BALANCE = %s, C_YTD_PAYMENT = %s, C_PAYMENT_CNT = %s WHERE C_W_ID = %s AND C_D_ID = %s AND C_ID = %s",
        "insertHistory": "INSERT INTO HISTORY VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
    },
    "STOCK_LEVEL": {
        "getStockCount": """
            SELECT COUNT(DISTINCT(OL_I_ID)) 
            FROM ORDER_LINE
            JOIN DISTRICT ON D_W_ID = OL_W_ID AND D_ID = OL_D_ID
            JOIN STOCK ON S_W_ID = OL_W_ID AND S_I_ID = OL_I_ID
            WHERE OL_W_ID = %s
              AND OL_D_ID = %s
              AND OL_O_ID < D_NEXT_O_ID
              AND OL_O_ID >= D_NEXT_O_ID - 20
              AND S_QUANTITY < %s
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
        super(OptimizedmysqlDriver, self).__init__("optimizedmysql", ddl)
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
        self.cursor = self.conn.conn.cursor() if hasattr(self.conn, 'conn') else self.conn.cursor()

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
    ## doDelivery
    ## ----------------------------------------------
    def doDelivery(self, params):
        q = TXN_QUERIES["DELIVERY"]

        w_id = params["w_id"]
        o_carrier_id = params["o_carrier_id"]
        ol_delivery_d = params["ol_delivery_d"]

        # Batch find the oldest unpaid order ID for each district in this warehouse
        self.cursor.execute(q["getNewOrdersBatch"], [w_id])
        new_orders = self.cursor.fetchall()

        if not new_orders:
            self.conn.commit()
            return []

        # Batch retrieve customer ID and calculate total amount for the order lines
        pairs_placeholders = ",".join(["(%s, %s)"] * len(new_orders))
        flattened_params = []
        for d_id, no_o_id in new_orders:
            flattened_params.extend([d_id, no_o_id])

        query = q["getOrderTotalsBatch"].replace("__IN_CLAUSE__", f"({pairs_placeholders})")
        self.cursor.execute(query, [w_id] + flattened_params)
        totals_info = self.cursor.fetchall()

        # Map details by (d_id, no_o_id)
        order_details = {}
        for row in totals_info:
            order_details[(row[0], row[1])] = (row[2], row[3])

        delete_new_orders = []
        update_orders = []
        update_order_lines = []
        update_customers = []
        result = []

        for d_id, no_o_id in new_orders:
            if (d_id, no_o_id) not in order_details:
                continue
            c_id, ol_total = order_details[(d_id, no_o_id)]

            assert ol_total is not None, "ol_total is NULL: there are no order lines. This should not happen"
            assert ol_total > 0.0

            delete_new_orders.append((d_id, w_id, no_o_id))
            update_orders.append((o_carrier_id, no_o_id, d_id, w_id))
            update_order_lines.append((ol_delivery_d, no_o_id, d_id, w_id))
            update_customers.append((ol_total, c_id, d_id, w_id))
            result.append((d_id, no_o_id))

        if delete_new_orders:
            self.cursor.executemany(q["deleteNewOrder"], delete_new_orders)
            self.cursor.executemany(q["updateOrders"], update_orders)
            self.cursor.executemany(q["updateOrderLine"], update_order_lines)
            self.cursor.executemany(q["updateCustomer"], update_customers)

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

        # Batch query for all ITEM details in a single query
        item_rows = {}
        if i_ids:
            placeholders = ",".join(["%s"] * len(i_ids))
            query = q["getItemInfo_batch_template"].replace("__IN_CLAUSE__", f"({placeholders})")
            self.cursor.execute(query, i_ids)
            for r in self.cursor.fetchall():
                item_rows[r[0]] = (r[1], r[2], r[3]) # I_PRICE, I_NAME, I_DATA

        items = []
        all_local = True
        for i in range(len(i_ids)):
            all_local = all_local and (i_w_ids[i] == w_id)
            item = item_rows.get(i_ids[i])
            if item is None:
                return None
            items.append(item)

        # Combine WAREHOUSE, DISTRICT and CUSTOMER configs in a single query
        self.cursor.execute(q["getNewOrderConfig"], [d_id, c_id, w_id])
        config_info = self.cursor.fetchone()
        w_tax, d_tax, d_next_o_id, c_discount, c_last, c_credit = config_info
        customer_info = (c_discount, c_last, c_credit)

        ol_cnt = len(i_ids)
        o_carrier_id = constants.NULL_CARRIER_ID

        # District and Order updates
        self.cursor.execute(q["incrementNextOrderId"], [d_next_o_id + 1, d_id, w_id])
        self.cursor.execute(q["createOrder"], [d_next_o_id, d_id, w_id, c_id, o_entry_d, o_carrier_id, ol_cnt, all_local])
        self.cursor.execute(q["createNewOrder"], [d_next_o_id, d_id, w_id])

        # Batch query for STOCK info
        stock_pairs = []
        flattened_stock_params = []
        for i in range(len(i_ids)):
            stock_pairs.append((i_ids[i], i_w_ids[i]))
            flattened_stock_params.extend([i_ids[i], i_w_ids[i]])

        in_clause_stock_pairs = ",".join(["(%s, %s)"] * len(stock_pairs))
        query = q["getStockInfo_batch_template"] % d_id
        query = query.replace("__IN_CLAUSE__", f"({in_clause_stock_pairs})")
        self.cursor.execute(query, flattened_stock_params)

        stock_rows = {}
        for row in self.cursor.fetchall():
            stock_rows[(row[0], row[1])] = row

        item_data = []
        total = 0
        stock_updates = []
        order_line_inserts = []

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
            s_data = stockInfo[3]
            s_ytd = stockInfo[4]
            s_order_cnt = stockInfo[5]
            s_remote_cnt = stockInfo[6]
            s_dist_xx = stockInfo[7]

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

            order_line_inserts.append((d_next_o_id, d_id, w_id, ol_number, ol_i_id, ol_supply_w_id, o_entry_d, ol_quantity, ol_amount, s_dist_xx))
            item_data.append((i_name, s_quantity, brand_generic, i_price, ol_amount))

        # Perform batched writes
        if stock_updates:
            self.cursor.executemany(q["updateStock"], stock_updates)
            self.cursor.executemany(q["createOrderLine"], order_line_inserts)

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

        # Fetch last order and lines using a combined subquery join
        self.cursor.execute(q["getLastOrderAndLines"], [w_id, d_id, c_id, w_id, d_id, c_id])
        rows = self.cursor.fetchall()

        if rows:
            order = (rows[0][0], rows[0][1], rows[0][2])
            orderLines = [(r[3], r[4], r[5], r[6], r[7]) for r in rows]
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
            # Fetch Customer, Warehouse and District config info in a single join query
            self.cursor.execute(q["getCustomerWithWarehouseAndDistrict"], [w_id, d_id, c_id])
            row = self.cursor.fetchone()
            customer = row[:18]
            warehouse = row[18:24]
            district = row[24:]
        else:
            self.cursor.execute(q["getCustomersByLastName"], [w_id, d_id, c_last])
            all_customers = self.cursor.fetchall()
            assert len(all_customers) > 0
            namecnt = len(all_customers)
            index = (namecnt - 1) // 2
            customer = all_customers[index]
            c_id = customer[0]

            self.cursor.execute(q["getWarehouseAndDistrict"], [w_id, d_id])
            row = self.cursor.fetchone()
            warehouse = row[:6]
            district = row[6:]

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

        # Combined query joining ORDER_LINE, DISTRICT and STOCK to check stock count in a single trip
        self.cursor.execute(q["getStockCount"], [w_id, d_id, threshold])
        result = self.cursor.fetchone()

        self.conn.commit()

        return int(result[0])
