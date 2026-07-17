import pytest
from datetime import datetime

from tpcc import constants


CUSTOMER_ROW = (
    1,
    "John", "M", "Doe",
    "123 Main St", "Apt 1", "Boston", "MA", "02101",
    "555-0100", datetime(2024, 1, 1), "GC", 50000.0, 0.05,
    1000.0, 500.0, 1, "data string",
)
WAREHOUSE_ROW = ("Warehouse_1", "100 Industrial Dr", "Suite 5", "Cambridge", "MA", "02142")
DISTRICT_ROW = ("District_1", "200 Main Ave", "Floor 3", "Somerville", "MA", "02143")
COMBINED_PAYMENT_ROW = CUSTOMER_ROW[:18] + WAREHOUSE_ROW + DISTRICT_ROW


@pytest.mark.usefixtures("driver", "mock_cursor", "mock_conn")
class TestDelivery:
    def test_returns_list_of_tuples(self, driver, mock_cursor):
        mock_cursor.fetchall.return_value = [
            (1, 10, 100, 50.0),
            (2, 20, 200, 75.0),
        ]
        params = {"w_id": 1, "o_carrier_id": 2, "ol_delivery_d": datetime.now()}
        result = driver.doDelivery(params)
        assert isinstance(result, list)
        assert len(result) == 2
        for item in result:
            assert isinstance(item, tuple) and len(item) == 2
            assert isinstance(item[0], int) and isinstance(item[1], int)

    def test_empty_no_pending(self, driver, mock_cursor):
        mock_cursor.fetchall.return_value = []
        params = {"w_id": 1, "o_carrier_id": 2, "ol_delivery_d": datetime.now()}
        result = driver.doDelivery(params)
        assert isinstance(result, list) and len(result) == 0

    def test_commits(self, driver, mock_cursor, mock_conn):
        mock_cursor.fetchall.return_value = [(1, 10, 100, 50.0)]
        driver.doDelivery({"w_id": 1, "o_carrier_id": 2, "ol_delivery_d": datetime.now()})
        assert mock_conn.commit.called


@pytest.mark.usefixtures("driver", "mock_cursor", "mock_conn")
class TestNewOrder:
    def test_returns_correct_structure(self, driver, mock_cursor):
        mock_cursor.fetchall.side_effect = [
            [(1, 10.0, "Item_A", "generic ORIGINAL data")],
            [(1, 1, 50, "generic data", 100, 5, 2, "dist01")],
        ]
        mock_cursor.fetchone.return_value = (0.08, 0.05, 1001, 0.05, "Doe", "GC")
        params = {"w_id": 1, "d_id": 1, "c_id": 100, "o_entry_d": datetime.now(),
                  "i_ids": [1], "i_w_ids": [1], "i_qtys": [5]}
        result = driver.doNewOrder(params)
        assert isinstance(result, list) and len(result) == 3
        customer_info, misc, item_data = result
        assert isinstance(customer_info, tuple)
        assert isinstance(misc, (list, tuple))
        assert isinstance(item_data, (list, tuple))

    def test_missing_items_returns_none(self, driver, mock_cursor):
        mock_cursor.fetchall.return_value = []
        params = {"w_id": 1, "d_id": 1, "c_id": 100, "o_entry_d": datetime.now(),
                  "i_ids": [999], "i_w_ids": [1], "i_qtys": [5]}
        assert driver.doNewOrder(params) is None

    def test_multiple_items(self, driver, mock_cursor):
        mock_cursor.fetchall.side_effect = [
            [(1, 10.0, "Item_A", "data"), (2, 20.0, "Item_B", "data")],
            [(1, 1, 50, "data", 100, 5, 2, "dist01"), (2, 1, 75, "data", 200, 3, 1, "dist01")],
        ]
        mock_cursor.fetchone.return_value = (0.08, 0.05, 1001, 0.05, "Doe", "GC")
        params = {"w_id": 1, "d_id": 1, "c_id": 100, "o_entry_d": datetime.now(),
                  "i_ids": [1, 2], "i_w_ids": [1, 1], "i_qtys": [5, 3]}
        result = driver.doNewOrder(params)
        assert result is not None and len(result[2]) == 2
        for entry in result[2]:
            assert len(entry) == 5
            i_name, s_quantity, brand, i_price, ol_amount = entry
            assert isinstance(i_name, str)
            assert isinstance(s_quantity, (int, float))
            assert brand in ("B", "G")
            assert isinstance(i_price, (int, float))
            assert isinstance(ol_amount, (int, float))

    def test_commits(self, driver, mock_cursor, mock_conn):
        mock_cursor.fetchall.side_effect = [
            [(1, 10.0, "Item_A", "data")],
            [(1, 1, 50, "data", 100, 5, 2, "dist01")],
        ]
        mock_cursor.fetchone.return_value = (0.08, 0.05, 1001, 0.05, "Doe", "GC")
        driver.doNewOrder({"w_id": 1, "d_id": 1, "c_id": 100, "o_entry_d": datetime.now(),
                           "i_ids": [1], "i_w_ids": [1], "i_qtys": [5]})
        assert mock_conn.commit.called


