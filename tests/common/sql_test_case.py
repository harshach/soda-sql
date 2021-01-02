#  Copyright 2020 Soda
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#   http://www.apache.org/licenses/LICENSE-2.0
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
import json
import logging
import os
from typing import List, Optional
from unittest import TestCase

from sodasql.scan.scan_parse import ScanParse
from sodasql.scan.warehouse_configuration import WarehouseConfiguration
from sodasql.scan.warehouse_parse import WarehouseParse
from sodasql.scan.db import sql_update, sql_updates
from sodasql.scan.scan_configuration import ScanConfiguration
from sodasql.scan.scan_result import ScanResult
from sodasql.scan.warehouse import Warehouse
from tests.common.env_vars_helper import EnvVarsHelper
from tests.common.logging_helper import LoggingHelper
from tests.common.warehouse_fixture import WarehouseFixture

LoggingHelper.configure_for_test()
EnvVarsHelper.load_test_environment_properties()


TARGET_POSTGRES = 'postgres'
TARGET_SNOWFLAKE = 'snowflake'
TARGET_REDSHIFT = 'redshift'
TARGET_ATHENA = 'athena'
TARGET_BIGQUERY = 'bigquery'


def equals_ignore_case(left, right):
    if not isinstance(left, str):
        return False
    if not isinstance(right, str):
        return False
    return left.lower() == right.lower()


class SqlTestCase(TestCase):

    warehouse_cache_by_target = {}
    warehouse_fixture_cache_by_target = {}
    warehouses_close_enabled = True
    default_test_table_name = 'test_table'

    def __init__(self, methodName: str = ...) -> None:
        super().__init__(methodName)
        self.warehouse: Optional[Warehouse] = None
        self.target: Optional[str] = None

    def setUp(self) -> None:
        logging.debug(f'\n\n--- {str(self)} ---')
        super().setUp()
        self.warehouse = self.setup_get_warehouse()

    def setup_get_warehouse(self):
        """self.target may be initialized by a test suite"""
        if self.target is None:
            self.target = os.getenv('SODA_TEST_TARGET', TARGET_POSTGRES)

        warehouse = SqlTestCase.warehouse_cache_by_target.get(self.target)
        if warehouse is None:
            logging.debug(f'Creating warehouse {self.target}')
            warehouse_fixture = WarehouseFixture.create(self.target)
            profile_parse = self.parse_test_profile(self.target)
            profile_parse.parse_logs.assert_no_warnings_or_errors()

            warehouse = Warehouse(profile_parse.warehouse_configuration)
            warehouse_fixture.warehouse = warehouse
            warehouse_fixture.create_database()
            SqlTestCase.warehouse_cache_by_target[self.target] = warehouse
            SqlTestCase.warehouse_fixture_cache_by_target[self.target] = warehouse_fixture

        return warehouse

    def parse_test_profile(self, target: str) -> WarehouseParse:
        tests_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        profiles_yml_path = f'{tests_dir}/warehouses/{target}_profiles.yml'
        return WarehouseParse('test', profiles_yml_path=profiles_yml_path)

    def tearDown(self) -> None:
        if self.warehouse.connection:
            warehouse_fixture: WarehouseFixture = SqlTestCase.warehouse_fixture_cache_by_target[self.target]
            warehouse_fixture.tear_down()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.warehouses_close_enabled:
            cls.teardown_close_warehouses()

    @classmethod
    def teardown_close_warehouses(cls):
        for target in SqlTestCase.warehouse_cache_by_target:
            warehouse_fixture: WarehouseFixture = SqlTestCase.warehouse_fixture_cache_by_target[target]
            warehouse_fixture.drop_database()
        SqlTestCase.warehouse_cache_by_target = {}

    def sql_update(self, sql: str) -> int:
        return sql_update(self.warehouse.connection, sql)

    def sql_updates(self, sqls: List[str]):
        return sql_updates(self.warehouse.connection, sqls)

    def sql_create_table(self, table_name: str, columns: List[str], rows: List[str]):
        joined_columns = ", ".join(columns)
        joined_rows = ", ".join(rows)
        self.sql_updates([
            f"DROP TABLE IF EXISTS {table_name}",
            f"CREATE TABLE {table_name} ( {joined_columns} )",
            f"INSERT INTO {table_name} VALUES {joined_rows}"])

    def scan(self, scan_configuration_dict: dict) -> ScanResult:
        logging.debug('Scan configuration \n'+json.dumps(scan_configuration_dict, indent=2))
        scan_parse = ScanParse(scan_dict=scan_configuration_dict)
        scan_parse.parse_logs.assert_no_warnings_or_errors()
        scan = self.warehouse.create_scan(scan_parse.scan_configuration)
        return scan.execute()

    def assertMeasurements(self, scan_result, column: str, expected_metrics_present):
        metrics_present = [measurement.metric for measurement in scan_result.measurements
                           if equals_ignore_case(measurement.column_name, column)]
        self.assertEqual(set(metrics_present), set(expected_metrics_present))

    def assertMeasurementsPresent(self, scan_result, column: str, expected_metrics_present):
        metrics_present = [measurement.metric for measurement in scan_result.measurements
                           if equals_ignore_case(measurement.column_name, column)]
        metrics_expected_and_not_present = [expected_metric for expected_metric in expected_metrics_present
                                            if expected_metric not in metrics_present]
        self.assertEqual(set(), set(metrics_expected_and_not_present))

    def assertMeasurementsAbsent(self, scan_result, column: str, expected_metrics_absent: list):
        metrics_present = [measurement.metric for measurement in scan_result.measurements
                           if equals_ignore_case(measurement.column_name, column)]
        metrics_present_and_expected_absent = set(expected_metrics_absent) & set(metrics_present)
        self.assertEqual(set(), metrics_present_and_expected_absent)