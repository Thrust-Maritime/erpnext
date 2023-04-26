# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import copy

import frappe
from frappe.tests.utils import FrappeTestCase, change_settings, timeout
from frappe.utils import add_days, add_months, add_to_date, cint, flt, now, today

from erpnext.manufacturing.doctype.job_card.job_card import JobCardCancelError
from erpnext.manufacturing.doctype.production_plan.test_production_plan import make_bom
from erpnext.manufacturing.doctype.work_order.work_order import (
	CapacityError,
	ItemHasVariantError,
	OverProductionError,
	StockOverProductionError,
	close_work_order,
	make_job_card,
	make_stock_entry,
	make_stock_return_entry,
	stop_unstop,
)
from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
from erpnext.stock.doctype.item.test_item import create_item, make_item
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from erpnext.stock.doctype.stock_entry import test_stock_entry
from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse
from erpnext.stock.utils import get_bin

test_dependencies = ["BOM"]


class TestWorkOrder(FrappeTestCase):
	def setUp(self):
		self.warehouse = "_Test Warehouse 2 - _TC"
		self.item = "_Test Item"
		prepare_data_for_backflush_based_on_materials_transferred()

	def tearDown(self):
		frappe.db.rollback()

	def check_planned_qty(self):

		planned0 = (
			frappe.db.get_value(
				"Bin", {"item_code": "_Test FG Item", "warehouse": "_Test Warehouse 1 - _TC"}, "planned_qty"
			)
			or 0
		)

		wo_order = make_wo_order_test_record()

		planned1 = frappe.db.get_value(
			"Bin", {"item_code": "_Test FG Item", "warehouse": "_Test Warehouse 1 - _TC"}, "planned_qty"
		)

		self.assertEqual(planned1, planned0 + 10)

		# add raw materials to stores
		test_stock_entry.make_stock_entry(
			item_code="_Test Item", target="Stores - _TC", qty=100, basic_rate=100
		)
		test_stock_entry.make_stock_entry(
			item_code="_Test Item Home Desktop 100", target="Stores - _TC", qty=100, basic_rate=100
		)

		# from stores to wip
		s = frappe.get_doc(make_stock_entry(wo_order.name, "Material Transfer for Manufacture", 4))
		for d in s.get("items"):
			d.s_warehouse = "Stores - _TC"
		s.insert()
		s.submit()

		# from wip to fg
		s = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 4))
		s.insert()
		s.submit()

		self.assertEqual(frappe.db.get_value("Work Order", wo_order.name, "produced_qty"), 4)

		planned2 = frappe.db.get_value(
			"Bin", {"item_code": "_Test FG Item", "warehouse": "_Test Warehouse 1 - _TC"}, "planned_qty"
		)

		self.assertEqual(planned2, planned0 + 6)

		return wo_order

	def test_over_production(self):
		wo_doc = self.check_planned_qty()

		test_stock_entry.make_stock_entry(
			item_code="_Test Item", target="_Test Warehouse - _TC", qty=100, basic_rate=100
		)
		test_stock_entry.make_stock_entry(
			item_code="_Test Item Home Desktop 100", target="_Test Warehouse - _TC", qty=100, basic_rate=100
		)

		s = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 7))
		s.insert()

		self.assertRaises(StockOverProductionError, s.submit)

	def test_planned_operating_cost(self):
		wo_order = make_wo_order_test_record(
			item="_Test FG Item 2", planned_start_date=now(), qty=1, do_not_save=True
		)
		wo_order.set_work_order_operations()
		cost = wo_order.planned_operating_cost
		wo_order.qty = 2
		wo_order.set_work_order_operations()
		self.assertEqual(wo_order.planned_operating_cost, cost * 2)

	def test_reserved_qty_for_partial_completion(self):
		item = "_Test Item"
		warehouse = "_Test Warehouse - _TC"

		bin1_at_start = get_bin(item, warehouse)

		# reset to correct value
		bin1_at_start.update_reserved_qty_for_production()

		wo_order = make_wo_order_test_record(
			item="_Test FG Item", qty=2, source_warehouse=warehouse, skip_transfer=1
		)

		reserved_qty_on_submission = cint(get_bin(item, warehouse).reserved_qty_for_production)

		# reserved qty for production is updated
		self.assertEqual(cint(bin1_at_start.reserved_qty_for_production) + 2, reserved_qty_on_submission)

		test_stock_entry.make_stock_entry(
			item_code="_Test Item", target=warehouse, qty=100, basic_rate=100
		)
		test_stock_entry.make_stock_entry(
			item_code="_Test Item Home Desktop 100", target=warehouse, qty=100, basic_rate=100
		)

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 1))
		s.submit()

		bin1_at_completion = get_bin(item, warehouse)

		self.assertEqual(
			cint(bin1_at_completion.reserved_qty_for_production), reserved_qty_on_submission - 1
		)

	def test_production_item(self):
		wo_order = make_wo_order_test_record(item="_Test FG Item", qty=1, do_not_save=True)
		frappe.db.set_value("Item", "_Test FG Item", "end_of_life", "2000-1-1")

		self.assertRaises(frappe.ValidationError, wo_order.save)

		frappe.db.set_value("Item", "_Test FG Item", "end_of_life", None)
		frappe.db.set_value("Item", "_Test FG Item", "disabled", 1)

		self.assertRaises(frappe.ValidationError, wo_order.save)

		frappe.db.set_value("Item", "_Test FG Item", "disabled", 0)

		wo_order = make_wo_order_test_record(item="_Test Variant Item", qty=1, do_not_save=True)
		self.assertRaises(ItemHasVariantError, wo_order.save)

	def test_reserved_qty_for_production_submit(self):
		self.bin1_at_start = get_bin(self.item, self.warehouse)

		# reset to correct value
		self.bin1_at_start.update_reserved_qty_for_production()

		self.wo_order = make_wo_order_test_record(
			item="_Test FG Item", qty=2, source_warehouse=self.warehouse
		)

		self.bin1_on_submit = get_bin(self.item, self.warehouse)

		# reserved qty for production is updated
		self.assertEqual(
			cint(self.bin1_at_start.reserved_qty_for_production) + 2,
			cint(self.bin1_on_submit.reserved_qty_for_production),
		)
		self.assertEqual(
			cint(self.bin1_at_start.projected_qty), cint(self.bin1_on_submit.projected_qty) + 2
		)

	def test_reserved_qty_for_production_cancel(self):
		self.test_reserved_qty_for_production_submit()

		self.wo_order.cancel()

		bin1_on_cancel = get_bin(self.item, self.warehouse)

		# reserved_qty_for_producion updated
		self.assertEqual(
			cint(self.bin1_at_start.reserved_qty_for_production),
			cint(bin1_on_cancel.reserved_qty_for_production),
		)
		self.assertEqual(self.bin1_at_start.projected_qty, cint(bin1_on_cancel.projected_qty))

	def test_reserved_qty_for_production_on_stock_entry(self):
		test_stock_entry.make_stock_entry(
			item_code="_Test Item", target=self.warehouse, qty=100, basic_rate=100
		)
		test_stock_entry.make_stock_entry(
			item_code="_Test Item Home Desktop 100", target=self.warehouse, qty=100, basic_rate=100
		)

		self.test_reserved_qty_for_production_submit()

		s = frappe.get_doc(make_stock_entry(self.wo_order.name, "Material Transfer for Manufacture", 2))

