# Copyright 2021 ACSONE SA/NV
# Copyright 2022 Tecnativa - Víctor Martínez
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from psycopg2 import IntegrityError

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

from .common import IntrastatProductCommon


class TestIntrastatProduct(IntrastatProductCommon):
    """Tests for this module"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env["product.product"].create(
            {
                "name": "Test product",
                "hs_code_id": cls.env.ref("product_harmonized_system.84715000").id,
                "origin_country_id": cls.env.ref("base.de").id,
                "weight": 1.25,
            }
        )
        cls.partner = cls.partner_obj.create(
            {
                "name": "Test partner",
                "country_id": cls.env.ref("base.fr").id,
                "invoice_intrastat_detail": True,
            }
        )
        cls.env["account.journal"].create(
            {"name": "Test sale journal", "type": "sale", "code": "TEST-sale"}
        )
        cls.report_obj = cls.env["ir.actions.report"]
        cls.sale_order = cls._create_sale_order(cls)
        cls.sale_order.action_confirm()
        cls.sale_order.order_line.qty_delivered = 1
        cls.invoice = cls.sale_order._create_invoices()

    # Test duplicates
    @mute_logger("odoo.sql_db")
    def test_region(self):
        with self.assertRaises(IntegrityError):
            self._create_region()

    @mute_logger("odoo.sql_db")
    def test_transaction(self):
        self._create_transaction()
        with self.assertRaises(IntegrityError):
            self._create_transaction()

    @mute_logger("odoo.sql_db")
    def test_transport_mode(self):
        vals = {"code": 1, "name": "Sea"}
        with self.assertRaises(IntegrityError):
            self._create_transport_mode(vals)

    def test_copy(self):
        """
        When copying declaration, the new one has an incremented revision
        value.
        """
        vals = {"declaration_type": "dispatches"}
        self._create_declaration(vals)
        decl_copy = self.declaration.copy()
        self.assertEqual(self.declaration.revision + 1, decl_copy.revision)

    def test_declaration_manual_lines(self):
        vals = {"declaration_type": "dispatches", "reporting_level": "extended"}
        self._create_declaration(vals)
        computation_line_form = Form(
            self.env["intrastat.product.computation.line"].with_context(
                default_parent_id=self.declaration.id
            )
        )
        computation_line_form.src_dest_country_id = self.env.ref("base.fr")
        computation_line_form.transaction_id = self.transaction
        computation_line_form.hs_code_id = self.hs_code_computer
        computation_line_form.region_code = "ZZ"
        computation_line_form.product_origin_country_code = "BE"
        computation_line_form.transport_id = self.env.ref(
            "intrastat_product.intrastat_transport_3"
        )
        computation_line = computation_line_form.save()
        self.assertEqual(computation_line.src_dest_country_code, "FR")
        declaration_line_form = Form(
            self.env["intrastat.product.declaration.line"].with_context(
                default_parent_id=self.declaration.id
            )
        )
        declaration_line_form.src_dest_country_code = "FR"
        declaration_line = declaration_line_form.save()
        self.assertEqual(declaration_line.src_dest_country_code, "FR")

        # Test Greece country code conversion
        declaration_line_form_greece = Form(
            self.env["intrastat.product.declaration.line"].with_context(
                default_parent_id=self.declaration.id
            )
        )
        declaration_line_form_greece.src_dest_country_id = self.env.ref("base.gr")
        declaration_line_form_greece = declaration_line_form.save()
        self.assertEqual(declaration_line_form_greece.src_dest_country_code, "EL")

    def test_declaration_no_country(self):
        self.demo_company.country_id = False
        with self.assertRaises(ValidationError):
            self._create_declaration()
            self.declaration.flush()

    def test_declaration_no_vat(self):
        self.demo_company.partner_id.vat = False
        with self.assertRaises(UserError):
            self._create_declaration()
            self.declaration._check_generate_xml()

    def test_declaration_state(self):
        self._create_declaration()
        self.declaration.unlink()

        self._create_declaration()
        self.declaration.state = "done"
        with self.assertRaises(UserError):
            self.declaration.unlink()

    def _create_sale_order(self):
        order_form = Form(self.env["sale.order"])
        order_form.partner_id = self.partner
        with order_form.order_line.new() as line_form:
            line_form.product_id = self.product
        with order_form.order_line.new() as line_form:
            line_form.product_id = self.product
        return order_form.save()

    def _test_invoice_report(self, weight):
        """We need to check weight because if intrastat_line_ids already exist
        the weight will be different because weight field in model is integer."""
        res = self.report_obj._render(
            "account.report_invoice_with_payments", self.invoice.ids
        )
        self.assertRegex(str(res[0]), self.product.hs_code_id.hs_code)
        self.assertRegex(str(res[0]), self.product.origin_country_id.name)
        res = list(self.invoice._get_intrastat_lines_info())
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["product_id"], self.product)
        self.assertEqual(res[0]["hs_code_id"], self.product.hs_code_id)
        self.assertEqual(res[0]["origin_country_id"], self.product.origin_country_id)
        self.assertEqual(res[0]["weight"], weight)

    def test_invoice_report_without_intrastat_lines(self):
        self._test_invoice_report(2.5)

    def test_invoice_report_with_intrastat_lines(self):
        self.invoice.compute_intrastat_lines()
        self._test_invoice_report(2)

    def test_zero_price_lines_transaction_23(self):
        """Zero price lines included in Intrastat must use transaction 23
        and the product's sale price as fiscal value."""
        self.demo_company.intrastat_include_zero_price_lines = True
        product_c3po = self.product_c3po.product_variant_ids[0]
        list_price = product_c3po.list_price
        self.assertTrue(list_price, "Product must have a list_price for this test")
        today = fields.Date.context_today(self.declaration_obj)
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "fiscal_position_id": self.position.id,
                "invoice_date": today,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": product_c3po.id,
                            "quantity": 3,
                            "price_unit": 0.0,
                            "name": "Warranty replacement",
                        },
                    )
                ],
            }
        )
        invoice.action_post()
        declaration = self.declaration_obj.create(
            {
                "company_id": self.demo_company.id,
                "declaration_type": "dispatches",
                "year": str(today.year),
                "month": str(today.month).zfill(2),
            }
        )
        declaration.action_gather()
        zero_lines = declaration.computation_line_ids.filtered(
            lambda l: l.invoice_line_id.move_id == invoice
        )
        self.assertEqual(len(zero_lines), 1)
        tr_23 = self.env.ref("intrastat_product.intrastat_transaction_23")
        self.assertEqual(zero_lines.transaction_id, tr_23)
        self.assertEqual(zero_lines.amount_company_currency, list_price * 3)

    def test_zero_price_lines_statistical_value(self):
        """When line_vals carries statistical_value_company_currency (added by
        downstream l10n_es_intrastat_accessory_costs), the zero-price handler
        also updates it with product's sale price."""
        product_c3po = self.product_c3po.product_variant_ids[0]
        list_price = product_c3po.list_price
        self.assertTrue(list_price, "Product must have a list_price for this test")
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "fiscal_position_id": self.position.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": product_c3po.id,
                            "quantity": 4,
                            "price_unit": 0.0,
                            "name": "Warranty replacement",
                        },
                    )
                ],
            }
        )
        invoice.action_post()
        declaration = self.declaration_obj.create(
            {
                "company_id": self.demo_company.id,
                "declaration_type": "dispatches",
            }
        )
        line_vals = {
            "invoice_line_id": invoice.invoice_line_ids[0].id,
            "amount_company_currency": 0.0,
            "statistical_value_company_currency": 0.0,
        }
        declaration._update_zero_price_line_vals(line_vals)
        self.assertEqual(line_vals["amount_company_currency"], list_price * 4)
        self.assertEqual(
            line_vals["statistical_value_company_currency"], list_price * 4
        )

    def test_zero_price_lines_with_preexisting_statistical(self):
        """When downstream modules (e.g. l10n_es_intrastat_statistic_added_cost)
        have already added a volumetric portion to statistical_value before
        this handler runs, the sale price is added on top — preserving the
        client's literal formula (qty * volume * added_cost) + (list_price * qty).
        Fiscal value receives the sale price added to whatever the accessory
        cost handler prorated; the two fields can differ if amount has the
        transport pro_rata and statistical has the statistic_added_cost portion."""
        product_c3po = self.product_c3po.product_variant_ids[0]
        list_price = product_c3po.list_price
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "fiscal_position_id": self.position.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": product_c3po.id,
                            "quantity": 2,
                            "price_unit": 0.0,
                            "name": "Warranty replacement",
                        },
                    )
                ],
            }
        )
        invoice.action_post()
        declaration = self.declaration_obj.create(
            {
                "company_id": self.demo_company.id,
                "declaration_type": "dispatches",
            }
        )
        amount_initial = 50.0
        statistical_initial = 30.0
        line_vals = {
            "invoice_line_id": invoice.invoice_line_ids[0].id,
            "amount_company_currency": amount_initial,
            "statistical_value_company_currency": statistical_initial,
        }
        declaration._update_zero_price_line_vals(line_vals)
        sale_value = list_price * 2
        self.assertEqual(
            line_vals["amount_company_currency"], amount_initial + sale_value
        )
        self.assertEqual(
            line_vals["statistical_value_company_currency"],
            statistical_initial + sale_value,
        )

    def test_zero_price_lines_statistical_value_absent(self):
        """When line_vals does not carry statistical_value_company_currency
        (no downstream module that adds the field), the handler skips it
        without crashing."""
        product_c3po = self.product_c3po.product_variant_ids[0]
        list_price = product_c3po.list_price
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "fiscal_position_id": self.position.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": product_c3po.id,
                            "quantity": 1,
                            "price_unit": 0.0,
                            "name": "Warranty replacement",
                        },
                    )
                ],
            }
        )
        invoice.action_post()
        declaration = self.declaration_obj.create(
            {
                "company_id": self.demo_company.id,
                "declaration_type": "dispatches",
            }
        )
        line_vals = {
            "invoice_line_id": invoice.invoice_line_ids[0].id,
            "amount_company_currency": 0.0,
        }
        declaration._update_zero_price_line_vals(line_vals)
        self.assertEqual(line_vals["amount_company_currency"], list_price)
        self.assertNotIn("statistical_value_company_currency", line_vals)

    def test_zero_price_lines_excluded_by_default(self):
        """Zero price lines must be excluded when config is disabled."""
        self.demo_company.intrastat_include_zero_price_lines = False
        product_c3po = self.product_c3po.product_variant_ids[0]
        today = fields.Date.context_today(self.declaration_obj)
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "fiscal_position_id": self.position.id,
                "invoice_date": today,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": product_c3po.id,
                            "quantity": 1,
                            "price_unit": 0.0,
                            "name": "Warranty replacement",
                        },
                    )
                ],
            }
        )
        invoice.action_post()
        declaration = self.declaration_obj.create(
            {
                "company_id": self.demo_company.id,
                "declaration_type": "dispatches",
                "year": str(today.year),
                "month": str(today.month).zfill(2),
            }
        )
        declaration.action_gather()
        zero_lines = declaration.computation_line_ids.filtered(
            lambda l: l.invoice_line_id.move_id == invoice
        )
        self.assertFalse(zero_lines)


class TestIntrastatProductCase(TestIntrastatProduct, TransactionCase):
    """Test Intrastat Product"""
