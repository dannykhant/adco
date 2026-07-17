import inspect

import pytest

from tpcc.drivers.abstractdriver import AbstractDriver

TRANSACTIONS = ["DELIVERY", "NEW_ORDER", "ORDER_STATUS", "PAYMENT", "STOCK_LEVEL"]
METHODS = ["doDelivery", "doNewOrder", "doOrderStatus", "doPayment", "doStockLevel"]
REQUIRED_HELPERS = [
    "_batch_items",
    "_batch_stock_info",
    "_batch_update_stock",
    "_batch_insert_order_lines",
    "_batch_delete_new_orders",
    "_batch_update_orders",
    "_batch_update_order_lines",
    "_batch_update_customers",
]


class TestDriverStructure:
    def test_class_exists(self, driver):
        assert isinstance(driver, AbstractDriver)

    def test_class_name_convention(self, driver_class, driver_name):
        expected = "%sDriver" % driver_name.title()
        assert driver_class.__name__ == expected

    def test_all_transaction_methods_defined(self, driver):
        for method in METHODS:
            assert hasattr(driver, method), "Missing method: %s" % method
            assert callable(getattr(driver, method))

    def test_txn_queries_exists(self, driver, driver_module):
        assert hasattr(driver_module, "TXN_QUERIES"), "No module-level TXN_QUERIES dict"
        tq = driver_module.TXN_QUERIES
        for txn in TRANSACTIONS:
            assert txn in tq, "Missing TXN_QUERIES key: %s" % txn
            assert len(tq[txn]) > 0, "Empty TXN_QUERIES key: %s" % txn

    def test_required_helpers_defined(self, driver):
        for helper in REQUIRED_HELPERS:
            assert hasattr(driver, helper), "Missing helper: %s" % helper
            assert callable(getattr(driver, helper))

    def test_default_config_present(self, driver_class):
        assert hasattr(driver_class, "DEFAULT_CONFIG")


class TestTxnQueriesSqlPatterns:
    def _all_query_strings(self, driver_module):
        tq = driver_module.TXN_QUERIES
        for txn in TRANSACTIONS:
            for key, sql in tq[txn].items():
                yield txn, key, sql

    def test_no_double_percent_s_in_txn_queries(self, driver_module):
        for txn, key, sql in self._all_query_strings(driver_module):
            assert "%%s" not in sql, (
                "%s.%s contains %%s (use %%s for cursor.execute)" % (txn, key)
            )

    def test_delivery_batch_writes_have_w_id_guard(self, driver):
        guard_checks = [
            ("_batch_delete_new_orders", ["NO_W_ID"]),
            ("_batch_update_orders", ["O_W_ID"]),
            ("_batch_update_order_lines", ["OL_W_ID"]),
            ("_batch_update_customers", ["C_W_ID"]),
        ]
        for method_name, columns in guard_checks:
            method = getattr(driver, method_name, None)
            assert method is not None, "Missing helper: %s" % method_name
            try:
                source = inspect.getsource(method)
                for col in columns:
                    assert col in source, (
                        "%s missing w_id filter '%s'" % (method_name, col)
                    )
            except (TypeError, OSError):
                pass

    def test_delivery_has_batch_new_orders_query(self, driver_module):
        tq = driver_module.TXN_QUERIES
        assert "DELIVERY" in tq
        keys = [k.lower() for k in tq["DELIVERY"]]
        assert any("batch" in k or "new" in k for k in keys), (
            "DELIVERY TXN_QUERIES missing a batch/new_order query"
        )

    def test_new_order_has_get_misc_info_query(self, driver_module):
        tq = driver_module.TXN_QUERIES
        assert "NEW_ORDER" in tq
        keys = [k.lower() for k in tq["NEW_ORDER"]]
        assert any("misc" in k or "warehouse" in k or "district" in k or "customer" in k for k in keys), (
            "NEW_ORDER TXN_QUERIES missing a misc/join query for warehouse+district+customer"
        )

    def test_order_status_has_merged_query(self, driver_module):
        tq = driver_module.TXN_QUERIES
        assert "ORDER_STATUS" in tq
        keys = [k.lower() for k in tq["ORDER_STATUS"]]
        assert any("lines" in k or "withlines" in k or "lastorder" in k for k in keys), (
            "ORDER_STATUS TXN_QUERIES missing a merged query for order+lines"
        )

    def test_payment_has_merged_query(self, driver_module):
        tq = driver_module.TXN_QUERIES
        assert "PAYMENT" in tq
        keys = [k.lower() for k in tq["PAYMENT"]]
        has_merged = any("bycustomerid" in k or "customerby" in k for k in keys)
        has_warehouse_district = any("warehouse" in k and "district" in k for k in keys)
        assert has_merged or has_warehouse_district, (
            "PAYMENT TXN_QUERIES missing merged customer/warehouse/district query"
        )

    def test_stock_level_has_count_query(self, driver_module):
        tq = driver_module.TXN_QUERIES
        assert "STOCK_LEVEL" in tq
        keys = [k.lower() for k in tq["STOCK_LEVEL"]]
        assert any("count" in k or "stock" in k for k in keys), (
            "STOCK_LEVEL TXN_QUERIES missing a count query"
        )