import frappe
from frappe import _, msgprint
from frappe.utils import cint, cstr, flt

import erpnext
from erpnext.accounts.utils import get_company_default
from erpnext.controllers.stock_controller import StockController
from erpnext.stock.doctype.batch.batch import get_batch_qty
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from erpnext.stock.utils import get_stock_balance


class OpeningEntryAccountError(frappe.ValidationError):
	pass


class EmptyStockReconciliationItemsError(frappe.ValidationError):
	pass


class StockReconciliation(StockController):
	def __init__(self, *args, **kwargs):
		super(StockReconciliation, self).__init__(*args, **kwargs)
		self.head_row = ["Item Code", "Warehouse", "Quantity", "Valuation Rate"]

	def validate(self):
		if not self.expense_account:
			self.expense_account = frappe.get_cached_value(
				"Company", self.company, "stock_adjustment_account"
			)
		if not self.cost_center:
			self.cost_center = frappe.get_cached_value("Company", self.company, "cost_center")
		self.validate_posting_time()
		self.remove_items_with_no_change()
		self.validate_data()
		self.validate_expense_account()
		self.validate_customer_provided_item()
		self.set_zero_value_for_customer_provided_items()
		self.clean_serial_nos()
		self.set_total_qty_and_amount()
		self.validate_putaway_capacity()

		if self._action == "submit":
			self.make_batches("warehouse")

	def on_submit(self):
		self.update_stock_ledger()
		self.make_gl_entries()
		self.repost_future_sle_and_gle()

		from erpnext.stock.doctype.serial_no.serial_no import update_serial_nos_after_submit

		update_serial_nos_after_submit(self, "items")

	def on_cancel(self):
		self.ignore_linked_doctypes = ("GL Entry", "Stock Ledger Entry", "Repost Item Valuation")
		self.make_sle_on_cancel()
		self.make_gl_entries_on_cancel()
		self.repost_future_sle_and_gle()
		self.delete_auto_created_batches()

	def remove_items_with_no_change(self):
		"""Remove items if qty or rate is not changed"""
		self.difference_amount = 0.0

		def _changed(item):
			item_dict = get_stock_balance_for(
				item.item_code, item.warehouse, self.posting_date, self.posting_time, batch_no=item.batch_no
			)

			if (
				(item.qty is None or item.qty == item_dict.get("qty"))
				and (item.valuation_rate is None or item.valuation_rate == item_dict.get("rate"))
				and (not item.serial_no or (item.serial_no == item_dict.get("serial_nos")))
			):
				return False
			else:
				# set default as current rates
				if item.qty is None:
					item.qty = item_dict.get("qty")

				if item.valuation_rate is None:
					item.valuation_rate = item_dict.get("rate")

				if item_dict.get("serial_nos"):
					item.current_serial_no = item_dict.get("serial_nos")
					if self.purpose == "Stock Reconciliation" and not item.serial_no:
						item.serial_no = item.current_serial_no

				item.current_qty = item_dict.get("qty")
				item.current_valuation_rate = item_dict.get("rate")
				self.difference_amount += flt(item.qty, item.precision("qty")) * flt(
					item.valuation_rate or item_dict.get("rate"), item.precision("valuation_rate")
				) - flt(item_dict.get("qty"), item.precision("qty")) * flt(
					item_dict.get("rate"), item.precision("valuation_rate")
				)
				return True

		items = list(filter(lambda d: _changed(d), self.items))

		if not items:
			frappe.throw(
				_("None of the items have any change in quantity or value."),
				EmptyStockReconciliationItemsError,
			)

		elif len(items) != len(self.items):
			self.items = items
			for i, item in enumerate(self.items):
				item.idx = i + 1
			frappe.msgprint(_("Removed items with no change in quantity or value."))

	def validate_data(self):
		def _get_msg(row_num, msg):
			return _("Row # {0}:").format(row_num + 1) + " " + msg

		self.validation_messages = []
		item_warehouse_combinations = []

		default_currency = frappe.db.get_default("currency")

		for row_num, row in enumerate(self.items):
			# find duplicates
			key = [row.item_code, row.warehouse]
			for field in ["serial_no", "batch_no"]:
				if row.get(field):
					key.append(row.get(field))

			if key in item_warehouse_combinations:
				self.validation_messages.append(_get_msg(row_num, _("Duplicate entry")))
			else:
				item_warehouse_combinations.append(key)

			self.validate_item(row.item_code, row)

			# validate warehouse
			if not frappe.db.get_value("Warehouse", row.warehouse):
				self.validation_messages.append(_get_msg(row_num, _("Warehouse not found in the system")))

			# if both not specified
			if row.qty in ["", None] and row.valuation_rate in ["", None]:
				self.validation_messages.append(
					_get_msg(row_num, _("Please specify either Quantity or Valuation Rate or both"))
				)

			# do not allow negative quantity
			if flt(row.qty) < 0:
				self.validation_messages.append(_get_msg(row_num, _("Negative Quantity is not allowed")))

			# do not allow negative valuation
			if flt(row.valuation_rate) < 0:
				self.validation_messages.append(_get_msg(row_num, _("Negative Valuation Rate is not allowed")))

			if row.qty and row.valuation_rate in ["", None]:
				row.valuation_rate = get_stock_balance(
					row.item_code, row.warehouse, self.posting_date, self.posting_time, with_valuation_rate=True
				)[1]
				if not row.valuation_rate:
					# try if there is a buying price list in default currency
					buying_rate = frappe.db.get_value(
						"Item Price",
						{"item_code": row.item_code, "buying": 1, "currency": default_currency},
						"price_list_rate",
					)
					if buying_rate:
						row.valuation_rate = buying_rate

					else:
						# get valuation rate from Item
						row.valuation_rate = frappe.get_value("Item", row.item_code, "valuation_rate")

		# throw all validation messages
		if self.validation_messages:
			for msg in self.validation_messages:
				msgprint(msg)

			raise frappe.ValidationError(self.validation_messages)

	def validate_item(self, item_code, row):
		from erpnext.stock.doctype.item.item import (
			validate_cancelled_item,
			validate_end_of_life,
			validate_is_stock_item,
		)

		# using try except to catch all validation msgs and display together

		try:
			item = frappe.get_doc("Item", item_code)

			# end of life and stock item
			validate_end_of_life(item_code, item.end_of_life, item.disabled)
			validate_is_stock_item(item_code, item.is_stock_item)

	def test_over_production_for_sales_order(self):
		so = make_sales_order(item_code="_Test FG Item", qty=2)

		if sl_entries:
			if has_serial_no:
				sl_entries = self.merge_similar_item_serial_nos(sl_entries)

			allow_negative_stock = False
			if has_batch_no:
				allow_negative_stock = True

			self.make_sl_entries(sl_entries, allow_negative_stock=allow_negative_stock)

		if has_serial_no and sl_entries:
			self.update_valuation_rate_for_serial_no()

	def get_sle_for_serialized_items(self, row, sl_entries):
		from erpnext.stock.stock_ledger import get_previous_sle

		serial_nos = get_serial_nos(row.serial_no)

		# To issue existing serial nos
		if row.current_qty and (row.current_serial_no or row.batch_no):
			args = self.get_sle_for_items(row)
			args.update(
				{
					"doctype": "Item Price",
					"item_code": "_Test FG Non Stock Item",
					"price_list_rate": 1000,
					"price_list": "_Test Price List India",
				}
			).insert(ignore_permissions=True)

		fg_item = "Finished Good Test Item For non stock"
		test_stock_entry.make_stock_entry(
			item_code="_Test FG Item", target="_Test Warehouse - _TC", qty=1, basic_rate=100
		)

		if not frappe.db.get_value("BOM", {"item": fg_item, "docstatus": 1}):
			bom = make_bom(
				item=fg_item,
				rate=1000,
				raw_materials=["_Test FG Item", "_Test FG Non Stock Item"],
				do_not_save=True,
			)
			bom.rm_cost_as_per = "Price List"  # non stock item won't have valuation rate
			bom.buying_price_list = "_Test Price List India"
			bom.currency = "INR"
			bom.save()

		wo = make_wo_order_test_record(production_item=fg_item)

		se = frappe.get_doc(make_stock_entry(wo.name, "Material Transfer for Manufacture", 1))
		se.insert()
		se.submit()

		ste = frappe.get_doc(make_stock_entry(wo.name, "Manufacture", 1))
		ste.insert()
		self.assertEqual(len(ste.additional_costs), 1)
		self.assertEqual(ste.total_additional_costs, 1000)

	@timeout(seconds=60)
	def test_job_card(self):
		stock_entries = []
		bom = frappe.get_doc("BOM", {"docstatus": 1, "with_operations": 1, "company": "_Test Company"})

		work_order = make_wo_order_test_record(
			item=bom.item, qty=1, bom_no=bom.name, source_warehouse="_Test Warehouse - _TC"
		)

		for row in work_order.required_items:
			stock_entry_doc = test_stock_entry.make_stock_entry(
				item_code=row.item_code, target="_Test Warehouse - _TC", qty=row.required_qty, basic_rate=100
			)

		ste = frappe.get_doc(make_stock_entry(work_order.name, "Material Transfer for Manufacture", 1))
		ste.submit()
		stock_entries.append(ste)

		job_cards = frappe.get_all(
			"Job Card", filters={"work_order": work_order.name}, order_by="creation asc"
		)
		self.assertEqual(len(job_cards), len(bom.operations))

		for i, job_card in enumerate(job_cards):
			doc = frappe.get_doc("Job Card", job_card)
			doc.time_logs[0].completed_qty = 1
			doc.submit()

		ste1 = frappe.get_doc(make_stock_entry(work_order.name, "Manufacture", 1))
		ste1.submit()
		stock_entries.append(ste1)

		for job_card in job_cards:
			doc = frappe.get_doc("Job Card", job_card)
			self.assertRaises(JobCardCancelError, doc.cancel)

		stock_entries.reverse()
		for stock_entry in stock_entries:
			stock_entry.cancel()

	def test_capcity_planning(self):
		frappe.db.set_value(
			"Manufacturing Settings",
			None,
			{"disable_capacity_planning": 0, "capacity_planning_for_days": 1},
		)

		data = frappe.get_cached_value(
			"BOM",
			{"docstatus": 1, "item": "_Test FG Item 2", "with_operations": 1, "company": "_Test Company"},
			["name", "item"],
		)

		if data:
			bom, bom_item = data

			planned_start_date = add_months(today(), months=-1)
			work_order = make_wo_order_test_record(
				item=bom_item, qty=10, bom_no=bom, planned_start_date=planned_start_date
			)

			previous_sle = get_previous_sle(
				{
					"item_code": row.item_code,
					"posting_date": self.posting_date,
					"posting_time": self.posting_time,
					"serial_no": serial_no,
				}
			)

			self.assertRaises(CapacityError, work_order1.submit)

			frappe.db.set_value("Manufacturing Settings", None, {"capacity_planning_for_days": 30})

			work_order1.reload()
			work_order1.submit()
			self.assertTrue(work_order1.docstatus, 1)

			work_order1.cancel()
			work_order.cancel()

	def test_work_order_with_non_transfer_item(self):
		frappe.db.set_value("Manufacturing Settings", None, "backflush_raw_materials_based_on", "BOM")

		items = {"Finished Good Transfer Item": 1, "_Test FG Item": 1, "_Test FG Item 1": 0}
		for item, allow_transfer in items.items():
			make_item(item, {"include_item_in_manufacturing": allow_transfer})

				new_args = args.copy()
				new_args.update(
					{
						"actual_qty": -1,
						"qty_after_transaction": qty_after_transaction,
						"warehouse": warehouse,
						"valuation_rate": previous_sle.get("valuation_rate"),
					}
				)

				sl_entries.append(new_args)

		if row.qty:
			args = self.get_sle_for_items(row)

			args.update(
				{
					"actual_qty": row.qty,
					"incoming_rate": row.valuation_rate,
					"valuation_rate": row.valuation_rate,
				}
			)

			sl_entries.append(args)

		bom_name = frappe.db.get_value(
			"BOM", {"item": fg_item, "is_active": 1, "with_operations": 1}, "name"
		)

		if not bom_name:
			bom = make_bom(item=fg_item, rate=1000, raw_materials=[rm1], do_not_save=True)
			bom.save()
			bom.submit()
			bom_name = bom.name

		ste1 = test_stock_entry.make_stock_entry(
			item_code=rm1, target="_Test Warehouse - _TC", qty=32, basic_rate=5000.0
		)

		work_order = make_wo_order_test_record(
			item=fg_item, skip_transfer=True, planned_start_date=now(), qty=1
		)
		ste1 = frappe.get_doc(make_stock_entry(work_order.name, "Manufacture", 1))
		for row in ste1.get("items"):
			if row.is_finished_item:
				self.assertEqual(row.item_code, fg_item)

		work_order = make_wo_order_test_record(
			item=fg_item, skip_transfer=True, planned_start_date=now(), qty=1
		)
		frappe.db.set_value("Manufacturing Settings", None, "make_serial_no_batch_from_work_order", 1)
		ste1 = frappe.get_doc(make_stock_entry(work_order.name, "Manufacture", 1))
		for row in ste1.get("items"):
			if row.is_finished_item:
				self.assertEqual(row.item_code, fg_item)

		work_order = make_wo_order_test_record(
			item=fg_item, skip_transfer=True, planned_start_date=now(), qty=30, do_not_save=True
		)
		work_order.batch_size = 10
		work_order.insert()
		work_order.submit()
		self.assertEqual(work_order.has_batch_no, 1)
		batches = frappe.get_all("Batch", filters={"reference_name": work_order.name})
		self.assertEqual(len(batches), 3)
		batches = [batch.name for batch in batches]

		ste1 = frappe.get_doc(make_stock_entry(work_order.name, "Manufacture", 10))
		for row in ste1.get("items"):
			if row.is_finished_item:
				self.assertEqual(row.item_code, fg_item)
				self.assertEqual(row.qty, 10)
				self.assertTrue(row.batch_no in batches)
				batches.remove(row.batch_no)

		ste1.submit()

		remaining_batches = []
		ste1 = frappe.get_doc(make_stock_entry(work_order.name, "Manufacture", 20))
		for row in ste1.get("items"):
			if row.is_finished_item:
				self.assertEqual(row.item_code, fg_item)
				self.assertEqual(row.qty, 10)
				remaining_batches.append(row.batch_no)

		self.assertEqual(sorted(remaining_batches), sorted(batches))

		frappe.db.set_value("Manufacturing Settings", None, "make_serial_no_batch_from_work_order", 0)

	def test_partial_material_consumption(self):
		frappe.db.set_value("Manufacturing Settings", None, "material_consumption", 1)
		wo_order = make_wo_order_test_record(planned_start_date=now(), qty=4)

		ste_cancel_list = []
		ste1 = test_stock_entry.make_stock_entry(
			item_code="_Test Item", target="_Test Warehouse - _TC", qty=20, basic_rate=5000.0
		)
		ste2 = test_stock_entry.make_stock_entry(
			item_code="_Test Item Home Desktop 100",
			target="_Test Warehouse - _TC",
			qty=20,
			basic_rate=1000.0,
		)

		ste_cancel_list.extend([ste1, ste2])

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Material Transfer for Manufacture", 4))
		s.submit()
		ste_cancel_list.append(s)

		ste1 = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 2))
		ste1.submit()
		ste_cancel_list.append(ste1)

	def update_valuation_rate_for_serial_no(self):
		for d in self.items:
			if not d.serial_no:
				continue

			serial_nos = get_serial_nos(d.serial_no)
			self.update_valuation_rate_for_serial_nos(d, serial_nos)

	def update_valuation_rate_for_serial_nos(self, row, serial_nos):
		valuation_rate = row.valuation_rate if self.docstatus == 1 else row.current_valuation_rate
		if valuation_rate is None:
			return

		for d in serial_nos:
			frappe.db.set_value("Serial No", d, "purchase_rate", valuation_rate)

	def get_sle_for_items(self, row, serial_nos=None):
		"""Insert Stock Ledger Entries"""

		if not serial_nos and row.serial_no:
			serial_nos = get_serial_nos(row.serial_no)

		data = frappe._dict(
			{
				"is_purchase_item": 0,
				"is_customer_provided_item": 1,
				"is_stock_item": 1,
				"include_item_in_manufacturing": 1,
				"customer": "_Test Customer",
			},
		)

		if not frappe.db.exists("BOM", {"item": finished_item}):
			make_bom(item=finished_item, raw_materials=[customer_provided_item], rm_qty=1)

		company = "_Test Company with perpetual inventory"
		customer_warehouse = create_warehouse("Test Customer Provided Warehouse", company=company)
		wo = make_wo_order_test_record(
			item=finished_item, qty=1, source_warehouse=customer_warehouse, company=company
		)

		ste = frappe.get_doc(make_stock_entry(wo.name, purpose="Material Transfer for Manufacture"))
		ste.insert()

		self.assertEqual(len(ste.items), 1)
		for item in ste.items:
			self.assertEqual(item.allow_zero_valuation_rate, 1)
			self.assertEqual(item.valuation_rate, 0)

	def test_valuation_rate_missing_on_make_stock_entry(self):
		item_name = "Test Valuation Rate Missing"
		rm_item = "_Test raw material item"
		make_item(
			item_name,
			{
				"is_stock_item": 1,
				"include_item_in_manufacturing": 1,
			},
		)
		make_item(
			"_Test raw material item",
			{
				"is_stock_item": 1,
				"include_item_in_manufacturing": 1,
			},
		)

		if not frappe.db.get_value("BOM", {"item": item_name}):
			make_bom(item=item_name, raw_materials=[rm_item], rm_qty=1)

		company = "_Test Company with perpetual inventory"
		source_warehouse = create_warehouse("Test Valuation Rate Missing Warehouse", company=company)
		wo = make_wo_order_test_record(
			item=item_name, qty=1, source_warehouse=source_warehouse, company=company
		)

		stock_entry = frappe.get_doc(make_stock_entry(wo.name, "Material Transfer for Manufacture"))
		self.assertRaises(frappe.ValidationError, stock_entry.save)

	def test_wo_completion_with_pl_bom(self):
		from erpnext.manufacturing.doctype.bom.test_bom import (
			create_bom_with_process_loss_item,
			create_process_loss_bom_items,
		)

		qty = 10
		scrap_qty = 0.25  # bom item qty = 1, consider as 25% of FG
		source_warehouse = "Stores - _TC"
		wip_warehouse = "_Test Warehouse - _TC"
		fg_item_non_whole, _, bom_item = create_process_loss_bom_items()

		test_stock_entry.make_stock_entry(
			item_code=bom_item.item_code, target=source_warehouse, qty=qty, basic_rate=100
		)

		bom_no = f"BOM-{fg_item_non_whole.item_code}-001"
		if not frappe.db.exists("BOM", bom_no):
			bom_doc = create_bom_with_process_loss_item(
				fg_item_non_whole, bom_item, fg_qty=1, process_loss_percentage=10
			)
			bom_doc.submit()

		wo = make_wo_order_test_record(
			production_item=fg_item_non_whole.item_code,
			bom_no=bom_no,
			wip_warehouse=wip_warehouse,
			qty=qty,
			skip_transfer=1,
			stock_uom=fg_item_non_whole.stock_uom,
		)

		se = frappe.get_doc(make_stock_entry(wo.name, "Material Transfer for Manufacture", qty))
		se.get("items")[0].s_warehouse = "Stores - _TC"
		se.insert()
		se.submit()

		se = frappe.get_doc(make_stock_entry(wo.name, "Manufacture", qty))
		se.insert()
		se.submit()

		# Testing stock entry values
		items = se.get("items")
		self.assertEqual(len(items), 2, "There should be 3 items including process loss.")
		fg_item = items[1]

		self.assertEqual(fg_item.qty, qty - 1)
		self.assertEqual(se.process_loss_percentage, 10)
		self.assertEqual(se.process_loss_qty, 1)

		wo.load_from_db()
		self.assertEqual(wo.status, "In Process")

		if changed_any_values:
			msgprint(
				_("Valuation rate for customer provided items has been set to zero."),
				title=_("Note"),
				indicator="blue",
			)

	def set_total_qty_and_amount(self):
		for d in self.get("items"):
			d.amount = flt(d.qty, d.precision("qty")) * flt(d.valuation_rate, d.precision("valuation_rate"))
			d.current_amount = flt(d.current_qty, d.precision("current_qty")) * flt(
				d.current_valuation_rate, d.precision("current_valuation_rate")
			)

			d.quantity_difference = flt(d.qty) - flt(d.current_qty)
			d.amount_difference = flt(d.amount) - flt(d.current_amount)

	def get_items_for(self, warehouse):
		self.items = []
		for item in get_items(warehouse, self.posting_date, self.posting_time, self.company):
			self.append("items", item)

	def submit(self):
		if len(self.items) > 100:
			msgprint(
				_(
					"The task has been enqueued as a background job. In case there is any issue on processing in background, the system will add a comment about the error on this Stock Reconciliation and revert to the Draft stage"
				)
			)
			self.queue_action("submit", timeout=2000)
		else:
			self._submit()

	def cancel(self):
		if len(self.items) > 100:
			msgprint(
				_(
					"The task has been enqueued as a background job. In case there is any issue on processing in background, the system will add a comment about the error on this Stock Reconciliation and revert to the Submitted stage"
				)
			)
			self.queue_action("cancel", timeout=2000)
		else:
			self._cancel()