@pytest.mark.usefixtures("driver", "mock_cursor", "mock_conn")
class TestOrderStatus:
    def test_by_id_with_order(self, driver, mock_cursor):
        mock_cursor.fetchall.return_value = [
            (1, "John", "M", "Doe", 1000.0, 42, 2, datetime(2024, 6, 1), 1, 101, 5, 50.0, None),
            (1, "John", "M", "Doe", 1000.0, 42, 2, datetime(2024, 6, 1), 1, 102, 3, 30.0, None),
        ]
        params = {"w_id": 1, "d_id": 1, "c_id": 1, "c_last": None}
        result = driver.doOrderStatus(params)
        assert isinstance(result, list) and len(result) == 3
        customer, order, order_lines = result
        assert customer is not None and len(customer) >= 4
        assert order is not None and len(order) >= 2
        assert isinstance(order_lines, (list, tuple))

    def test_by_id_no_order(self, driver, mock_cursor):
        mock_cursor.fetchall.return_value = [
            (1, "John", "M", "Doe", 1000.0, None, None, None, None, None, None, None, None),
        ]
        params = {"w_id": 1, "d_id": 1, "c_id": 1, "c_last": None}
        result = driver.doOrderStatus(params)
        assert isinstance(result, list) and len(result) == 3
        customer, order, order_lines = result
        assert customer is not None
        assert order is None or order == ()

    def test_by_last_name(self, driver, mock_cursor):
        mock_cursor.fetchall.side_effect = [
            [(1, "John", "M", "Doe", 1000.0)],
            [(42, 2, datetime(2024, 6, 1), 1, 101, 5, 50.0, None)],
        ]
        params = {"w_id": 1, "d_id": 1, "c_id": None, "c_last": "Doe"}
        result = driver.doOrderStatus(params)
        assert isinstance(result, list) and len(result) == 3
        assert result[0] is not None
        assert result[1] is not None

    def test_commits(self, driver, mock_cursor, mock_conn):
        mock_cursor.fetchall.return_value = [
            (1, "John", "M", "Doe", 1000.0, 42, 2, datetime(2024, 6, 1), 1, 101, 5, 50.0, None),
        ]
        driver.doOrderStatus({"w_id": 1, "d_id": 1, "c_id": 1, "c_last": None})
        assert mock_conn.commit.called


@pytest.mark.usefixtures("driver", "mock_cursor", "mock_conn")
class TestPayment:
    def test_by_id_good_credit(self, driver, mock_cursor):
        mock_cursor.fetchone.return_value = COMBINED_PAYMENT_ROW
        params = {"w_id": 1, "d_id": 1, "h_amount": 100.0, "c_w_id": 1, "c_d_id": 1,
                  "c_id": 1, "c_last": None, "h_date": datetime.now()}
        result = driver.doPayment(params)
        assert isinstance(result, list) and len(result) == 3
        warehouse, district, customer = result
        assert isinstance(warehouse, tuple) and len(warehouse) == 6
        assert isinstance(district, tuple) and len(district) == 6
        assert isinstance(customer, tuple) and len(customer) >= 15

    def test_by_id_bad_credit(self, driver, mock_cursor):
        bc = list(COMBINED_PAYMENT_ROW)
        bc[11] = constants.BAD_CREDIT
        mock_cursor.fetchone.return_value = tuple(bc)
        params = {"w_id": 1, "d_id": 1, "h_amount": 100.0, "c_w_id": 1, "c_d_id": 1,
                  "c_id": 1, "c_last": None, "h_date": datetime.now()}
        assert driver.doPayment(params) is not None

    def test_by_last_name(self, driver, mock_cursor):
        mock_cursor.fetchall.return_value = [CUSTOMER_ROW]
        mock_cursor.fetchone.return_value = WAREHOUSE_ROW + DISTRICT_ROW
        params = {"w_id": 1, "d_id": 1, "h_amount": 100.0, "c_w_id": 1, "c_d_id": 1,
                  "c_id": None, "c_last": "Doe", "h_date": datetime.now()}
        result = driver.doPayment(params)
        assert isinstance(result, list) and len(result) == 3

    def test_commits(self, driver, mock_cursor, mock_conn):
        mock_cursor.fetchone.return_value = COMBINED_PAYMENT_ROW
        driver.doPayment({"w_id": 1, "d_id": 1, "h_amount": 100.0, "c_w_id": 1, "c_d_id": 1,
                          "c_id": 1, "c_last": None, "h_date": datetime.now()})
        assert mock_conn.commit.called


@pytest.mark.usefixtures("driver", "mock_cursor", "mock_conn")
class TestStockLevel:
    def test_returns_int(self, driver, mock_cursor):
        mock_cursor.fetchone.return_value = (5,)
        result = driver.doStockLevel({"w_id": 1, "d_id": 1, "threshold": 10})
        assert isinstance(result, int)
        assert result == 5

    def test_zero(self, driver, mock_cursor):
        mock_cursor.fetchone.return_value = (0,)
        assert driver.doStockLevel({"w_id": 1, "d_id": 1, "threshold": 10}) == 0

    def test_commits(self, driver, mock_cursor, mock_conn):
        mock_cursor.fetchone.return_value = (5,)
        driver.doStockLevel({"w_id": 1, "d_id": 1, "threshold": 10})
        assert mock_conn.commit.called