@frappe.whitelist()
def get_items(
	warehouse, posting_date, posting_time, company, item_code=None, ignore_empty_stock=False
):
	ignore_empty_stock = cint(ignore_empty_stock)
	items = [frappe._dict({"item_code": item_code, "warehouse": warehouse})]

	if not item_code:
		items = get_items_for_stock_reco(warehouse, company)

	res = []
	itemwise_batch_data = get_itemwise_batch(warehouse, posting_date, company, item_code)

	for d in items:
		if d.item_code in itemwise_batch_data:
			valuation_rate = get_stock_balance(
				d.item_code, d.warehouse, posting_date, posting_time, with_valuation_rate=True
			)[1]

			for row in itemwise_batch_data.get(d.item_code):
				if ignore_empty_stock and not row.qty:
					continue

				args = get_item_data(row, row.qty, valuation_rate)
				res.append(args)
		else:
			stock_bal = get_stock_balance(
				d.item_code,
				d.warehouse,
				posting_date,
				posting_time,
				with_valuation_rate=True,
				with_serial_no=cint(d.has_serial_no),
			)
			qty, valuation_rate, serial_no = (
				stock_bal[0],
				stock_bal[1],
				stock_bal[2] if cint(d.has_serial_no) else "",
			)

			bom.submit()

		wo_order = make_wo_order_test_record(
			item=item, company=company, planned_start_date=now(), qty=20, skip_transfer=1
		)
		job_cards = frappe.db.get_value("Job Card", {"work_order": wo_order.name}, "name")

		if len(job_cards) == len(bom.operations):
			for jc in job_cards:
				job_card_doc = frappe.get_doc("Job Card", jc)
				job_card_doc.append(
					"time_logs",
					{"from_time": now(), "time_in_mins": 60, "completed_qty": job_card_doc.for_quantity},
				)

				job_card_doc.submit()

			close_work_order(wo_order, "Closed")
			self.assertEqual(wo_order.get("status"), "Closed")

	def test_fix_time_operations(self):
		bom = frappe.get_doc(
			{
				"doctype": "BOM",
				"item": "_Test FG Item 2",
				"is_active": 1,
				"is_default": 1,
				"quantity": 1.0,
				"with_operations": 1,
				"operations": [
					{
						"operation": "_Test Operation 1",
						"description": "_Test",
						"workstation": "_Test Workstation 1",
						"time_in_mins": 60,
						"operating_cost": 140,
						"fixed_time": 1,
					}
				],
				"items": [
					{
						"amount": 5000.0,
						"doctype": "BOM Item",
						"item_code": "_Test Item",
						"parentfield": "items",
						"qty": 1.0,
						"rate": 5000.0,
					},
				],
			}
		)
		bom.save()
		bom.submit()

		wo1 = make_wo_order_test_record(
			item=bom.item, bom_no=bom.name, qty=1, skip_transfer=1, do_not_submit=1
		)
		wo2 = make_wo_order_test_record(
			item=bom.item, bom_no=bom.name, qty=2, skip_transfer=1, do_not_submit=1
		)

		self.assertEqual(wo1.operations[0].time_in_mins, wo2.operations[0].time_in_mins)

	def test_partial_manufacture_entries(self):
		cancel_stock_entry = []

		frappe.db.set_value(
			"Manufacturing Settings",
			None,
			"backflush_raw_materials_based_on",
			"Material Transferred for Manufacture",
		)

		wo_order = make_wo_order_test_record(planned_start_date=now(), qty=100)
		ste1 = test_stock_entry.make_stock_entry(
			item_code="_Test Item", target="_Test Warehouse - _TC", qty=120, basic_rate=5000.0
		)
		ste2 = test_stock_entry.make_stock_entry(
			item_code="_Test Item Home Desktop 100",
			target="_Test Warehouse - _TC",
			qty=240,
			basic_rate=1000.0,
		)

		cancel_stock_entry.extend([ste1.name, ste2.name])

		sm = frappe.get_doc(make_stock_entry(wo_order.name, "Material Transfer for Manufacture", 100))
		for row in sm.get("items"):
			if row.get("item_code") == "_Test Item":
				row.qty = 120

		sm.submit()
		cancel_stock_entry.append(sm.name)

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 90))
		for row in s.get("items"):
			if row.get("item_code") == "_Test Item":
				self.assertEqual(row.get("qty"), 108)
		s.submit()
		cancel_stock_entry.append(s.name)

		s1 = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 5))
		for row in s1.get("items"):
			if row.get("item_code") == "_Test Item":
				self.assertEqual(row.get("qty"), 6)
		s1.submit()
		cancel_stock_entry.append(s1.name)

		s2 = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 5))
		for row in s2.get("items"):
			if row.get("item_code") == "_Test Item":
				self.assertEqual(row.get("qty"), 6)

		cancel_stock_entry.reverse()
		for ste in cancel_stock_entry:
			doc = frappe.get_doc("Stock Entry", ste)
			doc.cancel()

		frappe.db.set_value("Manufacturing Settings", None, "backflush_raw_materials_based_on", "BOM")

	@change_settings("Manufacturing Settings", {"make_serial_no_batch_from_work_order": 1})
	def test_auto_batch_creation(self):
		from erpnext.manufacturing.doctype.bom.test_bom import create_nested_bom

		fg_item = frappe.generate_hash(length=20)
		child_item = frappe.generate_hash(length=20)

			args = get_item_data(d, qty, valuation_rate, serial_no)

			res.append(args)

	return res


	@change_settings("Manufacturing Settings", {"make_serial_no_batch_from_work_order": 1})
	def test_auto_serial_no_creation(self):
		from erpnext.manufacturing.doctype.bom.test_bom import create_nested_bom

		fg_item = frappe.generate_hash(length=20)
		child_item = frappe.generate_hash(length=20)

		bom_tree = {fg_item: {child_item: {}}}

		create_nested_bom(bom_tree, prefix="")

		item = frappe.get_doc("Item", fg_item)
		item.has_serial_no = 1
		item.serial_no_series = f"{item.name}.#####"
		item.save()

		try:
			wo_order = make_wo_order_test_record(item=fg_item, qty=2, skip_transfer=True)
			serial_nos = wo_order.serial_no
			stock_entry = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 10))
			stock_entry.set_work_order_details()
			stock_entry.set_serial_no_batch_for_finished_good()
			for row in stock_entry.items:
				if row.item_code == fg_item:
					self.assertTrue(row.serial_no)
					self.assertEqual(sorted(get_serial_nos(row.serial_no)), sorted(get_serial_nos(serial_nos)))

		except frappe.MandatoryError:
			self.fail("Batch generation causing failing in Work Order")

	@change_settings(
		"Manufacturing Settings",
		{"backflush_raw_materials_based_on": "Material Transferred for Manufacture"},
	)

	items += frappe.db.sql(
		"""
		select
			i.name as item_code, i.item_name, id.default_warehouse as warehouse, i.has_serial_no, i.has_batch_no
		from
			tabItem i, `tabItem Default` id
		where
			i.name = id.parent
			and exists(
				select name from `tabWarehouse` where lft >= %s and rgt <= %s and name=id.default_warehouse
			)
			and i.is_stock_item = 1
			and i.has_variants = 0
			and IFNULL(i.disabled, 0) = 0
			and id.company = %s
		group by i.name
	""",
		(lft, rgt, company),
		as_dict=1,
	)

	# remove duplicates
	# check if item-warehouse key extracted from each entry exists in set iw_keys
	# and update iw_keys
	iw_keys = set()
	items = [
		item
		for item in items
		if [
			(item.item_code, item.warehouse) not in iw_keys,
			iw_keys.add((item.item_code, item.warehouse)),
		][0]
	]

	return items


def get_item_data(row, qty, valuation_rate, serial_no=None):
	return {
		"item_code": row.item_code,
		"warehouse": row.warehouse,
		"qty": qty,
		"item_name": row.item_name,
		"valuation_rate": valuation_rate,
		"current_qty": qty,
		"current_valuation_rate": valuation_rate,
		"current_serial_no": serial_no,
		"serial_no": serial_no,
		"batch_no": row.get("batch_no"),
	}


def get_itemwise_batch(warehouse, posting_date, company, item_code=None):
	from erpnext.stock.report.batch_wise_balance_history.batch_wise_balance_history import execute

	itemwise_batch_data = {}

	filters = frappe._dict(
		{"warehouse": warehouse, "from_date": posting_date, "to_date": posting_date, "company": company}
	)

	if item_code:
		filters.item_code = item_code

	columns, data = execute(filters)

	for row in data:
		itemwise_batch_data.setdefault(row[0], []).append(
			frappe._dict(
				{
					"item_code": row[0],
					"warehouse": warehouse,
					"qty": row[8],
					"item_name": row[1],
					"batch_no": row[4],
				}
			)
		)

	return itemwise_batch_data


	def test_backflushed_batch_raw_materials_based_on_transferred(self):
		frappe.db.set_value(
			"Manufacturing Settings",
			None,
			"backflush_raw_materials_based_on",
			"Material Transferred for Manufacture",
		)

		batch_item = "Test Batch MCC Keyboard"
		fg_item = "Test FG Item with Batch Raw Materials"

		ste_doc = test_stock_entry.make_stock_entry(
			item_code=batch_item, target="Stores - _TC", qty=2, basic_rate=100, do_not_save=True
		)

		ste_doc.append(
			"items",
			{
				"item_code": batch_item,
				"item_name": batch_item,
				"description": batch_item,
				"basic_rate": 100,
				"t_warehouse": "Stores - _TC",
				"qty": 2,
				"uom": "Nos",
				"stock_uom": "Nos",
				"conversion_factor": 1,
			},
		)

		# Inward raw materials in Stores warehouse
		ste_doc.insert()
		ste_doc.submit()

		batch_list = sorted([row.batch_no for row in ste_doc.items])

		wo_doc = make_wo_order_test_record(production_item=fg_item, qty=4)
		transferred_ste_doc = frappe.get_doc(
			make_stock_entry(wo_doc.name, "Material Transfer for Manufacture", 4)
		)

		transferred_ste_doc.items[0].qty = 2
		transferred_ste_doc.items[0].batch_no = batch_list[0]

		new_row = copy.deepcopy(transferred_ste_doc.items[0])
		new_row.name = ""
		new_row.batch_no = batch_list[1]

		# Transferred two batches from Stores to WIP Warehouse
		transferred_ste_doc.append("items", new_row)
		transferred_ste_doc.submit()

		# First Manufacture stock entry
		manufacture_ste_doc1 = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 1))

		# Batch no should be same as transferred Batch no
		self.assertEqual(manufacture_ste_doc1.items[0].batch_no, batch_list[0])
		self.assertEqual(manufacture_ste_doc1.items[0].qty, 1)

		manufacture_ste_doc1.submit()

		# Second Manufacture stock entry
		manufacture_ste_doc2 = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 2))

		# Batch no should be same as transferred Batch no
		self.assertEqual(manufacture_ste_doc2.items[0].batch_no, batch_list[0])
		self.assertEqual(manufacture_ste_doc2.items[0].qty, 1)
		self.assertEqual(manufacture_ste_doc2.items[1].batch_no, batch_list[1])
		self.assertEqual(manufacture_ste_doc2.items[1].qty, 1)

	def test_backflushed_serial_no_raw_materials_based_on_transferred(self):
		frappe.db.set_value(
			"Manufacturing Settings",
			None,
			"backflush_raw_materials_based_on",
			"Material Transferred for Manufacture",
		)

		sn_item = "Test Serial No BTT Headphone"
		fg_item = "Test FG Item with Serial No Raw Materials"

		ste_doc = test_stock_entry.make_stock_entry(
			item_code=sn_item, target="Stores - _TC", qty=4, basic_rate=100, do_not_save=True
		)

		# Inward raw materials in Stores warehouse
		ste_doc.submit()

		serial_nos_list = sorted(get_serial_nos(ste_doc.items[0].serial_no))

		wo_doc = make_wo_order_test_record(production_item=fg_item, qty=4)
		transferred_ste_doc = frappe.get_doc(
			make_stock_entry(wo_doc.name, "Material Transfer for Manufacture", 4)
		)

		transferred_ste_doc.items[0].serial_no = "\n".join(serial_nos_list)
		transferred_ste_doc.submit()

		# First Manufacture stock entry
		manufacture_ste_doc1 = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 1))

		# Serial nos should be same as transferred Serial nos
		self.assertEqual(get_serial_nos(manufacture_ste_doc1.items[0].serial_no), serial_nos_list[0:1])
		self.assertEqual(manufacture_ste_doc1.items[0].qty, 1)

		manufacture_ste_doc1.submit()

		# Second Manufacture stock entry
		manufacture_ste_doc2 = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 2))

		# Serial nos should be same as transferred Serial nos
		self.assertEqual(get_serial_nos(manufacture_ste_doc2.items[0].serial_no), serial_nos_list[1:3])
		self.assertEqual(manufacture_ste_doc2.items[0].qty, 2)

	def test_backflushed_serial_no_batch_raw_materials_based_on_transferred(self):
		frappe.db.set_value(
			"Manufacturing Settings",
			None,
			"backflush_raw_materials_based_on",
			"Material Transferred for Manufacture",
		)

		sn_batch_item = "Test Batch Serial No WebCam"
		fg_item = "Test FG Item with Serial & Batch No Raw Materials"

		ste_doc = test_stock_entry.make_stock_entry(
			item_code=sn_batch_item, target="Stores - _TC", qty=2, basic_rate=100, do_not_save=True
		)

		ste_doc.append(
			"items",
			{
				"item_code": sn_batch_item,
				"item_name": sn_batch_item,
				"description": sn_batch_item,
				"basic_rate": 100,
				"t_warehouse": "Stores - _TC",
				"qty": 2,
				"uom": "Nos",
				"stock_uom": "Nos",
				"conversion_factor": 1,
			},
		)

		# Inward raw materials in Stores warehouse
		ste_doc.insert()
		ste_doc.submit()

		batch_dict = {row.batch_no: get_serial_nos(row.serial_no) for row in ste_doc.items}
		batches = list(batch_dict.keys())

		wo_doc = make_wo_order_test_record(production_item=fg_item, qty=4)
		transferred_ste_doc = frappe.get_doc(
			make_stock_entry(wo_doc.name, "Material Transfer for Manufacture", 4)
		)

		transferred_ste_doc.items[0].qty = 2
		transferred_ste_doc.items[0].batch_no = batches[0]
		transferred_ste_doc.items[0].serial_no = "\n".join(batch_dict.get(batches[0]))

		new_row = copy.deepcopy(transferred_ste_doc.items[0])
		new_row.name = ""
		new_row.batch_no = batches[1]
		new_row.serial_no = "\n".join(batch_dict.get(batches[1]))

		# Transferred two batches from Stores to WIP Warehouse
		transferred_ste_doc.append("items", new_row)
		transferred_ste_doc.submit()

		# First Manufacture stock entry
		manufacture_ste_doc1 = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 1))

		# Batch no & Serial Nos should be same as transferred Batch no & Serial Nos
		batch_no = manufacture_ste_doc1.items[0].batch_no
		self.assertEqual(
			get_serial_nos(manufacture_ste_doc1.items[0].serial_no)[0], batch_dict.get(batch_no)[0]
		)
		self.assertEqual(manufacture_ste_doc1.items[0].qty, 1)

		manufacture_ste_doc1.submit()

		# Second Manufacture stock entry
		manufacture_ste_doc2 = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 2))

		# Batch no & Serial Nos should be same as transferred Batch no & Serial Nos
		batch_no = manufacture_ste_doc2.items[0].batch_no
		self.assertEqual(
			get_serial_nos(manufacture_ste_doc2.items[0].serial_no)[0], batch_dict.get(batch_no)[1]
		)
		self.assertEqual(manufacture_ste_doc2.items[0].qty, 1)

		batch_no = manufacture_ste_doc2.items[1].batch_no
		self.assertEqual(
			get_serial_nos(manufacture_ste_doc2.items[1].serial_no)[0], batch_dict.get(batch_no)[0]
		)
		self.assertEqual(manufacture_ste_doc2.items[1].qty, 1)

	def test_non_consumed_material_return_against_work_order(self):
		frappe.db.set_value(
			"Manufacturing Settings",
			None,
			"backflush_raw_materials_based_on",
			"Material Transferred for Manufacture",
		)

		item = make_item(
			"Test FG Item To Test Return Case",
			{
				"is_stock_item": 1,
			},
		)

		item_code = item.name
		bom_doc = make_bom(
			item=item_code,
			source_warehouse="Stores - _TC",
			raw_materials=["Test Batch MCC Keyboard", "Test Serial No BTT Headphone"],
		)

		# Create a work order
		wo_doc = make_wo_order_test_record(production_item=item_code, qty=5)
		wo_doc.save()

		self.assertEqual(wo_doc.bom_no, bom_doc.name)

		# Transfer material for manufacture
		ste_doc = frappe.get_doc(make_stock_entry(wo_doc.name, "Material Transfer for Manufacture", 5))
		for row in ste_doc.items:
			row.qty += 2
			row.transfer_qty += 2
			nste_doc = test_stock_entry.make_stock_entry(
				item_code=row.item_code, target="Stores - _TC", qty=row.qty, basic_rate=100
			)

			row.batch_no = nste_doc.items[0].batch_no
			row.serial_no = nste_doc.items[0].serial_no

		ste_doc.save()
		ste_doc.submit()
		ste_doc.load_from_db()

		# Create a stock entry to manufacture the item
		ste_doc = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 5))
		for row in ste_doc.items:
			if row.s_warehouse and not row.t_warehouse:
				row.qty -= 2
				row.transfer_qty -= 2

				if row.serial_no:
					serial_nos = get_serial_nos(row.serial_no)
					row.serial_no = "\n".join(serial_nos[0:5])

		ste_doc.save()
		ste_doc.submit()

		wo_doc.load_from_db()
		for row in wo_doc.required_items:
			self.assertEqual(row.transferred_qty, 7)
			self.assertEqual(row.consumed_qty, 5)

		self.assertEqual(wo_doc.status, "Completed")
		return_ste_doc = make_stock_return_entry(wo_doc.name)
		return_ste_doc.save()

		self.assertTrue(return_ste_doc.is_return)
		for row in return_ste_doc.items:
			self.assertEqual(row.qty, 2)

	def test_workstation_type_for_work_order(self):
		prepare_data_for_workstation_type_check()

		workstation_types = ["Workstation Type 1", "Workstation Type 2", "Workstation Type 3"]
		planned_start_date = "2022-11-14 10:00:00"

		wo_order = make_wo_order_test_record(
			item="Test FG Item For Workstation Type", planned_start_date=planned_start_date, qty=2
		)

		job_cards = frappe.get_all(
			"Job Card",
			fields=[
				"`tabJob Card`.`name`",
				"`tabJob Card`.`workstation_type`",
				"`tabJob Card`.`workstation`",
				"`tabJob Card Time Log`.`from_time`",
				"`tabJob Card Time Log`.`to_time`",
				"`tabJob Card Time Log`.`time_in_mins`",
			],
			filters=[
				["Job Card", "work_order", "=", wo_order.name],
				["Job Card Time Log", "docstatus", "=", 1],
			],
			order_by="`tabJob Card`.`creation` desc",
		)

		workstations_to_check = ["Workstation 1", "Workstation 3", "Workstation 5"]
		for index, row in enumerate(job_cards):
			if index != 0:
				planned_start_date = add_to_date(planned_start_date, minutes=40)

			self.assertEqual(row.workstation_type, workstation_types[index])
			self.assertEqual(row.from_time, planned_start_date)
			self.assertEqual(row.to_time, add_to_date(planned_start_date, minutes=30))
			self.assertEqual(row.workstation, workstations_to_check[index])

		planned_start_date = "2022-11-14 10:00:00"

		wo_order = make_wo_order_test_record(
			item="Test FG Item For Workstation Type", planned_start_date=planned_start_date, qty=2
		)

		job_cards = frappe.get_all(
			"Job Card",
			fields=[
				"`tabJob Card`.`name`",
				"`tabJob Card`.`workstation_type`",
				"`tabJob Card`.`workstation`",
				"`tabJob Card Time Log`.`from_time`",
				"`tabJob Card Time Log`.`to_time`",
				"`tabJob Card Time Log`.`time_in_mins`",
			],
			filters=[
				["Job Card", "work_order", "=", wo_order.name],
				["Job Card Time Log", "docstatus", "=", 1],
			],
			order_by="`tabJob Card`.`creation` desc",
		)

		workstations_to_check = ["Workstation 2", "Workstation 4", "Workstation 6"]
		for index, row in enumerate(job_cards):
			if index != 0:
				planned_start_date = add_to_date(planned_start_date, minutes=40)

			self.assertEqual(row.workstation_type, workstation_types[index])
			self.assertEqual(row.from_time, planned_start_date)
			self.assertEqual(row.to_time, add_to_date(planned_start_date, minutes=30))
			self.assertEqual(row.workstation, workstations_to_check[index])

	def test_job_card_extra_qty(self):
		items = [
			"Test FG Item for Scrap Item Test 1",
			"Test RM Item 1 for Scrap Item Test 1",
			"Test RM Item 2 for Scrap Item Test 1",
		]

		company = "_Test Company with perpetual inventory"
		for item_code in items:
			create_item(
				item_code=item_code,
				is_stock_item=1,
				is_purchase_item=1,
				opening_stock=100,
				valuation_rate=10,
				company=company,
				warehouse="Stores - TCP1",
			)

		item = "Test FG Item for Scrap Item Test 1"
		raw_materials = ["Test RM Item 1 for Scrap Item Test 1", "Test RM Item 2 for Scrap Item Test 1"]
		if not frappe.db.get_value("BOM", {"item": item}):
			bom = make_bom(
				item=item, source_warehouse="Stores - TCP1", raw_materials=raw_materials, do_not_save=True
			)
			bom.with_operations = 1
			bom.append(
				"operations",
				{
					"operation": "_Test Operation 1",
					"workstation": "_Test Workstation 1",
					"hour_rate": 20,
					"time_in_mins": 60,
				},
			)

			bom.submit()

		wo_order = make_wo_order_test_record(
			item=item,
			company=company,
			planned_start_date=now(),
			qty=20,
		)
		job_card = frappe.db.get_value("Job Card", {"work_order": wo_order.name}, "name")
		job_card_doc = frappe.get_doc("Job Card", job_card)

		# Make another Job Card for the same Work Order
		job_card2 = frappe.copy_doc(job_card_doc)
		self.assertRaises(frappe.ValidationError, job_card2.save)


def prepare_data_for_workstation_type_check():
	from erpnext.manufacturing.doctype.operation.test_operation import make_operation
	from erpnext.manufacturing.doctype.workstation.test_workstation import make_workstation
	from erpnext.manufacturing.doctype.workstation_type.test_workstation_type import (
		create_workstation_type,
	)

	workstation_types = ["Workstation Type 1", "Workstation Type 2", "Workstation Type 3"]
	for workstation_type in workstation_types:
		create_workstation_type(workstation_type=workstation_type)

	operations = ["Cutting", "Sewing", "Packing"]
	for operation in operations:
		make_operation(
			{
				"operation": operation,
			}
		)

	workstations = [
		{
			"workstation": "Workstation 1",
			"workstation_type": "Workstation Type 1",
		},
		{
			"workstation": "Workstation 2",
			"workstation_type": "Workstation Type 1",
		},
		{
			"workstation": "Workstation 3",
			"workstation_type": "Workstation Type 2",
		},
		{
			"workstation": "Workstation 4",
			"workstation_type": "Workstation Type 2",
		},
		{
			"workstation": "Workstation 5",
			"workstation_type": "Workstation Type 3",
		},
		{
			"workstation": "Workstation 6",
			"workstation_type": "Workstation Type 3",
		},
	]

	for row in workstations:
		make_workstation(row)

	fg_item = make_item(
		"Test FG Item For Workstation Type",
		{
			"is_stock_item": 1,
		},
	)

	rm_item = make_item(
		"Test RM Item For Workstation Type",
		{
			"is_stock_item": 1,
		},
	)

	if not frappe.db.exists("BOM", {"item": fg_item.name}):
		bom_doc = make_bom(
			item=fg_item.name,
			source_warehouse="Stores - _TC",
			raw_materials=[rm_item.name],
			do_not_submit=True,
		)

		submit_bom = False
		for index, operation in enumerate(operations):
			if not frappe.db.exists("BOM Operation", {"parent": bom_doc.name, "operation": operation}):
				bom_doc.append(
					"operations",
					{
						"operation": operation,
						"time_in_mins": 30,
						"hour_rate": 100,
						"workstation_type": workstation_types[index],
					},
				)

				submit_bom = True

		if submit_bom:
			bom_doc.submit()


def prepare_data_for_backflush_based_on_materials_transferred():
	batch_item_doc = make_item(
		"Test Batch MCC Keyboard",
		{
			"is_stock_item": 1,
			"has_batch_no": 1,
			"create_new_batch": 1,
			"batch_number_series": "TBMK.#####",
			"valuation_rate": 100,
			"stock_uom": "Nos",
		},
	)

	item = make_item(
		"Test FG Item with Batch Raw Materials",
		{
			"is_stock_item": 1,
		},
	)

	make_bom(item=item.name, source_warehouse="Stores - _TC", raw_materials=[batch_item_doc.name])

	sn_item_doc = make_item(
		"Test Serial No BTT Headphone",
		{
			"is_stock_item": 1,
			"has_serial_no": 1,
			"serial_no_series": "TSBH.#####",
			"valuation_rate": 100,
			"stock_uom": "Nos",
		},
	)

	item = make_item(
		"Test FG Item with Serial No Raw Materials",
		{
			"is_stock_item": 1,
		},
	)

	make_bom(item=item.name, source_warehouse="Stores - _TC", raw_materials=[sn_item_doc.name])

	sn_batch_item_doc = make_item(
		"Test Batch Serial No WebCam",
		{
			"is_stock_item": 1,
			"has_batch_no": 1,
			"create_new_batch": 1,
			"batch_number_series": "TBSW.#####",
			"has_serial_no": 1,
			"serial_no_series": "TBSWC.#####",
			"valuation_rate": 100,
			"stock_uom": "Nos",
		},
	)

	item = make_item(
		"Test FG Item with Serial & Batch No Raw Materials",
		{
			"is_stock_item": 1,
		},
	)

	make_bom(item=item.name, source_warehouse="Stores - _TC", raw_materials=[sn_batch_item_doc.name])


def update_job_card(job_card, jc_qty=None):
	employee = frappe.db.get_value("Employee", {"status": "Active"}, "name")
	job_card_doc = frappe.get_doc("Job Card", job_card)
	job_card_doc.set(
		"scrap_items",
		[
			{"item_code": "Test RM Item 1 for Scrap Item Test", "stock_qty": 2},
			{"item_code": "Test RM Item 2 for Scrap Item Test", "stock_qty": 2},
		],
	)

	serial_nos = ""
	with_serial_no = True if item_dict.get("has_serial_no") else False
	data = get_stock_balance(
		item_code,
		warehouse,
		posting_date,
		posting_time,
		with_valuation_rate=with_valuation_rate,
		with_serial_no=with_serial_no,
	)

	if with_serial_no:
		qty, rate, serial_nos = data
	else:
		qty, rate = data

	if item_dict.get("has_batch_no"):
		qty = (
			get_batch_qty(batch_no, warehouse, posting_date=posting_date, posting_time=posting_time) or 0
		)

	return {"qty": qty, "rate": rate, "serial_nos": serial_nos}


@frappe.whitelist()
def get_difference_account(purpose, company):
	if purpose == "Stock Reconciliation":
		account = get_company_default(company, "stock_adjustment_account")
	else:
		account = frappe.db.get_value(
			"Account", {"is_group": 0, "company": company, "account_type": "Temporary"}, "name"
		)

	wo_order = frappe.new_doc("Work Order")
	wo_order.production_item = args.production_item or args.item or args.item_code or "_Test FG Item"
	wo_order.bom_no = args.bom_no or frappe.db.get_value(
		"BOM", {"item": wo_order.production_item, "is_active": 1, "is_default": 1}
	)
	wo_order.qty = args.qty or 10
	wo_order.wip_warehouse = args.wip_warehouse or "_Test Warehouse - _TC"
	wo_order.fg_warehouse = args.fg_warehouse or "_Test Warehouse 1 - _TC"
	wo_order.scrap_warehouse = args.fg_warehouse or "_Test Scrap Warehouse - _TC"
	wo_order.company = args.company or "_Test Company"
	wo_order.stock_uom = args.stock_uom or "_Test UOM"
	wo_order.use_multi_level_bom = args.use_multi_level_bom or 0
	wo_order.skip_transfer = args.skip_transfer or 0
	wo_order.get_items_and_operations_from_bom()
	wo_order.sales_order = args.sales_order or None
	wo_order.planned_start_date = args.planned_start_date or now()
	wo_order.transfer_material_against = args.transfer_material_against or "Work Order"

	if args.source_warehouse:
		for item in wo_order.get("required_items"):
			item.source_warehouse = args.source_warehouse

	if not args.do_not_save:
		wo_order.insert()

		if not args.do_not_submit:
			wo_order.submit()
	return wo_order


test_records = frappe.get_test_records("Work Order")
